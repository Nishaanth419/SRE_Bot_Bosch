import os
from dotenv import load_dotenv
from openai import AzureOpenAI
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient

# 1. Load Secrets (loads from Azure.env using absolute path so it works regardless of CWD)
_config_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_config_dir, "Azure.env"))

# 2. Azure OpenAI Client (API key auth)
ai_client = AzureOpenAI(
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
)

# 3. Vector RAG Setup
AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT")
AZURE_SEARCH_INDEX = os.getenv("AZURE_SEARCH_INDEX", "sre-incidents")

# 4. Configuration Constants
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-5.4-mini")
EMBED_MODEL = os.getenv("EMBED_MODEL")

# 5. Search Client Initialization (API key auth)
search_client = SearchClient(
    endpoint=AZURE_SEARCH_ENDPOINT,
    index_name=AZURE_SEARCH_INDEX,
    credential=AzureKeyCredential(os.getenv("AZURE_SEARCH_API_KEY")),
)

def get_embedding(text: str):
    """
    Helper function: Sends text to Azure OpenAI -> Returns Vector
    """
    text = text.replace("\n", " ")
    return ai_client.embeddings.create(input=[text], model=EMBED_MODEL).data[0].embedding