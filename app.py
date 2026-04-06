import os
import sys
import threading

from flask import Flask, request, jsonify

# Force UTF-8 output so emoji in print() works on Windows (cp1252 consoles)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------
# 🌐 LOAD AZURE SERVICES
# ---------------------------------------------------------
try:
    from azure_config import ai_client, CHAT_MODEL, get_embedding, search_client
    print("✅ Azure Configuration loaded.")
except ImportError as e:
    print(f"❌ Error loading config: {e}")
    sys.exit()

from response_metrics import evaluate_response, evaluate_followup, ResponseTimer
from database import find_similar_incident
from orchestrator import process_teams_message

# ---------------------------------------------------------
# 🌐 FLASK APP
# ---------------------------------------------------------
app = Flask(__name__)


@app.route("/", methods=["GET"])
def health_check():
    """Tells Azure the server is alive and healthy."""
    return "✅ SRE Bot Server is running perfectly!", 200


@app.route("/api/teams", methods=["POST"])
def teams_webhook():
    """Receive messages from Power Automate and process them in the background."""
    data       = request.json
    message_id = str(data.get("message_id", "")).strip()
    parent_id  = str(data.get("parent_id",  "")).strip()
    subject    = data.get("subject", "SRE Request")
    body       = data.get("body", "")
    base64_images = data.get("base64_images", [])

    if base64_images:
        print(f"🖼️ Received {len(base64_images)} base64 image(s) from Power Automate payload")

    if parent_id and parent_id.lower() != "null" and parent_id != message_id:
        tid      = parent_id
        is_reply = True
    else:
        tid      = message_id
        is_reply = False

    print(f"\n📥 Received webhook! (Msg: {message_id} | Parent: {parent_id} | Is Reply: {is_reply})")

    threading.Thread(
        target=process_teams_message,
        args=(tid, is_reply, subject, body, base64_images),
    ).start()

    return jsonify({"status": "Success", "message": "Processing in background"}), 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    print(f"🤖 SRE CLOUD BOT STARTED. Listening on port {port}...")
    app.run(host="0.0.0.0", port=port)

