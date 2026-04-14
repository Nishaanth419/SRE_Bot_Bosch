"""
Clear all documents from the sre-incidents Azure AI Search index.

WARNING: This is irreversible. All stored incidents, runbooks, and chat
history will be permanently deleted from the index.

Usage:
    python tools/clear_incidents.py              # prompts for confirmation
    python tools/clear_incidents.py --force      # skips confirmation prompt
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_root, "Azure.env"))

from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential

INDEX = os.getenv("AZURE_SEARCH_INDEX", "sre-incidents")
client = SearchClient(
    endpoint=os.getenv("AZURE_SEARCH_ENDPOINT"),
    index_name=INDEX,
    credential=AzureKeyCredential(os.getenv("AZURE_SEARCH_API_KEY", "")),
)

BATCH_SIZE = 1000


def get_all_ids() -> list[str]:
    """Fetch all document IDs from the index."""
    ids = []
    results = client.search(search_text="*", select=["id"], top=BATCH_SIZE)
    for doc in results:
        ids.append(doc["id"])
    return ids


def delete_all():
    ids = get_all_ids()
    if not ids:
        print(f"ℹ️  Index '{INDEX}' is already empty.")
        return

    print(f"Found {len(ids)} document(s) in '{INDEX}'.")

    # Confirm unless --force flag passed
    if "--force" not in sys.argv:
        confirm = input(f"\n⚠️  This will permanently delete ALL {len(ids)} document(s). Type 'yes' to continue: ").strip().lower()
        if confirm != "yes":
            print("Aborted.")
            sys.exit(0)

    # Delete in batches of 1000 (Azure Search limit per batch)
    deleted = 0
    for i in range(0, len(ids), BATCH_SIZE):
        batch = [{"id": doc_id} for doc_id in ids[i:i + BATCH_SIZE]]
        client.delete_documents(documents=batch)
        deleted += len(batch)
        print(f"  Deleted {deleted}/{len(ids)}...")

    print(f"\n✅ Done. {deleted} document(s) removed from '{INDEX}'.")


if __name__ == "__main__":
    delete_all()
