import os
import json
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from openai import OpenAI
from openai.agents import Tool, create_openai_functions_agent
from dotenv import load_dotenv

# Carica le variabili da .env
load_dotenv()

# Importa i tool definiti in tools.pyrom tools import send_whatsapp, schedule_event, list_events

# Verifica chiavi
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("Devi impostare OPENAI_API_KEY nel file .env o come variabile d'ambiente")

# Inizializza client OpenAI
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Definizione dei Tool
tools = [
    Tool(
        name="send_whatsapp",
        func=send_whatsapp,
        description="Invia un messaggio WhatsApp immediato."
    ),
    Tool(
        name="schedule_event",
        func=schedule_event,
        description="Programma un reminder 1h prima e un follow-up 2h dopo",
        parameters={
            "type": "object",
            "properties": {
                "user": {"type": "string", "description": "Numero WhatsApp in formato 'whatsapp:+39...'"},
                "action": {"type": "string", "description": "Descrizione dell'azione/evento"},
                "when_iso": {"type": "string", "description": "Data e ora in formato ISO 8601"}
            },
            "required": ["user", "action", "when_iso"]
        }
    ),
    Tool(
        name="list_events",
        func=list_events,
        description="Elenca i prossimi eventi schedulati per un utente",
        parameters={
            "type": "object",
            "properties": {
                "user": {"type": "string", "description": "Numero WhatsApp in formato 'whatsapp:+39...'"}
            },
            "required": ["user"]
        }
    )
]

# Crea l'Agent
agent = create_openai_functions_agent(
    client=openai_client,
    llm="gpt-4o-0613",  # o "gpt-4-0613"
    tools=tools,
    verbose=True
)

# Setup Flask
app = Flask(__name__)

# Endpoint per Twilio
@app.route("/bot", methods=["POST"])
def bot():
    incoming = request.values.get("Body", "").strip()
    sender = request.values.get("From", "").strip()
    print(f"Messaggio ricevuto da {sender}: {incoming}")

    # Esegui l'agent
    response = agent.run(messages=[{"role": "user", "content": incoming, "name": sender}])

    # Invia la risposta finale
    twresp = MessagingResponse()
    twresp.message(response)
    return str(twresp)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)