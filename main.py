import os
import json
from datetime import datetime, timedelta

import pytz
import dateparser
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from apscheduler.schedulers.background import BackgroundScheduler
from openai import OpenAI

# === CONFIG ===
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

def load_memory():
    if os.path.exists(memory_file):
        with open(memory_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_memory(mem):
    with open(memory_file, "w", encoding="utf-8") as f:
        json.dump(mem, f, ensure_ascii=False, indent=2)

memory = load_memory()

def estrai_evento(text: str):
    """
    1) Se 'tra X minuti' o 'fra X ore', lo interpretiamo qui in Python.
    2) Altrimenti chiamiamo GPT per estrarre data ISO.
    """
    lower = text.lower()
    now = datetime.now(tz)

    # pattern very simple: tra N minuti / fra N ore
    if "tra " in lower and "minut" in lower:
        try:
            n = int([w for w in lower.split() if w.isdigit()][0])
            dt = now + timedelta(minutes=n)
        except:
            dt = None
    elif "tra " in lower and "ora" in lower:
        try:
            n = int([w for w in lower.split() if w.isdigit()][0])
            dt = now + timedelta(hours=n)
        except:
            dt = None
    else:
        dt = None

    if dt:
        # abbiamo calcolato noi: non chiamiamo GPT
        return {"azione": text, "data": dt.isoformat()}

    # fallback GPT per date puntuali
    today = now.date().isoformat()
    prompt = f"""You are a JSON assistant. Current date is {today} (Europe/Rome).
Extract action and exact datetime (ISO8601+02:00). If none, return nulls.

Respond ONLY JSON: {{"azione":"…","data":"YYYY-MM-DDTHH:MM:SS+02:00"}}
User: "{text}"
"""
    resp = openai_client.chat.completions.create(
        model="gpt-4",
        messages=[{"role":"user","content":prompt}]
    )
    try:
        ev = json.loads(resp.choices[0].message.content.strip())
        return ev
    except:
        return {"azione": None, "data": None}

scheduler = BackgroundScheduler()
def check_eventi():
    now = datetime.now(tz)
    for user,data in memory.items():
        nuovi = []
        for ev in data.get("events",[]):
            dt = datetime.fromisoformat(ev["datetime_evento"]).astimezone(tz)
            az = ev["azione"]

            if not ev.get("sent_before") and now >= dt - timedelta(hours=1):
                send_whatsapp(user, f"Ehi! Alle {dt.strftime('%H:%M')} avevi in programma “{az}” – preparati! 😉")
                ev["sent_before"]=True

            if not ev.get("sent_after") and now >= dt + timedelta(hours=2):
                send_whatsapp(user, f"Com'è andata l'attività “{az}”? Raccontami!")
                ev["sent_after"]=True

            if not (ev.get("sent_before") and ev.get("sent_after")):
                nuovi.append(ev)

        memory[user]["events"] = nuovi
    save_memory(memory)

scheduler.add_job(check_eventi,"interval",minutes=1)
scheduler.start()

def send_whatsapp(to,body):
    from twilio.rest import Client as TwilioClient
    tw=TwilioClient(TWILIO_ACCOUNT_SID,TWILIO_AUTH_TOKEN)
    m=tw.messages.create(body=body,from_=TWILIO_WHATSAPP_NUMBER,to=to)
    print(f"[TWILIO] inviato a {to}, SID={m.sid}")

@app.route("/bot",methods=["POST"])
def bot():
    incoming=request.values.get("Body","").strip()
    sender=request.values.get("From","").strip()
    print(f"Ricevuto {incoming} da {sender}")

    memory.setdefault(sender,{"messages":[],"events":[]})
    memory[sender]["messages"].append({"role":"user","content":incoming})

    ev=estrai_evento(incoming)
    print("[DEBUG] Evento:",ev)
    if ev["azione"] and ev["data"]:
        dt=dateparser.parse(ev["data"],settings={"TIMEZONE":"Europe/Rome","RETURN_AS_TIMEZONE_AWARE":True,"PREFER_DATES_FROM":"future"})
        if dt:
            ev["datetime_evento"]=dt.isoformat()
            memory[sender]["events"].append(ev)
            save_memory(memory)
            human=dt.strftime("%d/%m alle %H:%M")
            reply=f"Perfetto, ho registrato “{ev['azione']}” per il {human}. Ci sentiamo poco prima 🙂"
        else:
            reply="Ho capito l'azione ma non la data chiaramente. Puoi ripetere?"
    else:
        # conversazione standard
        hist=memory[sender]["messages"][-10:]
        resp=openai_client.chat.completions.create(model="gpt-4",messages=hist)
        msg=resp.choices[0].message.content.strip()
        memory[sender]["messages"].append({"role":"assistant","content":msg})
        save_memory(memory)
        reply=msg

    twresp=MessagingResponse(); twresp.message(reply)
    return str(twresp)

if __name__=="__main__":
    import os
    p=int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0",port=p,debug=False)

