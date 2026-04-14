"""
Confluence → Azure AI Search ingestion tool.

Fetches pages from one or more Confluence Cloud spaces and stores each page
as a single document in the 'confluence-kb' index with 3 fields:
  id           — unique document key (confluence-{pageId})
  page_name    — the Confluence page title
  page_content — full plain-text body of the page

This index is read at app startup by confluence_context.py and injected
into every AI prompt as always-on project knowledge.

Run from the Deploy/ root directory:
    python tools/confluence_ingest.py

Required env vars (add to Azure.env):
    CONFLUENCE_URL           https://yourcompany.atlassian.net
    CONFLUENCE_USER          user@company.com
    CONFLUENCE_API_TOKEN     <atlassian API token>
    CONFLUENCE_SPACE_KEYS    SRE,DEVOPS,ALGO   (comma-separated)
    CONFLUENCE_INDEX         confluence-kb
"""

import os
import sys
import re
import time
import hashlib

# Allow running from tools/ subdirectory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Load secrets from Azure.env (same file the main app uses)
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_root, "Azure.env"))

from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from azure_config import get_embedding, ai_client, CHAT_MODEL

# ---------------------------------------------------------
#   CONFIGURATION
# ---------------------------------------------------------
CONFLUENCE_URL        = os.getenv("CONFLUENCE_URL", "").rstrip("/")
CONFLUENCE_USER       = os.getenv("CONFLUENCE_USER", "")
CONFLUENCE_API_TOKEN  = os.getenv("CONFLUENCE_API_TOKEN", "")
CONFLUENCE_SPACE_KEYS = [
    k.strip() for k in os.getenv("CONFLUENCE_SPACE_KEYS", "").split(",") if k.strip()
]

# Dedicated search client for the confluence-kb index (separate from sre-incidents)
_confluence_index  = os.getenv("CONFLUENCE_INDEX", "confluence-kb")
_confluence_client = SearchClient(
    endpoint=os.getenv("AZURE_SEARCH_ENDPOINT"),
    index_name=_confluence_index,
    credential=AzureKeyCredential(os.getenv("AZURE_SEARCH_API_KEY", "")),
)

REQUEST_DELAY_S = 0.3
# Max characters stored per page (keeps the AI context window manageable)
MAX_PAGE_CHARS  = 4000


# ---------------------------------------------------------
#   CONFLUENCE API CLIENT
# ---------------------------------------------------------
_session = requests.Session()
_session.auth = (CONFLUENCE_USER, CONFLUENCE_API_TOKEN)
_session.headers.update({"Accept": "application/json"})


def _get(path: str, params: dict = None) -> dict:
    """GET from Confluence Cloud REST API v2."""
    url = f"{CONFLUENCE_URL}/wiki/api/v2{path}"
    resp = _session.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _resolve_space_id(space_key: str) -> str:
    """Resolve a Confluence space key (e.g. 'PH') to its numeric space ID."""
    data = _get("/spaces", params={"keys": space_key, "limit": 1})
    results = data.get("results", [])
    if not results:
        raise ValueError(f"Space '{space_key}' not found. Check the key and your permissions.")
    space_id = results[0]["id"]
    print(f"   Space '{space_key}' resolved to id={space_id}")
    return space_id


def fetch_pages_in_space(space_key: str) -> list[dict]:
    """Paginate through all pages in a Confluence space."""
    print(f" Fetching pages in space: {space_key}")
    space_id = _resolve_space_id(space_key)
    pages = []
    cursor = None

    while True:
        params = {"space-id": space_id, "limit": 50, "status": "current"}
        if cursor:
            params["cursor"] = cursor

        data = _get("/pages", params=params)
        results = data.get("results", [])
        pages.extend(results)
        print(f"   Fetched {len(results)} pages (total so far: {len(pages)})")

        # Follow next-page cursor if present
        next_link = data.get("_links", {}).get("next")
        if not next_link:
            break
        # Extract cursor value from next URL  e.g. ?cursor=abc123
        m = re.search(r"cursor=([^&]+)", next_link)
        cursor = m.group(1) if m else None
        if not cursor:
            break
        time.sleep(REQUEST_DELAY_S)

    return pages


def fetch_page_body(page_id: str) -> str:
    """Fetch the storage-format HTML body for a single page."""
    data = _get(f"/pages/{page_id}", params={"body-format": "storage"})
    return data.get("body", {}).get("storage", {}).get("value", "")


# ---------------------------------------------------------
#   INDEXING  — one document per page, 3 fields only
# ---------------------------------------------------------
def _html_to_plain(html: str) -> str:
    """Convert Confluence storage HTML to clean plain text."""
    soup = BeautifulSoup(html, "html.parser")
    for macro in soup.find_all("ac:structured-macro"):
        macro.unwrap()
    return soup.get_text(separator=" ", strip=True)


def _summarise_page(page_title: str, content: str) -> str:
    """Use Azure OpenAI to summarise a Confluence page into ~500 chars.
    The summary is what gets stored and injected into AI prompts.
    The full content is still used for the embedding vector."""
    prompt = (
        f"Summarise the following Confluence page titled '{page_title}' "
        f"into 3-5 concise sentences. Focus on: what it is, what problem it solves, "
        f"key steps or commands, and any important warnings. "
        f"Output plain text only, no markdown, no headers.\n\n"
        f"{content[:3000]}"
    )
    try:
        resp = ai_client.chat.completions.create(
            model=CHAT_MODEL,
            max_completion_tokens=200,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        summary = resp.choices[0].message.content.strip()
        if not summary:
            # Model returned empty — fall back to raw excerpt
            summary = content[:500]
        print(f"   📝 Summary ({len(summary)} chars): {summary[:80]}...")
        return summary
    except Exception as e:
        print(f"   ⚠️ Summarisation failed, storing truncated content: {e}")
        return content[:500]


def index_page(page_id: str, page_title: str, html_body: str) -> bool:
    """Summarise, embed, and upsert a single page into confluence-kb.
    - page_content stores the AI-generated summary (~500 chars) for lean prompt injection
    - contentVector is embedded from the full text for accurate similarity search
    """
    full_content = _html_to_plain(html_body).strip()[:MAX_PAGE_CHARS]
    if not full_content:
        print(f"    Empty content — skipping")
        return False

    summary = _summarise_page(page_title, full_content)

    try:
        # Embed full content for better vector quality
        vector = get_embedding(f"{page_title} {full_content}")
    except Exception as e:
        print(f"   Embedding error for '{page_title[:60]}': {e}")
        return False

    document = {
        "id":            f"confluence-{page_id}",
        "page_name":     page_title,
        "page_content":  summary,          # summary only — saves tokens at query time
        "contentVector": vector,            # full-text embedding — keeps search accurate
    }

    try:
        _confluence_client.upload_documents(documents=[document])
        return True
    except Exception as e:
        print(f"   Index error for '{page_title[:60]}': {e}")
        return False


# ---------------------------------------------------------
#   MAIN SYNC
# ---------------------------------------------------------
def sync_space(space_key: str) -> dict:
    """Fetch and index all pages in a Confluence space (one doc per page)."""
    pages   = fetch_pages_in_space(space_key)
    indexed = 0
    skipped = 0

    for page in pages:
        page_id    = page["id"]
        page_title = page.get("title", f"Page {page_id}")

        print(f"\n  Processing: {page_title} (id={page_id})")

        try:
            body = fetch_page_body(page_id)
        except Exception as e:
            print(f"    Could not fetch body: {e}")
            skipped += 1
            continue

        if not body.strip():
            print(f"    Empty body — skipping")
            skipped += 1
            continue

        ok = index_page(page_id, page_title, body)
        if ok:
            indexed += 1
            print(f"   ✅ Indexed: {page_title}")
        else:
            skipped += 1
        time.sleep(REQUEST_DELAY_S)

    return {
        "space":   space_key,
        "pages":   len(pages),
        "indexed": indexed,
        "skipped": skipped,
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("=" * 60)
    print("  Confluence  Azure AI Search Sync")
    print("=" * 60)

    # Validate config
    missing = [v for v, val in [
        ("CONFLUENCE_URL",       CONFLUENCE_URL),
        ("CONFLUENCE_USER",      CONFLUENCE_USER),
        ("CONFLUENCE_API_TOKEN", CONFLUENCE_API_TOKEN),
    ] if not val]

    if missing:
        print(f"\n Missing environment variables: {', '.join(missing)}")
        print("   Add them to Azure.env and re-run.")
        sys.exit(1)

    if not CONFLUENCE_SPACE_KEYS:
        print("\n CONFLUENCE_SPACE_KEYS is empty  set it in Azure.env (e.g. SRE,DEVOPS)")
        sys.exit(1)

    print(f"\n  Spaces to sync: {', '.join(CONFLUENCE_SPACE_KEYS)}")
    print(f"  Azure Search index: {_confluence_index}\n")

    total_indexed = 0
    total_skipped = 0

    for space_key in CONFLUENCE_SPACE_KEYS:
        print(f"\n{''*60}")
        result = sync_space(space_key)
        total_indexed += result["indexed"]
        total_skipped += result["skipped"]
        print(f"\n  Space {result['space']}: "
              f"{result['pages']} pages | "
              f"{result['indexed']} indexed | "
              f"{result['skipped']} skipped")

    print(f"\n{'='*60}")
    print(f" Sync complete  {total_indexed} chunks indexed, {total_skipped} skipped")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
