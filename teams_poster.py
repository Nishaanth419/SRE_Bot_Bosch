import markdown as _md

from config import session, TEAMS_WEBHOOK_URL

# ---------------------------------------------------------
# 📤 TEAMS CARD POSTING
# ---------------------------------------------------------

def _md_to_html(text: str) -> str:
    """Convert Markdown to HTML for Teams rendering.
    Uses the 'fenced_code' and 'tables' extensions so ```bash blocks
    and tables render correctly."""
    return _md.markdown(
        text,
        extensions=["fenced_code", "tables", "nl2br"],
    )


def post_incident_card(tid, title, raw_log, ai_diagnosis, is_cached=False, history_text=None, kb_sources=None):
    """Post a structured incident runbook reply to a Teams thread."""
    print(f"📤 Posting Incident Reply to {tid}...")
    safe_trigger_instruction = "@BOT DLL-WORK\u200bFLOWS"
    ai_diagnosis = ai_diagnosis.replace("DLL-WORKFLOWS", "Workflows")

    html_body = (
        f"<h2>🤖 SRE Bot Support</h2>\n<hr>\n"
        f"{_md_to_html(ai_diagnosis)}\n<hr>\n"
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


def post_chat_card(tid, question, answer, kb_sources=None, note=None):
    """Post a general chat reply to a Teams thread."""
    print(f"📤 Posting Chat Reply to {tid}...")
    safe_trigger_instruction = "@BOT DLL-WORK\u200bFLOWS"
    answer = answer.replace("DLL-WORKFLOWS", "Workflows")

    footer_note = note if note else f"ℹ️ <b>Tip:</b> To reply, start your message with <b>{safe_trigger_instruction}</b>"

    markdown_body = (
        '<h2 style="color:#6264A7;margin-bottom:5px;">🤖 SRE Bot Support</h2>\n'
        f"{_md_to_html(answer)}\n"
        f"{footer_note}\n"
    )

    if kb_sources:
        pass  # sources available internally but not shown in card

    payload = {"messageId": tid, "replyText": markdown_body}
    try:
        session.post(TEAMS_WEBHOOK_URL, json=payload)
    except Exception as e:
        print(f"❌ Error posting to Teams: {e}")
