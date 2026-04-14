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
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "Azure.env"))

from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from azure.core.credentials import AzureKeyCredential
from azure_config import get_embedding

_CONFLUENCE_INDEX = os.getenv("CONFLUENCE_INDEX", "confluence-kb")
_SEARCH_ENDPOINT  = os.getenv("AZURE_SEARCH_ENDPOINT", "")
_SEARCH_API_KEY   = os.getenv("AZURE_SEARCH_API_KEY", "")

_client = SearchClient(
    endpoint=_SEARCH_ENDPOINT,
    index_name=_CONFLUENCE_INDEX,
    credential=AzureKeyCredential(_SEARCH_API_KEY),
)

# Minimum vector similarity score to include a page (0.0–1.0)
_MIN_SCORE = 0.55


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
            select=["page_name", "page_content"],
            top=top_k,
        ))
    except Exception as e:
        print(f"⚠️ Confluence KB search failed: {e}")
        return ""

    sections = []
    for doc in results:
        score   = doc.get("@search.score", 0)
        if score < _MIN_SCORE:
            continue
        name    = doc.get("page_name", "")
        content = doc.get("page_content", "").strip()
        if content:
            sections.append(f"### {name}\n{content}")
            print(f"📄 Confluence KB match: '{name}' (score={score:.2f})")

    if not sections:
        return ""

    return (
        "📚 RELEVANT PROJECT KNOWLEDGE (from Confluence — use this to answer accurately):\n\n"
        + "\n\n".join(sections)
        + "\n"
    )
