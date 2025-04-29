import os
import json
from datetime import datetime, timedelta

import pytz
import dateparser
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from apscheduler.schedulers.background import BackgroundScheduler
from openai import OpenAI

# === CONFIG DA VARIABILI D’AMBIENTE ===
OPENAI_API_KEY         = os.getenv("OPENAI_API_KEY")
TWILIO_ACCOUNT_SID     = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN      = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")

if not OPENAI_API_KEY:
    raise RuntimeError("Devi impostare OPENAI_API_KEY")
if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_NUMBER]):
    raise RuntimeError("Devi impostare TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN e TWILIO_WHATSAPP_NUMBER")

openai_client = OpenAI(api_key=OPENAI_API_KEY)

app = Flask(__name__)
tz = pytz.timezone("Europe/Rome")
memory_file = "memory.json"

# === MEMORIA ===
def load_memory():
    if os.path.exists(memory_file):
        with open(memory_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_memory(mem):
    with open(memory_file, "w", encoding="utf-8") as f:
        json.dump(mem, f, ensure_ascii=False, indent=2)

memory = load_memory()

# === ESTRAZIONE EVENTI ===
def estrai_evento(text: str):
    today = datetime.now(tz).date().isoformat()
    prompt = f"""You are a JSON assistant. Current date is {today} (Europe/Rome timezone).
Given the user message below, extract **exactly** the planned action and the exact datetime (with hours and minutes) in ISO-8601 format (including timezone).
- Resolve relative expressions like "Lunedì prossimo alle 15" to the actual next Monday at 15:00.
- If the message includes a date and time, include both; if only a date, assume 00:00:00; if none, return nulls.

Respond **only** with a JSON object, no extra text:

{{"azione": "…", "data": "YYYY-MM-DDTHH:MM:SS+02:00"}}

User message:
\"\"\"{text}\"\"\"
"""
    resp = openai_client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}]
    )
    content = resp.choices[0].message.content.strip()
    try:
        ev = json.loads(content)
        if "azione" in ev and "data" in ev:
            return ev
    except Exception:
        pass
    return {"azione": None, "data": None}

# === SCHEDULER ===
scheduler = BackgroundScheduler()

def check_eventi():
    now = datetime.now(tz)
    for user, data in memory.items():
        nuovi = []
        for ev in data.get("events", []):
            dt = datetime.fromisoformat(ev["datetime_evento"]).astimezone(tz)
            az = ev.get("azione", "")

            # reminder 1h prima
            if not ev.get("sent_before") and now >= dt - timedelta(hours=1):
                orario = dt.strftime("%H:%M")
                testo = (
                    f"Ehi! Alle {orario} avevi in programma “{az}” – "
                    "è quasi il momento, preparati! 😉"
                )
                print(f"[DEBUG] INVIO reminder-before a {user} per evento “{az}” alle {orario}")
                send_whatsapp(user, testo)
                ev["sent_before"] = True

            # follow-up 2h dopo
            if not ev.get("sent_after") and now >= dt + timedelta(hours=2):
                testo2 = f"Com'è andata l'attività “{az}”? Raccontami!"
                print(f"[DEBUG] INVIO follow-up-after a {user} per evento “{az}”")
                send_whatsapp(user, testo2)
                ev["sent_after"] = True

            if not (ev.get("sent_before") and ev.get("sent_after")):
                nuovi.append(ev)

        memory[user]["events"] = nuovi

    save_memory(memory)

scheduler.add_job(check_eventi, "interval", minutes=1)
scheduler.start()

# === INVIO WHATSAPP REALE ===
def send_whatsapp(to: str, body: str):
    from twilio.rest import Client as TwilioClient
    tw = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    msg = tw.messages.create(
        body=body,
        from_=TWILIO_WHATSAPP_NUMBER,
        to=to
    )
    print(f"[TWILIO] inviato a {to}, SID={msg.sid}")

# === ENDPOINT FLASK ===
@app.route("/bot", methods=["POST"])
def bot():
    incoming = request.values.get("Body", "").strip()
    sender   = request.values.get("From", "").strip()
    print(f"Messaggio ricevuto da {sender}: {incoming}")

    memory.setdefault(sender, {"messages": [], "events": []})
    memory[sender]["messages"].append({"role": "user", "content": incoming})

    # prova a estrarre un evento futuro
    ev = estrai_evento(incoming)
    print("[DEBUG] Evento estratto:", ev)

    if ev["azione"] and ev["data"]:
        dt = dateparser.parse(
            ev["data"],
            settings={
                "TIMEZONE": "Europe/Rome",
                "RETURN_AS_TIMEZONE_AWARE": True,
                "PREFER_DATES_FROM": "future"
            }
        )
        if dt:
            ev["datetime_evento"] = dt.isoformat()
            memory[sender]["events"].append(ev)
            save_memory(memory)
            human_date = dt.strftime("%d/%m alle %H:%M")
            reply = f"Perfetto, ho registrato “{ev['azione']}” per il {human_date}. Ci sentiamo poco prima per un ripasso 🙂"
        else:
            reply = "Ho capito l'azione, ma non la data in modo chiaro. Puoi riscriverla?"
    else:
        # branch conversazione normale
        history = memory[sender]["messages"][-10:]
        resp = openai_client.chat.completions.create(
            model="gpt-4",
            messages=history
        )
        assistant_msg = resp.choices[0].message
        assistant = assistant_msg.to_dict()

        # se c'è una chiamata di funzione, la gestiamo qui…
        if assistant.get("function_call"):
            # esempio:
            fc = assistant["function_call"]
            # name = fc["name"], args = json.loads(fc["arguments"])
            # -> chiama la tua funzione...
            reply = f"Ecco la risposta dalla funzione **{fc['name']}**!"
        else:
            reply = assistant.get("content", "")
            memory[sender]["messages"].append({"role": "assistant", "content": reply})
            save_memory(memory)

    twresp = MessagingResponse()
    twresp.message(reply)
    return str(twresp)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

