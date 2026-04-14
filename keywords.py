import re
from functools import lru_cache
from utils import _strip_html

# ---------------------------------------------------------
# 🔑 CLUSTER VOCABULARY
# These are HIGH-SIGNAL tokens: always keep them if found in a log.
# ---------------------------------------------------------
_CLUSTER_KEYWORDS = frozenset({
    # Namespaces
    "apache-ballista", "argo", "argo-dev", "argo-dmt-enrich", "argo-dmt-scenar",
    "argo-dmv", "argo-dsp-al", "argo-dsp-de", "argo-dsp-op", "argo-dsp-vp",
    "argo-edge-cloud", "argo-events", "argo-goc", "argo-gt-3d", "argo-gt-fusion",
    "argo-gt-op", "argo-gt-xcalib", "argo-image-search", "argo-label-s",
    "argo-lane-ops", "argo-lapi", "argo-lgt-mapfm", "argo-map-pypln",
    "argo-mdm-deep", "argo-mdr", "argo-psd", "argo-unicorn",
    "atlas", "azure-workload-identity-system", "blueprint", "dataset-service",
    "dll-viz", "dmv", "flux-system", "gatekeeper-system", "gpu-feature-discovery",
    "image-search", "istio-system", "kube-system", "kyverno", "labels-for-you",
    "maploc-vde", "node-feature-discovery", "postgres",
    # Services / deployments / apps
    "alloy", "alertmanager", "argo-events-controller-manager",
    "argo-workflows-workflow-controller", "argocli-exporter",
    "ballista-executor", "ballista-scheduler", "business-events-hub",
    "cert-manager", "clip-microservice", "clip-vit-b-32", "clip-vit-l-14-336",
    "cluster-resources-tracker", "cufu-datamining", "customer-onboarding",
    "dagster-platform", "dagster-user-deployment", "datasets-service-v2",
    "datasets-viz-backend", "dd-lapi-kognic", "dd-lapi-labels-fetcher",
    "dd-lapi-mdm-export", "dd-lapi-supplier-provisioning",
    "enrichment-backfilling", "eventbus-default-stan", "eventrouter",
    "gatekeeper-controller", "gmdm-sha-webhook", "helm-controller",
    "indexation-service", "istiod", "jupyter-deployment", "konnectivity-agent",
    "kustomize-controller", "labels4u", "loki-backend", "loki-write",
    "lz-template-fredrik", "mdm-unicorn-notification",
    "oauth2-proxy", "postgres-postgresql", "prometheus-operator",
    "prometheus-pushgateway", "qdrant", "queue-3-sensor",
    "ray-cluster", "recordings-data-product", "redis", "redis-datasets-service",
    "redpanda", "sequencing-cron", "session-handler-api",
    "shell-ui-backend", "signalr-broadcast-func", "signalr-negotiate-func",
    "silver-weather-recording", "source-controller",
    "test-cake-events", "test-cake-sensor", "test-sensor1",
    "test-service-bus123", "test-source-eventsource",
    "thanos-compactor", "thanos-query", "thanos-query-frontend",
    "thanos-ruler", "thanos-storegateway",
    "thumbnail-microservice", "tileserver-microservice",
    "visualization-api", "webhook-eventsource", "webhook-sensor",
    "workflow-sensor", "print-sha-sensor",
    # Nodepools
    "adspm10", "argolarge", "argoprio", "argow1f9b", "argosystem",
    "largeb59b", "minion", "normalefb1", "systema3bb",
})

# Build a single compiled regex for cluster keywords (word-boundary match, no false positives)
# Sort longest-first so "argo-events" matches before "argo"
_CLUSTER_KW_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(kw) for kw in sorted(_CLUSTER_KEYWORDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# Common noise words — skip these during keyword extraction
_STOP_WORDS = frozenset({
    # English
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "must", "can", "could", "to", "of", "in",
    "for", "on", "with", "at", "by", "from", "as", "into", "about",
    "between", "through", "after", "before", "above", "below", "up",
    "down", "out", "off", "over", "under", "then", "than", "that",
    "this", "these", "those", "it", "its", "not", "no", "nor", "and",
    "or", "but", "if", "so", "yet", "both", "each", "all", "any",
    "few", "more", "most", "some", "such", "only", "same", "other",
    "new", "old", "one", "two", "first", "last", "also", "just",
    "like", "well", "very", "even", "still", "already", "here", "there",
    "when", "where", "how", "what", "which", "who", "whom", "why",
    "true", "false", "null", "none", "yes",
    # K8s structural noise — every log has these
    "kubectl", "get", "describe", "logs", "pod", "pods", "namespace",
    "default", "kube", "system", "name", "status", "age", "ready",
    "restarts", "node", "events", "type", "reason", "message",
    "normal", "warning", "info", "time", "timestamp", "utc", "ago",
    "running", "container", "containers", "image", "spec", "metadata",
    "labels", "annotations", "created", "started", "pulling", "pulled",
    "assigned", "successfully", "scheduled", "version", "output",
    "level", "msg", "source", "component", "count", "object",
    "apiversion", "kind", "uid", "resourceversion", "generation",
    "fieldstype", "fieldsv1", "managedfields", "manager", "operation",
    "update", "subresource", "request", "response", "watch",
    "line", "file", "func", "caller", "logger",
})

# ---------------------------------------------------------
# 🔍 CLASSIFIER PATTERNS (pre-compiled for performance)
# ---------------------------------------------------------
_GREETING_RES = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"^\s*(hi|hello|hey|yo|good\s+(morning|afternoon|evening))\b",
    r"^\s*(thanks|thank you|thx)\b",
    r"^\s*(help|what can you do)\??\s*$",
))

_INCIDENT_RES = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\b(error|exception|failed|failure|fatal|panic|critical|incident)\b",
    r"\b(traceback|stack\s*trace|crashloopbackoff|imagepullbackoff|errimagepull|oomkilled|segfault)\b",
    r"\b(timeout|timed\s*out|refused|forbidden|unauthorized|denied|unhealthy|degraded|evicted)\b",
    r"\b(restart(?:ing|ed|s)?|back-?off|probe failed|readiness failed|liveness failed|exit code)\b",
    r"\b(http|grpc)\s*(4\d\d|5\d\d)\b",
))

_LOG_RES = tuple(re.compile(p, re.IGNORECASE | re.MULTILINE) for p in (
    r"\b(kubectl|pod|pods|deployment|container|namespace|node|logs|events?)\b",
    r"\b(azure|aks|kubernetes|helm|docker|service bus|event hub|redis|postgres)\b",
    r"\b(cronjob|daemonset|statefulset|replicaset|ingress|service|nodepool)\b",
    r"(^|\n)\s*(warning|error|fatal|info)\b",
    r"(^|\n)\s*[A-Za-z0-9_.-]+:\s+",
))

_HISTORY_RES = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\b(previous|past|earlier|old)\b.*\b(chats?|conversations?|issues?|incidents?|problems?|threads?|cases?|tickets?|history)\b",
    r"\b(chats?|conversations?|issues?|incidents?|problems?|threads?|cases?|tickets?|history)\b.*\b(previous|past|earlier|old)\b",
    r"\b(similar|same)\b.*\b(issues?|incidents?|problems?|errors?|failures?)\b",
    r"\b(seen|happened|occurred)\b.*\b(before|previously|earlier)\b",
    r"\b(show|find|search|get|look up|pull)\b.*\b(history|previous|past|earlier)\b",
))

_QUESTION_RE = re.compile(r"\b(how|what|why|can you|could you|explain|tell me|show me)\b", re.IGNORECASE)

# Pre-compiled token filters for extract_keywords
_TOKEN_SPLIT_RE = re.compile(r'[\s/=:,;|{}\[\]()\"\']+')
_FILTER_TIMESTAMP_RE = re.compile(r'^\d{4}-\d{2}-\d{2}')
_FILTER_TIME_RE = re.compile(r'^\d{2}:\d{2}')
_FILTER_HEX_RE = re.compile(r'^[0-9a-f]{8,}$')
_FILTER_DIGITS_RE = re.compile(r'^\d+$')
_FILTER_VERSION_RE = re.compile(r'^v?\d+\.\d+')
_FILTER_IP_RE = re.compile(r'^\d+\.\d+\.\d+\.\d+')
_FILTER_VMSS_RE = re.compile(r'^vmss\d')


# ---------------------------------------------------------
# 🔑 KEYWORD EXTRACTION
# ---------------------------------------------------------
@lru_cache(maxsize=512)
def extract_keywords(text, max_keywords=40):
    """Extract distinctive keywords from log text, keeping cluster-specific
    names and stripping common noise."""
    if not text:
        return ""

    text_lower = text.lower()

    # 1. Check for known cluster keywords — word-boundary regex, no false positives
    cluster_hits = [m.group().lower() for m in _CLUSTER_KW_RE.finditer(text_lower)]

    # 2. Tokenize on whitespace + common log separators
    tokens = _TOKEN_SPLIT_RE.split(text)

    # 3. Filter out stop words, timestamps, hex hashes, numbers
    seen = set(cluster_hits)
    general_kw = []
    for token in tokens:
        clean = token.strip(".-_#<>").lower()
        if (
            clean
            and clean not in _STOP_WORDS
            and clean not in seen
            and len(clean) >= 3
            and len(clean) <= 80
            and not _FILTER_TIMESTAMP_RE.match(clean)
            and not _FILTER_TIME_RE.match(clean)
            and not _FILTER_HEX_RE.match(clean)
            and not _FILTER_DIGITS_RE.match(clean)
            and not _FILTER_VERSION_RE.match(clean)
            and not _FILTER_IP_RE.match(clean)
            and not _FILTER_VMSS_RE.match(clean)
        ):
            general_kw.append(clean)
            seen.add(clean)

    final = (cluster_hits + general_kw)[:max_keywords]
    keyword_str = " ".join(final)
    print(f"🔑 Extracted {len(final)} keywords: {keyword_str[:150]}{'...' if len(keyword_str) > 150 else ''}")
    return keyword_str


def is_incident_message(text, subject=""):
    """Heuristic classifier: returns True when the message looks like a real incident log."""
    combined = f"{subject}\n{text}".strip()
    if not combined:
        return False

    combined_lower = combined.lower()
    text_lower = text.lower()

    # Greeting guard — bail unless incident keywords are also present
    if any(r.search(combined_lower) for r in _GREETING_RES):
        if not any(r.search(combined_lower) for r in _INCIDENT_RES):
            return False

    score = 0

    incident_hits = sum(1 for r in _INCIDENT_RES if r.search(combined_lower))
    log_hits = sum(1 for r in _LOG_RES if r.search(combined))
    cluster_hits = len(_CLUSTER_KW_RE.findall(combined_lower))

    score += min(incident_hits, 2) * 2
    score += min(log_hits, 2)
    score += 1 if cluster_hits else 0

    # Multi-line structured text bonus
    if len(text.splitlines()) >= 4 and re.search(r"[:={}\[\]]", text):
        score += 1

    # Question-word penalty: require at least 1 incident keyword,
    # otherwise questions like "how does argo work?" get misrouted.
    # Extra-strong demotion when the message BODY is a single-line question
    # (prevents subject keywords like "incident" in a bot name from misrouting).
    if _QUESTION_RE.search(text_lower):
        is_single_line_question = "\n" not in text.strip()
        if incident_hits == 0:
            score -= 3 if is_single_line_question else 2
        else:
            score -= 2 if is_single_line_question else 1

    return score >= 2


_PURE_GREETING_RE = re.compile(
    r"^\s*(hi|hello|hey|yo|good\s+(morning|afternoon|evening)|thanks|thank\s*you|thx|help)\s*[!?.]*\s*$",
    re.IGNORECASE,
)

_GREETING_RESPONSES = {
    "hi": "👋 Hello! I'm your SRE assistant. Paste an error log and I'll diagnose it, or ask me any DevOps question.",
    "hello": "👋 Hello! I'm your SRE assistant. Paste an error log and I'll diagnose it, or ask me any DevOps question.",
    "hey": "👋 Hey! I'm your SRE assistant. Paste an error log and I'll diagnose it, or ask me any DevOps question.",
    "yo": "👋 Yo! I'm your SRE assistant. Paste an error log and I'll diagnose it, or ask me any DevOps question.",
    "thanks": "👍 You're welcome! Let me know if anything else comes up.",
    "thank you": "👍 You're welcome! Let me know if anything else comes up.",
    "thx": "👍 You're welcome! Let me know if anything else comes up.",
    "help": "🤖 I can help with:\n• **Paste an error log** → I'll diagnose it and give you a fix\n• **Ask a question** → DevOps, K8s, Azure, tooling\n• **Ask about past incidents** → I'll search history for patterns",
}


def greeting_response(text):
    """Return a canned response for simple greetings, or None if not a greeting."""
    if not text or not _PURE_GREETING_RE.match(_strip_html(text)):
        return None
    key = re.sub(r'[!?.]+$', '', _strip_html(text).strip().lower()).strip()
    # Normalize multi-word keys
    if key.startswith("good "):
        return "👋 Good " + key.split(" ", 1)[1].capitalize() + "! I'm your SRE assistant. Paste an error log or ask me a question."
    return _GREETING_RESPONSES.get(key)


def is_history_request(text):
    """Returns True when the user is asking about previous incidents or conversations."""
    if not text:
        return False
    normalized = text.lower().strip()
    return any(r.search(normalized) for r in _HISTORY_RES)


def _extract_history_query_keywords(query_text, max_keywords=12):
    keywords = extract_keywords(query_text, max_keywords=max_keywords)
    if keywords:
        return keywords
    fallback = [
        t for t in re.findall(r"[a-z0-9-]{3,}", (query_text or "").lower())
        if t not in _STOP_WORDS
    ]
    return " ".join(fallback[:max_keywords])


def _keyword_overlap_ratio(left_keywords, right_keywords):
    left_set = set((left_keywords or "").split())
    right_set = set((right_keywords or "").split())
    if not left_set or not right_set:
        return 0.0
    intersection = left_set & right_set
    union = left_set | right_set
    return len(intersection) / len(union) if union else 0.0
