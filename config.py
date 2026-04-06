import requests

# ---------------------------------------------------------
# ⚙️ SHARED CONFIGURATION
# ---------------------------------------------------------
TEAMS_WEBHOOK_URL = (
    "https://default0ae51e1907c84e4bbb6d648ee58410.f4.environment.api.powerplatform.com:443"
    "/powerautomate/automations/direct/workflows/953db0999a4d4607abb43743e8e42a29"
    "/triggers/manual/paths/invoke?api-version=1&sp=%2Ftriggers%2Fmanual%2Frun"
    "&sv=1.0&sig=DFp1BPQDRu3B1PdIpNt0OErg1Q68RwnW8kSU8TWnzSQ"
)

# Shared HTTP session — used by Teams poster and GitHub grounding
session = requests.Session()
session.headers.update({"Content-Type": "application/json"})
