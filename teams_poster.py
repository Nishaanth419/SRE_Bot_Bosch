from config import session, TEAMS_WEBHOOK_URL

# ---------------------------------------------------------
# 📤 TEAMS CARD POSTING
# ---------------------------------------------------------

def post_incident_card(tid, title, raw_log, ai_diagnosis, is_cached=False, history_text=None):
    """Post a structured incident runbook reply to a Teams thread."""
    print(f"📤 Posting Incident Reply to {tid}...")
    safe_trigger_instruction = "@BOT DLL-WORK\u200bFLOWS"
    ai_diagnosis = ai_diagnosis.replace("DLL-WORKFLOWS", "Workflows")

    if "<" not in ai_diagnosis:
        ai_diagnosis = ai_diagnosis.replace('\n', '<br>')
        ai_diagnosis = ai_diagnosis.replace('**', '<b>')

    html_body = (
        f"<h2>🤖 SRE Bot Support</h2>\n<hr>\n"
        f"{ai_diagnosis}\n<hr>\n"
        f"ℹ️ <b>Tip:</b> To reply, start your message with <b>{safe_trigger_instruction}</b>\n"
    )

    if history_text:
        formatted_history = history_text.replace('\n', '<br>')
        html_body += (
            "<br>"
            '<div style="background-color:#f9f9f9;padding:10px;border-radius:5px;margin-top:10px;">'
            f"<b>📜 Previous Chat History:</b><br>"
            f'<span style="color:#666;font-size:12px;">{formatted_history}</span>'
            "</div>"
        )

    payload = {"messageId": tid, "replyText": html_body}
    try:
        session.post(TEAMS_WEBHOOK_URL, json=payload)
    except Exception as e:
        print(f"Post Error: {e}")


def post_chat_card(tid, question, answer):
    """Post a general chat reply to a Teams thread."""
    print(f"📤 Posting Chat Reply to {tid}...")
    safe_trigger_instruction = "@BOT DLL-WORK\u200bFLOWS"
    answer = answer.replace("DLL-WORKFLOWS", "Workflows")

    markdown_body = (
        '\n<h2 style="color:#6264A7;margin-bottom:5px;">🤖 SRE Bot Support</h2>\n\n'
        f"{answer}\n\n---\n"
        f"ℹ️ <b>Tip:</b> To reply, start your message with <b>{safe_trigger_instruction}</b>\n"
    )

    payload = {"messageId": tid, "replyText": markdown_body}
    try:
        session.post(TEAMS_WEBHOOK_URL, json=payload)
    except Exception as e:
        print(f"❌ Error posting to Teams: {e}")
