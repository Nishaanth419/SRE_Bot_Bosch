import re
import time

from config import session

# ---------------------------------------------------------
# 🌐 GITHUB REPO MAP — full team tech stack
# Add or update entries as the stack evolves.
# ---------------------------------------------------------
_GITHUB_REPO_MAP = {
    # ── Argo family ──────────────────────────────────────────────
    "argo workflows":               "argoproj/argo-workflows",
    "argo-workflows":               "argoproj/argo-workflows",
    "argo events":                  "argoproj/argo-events",
    "argo-events":                  "argoproj/argo-events",
    "argo cd":                      "argoproj/argo-cd",
    "argocd":                       "argoproj/argo-cd",
    # ── Kubernetes core ──────────────────────────────────────────
    "kubernetes":                   "kubernetes/kubernetes",
    "k8s":                          "kubernetes/kubernetes",
    "kubectl":                      "kubernetes/kubernetes",
    "helm":                         "helm/helm",
    "kustomize":                    "kubernetes-sigs/kustomize",
    # ── GitOps / Flux ─────────────────────────────────────────────
    "flux":                         "fluxcd/flux2",
    "fluxcd":                       "fluxcd/flux2",
    "flux2":                        "fluxcd/flux2",
    "helm-controller":              "fluxcd/helm-controller",
    "source-controller":            "fluxcd/source-controller",
    "kustomize-controller":         "fluxcd/kustomize-controller",
    # ── Service mesh & networking ────────────────────────────────
    "istio":                        "istio/istio",
    "istiod":                       "istio/istio",
    "cert-manager":                 "cert-manager/cert-manager",
    "certmanager":                  "cert-manager/cert-manager",
    "oauth2-proxy":                 "oauth2-proxy/oauth2-proxy",
    "oauth2 proxy":                 "oauth2-proxy/oauth2-proxy",
    # ── Policy & security ─────────────────────────────────────────
    "kyverno":                      "kyverno/kyverno",
    "gatekeeper":                   "open-policy-agent/gatekeeper",
    "opa gatekeeper":               "open-policy-agent/gatekeeper",
    "azure workload identity":      "Azure/azure-workload-identity",
    "workload identity":            "Azure/azure-workload-identity",
    # ── Observability stack ───────────────────────────────────────
    "prometheus":                   "prometheus/prometheus",
    "prometheus operator":          "prometheus-operator/prometheus-operator",
    "kube-prometheus":              "prometheus-operator/kube-prometheus",
    "alertmanager":                 "prometheus/alertmanager",
    "grafana":                      "grafana/grafana",
    "loki":                         "grafana/loki",
    "alloy":                        "grafana/alloy",
    "grafana alloy":                "grafana/alloy",
    "thanos":                       "thanos-io/thanos",
    "pushgateway":                  "prometheus/pushgateway",
    "prometheus pushgateway":       "prometheus/pushgateway",
    # ── Data, streaming & storage ─────────────────────────────────
    "redpanda":                     "redpanda-data/redpanda",
    "redis":                        "redis/redis",
    "qdrant":                       "qdrant/qdrant",
    # ── ML / compute platforms ────────────────────────────────────
    "ray":                          "ray-project/ray",
    "dagster":                      "dagster-io/dagster",
    "jupyter":                      "jupyter/notebook",
    "jupyterlab":                   "jupyterlab/jupyterlab",
    "apache ballista":              "apache/arrow-ballista",
    "ballista":                     "apache/arrow-ballista",
    # ── Node / hardware discovery ─────────────────────────────────
    "node feature discovery":       "kubernetes-sigs/node-feature-discovery",
    "node-feature-discovery":       "kubernetes-sigs/node-feature-discovery",
    "nfd":                          "kubernetes-sigs/node-feature-discovery",
    "gpu feature discovery":        "NVIDIA/k8s-device-plugin",
    "gpu-feature-discovery":        "NVIDIA/k8s-device-plugin",
    # ── Azure SDKs / platform ─────────────────────────────────────
    "azure sdk python":             "Azure/azure-sdk-for-python",
    "azure sdk":                    "Azure/azure-sdk-for-python",
    "azure cli":                    "Azure/azure-cli",
}

# Regex: does the message ask about versions, releases, or changelogs?
_VERSION_QUERY_RE = re.compile(
    r"\b(latest|version|release|changelog|bug\s*fix|what.?s new|upgrade|v\d+\.\d+)\b",
    re.IGNORECASE,
)

# In-process TTL cache: { repo_slug: (fetched_at_unix_ts, release_dict) }
_release_cache: dict = {}
_RELEASE_CACHE_TTL = 3600  # 1 hour — re-fetch after expiry


def _detect_repos(text: str) -> list:
    """Return all matching GitHub owner/repo slugs found in *text* (order preserved, no dupes)."""
    text_lower = text.lower()
    seen: set = set()
    repos = []
    for tool, slug in _GITHUB_REPO_MAP.items():
        if tool in text_lower and slug not in seen:
            seen.add(slug)
            repos.append(slug)
    return repos


def fetch_github_latest_release(repo_slug: str) -> dict:
    """Call GitHub's public releases/latest API and return release metadata.
    Cached for _RELEASE_CACHE_TTL seconds. Returns None on any error."""
    now    = time.time()
    cached = _release_cache.get(repo_slug)
    if cached and (now - cached[0]) < _RELEASE_CACHE_TTL:
        print(f"📦 Using cached release data for {repo_slug}")
        return cached[1]

    url = f"https://api.github.com/repos/{repo_slug}/releases/latest"
    try:
        resp = session.get(url, timeout=6, headers={"Accept": "application/vnd.github+json"})
        if resp.status_code == 200:
            data   = resp.json()
            result = {
                "tag":       data.get("tag_name", ""),
                "name":      data.get("name", ""),
                "published": (data.get("published_at") or "")[:10],
                "url":       data.get("html_url", ""),
                "body":      (data.get("body") or "")[:2500],
            }
            _release_cache[repo_slug] = (now, result)
            return result
        print(f"⚠️ GitHub API {resp.status_code} for {repo_slug}")
    except Exception as exc:
        print(f"⚠️ GitHub fetch error ({repo_slug}): {exc}")
    return None


def _build_release_context(user_message: str) -> str:
    """If the message asks about versions/releases for a known tool, fetch live
    GitHub data and return a grounding block ready to inject into a system prompt.
    Returns empty string when not applicable."""
    if not _VERSION_QUERY_RE.search(user_message):
        return ""
    repos = _detect_repos(user_message)
    if not repos:
        return ""

    blocks = []
    for repo in repos[:4]:    # cap at 4 tools per query
        print(f"🌐 Fetching live release data: {repo}")
        release = fetch_github_latest_release(repo)
        if not release:
            continue
        blocks.append(
            f"Repository : {repo}\n"
            f"Latest tag : {release['tag']}\n"
            f"Published  : {release['published']}\n"
            f"URL        : {release['url']}\n"
            f"Release notes:\n{release['body']}"
        )
        print(f"✅ Injected live release: {repo} ({release['tag']})")

    if not blocks:
        return ""

    separator = "\n" + "-" * 60 + "\n"
    return (
        "\n\n=== LIVE GITHUB RELEASE DATA (fetched now — use this as authoritative source) ===\n"
        + separator.join(blocks)
        + "\n=== END LIVE DATA ===\n"
        "IMPORTANT: For any version numbers, release dates, or changelogs, use ONLY "
        "the data above. Do NOT fall back to your training-data knowledge for these facts."
    )
