import os
from dotenv import load_dotenv
from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.search.documents import SearchClient

# 1. Load Secrets (loads from Azure.env using absolute path so it works regardless of CWD)
_config_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_config_dir, "Azure.env"))

# 2. Shared Azure credential (Managed Identity on App Service, az login locally)
azure_credential = DefaultAzureCredential()

# 3. Azure OpenAI Client (RBAC — no API key needed)
token_provider = get_bearer_token_provider(
    azure_credential, "https://cognitiveservices.azure.com/.default"
)
ai_client = AzureOpenAI(
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    azure_ad_token_provider=token_provider,
)

# 4. Vector RAG Setup
AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT")
AZURE_SEARCH_INDEX = os.getenv("AZURE_SEARCH_INDEX", "sre-incidents")

# 5. Configuration Constants
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-4.1-mini")
EMBED_MODEL = os.getenv("EMBED_MODEL")

# 6. Search Client Initialization (RBAC — no API key needed)
search_client = SearchClient(
    endpoint=AZURE_SEARCH_ENDPOINT,
    index_name=AZURE_SEARCH_INDEX,
    credential=azure_credential,
)

def get_embedding(text: str):
    """
    Helper function: Sends text to Azure OpenAI -> Returns Vector
    """
    text = text.replace("\n", " ")
    return ai_client.embeddings.create(input=[text], model=EMBED_MODEL).data[0].embedding