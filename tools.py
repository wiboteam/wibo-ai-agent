# tools.py
import os, json
from datetime import datetime, timedelta
import dateparser
import pytz
from twilio.rest import Client as TwilioClient
from apscheduler.schedulers.background import BackgroundScheduler

# Carica env
from dotenv import load_dotenv
load_dotenv()

# Inizializza Twilio e Scheduler
TW_SID   = os.getenv("TWILIO_ACCOUNT_SID")
TW_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TW_FROM  = os.getenv("TWILIO_WHATSAPP_NUMBER")
twilio   = TwilioClient(TW_SID, TW_TOKEN)
sched    = BackgroundScheduler(timezone="Europe/Rome")
sched.start()
tz       = pytz.timezone("Europe/Rome")

# Memoria semplice in lista (poi potrai passare a DB)
events = []

def send_whatsapp(user: str, body: str) -> str:
    """Invia subito un messaggio WhatsApp via Twilio."""
    msg = twilio.messages.create(
        body=body,
        from_=TW_FROM,
        to=user
    )
    return msg.sid

def schedule_event(user: str, action: str, when_iso: str) -> str:
    """Programma un reminder 1h prima e un follow-up 2h dopo."""
    dt = dateparser.parse(
        when_iso,
        settings={"TIMEZONE":"Europe/Rome","RETURN_AS_TIMEZONE_AWARE":True}
    )
    if not dt:
        raise ValueError("Data non valida")
    ev = {"user":user, "action":action, "dt":dt, "before":False, "after":False}
    events.append(ev)

    # reminder 1h prima
    sched.add_job(
      func=lambda: send_whatsapp(user, f"Tra un’ora: “{action}” alle {dt.strftime('%H:%M')} 😉"),
      trigger="date", run_date=dt - timedelta(hours=1)
    )
    # follow-up 2h dopo
    sched.add_job(
      func=lambda: send_whatsapp(user, f"Com’è andata l’attività “{action}”?"),
      trigger="date", run_date=dt + timedelta(hours=2)
    )
    return f"Evento schedulato per {dt.strftime('%d/%m %H:%M')}"

def list_events(user: str) -> str:
    """Ritorna gli eventi futuri per un dato utente."""
    now = datetime.now(tz)
    futuri = [ev for ev in events if ev["user"]==user and ev["dt"]>now]
    if not futuri:
        return "Nessun evento programmato."
    lines = []
    for ev in futuri:
        lines.append(f"- “{ev['action']}” il {ev['dt'].strftime('%d/%m alle %H:%M')}")
    return "\n".join(lines)

