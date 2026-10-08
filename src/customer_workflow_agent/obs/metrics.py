"""Prometheus metrics, served on /metrics. Alert rules: ops/prometheus/alerts.yml.

LLM metrics come in two levels:
- llm_calls_total / llm_call_seconds: every attempt on a model (retries and fallbacks included);
- llm_requests_total: one per question we ask, labelled with which model answered it in the end
  ("primary", "fallback", or "none" when every model failed).
"""

from prometheus_client import Counter, Gauge, Histogram
from prometheus_fastapi_instrumentator import metrics as http_metrics

# HTTP request metrics (http_requests_total, ...), created once per process and shared by every
# app instance: the instrumentator would otherwise make a fresh, unregistered set per app.
HTTP = http_metrics.default()

LATENCY_BUCKETS = (0.25, 0.5, 1, 2, 3, 5, 8, 10, 15, 20, 30, 45, 60)

LLM_CALLS = Counter(
    "llm_calls_total",
    "Model attempts by outcome: ok, timeout, rate_limited, server_error, bad_output, error",
    ["model", "prompt", "outcome"],
)
LLM_CALL_SECONDS = Histogram(
    "llm_call_seconds", "Duration of one model attempt", ["model"], buckets=LATENCY_BUCKETS
)
LLM_REQUESTS = Counter(
    "llm_requests_total",
    "LLM questions by the model that finally answered (primary, fallback, none)",
    ["prompt", "served_by"],
)
LLM_RATE_LIMITER_WAIT = Histogram(
    "llm_rate_limiter_wait_seconds",
    "Time spent waiting for the shared requests-per-minute limiter",
    buckets=(0.01, 0.1, 0.5, 1, 2, 5, 10, 20, 30, 60),
)

AGENT_REPLY_SECONDS = Histogram(
    "agent_reply_seconds",
    "Time from a customer action to the agent's reply (one graph run)",
    ["action"],
    buckets=LATENCY_BUCKETS,
)
OPEN_CHATS = Gauge("agent_open_chats", "Chats counted against the capacity limit")
CHATS_REFUSED = Counter("agent_chats_refused_total", "Chats turned away because the app was full")
APPROVALS_PENDING = Gauge("agent_approvals_pending", "Returns waiting for a supervisor")
OLDEST_APPROVAL_WAIT = Gauge(
    "agent_oldest_approval_wait_seconds", "How long the oldest pending approval has waited"
)
READY = Gauge("agent_ready", "1 when /readyz passes, else 0")


OUTCOMES = ("ok", "timeout", "rate_limited", "server_error", "bad_output", "error")


def start_llm_series(prompt_labels: list[str], models: list[str]) -> None:
    """Export every LLM counter at 0 from the start. A labelled counter otherwise only appears
    at its first increment, and Prometheus's increase() can't see that first step, so e.g. the
    first "every model failed" would never alert."""
    for prompt in prompt_labels:
        for served_by in ("primary", "fallback", "none"):
            LLM_REQUESTS.labels(prompt, served_by)
        for model in models:
            for outcome in OUTCOMES:
                LLM_CALLS.labels(model, prompt, outcome)


def outcome_of(error: BaseException | None) -> str:
    """Sort a LiteLLM exception into a metric outcome."""
    if error is None:
        return "ok"
    name = type(error).__name__
    if name == "Timeout" or "Timeout" in name:
        return "timeout"
    if name == "RateLimitError":
        return "rate_limited"
    if name == "JSONSchemaValidationError":
        return "bad_output"
    if name in {
        "InternalServerError",
        "ServiceUnavailableError",
        "APIConnectionError",
        "BadGatewayError",
    }:
        return "server_error"
    return "error"
