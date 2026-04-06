from functools import lru_cache
from azure.search.documents.models import VectorizedQuery

from azure_config import get_embedding, search_client
from keywords import extract_keywords, _extract_history_query_keywords, _keyword_overlap_ratio
from utils import _strip_html, _escape_html, _shorten, parse_chat_history

# ---------------------------------------------------------
# 💾 EMBEDDING CACHE
# ---------------------------------------------------------
@lru_cache(maxsize=256)
def get_cached_embedding(text):
    cleaned = (text or "").replace("\n", " ").strip()
    if not cleaned:
        return []
    return get_embedding(cleaned)


# ---------------------------------------------------------
# 💾 THREAD CONTEXT
# ---------------------------------------------------------
def get_thread_context(thread_id):
    """Fetch a stored thread from Azure AI Search by ID."""
    print(f"🔄 Fetching context for Thread ID: {thread_id}...")
    try:
        result = search_client.get_document(key=str(thread_id))
        return {
            "subject":      result.get("subject", ""),
            "log":          result.get("log", ""),
            "diagnosis":    result.get("diagnosis", ""),
            "chat_history": result.get("chat_history", ""),
        }
    except Exception as e:
        print(f"⚠️ DB Fetch Error (or ID not found): {e}")
        return None


def save_or_update_incident(thread_id, subject, log, diagnosis, chat_history, skip_embedding=False):
    """Upsert an incident document. Embeds KEYWORDS (not raw log) for precise matching.
    Set skip_embedding=True on follow-ups where log/subject haven't changed to save an embedding call."""
    print(f"💾 Saving/Updating Incident {thread_id} to Azure...")
    try:
        document = {
            "id":           str(thread_id),
            "log":          log,
            "subject":      subject,
            "diagnosis":    diagnosis,
            "chat_history": chat_history,
        }
        if not skip_embedding:
            clean_log = _strip_html(log)
            keywords = extract_keywords(f"{subject} {clean_log}")
            vector = get_embedding(keywords)
            document["contentVector"] = vector
        else:
            print("⏭️ Skipping embedding (log unchanged — follow-up only)")

        search_client.upload_documents(documents=[document])
        print("✅ Database Sync Complete.")
    except Exception as e:
        print(f"❌ DB Save Error: {e}")


# ---------------------------------------------------------
# 🔍 SIMILARITY SEARCH
# ---------------------------------------------------------
def find_similar_incident(log_text, subject=""):
    """Vector + Jaccard search for a matching past incident."""
    print("🕵️ Searching Azure memory for similar logs...")
    try:
        clean_text = _strip_html(log_text)
        query_keywords = extract_keywords(f"{subject} {clean_text}")
        if not query_keywords:
            print("⚠️ No meaningful keywords extracted — skipping search.")
            return None

        query_kw_set = set(query_keywords.split())
        query_vec = get_cached_embedding(query_keywords)
        vector_query = VectorizedQuery(
            vector=query_vec,
            k_nearest_neighbors=3,
            fields="contentVector",
        )
        results = search_client.search(
            search_text=None,
            vector_queries=[vector_query],
            select=["subject", "diagnosis", "log", "chat_history"],
        )

        VECTOR_THRESHOLD   = 0.60
        KEYWORD_OVERLAP_MIN = 0.10

        best_match    = None
        best_combined = 0.0

        for result in results:
            vec_score      = result["@search.score"]
            stored_log     = result.get("log", "")
            stored_subject = result.get("subject", "")

            stored_keywords = extract_keywords(f"{stored_subject} {_strip_html(stored_log)}")
            stored_kw_set   = set(stored_keywords.split()) if stored_keywords else set()

            if query_kw_set and stored_kw_set:
                intersection = query_kw_set & stored_kw_set
                union        = query_kw_set | stored_kw_set
                kw_overlap   = len(intersection) / len(union)
            else:
                intersection = set()
                kw_overlap   = 0.0

            combined = (vec_score * 0.65) + (kw_overlap * 0.35)
            print(
                f"📊 Candidate: vec={vec_score:.3f} | kw_overlap={kw_overlap:.2f} "
                f"({len(intersection)} shared: {', '.join(sorted(intersection)[:8])}) | combined={combined:.3f}"
            )

            if vec_score >= VECTOR_THRESHOLD and kw_overlap >= KEYWORD_OVERLAP_MIN and combined > best_combined:
                best_combined = combined
                best_match    = result

        if best_match:
            print(f"🎯 Match accepted! (combined: {best_combined:.3f})")
            return {
                "subject":      best_match.get("subject"),
                "diagnosis":    best_match.get("diagnosis"),
                "log":          best_match.get("log"),
                "chat_history": best_match.get("chat_history"),
            }

        print("⚠️ No match passed both thresholds. Treating as new incident.")
        return None

    except Exception as e:
        print(f"⚠️ Search Error: {e}")
        return None


def search_similar_topics(query_text, top_k=3, threshold=0.40):
    """Broader history search — used when the user explicitly asks about past issues."""
    print("🔎 Searching for related past conversations...")
    try:
        query_keywords = _extract_history_query_keywords(query_text)
        if not query_keywords:
            print("⚠️ No meaningful keywords — skipping topic search.")
            return []

        query_vec = get_cached_embedding(query_keywords)
        vector_query = VectorizedQuery(
            vector=query_vec,
            k_nearest_neighbors=max(top_k * 2, 6),
            fields="contentVector",
        )
        results = search_client.search(
            search_text=None,
            vector_queries=[vector_query],
            select=["id", "subject", "diagnosis", "log", "chat_history"],
        )

        matches = []
        for result in results:
            raw_score      = float(result.get("@search.score", 0.0))
            stored_text    = f"{result.get('subject', '')} {_strip_html(result.get('log', ''))}"
            stored_keywords = extract_keywords(stored_text, max_keywords=20)
            overlap        = _keyword_overlap_ratio(query_keywords, stored_keywords)
            combined_score = round((raw_score * 0.75) + (overlap * 0.25), 3)

            if combined_score < threshold:
                continue

            matches.append({
                "id":           result.get("id", ""),
                "subject":      result.get("subject", "Untitled incident"),
                "diagnosis":    result.get("diagnosis", ""),
                "log":          result.get("log", ""),
                "chat_history": result.get("chat_history", ""),
                "score":        combined_score,
            })

        matches.sort(key=lambda item: item["score"], reverse=True)
        trimmed = matches[:top_k]
        print(f"📚 Found {len(trimmed)} related conversation(s) above threshold {threshold}")
        return trimmed

    except Exception as e:
        print(f"⚠️ Topic Search Error: {e}")
        return []


# ---------------------------------------------------------
# 📜 HISTORY FORMATTING (fallback HTML renderer)
# ---------------------------------------------------------
def format_history_explanation(matches, query_text):
    """Teams-friendly HTML summary of past similar conversations."""
    safe_query = _escape_html(_shorten(_strip_html(query_text), 140))
    lines = [
        f"<h3>📜 History Search — {len(matches)} relevant past conversation(s) found</h3>",
        f"<p><i>Query:</i> <b>{safe_query}</b></p>",
        "<hr>",
    ]

    for idx, match in enumerate(matches, 1):
        safe_subject = _escape_html(match.get("subject", "Untitled incident"))
        clean_log    = _strip_html(match.get("log", ""))
        clean_diag   = _strip_html(match.get("diagnosis", ""))
        safe_log     = _escape_html(_shorten(clean_log,  240))
        safe_diag    = _escape_html(_shorten(clean_diag, 320))
        score_pct    = int(match.get("score", 0) * 100)

        lines.append(f"<p><b>{idx}. {safe_subject}</b> &nbsp;|&nbsp; Relevance: <b>{score_pct}%</b></p>")
        if safe_log:
            lines.append(f"<p><b>🔴 What happened:</b><br>{safe_log}</p>")
        if safe_diag:
            lines.append(f"<p><b>✅ How it was handled:</b><br>{safe_diag}</p>")

        history_items = []
        for msg in parse_chat_history(match.get("chat_history", ""))[:4]:
            role    = "User" if msg.get("role") == "user" else "Bot"
            content = _escape_html(_shorten(_strip_html(msg.get("content", "")), 160))
            if content:
                history_items.append(f"&nbsp;&nbsp;• <b>{role}:</b> {content}")

        if history_items:
            lines.append("<p><b>💬 Chat highlights:</b><br>")
            lines.append("<br>".join(history_items) + "</p>")

        lines.append("<hr>")

    lines.append(
        "<p><b>💡 Tip:</b> These earlier threads can help you compare symptoms, "
        "prior fixes, and likely next steps — without waiting for a new AI analysis.</p>"
    )
    return "".join(lines)
