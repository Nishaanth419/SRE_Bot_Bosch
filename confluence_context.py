"""
Query-time Confluence knowledge retrieval.

At query time, embed the user's message and vector-search 'confluence-kb'
to find the 2 most relevant pages. Only those pages are injected into the
AI prompt — keeping the context window lean and the answer relevant.

Usage in ai_handlers.py:
    from confluence_context import get_relevant_pages
    kb_context = get_relevant_pages(user_query)
    # inject kb_context into system_prompt
"""

import os
import threading
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "Azure.env"))

from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from azure.core.credentials import AzureKeyCredential
from azure_config import get_embedding

_CONFLUENCE_INDEX    = os.getenv("CONFLUENCE_INDEX", "confluence-kb")
_SEARCH_ENDPOINT     = os.getenv("AZURE_SEARCH_ENDPOINT", "")
_SEARCH_API_KEY      = os.getenv("AZURE_SEARCH_API_KEY", "")
_CONFLUENCE_BASE_URL = os.getenv("CONFLUENCE_URL", "").rstrip("/")

_client = SearchClient(
    endpoint=_SEARCH_ENDPOINT,
    index_name=_CONFLUENCE_INDEX,
    credential=AzureKeyCredential(_SEARCH_API_KEY),
)

# Minimum vector similarity score to include a page (0.0–1.0)
_MIN_SCORE = 0.65

# Thread-local storage — each request thread tracks its own matched sources
# so concurrent requests don't interfere with each other.
_tls = threading.local()


def get_last_sources() -> list[dict]:
    """Return matched Confluence pages from the most recent get_relevant_pages() call
    on this thread. Each entry: {"name": str, "url": str}.
    Clears the list after reading so stale results don't carry over."""
    sources = getattr(_tls, "sources", [])
    _tls.sources = []
    return sources


def get_relevant_pages(query: str, top_k: int = 3) -> str:
    """
    Embed the query, vector-search confluence-kb, and return a formatted
    context block containing only the most relevant page(s).

    Returns an empty string if no pages score above the threshold,
    so callers can safely append it to a system prompt without padding.
    """
    if not query or not _SEARCH_ENDPOINT or not _SEARCH_API_KEY:
        return ""

    try:
        vector = get_embedding(query)
    except Exception as e:
        print(f"⚠️ Confluence KB embedding failed: {e}")
        return ""

    try:
        results = list(_client.search(
            search_text=None,
            vector_queries=[VectorizedQuery(
                vector=vector,
                k_nearest_neighbors=top_k,
                fields="contentVector",
            )],
            select=["id", "page_name", "page_content"],
            top=top_k,
        ))
    except Exception as e:
        print(f"⚠️ Confluence KB search failed: {e}")
        return ""

    sections = []
    sources  = []
    for doc in results:
        score   = doc.get("@search.score", 0)
        if score < _MIN_SCORE:
            continue
        name    = doc.get("page_name", "")
        content = doc.get("page_content", "").strip()
        doc_id  = doc.get("id", "")  # e.g. "confluence-131074"
        if content:
            sections.append(f"### {name}\n{content}")
            # Build Confluence permalink from stored id
            page_id = doc_id.replace("confluence-", "")
            url = f"{_CONFLUENCE_BASE_URL}/wiki/pages/{page_id}" if page_id else ""
            sources.append({"name": name, "url": url})
            print(f"📄 Confluence KB match: '{name}' (score={score:.2f})")

    # Store matched sources in thread-local so orchestrator can read them
    _tls.sources = sources

    if not sections:
        return ""

    return (
        "📚 RELEVANT PROJECT KNOWLEDGE (from Confluence — use this to answer accurately):\n\n"
        + "\n\n".join(sections)
        + "\n"
    )
