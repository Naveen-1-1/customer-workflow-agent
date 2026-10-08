"""Build the NVIDIA chat models (primary + fallback) with a shared rate limiter."""

from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_nvidia_ai_endpoints import ChatNVIDIA, Model, register_model
from langchain_nvidia_ai_endpoints._statics import lookup_model

from customer_workflow_agent.settings import Settings

NO_THINKING = {"chat_template_kwargs": {"enable_thinking": False}}


def register_models(settings: Settings) -> None:
    """Register models the package doesn't know yet (e.g. Lightning in 1.4.3).

    Registering with an explicit endpoint also stops ChatNVIDIA from calling /v1/models.
    """
    for model_id in (settings.llm_primary_model, settings.llm_fallback_model):
        if lookup_model(model_id) is None:
            register_model(
                Model(
                    id=model_id,
                    model_type="chat",
                    client="ChatNVIDIA",
                    endpoint=f"{settings.nvidia_base_url.rstrip('/')}/chat/completions",
                    supports_tools=True,
                    supports_structured_output=True,
                    supports_thinking=True,
                    thinking_param_enable={"chat_template_kwargs": {"enable_thinking": True}},
                    thinking_param_disable=NO_THINKING,
                )
            )


def make_rate_limiter(settings: Settings) -> InMemoryRateLimiter:
    return InMemoryRateLimiter(
        requests_per_second=settings.llm_requests_per_minute / 60,
        check_every_n_seconds=0.1,
        max_bucket_size=4,
    )


def build_chat_models(
    settings: Settings, *, temperature: float, rate_limiter: InMemoryRateLimiter
) -> list[ChatNVIDIA]:
    """[primary, fallback], both with thinking off and the same rate limiter."""
    if settings.nvidia_api_key is None:
        raise RuntimeError("NVIDIA_API_KEY is not set (add it to .env)")
    register_models(settings)
    return [
        ChatNVIDIA(
            model=model_id,
            api_key=settings.nvidia_api_key.get_secret_value(),
            base_url=settings.nvidia_base_url,
            temperature=temperature,
            max_completion_tokens=settings.llm_max_completion_tokens,
            rate_limiter=rate_limiter,
            model_kwargs=NO_THINKING,
        )
        for model_id in (settings.llm_primary_model, settings.llm_fallback_model)
    ]
