import os
import json
from datetime import datetime, timedelta

import pytz
import dateparser
from flask import Flask, request
from apscheduler.schedulers.background import BackgroundScheduler
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient
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
    # estrai azione+data futura con ChatCompletion
    prompt = f"""
L'utente ha scritto: "{text}"
Estrai l'azione pianificata e la data/ora in ISO-8601 (incluso timezone Europe/Rome). 
Se non c'è intenzione futura, restituisci {{ "azione": null, "data": null }}.

Rispondi solo con JSON:
{{"azione":"...","data":"YYYY-MM-DDTHH:MM:SS+02:00"}}
"""
    resp = openai_client.chat.completions.create(
        model="gpt-4",
        messages=[{"role":"user","content":prompt}]
    )
    content = resp.choices[0].message.content.strip()
    try:
        ev = json.loads(content)
        if "azione" in ev and "data" in ev:
            return ev
    except:
        pass
    return {"azione": None, "data": None}

# === SCHEDULER ===
scheduler = BackgroundScheduler()

def check_eventi():
    now = datetime.now(tz)
    updated = False
    for user, data in memory.items():
        nuovi = []
        for ev in data.get("events", []):
            dt = datetime.fromisoformat(ev["datetime_evento"]).astimezone(tz)
            az = ev.get("azione","")

            # reminder un’ora prima
            if not ev.get("sent_before") and now >= dt - timedelta(hours=1):
                testo = f"Ehi! Alle {dt.strftime('%H:%M')} avevi in programma “{az}” – preparati! 😉"
                send_whatsapp(user, testo)
                ev["sent_before"] = True
                updated = True

            # follow-up due ore dopo
            if not ev.get("sent_after") and now >= dt + timedelta(hours=2):
                testo2 = f"Com'è andata l'attività “{az}”? Raccontami!"
                send_whatsapp(user, testo2)
                ev["sent_after"] = True
                updated = True

            if not (ev.get("sent_before") and ev.get("sent_after")):
                nuovi.append(ev)

        memory[user]["events"] = nuovi

    if updated:
        save_memory(memory)

scheduler.add_job(check_eventi, "interval", minutes=1)
scheduler.start()

# === INVIO WHATSAPP REALE ===
def send_whatsapp(to: str, body: str):
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
    incoming = request.values.get("Body","").strip()
    sender   = request.values.get("From","").strip()
    print(f"Messaggio ricevuto da {sender}: {incoming}")

    memory.setdefault(sender, {"messages":[], "events":[]})
    memory[sender]["messages"].append({"role":"user","content":incoming})

    ev = estrai_evento(incoming)
    print("[DEBUG] Evento estratto:", ev)

    if ev["azione"] and ev["data"]:
        dt = dateparser.parse(
            ev["data"],
            settings={
                "TIMEZONE":"Europe/Rome",
                "RETURN_AS_TIMEZONE_AWARE":True,
                "PREFER_DATES_FROM":"future"
            }
        )
        if dt:
            ev["datetime_evento"] = dt.isoformat()
            memory[sender]["events"].append(ev)
            save_memory(memory)
            human_date = dt.strftime("%d/%m alle %H:%M")
            reply = f"Perfetto, ho registrato “{ev['azione']}” per il {human_date}. Ci sentiamo poco prima per un ripasso 🙂"
        else:
            reply = "Ho capito l'azione, ma non la data. Puoi riscriverla?"
    else:
        history = memory[sender]["messages"][-10:]
        resp = openai_client.chat.completions.create(
            model="gpt-4",
            messages=history
        )
        reply = resp.choices[0].message.content.strip()
        memory[sender]["messages"].append({"role":"assistant","content":reply})
        save_memory(memory)

    twresp = MessagingResponse()
    twresp.message(reply)
    return str(twresp)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
