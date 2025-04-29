import os
import json
import dateparser
import pytz
from datetime import datetime, timedelta
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from apscheduler.schedulers.background import BackgroundScheduler
from openai import OpenAI

# === CONFIG DA ENV VARS ===
OPENAI_API_KEY         = os.getenv("OPENAI_API_KEY")
TWILIO_ACCOUNT_SID     = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN      = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")

if not OPENAI_API_KEY:
    raise RuntimeError("Devi impostare OPENAI_API_KEY")
if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_NUMBER]):
    raise RuntimeError("Devi impostare TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN e TWILIO_WHATSAPP_NUMBER")

# client OpenAI
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Flask app e timezone
app = Flask(__name__)
tz = pytz.timezone("Europe/Rome")
memory_file = "memory.json"

# === MEMORY ===
def load_memory():
    if os.path.exists(memory_file):
        with open(memory_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_memory(mem):
    with open(memory_file, "w", encoding="utf-8") as f:
        json.dump(mem, f, ensure_ascii=False, indent=2)

memory = load_memory()

# === Function Calling Schema ===
functions = [
  {
    "name": "schedule_event",
    "description": "Registra un evento futuro da ricordare",
    "parameters": {
      "type": "object",
      "properties": {
        "action": {
          "type": "string",
          "description": "Cosa farà l'utente"
        },
        "datetime": {
          "type": "string",
          "description": "Data e ora ISO con timezone, es. 2025-05-20T15:00:00+02:00"
        }
      },
      "required": ["action", "datetime"]
    }
  }
]

# === SCHEDULER ===
scheduler = BackgroundScheduler()

def check_eventi():
    now = datetime.now(tz)
    updated = False

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
                updated = True

            # follow-up 2h dopo
            if not ev.get("sent_after") and now >= dt + timedelta(hours=2):
                testo2 = f"Com'è andata l'attività “{az}”? Raccontami!"
                print(f"[DEBUG] INVIO follow-up-after a {user} per evento “{az}”")
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

# === SEND REAL WHATSAPP ===
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

    # inizializza la memoria per il nuovo utente
    memory.setdefault(sender, {"messages": [], "events": []})
    memory[sender]["messages"].append({"role": "user", "content": incoming})

    # chiamata GPT con function calling
    resp = openai_client.chat.completions.create(
        model="gpt-4-0613",
        messages=memory[sender]["messages"],
        functions=functions,
        function_call="auto"
    )
    msg = resp.choices[0].message

    # se GPT chiama la nostra funzione schedule_event
    if msg.get("function_call"):
        payload = json.loads(msg.function_call.arguments)
        action = payload["action"]
        dt_iso = payload["datetime"]

        # salva l'evento in memoria
        ev = {"azione": action, "datetime_evento": dt_iso}
        memory[sender]["events"].append(ev)
        save_memory(memory)

        # rispondi all’utente con conferma
        dt = dateparser.parse(
            dt_iso,
            settings={
                "TIMEZONE": "Europe/Rome",
                "RETURN_AS_TIMEZONE_AWARE": True
            }
        )
        human_date = dt.strftime("%d/%m alle %H:%M")
        reply = f"Perfetto, ho registrato “{action}” per il {human_date}. Ci sentiamo poco prima per un ripasso 🙂"

    else:
        # normale chat reply
        reply = msg.content.strip()
        memory[sender]["messages"].append({"role": "assistant", "content": reply})
        save_memory(memory)

    twresp = MessagingResponse()
    twresp.message(reply)
    return str(twresp)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
