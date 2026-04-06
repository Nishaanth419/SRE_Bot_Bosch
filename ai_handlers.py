import re

from azure_config import ai_client, CHAT_MODEL
from utils import build_content_parts, _strip_html, _shorten, parse_chat_history, trim_chat_history
from github_grounding import _build_release_context

# ---------------------------------------------------------
# 🧠 AI HANDLERS
# ---------------------------------------------------------

def ask_ai_history_summary(query_text, matches):
    """Ask the AI to summarise patterns across matched past incidents."""
    print("🧠 AI Summarizing History Patterns...")

    history_blocks = []
    for idx, match in enumerate(matches, 1):
        clean_log  = _strip_html(match.get("log", ""))
        clean_diag = _strip_html(match.get("diagnosis", ""))
        chat_msgs  = parse_chat_history(match.get("chat_history", ""))
        chat_text  = "\n".join(
            f"  {('User' if m.get('role') == 'user' else 'Bot')}: {_strip_html(m.get('content', ''))}"
            for m in chat_msgs[:6]
        )
        score_pct = int(match.get("score", 0) * 100)
        history_blocks.append(
            f"--- Past Incident #{idx} (Relevance: {score_pct}%) ---\n"
            f"Subject : {match.get('subject', 'Untitled')}\n"
            f"Log     : {_shorten(clean_log, 500)}\n"
            f"Fix     : {_shorten(clean_diag, 500)}\n"
            f"Chat    :\n{chat_text}\n"
        )

    combined_history = "\n".join(history_blocks)

    system_prompt = """\
You are a Senior SRE reviewing past incident records to help the user.

The user asked about previous or similar incidents. You have matching records below.

YOUR JOB:
1. Summarize what happened in these past incidents — root causes, affected services, symptoms.
2. Highlight what fixes were applied and whether they worked.
3. If there's a clear pattern (e.g. same service keeps failing), call it out.
4. Give concrete next steps the user can take based on these patterns.

RULES:
- Answer based ONLY on the provided records. Do not make up incidents.
- Use Markdown with ## headers, bullet points, and ```bash blocks for commands.
- Be concise — summarize, don't dump raw data back at the user.
- Do NOT output the string "DLL-WORKFLOWS". Use "Workflows" instead.
"""
    user_content = (
        f"USER QUESTION:\n{query_text}\n\n"
        f"MATCHING PAST INCIDENTS ({len(matches)} found):\n\n"
        f"{combined_history}\n\n"
        "Using the records above, provide a clear summary and actionable advice."
    )

    try:
        response = ai_client.chat.completions.create(
            model=CHAT_MODEL,
            max_completion_tokens=3072,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.2,
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"⚠️ AI History Summary Error: {e}")
        from database import format_history_explanation
        return format_history_explanation(matches, query_text)


def ask_ai_incident(user_log, memory_data=None, base64_images=None):
    """Analyse an incident log and output a structured runbook."""
    print("🧠 AI Analyzing Incident...")
    system_prompt = """You are a Senior Site Reliability Engineer and Incident Commander.

The user has pasted an error log, alert, or failure message. Your job is to:
1. **Diagnose** — read the log carefully, identify the EXACT root cause, and explain WHY this happened in plain language.
2. **Fix** — provide a clear, step-by-step resolution with copy-pasteable commands.

📋 RESPONSE STRUCTURE (follow this exactly):

## 🔍 What Went Wrong
Explain the root cause in 2-4 sentences. Reference specific lines, error codes,
or messages from the log. Tell the user WHY this happened, not just what happened.

## 🎯 Impact
One line: [High/Medium/Low] — what is broken and who/what is affected.

---

## 🛠️ Step-by-Step Fix

**Step 1: [Short action name]**
_Why:_ [One sentence explaining why this step is needed]
```bash
[command]
```
_Expected output:_ [What the user should see if this worked]

**Step 2: [Short action name]**
_Why:_ [One sentence]
```bash
[command]
```
_Expected output:_ [What to look for]

(Continue with as many steps as needed — be thorough but don't pad with unnecessary steps)

---

## ✅ Verify the Fix
```bash
[verification command]
```
_You should see:_ [describe healthy output]

## ⚠️ If It's Still Broken
[One or two fallback actions or escalation steps]

---

🛑 RULES:
- Output ONLY in Markdown. No HTML tags.
- Every command must be in a ```bash block.
- Be specific to the ACTUAL error in the log — do not give generic advice.
- If the log mentions specific namespaces, pods, services, or clusters, use those exact names in your commands.
- Do NOT output the string "DLL-WORKFLOWS". Use "Workflows" instead.
"""
    if memory_data:
        past_log  = _shorten(_strip_html(memory_data.get('log', '')), 800)
        past_diag = _shorten(_strip_html(memory_data.get('diagnosis', '')), 800)
        past_chat = _shorten(_strip_html(memory_data.get('chat_history', '')), 400)
        user_content = (
            f"--- HISTORICAL REFERENCE FROM INDEX ---\n"
            f"Past Log: {past_log}\n"
            f"Past Fix: {past_diag}\n"
            f"Past Chat Context: {past_chat}\n"
            f"---------------------------------------\n\n"
            f"CURRENT ERROR LOG:\n{user_log}\n\n"
            "Using the historical reference above, generate a clean runbook for the CURRENT error."
        )
    else:
        user_content = f"CURRENT ERROR LOG:\n{user_log}"

    content_parts = build_content_parts(user_content, base64_images=base64_images)

    try:
        response = ai_client.chat.completions.create(
            model=CHAT_MODEL,
            max_completion_tokens=4096,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content_parts},
            ],
            temperature=0.2,
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"AI Error: {e}"


def ask_ai_followup(user_question, log_context, chat_history_list, base64_images=None):
    """Handle a follow-up question inside an incident thread."""
    print("🧠 AI Thinking (Follow-up)...")
    system_instruction = """You are a Senior SRE Bot continuing a conversation about an incident.

The user is asking a follow-up question about an error they already shared. The original
error log and previous conversation are provided below.

RULES:
- Answer ONLY what the user asked. Do not repeat the full diagnosis or re-explain the original error.
- If they ask "why" — explain the cause clearly.
- If they ask "how to fix" — give step-by-step commands.
- If they ask a specific question — answer that specific question. Do not go beyond it.
- Use Markdown. Put commands in ```bash blocks.
- Be concise. A follow-up answer should be shorter than the original diagnosis.
- Use **bold** for step titles. Do not use HTML tags.
- You do NOT have access to images from previous messages, but you already analyzed them.
  Do NOT mention that you cannot see images. Use the error log and chat history for context.
- Do NOT output the string "DLL-WORKFLOWS". Use "Workflows" instead.
"""
    truncated_log = _shorten(log_context, 2000)
    combined_system = f"{system_instruction}\n\nORIGINAL ERROR LOG (truncated):\n{truncated_log}"

    msgs = [{"role": "system", "content": combined_system}]
    if chat_history_list:
        msgs.extend(trim_chat_history(chat_history_list, max_turns=6))
    msgs.append({"role": "user", "content": build_content_parts(user_question, base64_images=base64_images)})

    try:
        response = ai_client.chat.completions.create(
            model=CHAT_MODEL,
            max_completion_tokens=2048,
            messages=msgs,
        )
        raw_answer = response.choices[0].message.content
        return re.sub(r'^[ \t]+', '', raw_answer, flags=re.MULTILINE)
    except Exception as e:
        return f"AI Error: {e}"


def ask_ai_general(user_message, chat_history_list=None, thread_context="", base64_images=None):
    """Handle general (non-incident) chat messages."""
    print("💬 AI Handling General Chat...")
    system_prompt = """You are a helpful SRE and DevOps assistant.

The user is asking a question — NOT reporting an incident. Answer their question directly.

RULES:
- Answer ONLY what was asked. Do not invent problems, assume errors, or generate runbooks.
- If the user says "hi" or "hello", respond with a brief friendly greeting.
- If they ask a technical question (e.g. "how do I scale a deployment"), give a clear
  answer with commands in ```bash blocks where appropriate.
- If they ask about a tool, version, or concept, explain it concisely.
- Use Markdown formatting. Use bullet points or numbered steps when they help readability.
- Keep answers focused and appropriately sized — short questions get short answers,
  detailed questions get detailed answers.
- Do not force structure (headers, sections) on simple answers. Use headers only when
  the answer genuinely has multiple distinct parts.
- When LIVE GITHUB RELEASE DATA is present in this prompt, use it as the authoritative
  source for version numbers and release dates. Never contradict it.
- Do NOT output the string "DLL-WORKFLOWS". Use "Workflows" instead.
"""

    if thread_context:
        system_prompt += f"\n\nConversation context:\n{thread_context}"

    # Inject live GitHub release data when the question is about a known tool
    system_prompt += _build_release_context(user_message)

    msgs = [{"role": "system", "content": system_prompt}]
    if chat_history_list:
        msgs.extend(trim_chat_history(chat_history_list, max_turns=6))
    msgs.append({"role": "user", "content": build_content_parts(user_message, base64_images=base64_images)})

    try:
        response = ai_client.chat.completions.create(
            model=CHAT_MODEL,
            max_completion_tokens=2048,
            messages=msgs,
            temperature=0.4,
        )
        raw_answer = response.choices[0].message.content
        return re.sub(r'^[ \t]+', '', raw_answer, flags=re.MULTILINE)
    except Exception as e:
        return f"AI Error: {e}"
