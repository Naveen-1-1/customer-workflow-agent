"""The LiteLLM Router: primary and fallback NVIDIA models, with timeouts, retries and fallback.

What happens on each kind of failure (all handled by LiteLLM):
- 429 / 5xx / connection error: retried twice with backoff, then the fallback model.
- timeout (`llm_timeout_s`): straight to the fallback model, no retry.
- output that doesn't match the JSON schema: retried twice, then the fallback model.
- other errors (400, 401, ...): straight to the fallback model.
"""

import litellm
from langchain_core.rate_limiters import InMemoryRateLimiter
from litellm import Router
from litellm.router import RetryPolicy

from customer_workflow_agent.llm import observe
from customer_workflow_agent.settings import Settings

PRIMARY, FALLBACK = "primary", "fallback"
NO_THINKING = {"chat_template_kwargs": {"enable_thinking": False}}

# Process-wide LiteLLM switches.
litellm.enable_json_schema_validation = True  # bad structured output raises, so it's retried
litellm.suppress_debug_info = True
litellm.telemetry = False


def _deployment(name: str, model_id: str, settings: Settings, extra: dict) -> dict:
    assert settings.nvidia_api_key is not None
    return {
        "model_name": name,
        "litellm_params": {
            "model": f"openai/{model_id}",  # NVIDIA's API is OpenAI-compatible
            "api_base": settings.llm_base_url,
            "api_key": settings.nvidia_api_key.get_secret_value(),
            "max_tokens": settings.llm_max_completion_tokens,
            "extra_body": NO_THINKING,
            **extra,
        },
    }


def build_router(settings: Settings, extra_params: dict[str, dict] | None = None) -> Router:
    """`extra_params` adds LiteLLM params per model ("primary"/"fallback"), e.g. mock
    responses in tests."""
    if settings.nvidia_api_key is None:
        raise RuntimeError("NVIDIA_API_KEY is not set (add it to .env)")
    observe.install()
    extra = extra_params or {}
    retries = settings.llm_max_attempts - 1
    return Router(
        model_list=[
            _deployment(PRIMARY, settings.llm_primary_model, settings, extra.get(PRIMARY, {})),
            _deployment(FALLBACK, settings.llm_fallback_model, settings, extra.get(FALLBACK, {})),
        ],
        fallbacks=[{PRIMARY: [FALLBACK]}],
        timeout=settings.llm_timeout_s,
        num_retries=retries,
        retry_policy=RetryPolicy(
            TimeoutErrorRetries=0,
            RateLimitErrorRetries=retries,
            InternalServerErrorRetries=retries,
            BadRequestErrorRetries=0,
            AuthenticationErrorRetries=0,
        ),
        # The two models are different, so a failing one must not be "cooled down" in favour of
        # the other: that's what the fallback is for.
        disable_cooldowns=True,
    )


def make_rate_limiter(settings: Settings) -> InMemoryRateLimiter:
    return InMemoryRateLimiter(
        requests_per_second=settings.llm_requests_per_minute / 60,
        check_every_n_seconds=0.1,
        max_bucket_size=4,
    )
