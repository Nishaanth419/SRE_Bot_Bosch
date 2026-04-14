from utils import parse_chat_history, append_chat_history, _strip_html
from keywords import is_history_request, is_incident_message, greeting_response
from database import (
    get_thread_context,
    save_or_update_incident,
    find_similar_incident,
    search_similar_topics,
)
from ai_handlers import (
    ask_ai_history_summary,
    ask_ai_incident,
    ask_ai_followup,
    ask_ai_general,
)
from teams_poster import post_incident_card, post_chat_card
from response_metrics import evaluate_response, evaluate_followup, ResponseTimer
from confluence_context import get_last_sources

# ---------------------------------------------------------
# 🚀 CORE MESSAGE ORCHESTRATION
# ---------------------------------------------------------

def process_teams_message(tid, is_reply, subject, body, base64_images=None):
    """Route an incoming Teams message to the correct AI handler and persist the result."""
    if not tid or not body:
        return

    # ──────────────────────────────────────────────────────────
    # 👋 GREETING SHORTCUT — skip AI for simple greetings
    # ──────────────────────────────────────────────────────────
    if not is_reply:
        canned = greeting_response(body)
        if canned:
            print("👋 Detected simple greeting — responding without AI call")
            post_chat_card(tid, body, canned)
            return

    if base64_images:
        print(f"🖼️ Message includes {len(base64_images)} valid base64 image(s)")

    # ──────────────────────────────────────────────────────────
    # 📜 HISTORY REQUEST
    # ──────────────────────────────────────────────────────────
    if is_history_request(body):
        print("📜 Detected: HISTORY REQUEST")
        clean_body = _strip_html(body)
        query_text = "\n".join(
            part for part in [subject if subject != "SRE Request" else "", clean_body] if part
        )
        matches = search_similar_topics(query_text, top_k=3, threshold=0.40)

        if matches:
            answer = ask_ai_history_summary(query_text, matches)
            if "<" not in answer:
                answer = answer.replace('\n', '<br>')
                answer = answer.replace('**', '<b>')
        else:
            answer = (
                "🔍 I searched previous incidents and chat history but found no strong matches "
                "for that topic. This looks like a new issue or the stored history is too limited."
            )

        post_chat_card(
            tid, body, answer,
            kb_sources=get_last_sources(),
            note="📌 <b>Note:</b> This is a historical summary — no need to reply to this message.",
        )

        # Persist only when this is a reply in an existing thread
        if is_reply:
            context = get_thread_context(tid)
            if context:
                save_chat = append_chat_history(context.get("chat_history", "[]"), body, answer)
                save_or_update_incident(
                    tid,
                    context.get("subject", subject or "History Request"),
                    context.get("log", ""),
                    context.get("diagnosis", ""),
                    save_chat,
                    skip_embedding=True,
                )
        return

    # ──────────────────────────────────────────────────────────
    # 🔵 SCENARIO A: FOLLOW-UP REPLY
    # ──────────────────────────────────────────────────────────
    if is_reply:
        print(f"💬 Detected Reply to Thread {tid}")
        context = get_thread_context(tid)

        if context:
            history_list       = parse_chat_history(context["chat_history"])
            is_incident_thread = is_incident_message(
                context.get("log", ""), subject=context.get("subject", "")
            )

            with ResponseTimer() as timer:
                if is_incident_thread:
                    answer = ask_ai_followup(
                        body, context["log"], history_list, base64_images=base64_images
                    )
                else:
                    answer = ask_ai_general(
                        body,
                        chat_history_list=history_list,
                        thread_context=context.get("log", ""),
                        base64_images=base64_images,
                    )

            report = evaluate_followup(answer, body, context.get("log", ""), timer.elapsed)
            print(f"📊 Quality: {report.summary}")
            for m in report.metrics:
                print(f"   ├─ {m.name}: {m.score:.2f} – {m.explanation}")

            post_chat_card(tid, body, answer, kb_sources=get_last_sources())

            new_history = append_chat_history(context.get("chat_history", "[]"), body, answer)
            save_or_update_incident(
                tid, context["subject"], context["log"], context["diagnosis"], new_history,
                skip_embedding=True,
            )
        else:
            post_chat_card(tid, body, "⚠️ Session Expired: I cannot find the original error in my database.")

    # ──────────────────────────────────────────────────────────
    # 🔴 SCENARIO B: NEW INCIDENT OR GENERAL CHAT
    # ──────────────────────────────────────────────────────────
    else:
        if is_incident_message(body, subject=subject):
            print("⚙️ Detected: NEW INCIDENT")

            memory = find_similar_incident(body, subject=subject)

            with ResponseTimer() as timer:
                if memory:
                    print("🧠 Cache Hit! Generating RAG response...")
                    diag      = ask_ai_incident(body, memory_data=memory, base64_images=base64_images)
                    is_cached = True
                else:
                    print("🧠 Fresh Analysis...")
                    diag      = ask_ai_incident(body, base64_images=base64_images)
                    is_cached = False

            report = evaluate_response(diag, body, timer.elapsed, used_cache=is_cached)
            print(f"📊 Quality: {report.summary}")
            for m in report.metrics:
                print(f"   ├─ {m.name}: {m.score:.2f} – {m.explanation}")
            if report.recommendations:
                print(f"   └─ Recommendations: {'; '.join(report.recommendations)}")

            save_chat = append_chat_history("[]", body, diag)
            post_incident_card(tid, subject, body, diag, is_cached=is_cached, kb_sources=get_last_sources())
            save_or_update_incident(tid, subject, body, diag, save_chat)

        else:
            print("💬 Detected: GENERAL CHAT")

            with ResponseTimer() as timer:
                answer = ask_ai_general(body, thread_context=subject, base64_images=base64_images)

            report = evaluate_followup(answer, body, subject, timer.elapsed)
            print(f"📊 Quality: {report.summary}")
            for m in report.metrics:
                print(f"   ├─ {m.name}: {m.score:.2f} – {m.explanation}")

            post_chat_card(tid, body, answer, kb_sources=get_last_sources())
            save_chat = append_chat_history("[]", body, answer)
            save_or_update_incident(tid, subject or "General Chat", body, answer, save_chat)
