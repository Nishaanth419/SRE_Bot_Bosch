"""
One-time setup: creates the 'confluence-kb' Azure AI Search index.
Fields: id, page_name, page_content, contentVector (1536-dim).
Run once: python tools/create_confluence_index.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Azure.env"))

from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex, SearchFieldDataType,
    SimpleField, SearchableField, SearchField,
    VectorSearch, HnswAlgorithmConfiguration, VectorSearchProfile,
)
from azure.core.credentials import AzureKeyCredential

endpoint   = os.getenv("AZURE_SEARCH_ENDPOINT")
api_key    = os.getenv("AZURE_SEARCH_API_KEY")
index_name = os.getenv("CONFLUENCE_INDEX", "confluence-kb")

client = SearchIndexClient(endpoint, AzureKeyCredential(api_key))

fields = [
    SimpleField(name="id",           type=SearchFieldDataType.String, key=True, filterable=True),
    SearchableField(name="page_name",    type=SearchFieldDataType.String),
    SearchableField(name="page_content", type=SearchFieldDataType.String),
    SearchField(
        name="contentVector",
        type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
        searchable=True,
        vector_search_dimensions=1536,
        vector_search_profile_name="hnsw-profile",
    ),
]

vector_search = VectorSearch(
    algorithms=[HnswAlgorithmConfiguration(name="hnsw-algo")],
    profiles=[VectorSearchProfile(name="hnsw-profile", algorithm_configuration_name="hnsw-algo")],
)

index = SearchIndex(name=index_name, fields=fields, vector_search=vector_search)

try:
    client.get_index(index_name)
    print(f"Index '{index_name}' already exists — skipping creation.")
except Exception:
    client.create_index(index)
    print(f"✅ Index '{index_name}' created successfully.")
