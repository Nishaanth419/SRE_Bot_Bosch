"""
📊 SRE Bot - Response Quality Metrics Evaluator
================================================
Evaluates AI-generated incident runbooks and follow-up answers
across multiple quality dimensions. Each metric returns a score
from 0.0 to 1.0 plus a human-readable explanation.

Metrics:
  1. Structural Completeness  – Required runbook sections present
  2. Actionability             – Concrete commands / code blocks
  3. Relevance                 – Response references input log terms
  4. Conciseness               – Appropriate length (not too short/long)
  5. Format Compliance         – Markdown formatting quality
  6. Code Block Quality        – Bash blocks are well-formed
  7. Verification Coverage     – Includes rollback / verification steps
  8. Response Latency          – Wall-clock time to generate answer

Usage:
    from response_metrics import evaluate_response, evaluate_followup
    
    report = evaluate_response(
        ai_response=diagnosis_text,
        input_log=user_log,
        latency_seconds=elapsed,
        used_cache=True
    )
    print(report["summary"])       # One-line summary
    print(report["overall_score"]) # 0.0 – 1.0
"""

import re
import time
import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger("sre_bot.metrics")


# ─────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────
@dataclass
class MetricResult:
    """Single metric evaluation result."""
    name: str
    score: float          # 0.0 – 1.0
    max_score: float      # always 1.0
    explanation: str
    details: dict = field(default_factory=dict)


@dataclass
class EvaluationReport:
    """Full evaluation report for one response."""
    response_type: str                    # "incident" | "followup"
    metrics: list                         # list[MetricResult]
    overall_score: float = 0.0           # weighted mean
    grade: str = ""                       # A / B / C / D / F
    summary: str = ""
    recommendations: list = field(default_factory=list)

    def to_dict(self):
        return {
            "response_type": self.response_type,
            "overall_score": round(self.overall_score, 3),
            "grade": self.grade,
            "summary": self.summary,
            "recommendations": self.recommendations,
            "metrics": [asdict(m) for m in self.metrics],
        }

    def to_json(self, indent=2):
        return json.dumps(self.to_dict(), indent=indent)


# ─────────────────────────────────────────────
# Metric weights (must sum to 1.0)
# ─────────────────────────────────────────────
INCIDENT_WEIGHTS = {
    "structural_completeness": 0.20,
    "actionability":           0.25,
    "relevance":               0.15,
    "conciseness":             0.10,
    "code_block_quality":      0.15,
    "verification_coverage":   0.10,
    "response_latency":        0.05,
}

FOLLOWUP_WEIGHTS = {
    "actionability":      0.30,
    "relevance":          0.25,
    "conciseness":        0.20,
    "code_block_quality": 0.15,
    "response_latency":   0.10,
}


# ─────────────────────────────────────────────
# 1. Structural Completeness (incident only)
# ─────────────────────────────────────────────
_REQUIRED_SECTIONS = [
    ("root_cause",        r"(root\s*cause|why\s+this\s+(failure|error)\s+occurred)"),
    ("impact_severity",   r"(impact|severity)"),
    ("resolution_steps",  r"(step\s*\d|resolution\s*path|detailed\s*resolution)"),
    ("verification",      r"(verif(y|ication)|rollback|confirm.*(healthy|running))"),
]

def _structural_completeness(text: str) -> MetricResult:
    found = {}
    for section_name, pattern in _REQUIRED_SECTIONS:
        found[section_name] = bool(re.search(pattern, text, re.IGNORECASE))

    present = sum(found.values())
    score = present / len(_REQUIRED_SECTIONS)

    missing = [s for s, ok in found.items() if not ok]
    explanation = (
        f"{present}/{len(_REQUIRED_SECTIONS)} required sections present."
        + (f" Missing: {', '.join(missing)}." if missing else " All sections present.")
    )
    return MetricResult(
        name="structural_completeness",
        score=score,
        max_score=1.0,
        explanation=explanation,
        details=found,
    )


# ─────────────────────────────────────────────
# 2. Actionability
# ─────────────────────────────────────────────
_ACTION_PATTERNS = [
    r"```",                       # code blocks
    r"kubectl\s+\w+",            # kubectl commands
    r"az\s+\w+",                 # Azure CLI commands
    r"helm\s+\w+",               # Helm commands
    r"docker\s+\w+",             # Docker commands
    r"curl\s+",                  # API calls
    r"(run|execute|apply|create|delete|restart|scale)", # action verbs
]

def _actionability(text: str) -> MetricResult:
    code_blocks = re.findall(r"```[\s\S]*?```", text)
    num_blocks = len(code_blocks)

    action_hits = sum(1 for p in _ACTION_PATTERNS if re.search(p, text, re.IGNORECASE))
    action_ratio = action_hits / len(_ACTION_PATTERNS)

    # Score: 50% code blocks present, 50% action keyword density
    block_score = min(num_blocks / 3, 1.0)  # 3+ blocks = full marks
    score = 0.5 * block_score + 0.5 * action_ratio

    explanation = (
        f"{num_blocks} code block(s) found; "
        f"{action_hits}/{len(_ACTION_PATTERNS)} action patterns matched."
    )
    return MetricResult(
        name="actionability",
        score=round(score, 3),
        max_score=1.0,
        explanation=explanation,
        details={"code_blocks": num_blocks, "action_hits": action_hits},
    )


# ─────────────────────────────────────────────
# 3. Relevance
# ─────────────────────────────────────────────
def _extract_keywords(log_text: str, min_length: int = 4) -> set:
    """Extract meaningful tokens from the input log for relevance matching."""
    tokens = re.findall(r"[A-Za-z_\-]{4,}", log_text)
    # Remove very common stop-words
    stopwords = {
        "that", "this", "with", "from", "have", "been", "will", "your",
        "they", "them", "than", "into", "also", "just", "more", "some",
        "when", "what", "which", "could", "would", "should", "there",
        "about", "each", "make", "like", "very", "after", "before",
        "error", "warning", "info",  # too generic for SRE context
    }
    return {t.lower() for t in tokens if t.lower() not in stopwords and len(t) >= min_length}

def _relevance(response: str, input_log: str) -> MetricResult:
    keywords = _extract_keywords(input_log)
    if not keywords:
        return MetricResult(
            name="relevance", score=1.0, max_score=1.0,
            explanation="No keywords extracted from input; skipping relevance check.",
        )

    response_lower = response.lower()
    matched = {kw for kw in keywords if kw in response_lower}
    ratio = len(matched) / len(keywords)

    # Generous curve: 40%+ keyword overlap → full score
    score = min(ratio / 0.4, 1.0)

    explanation = (
        f"{len(matched)}/{len(keywords)} input keywords reflected in response "
        f"({ratio:.0%} overlap)."
    )
    return MetricResult(
        name="relevance",
        score=round(score, 3),
        max_score=1.0,
        explanation=explanation,
        details={"total_keywords": len(keywords), "matched": len(matched)},
    )


# ─────────────────────────────────────────────
# 4. Conciseness
# ─────────────────────────────────────────────
# Ideal word-count ranges (inclusive)
_INCIDENT_RANGE = (150, 1200)
_FOLLOWUP_RANGE = (50, 600)

def _conciseness(text: str, response_type: str = "incident") -> MetricResult:
    word_count = len(text.split())
    lo, hi = _INCIDENT_RANGE if response_type == "incident" else _FOLLOWUP_RANGE

    if lo <= word_count <= hi:
        score = 1.0
        explanation = f"Word count ({word_count}) is within ideal range ({lo}–{hi})."
    elif word_count < lo:
        score = max(word_count / lo, 0.2)
        explanation = f"Response too short ({word_count} words, ideal ≥ {lo})."
    else:
        over = word_count - hi
        penalty = min(over / hi, 0.8)   # cap penalty at 0.8
        score = 1.0 - penalty
        explanation = f"Response verbose ({word_count} words, ideal ≤ {hi})."

    return MetricResult(
        name="conciseness",
        score=round(score, 3),
        max_score=1.0,
        explanation=explanation,
        details={"word_count": word_count, "ideal_range": [lo, hi]},
    )


# ─────────────────────────────────────────────
# 5. Code Block Quality
# ─────────────────────────────────────────────
def _code_block_quality(text: str) -> MetricResult:
    blocks = re.findall(r"```(\w*)\n([\s\S]*?)```", text)
    if not blocks:
        return MetricResult(
            name="code_block_quality",
            score=0.0,
            max_score=1.0,
            explanation="No code blocks found in response.",
        )

    scores = []
    issues = []
    for i, (lang, body) in enumerate(blocks, 1):
        block_score = 1.0

        # Penalize missing language hint
        if not lang:
            block_score -= 0.2
            issues.append(f"Block {i}: missing language hint")

        # Penalize empty blocks
        stripped = body.strip()
        if not stripped:
            block_score -= 0.5
            issues.append(f"Block {i}: empty body")

        # Penalize placeholder commands
        if re.search(r"\[.*?\]", stripped):
            block_score -= 0.1
            issues.append(f"Block {i}: contains placeholder brackets")

        scores.append(max(block_score, 0.0))

    avg = sum(scores) / len(scores)
    explanation = (
        f"{len(blocks)} code block(s) evaluated, avg quality {avg:.2f}."
        + (f" Issues: {'; '.join(issues)}." if issues else "")
    )
    return MetricResult(
        name="code_block_quality",
        score=round(avg, 3),
        max_score=1.0,
        explanation=explanation,
        details={"num_blocks": len(blocks), "issues": issues},
    )


# ─────────────────────────────────────────────
# 7. Verification Coverage (incident only)
# ─────────────────────────────────────────────
_VERIFY_PATTERNS = [
    r"verif(y|ication)",
    r"rollback",
    r"confirm",
    r"health\s*check",
    r"test\s+(that|the|if|by)",
    r"expected\s+output",
    r"should\s+(see|show|return|output)",
]

def _verification_coverage(text: str) -> MetricResult:
    hits = sum(1 for p in _VERIFY_PATTERNS if re.search(p, text, re.IGNORECASE))
    score = min(hits / 3, 1.0)   # 3+ verification cues = full marks

    explanation = (
        f"{hits}/{len(_VERIFY_PATTERNS)} verification/rollback cues found."
    )
    return MetricResult(
        name="verification_coverage",
        score=round(score, 3),
        max_score=1.0,
        explanation=explanation,
        details={"hits": hits},
    )


# ─────────────────────────────────────────────
# 8. Response Latency
# ─────────────────────────────────────────────
# Thresholds in seconds
_LATENCY_EXCELLENT = 5.0
_LATENCY_ACCEPTABLE = 15.0
_LATENCY_POOR = 30.0

def _response_latency(seconds: float) -> MetricResult:
    if seconds <= _LATENCY_EXCELLENT:
        score = 1.0
        explanation = f"Excellent latency ({seconds:.1f}s ≤ {_LATENCY_EXCELLENT}s)."
    elif seconds <= _LATENCY_ACCEPTABLE:
        score = 1.0 - (seconds - _LATENCY_EXCELLENT) / (_LATENCY_ACCEPTABLE - _LATENCY_EXCELLENT) * 0.5
        explanation = f"Acceptable latency ({seconds:.1f}s)."
    elif seconds <= _LATENCY_POOR:
        score = 0.5 - (seconds - _LATENCY_ACCEPTABLE) / (_LATENCY_POOR - _LATENCY_ACCEPTABLE) * 0.4
        explanation = f"Slow latency ({seconds:.1f}s); consider optimisation."
    else:
        score = 0.1
        explanation = f"Very slow ({seconds:.1f}s); investigate bottleneck."

    return MetricResult(
        name="response_latency",
        score=round(max(score, 0.0), 3),
        max_score=1.0,
        explanation=explanation,
        details={"seconds": round(seconds, 2)},
    )


# ─────────────────────────────────────────────
# Grading helper
# ─────────────────────────────────────────────
def _grade(score: float) -> str:
    if score >= 0.9:
        return "A"
    if score >= 0.8:
        return "B"
    if score >= 0.65:
        return "C"
    if score >= 0.5:
        return "D"
    return "F"


def _build_recommendations(metrics: list) -> list:
    """Generate actionable recommendations for any metric below 0.7."""
    tips = {
        "structural_completeness": "Ensure the system prompt enforces Root Cause, Impact, Resolution Steps, and Verification sections.",
        "actionability": "Add more concrete, copy-pasteable commands (kubectl, az cli, docker) in code blocks.",
        "relevance": "Improve prompt grounding—include more input-log keywords in the system instructions.",
        "conciseness": "Tune max_tokens or add instructions to keep responses within ideal word-count ranges.",
        "code_block_quality": "Require language hints (```bash) and discourage placeholder brackets in commands.",
        "verification_coverage": "Always require a Verification & Rollback section with health-check commands.",
        "response_latency": "Consider streaming responses, prompt caching, or a smaller model for follow-ups.",
    }
    recs = []
    for m in metrics:
        if m.score < 0.7 and m.name in tips:
            recs.append(f"[{m.name}] {tips[m.name]}")
    return recs


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────
def evaluate_response(
    ai_response: str,
    input_log: str,
    latency_seconds: float = 0.0,
    used_cache: bool = False,
) -> EvaluationReport:
    """
    Evaluate a full incident runbook response.
    
    Args:
        ai_response:     The AI-generated diagnosis / runbook text.
        input_log:        The original error log submitted by the user.
        latency_seconds:  Wall-clock seconds to generate the response.
        used_cache:       Whether the response leveraged a cached similar incident.
    
    Returns:
        EvaluationReport with per-metric scores and an overall grade.
    """
    metrics = [
        _structural_completeness(ai_response),
        _actionability(ai_response),
        _relevance(ai_response, input_log),
        _conciseness(ai_response, "incident"),
        _code_block_quality(ai_response),
        _verification_coverage(ai_response),
        _response_latency(latency_seconds),
    ]

    weighted_sum = sum(
        m.score * INCIDENT_WEIGHTS.get(m.name, 0) for m in metrics
    )

    report = EvaluationReport(
        response_type="incident",
        metrics=metrics,
        overall_score=round(weighted_sum, 3),
        grade=_grade(weighted_sum),
        recommendations=_build_recommendations(metrics),
    )
    report.summary = (
        f"Incident response quality: {report.grade} ({report.overall_score:.0%}) "
        f"| Cache={'hit' if used_cache else 'miss'} "
        f"| Latency={latency_seconds:.1f}s"
    )
    logger.info(report.summary)
    return report


def evaluate_followup(
    ai_response: str,
    input_question: str,
    original_log: str = "",
    latency_seconds: float = 0.0,
) -> EvaluationReport:
    """
    Evaluate a follow-up / chat answer.
    
    Args:
        ai_response:      The AI-generated follow-up answer.
        input_question:    The user's follow-up question.
        original_log:      The original error log for relevance grounding.
        latency_seconds:   Wall-clock seconds to generate the response.
    
    Returns:
        EvaluationReport with per-metric scores and an overall grade.
    """
    context_text = f"{input_question} {original_log}"

    metrics = [
        _actionability(ai_response),
        _relevance(ai_response, context_text),
        _conciseness(ai_response, "followup"),
        _code_block_quality(ai_response),
        _response_latency(latency_seconds),
    ]

    weighted_sum = sum(
        m.score * FOLLOWUP_WEIGHTS.get(m.name, 0) for m in metrics
    )

    report = EvaluationReport(
        response_type="followup",
        metrics=metrics,
        overall_score=round(weighted_sum, 3),
        grade=_grade(weighted_sum),
        recommendations=_build_recommendations(metrics),
    )
    report.summary = (
        f"Follow-up response quality: {report.grade} ({report.overall_score:.0%}) "
        f"| Latency={latency_seconds:.1f}s"
    )
    logger.info(report.summary)
    return report


# ─────────────────────────────────────────────
# Timing context manager for easy latency capture
# ─────────────────────────────────────────────
class ResponseTimer:
    """Context manager to measure response generation time.

    Usage:
        with ResponseTimer() as timer:
            diag = ask_ai_incident(log)
        report = evaluate_response(diag, log, timer.elapsed)
    """
    def __init__(self):
        self.start: float = 0.0
        self.end: float = 0.0
        self.elapsed: float = 0.0

    def __enter__(self):
        self.start = time.time()
        return self

    def __exit__(self, *args):
        self.end = time.time()
        self.elapsed = round(self.end - self.start, 3)
