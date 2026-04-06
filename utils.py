import re
import json

# ---------------------------------------------------------
# 🌐 HTML UTILITIES
# ---------------------------------------------------------
_AT_TAG_RE  = re.compile(r'<at[^>]*>.*?</at>', re.DOTALL | re.IGNORECASE)
_HTML_TAG_RE = re.compile(r'<[^>]+>', re.DOTALL)


def _escape_html(text):
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _strip_html(text):
    """Remove Teams HTML markup and decode entities.
    Decodes entities FIRST so escaped tags like &lt;at&gt; are also caught."""
    if not text:
        return ""
    text = (
        text
        .replace('&lt;', '<')
        .replace('&gt;', '>')
        .replace('&amp;', '&')
        .replace('&quot;', '"')
        .replace('&#39;', "'")
        .replace('&nbsp;', ' ')
    )
    text = _AT_TAG_RE.sub('', text)
    text = _HTML_TAG_RE.sub(' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _shorten(text, limit=220):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


# ---------------------------------------------------------
# 🖼️ IMAGE UTILITIES
# ---------------------------------------------------------
def prepare_image_content_blocks(base64_images):
    """Converts a list of base64 image strings into OpenAI vision content blocks."""
    blocks = []
    for b64_data in base64_images:
        media_type = "image/png"
        if b64_data.startswith("data:") and "," in b64_data:
            # Already has data URI prefix — use as-is
            data_url = b64_data
        else:
            data_url = f"data:{media_type};base64,{b64_data}"
        blocks.append({
            "type": "image_url",
            "image_url": {"url": data_url, "detail": "auto"},
        })
    print(f"✅ Prepared {len(blocks)} image block(s) for OpenAI vision")
    return blocks


def build_content_parts(text, base64_images=None):
    """Create OpenAI multimodal content blocks.

    If there are no images returns a plain string (cheaper and compatible
    with models that don't support vision).  With images returns the list
    format required by the OpenAI Chat Completions API.
    """
    if not base64_images:
        return text

    content_parts = []
    image_blocks = prepare_image_content_blocks(base64_images)
    if image_blocks:
        print(f"🖼️ Including {len(image_blocks)} screenshot(s) in AI analysis")
        content_parts.extend(image_blocks)
    content_parts.append({"type": "text", "text": text})
    return content_parts


# ---------------------------------------------------------
# 💬 CHAT HISTORY UTILITIES
# ---------------------------------------------------------
def parse_chat_history(chat_history):
    """Return OpenAI-compatible message list, tolerating legacy string formats."""
    if not chat_history:
        return []
    if isinstance(chat_history, list):
        return chat_history
    if isinstance(chat_history, str):
        stripped = chat_history.strip()
        if not stripped or stripped == "[]":
            return []
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                return [
                    {"role": item.get("role", "assistant"), "content": item.get("content", "")}
                    for item in parsed
                    if isinstance(item, dict) and item.get("content")
                ]
        except json.JSONDecodeError:
            pass
        return [{"role": "assistant", "content": stripped}]
    return []


def append_chat_history(chat_history, user_message, assistant_message):
    """Append a conversation turn to the history and return it as a JSON string."""
    history = parse_chat_history(chat_history)
    history.append({"role": "user", "content": user_message})
    history.append({"role": "assistant", "content": assistant_message})
    return json.dumps(history)


def trim_chat_history(messages, max_turns=6):
    """Keep only the last *max_turns* user/assistant pairs to limit token usage.
    A 'turn' is one user message + one assistant message (2 list items).
    Always preserves the most recent context."""
    if not messages:
        return []
    max_items = max_turns * 2
    if len(messages) <= max_items:
        return messages
    trimmed = messages[-max_items:]
    print(f"✂️ Trimmed chat history from {len(messages)} to {len(trimmed)} messages ({max_turns} turns)")
    return trimmed
