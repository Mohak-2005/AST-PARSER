from dataclasses import dataclass
from typing import Optional, Tuple
import time
import httpx
from app.config import settings
from app.logger import backend_logger, log_stage, log_error, log_llm_metrics


@dataclass
class ProviderConfig:
    name: str
    model: str
    base_url: str
    api_key: str


def get_active_provider_name(override: Optional[str] = None) -> str:
    """Returns the normalized provider name from override or settings."""
    raw = override or settings.llm_provider or "groq"
    norm = raw.strip().lower()
    if norm in ("huggingface", "hugging_face"):
        return "hf"
    return norm


def get_provider_config(provider_name: Optional[str] = None, require_api_key: bool = True) -> ProviderConfig:
    """
    Resolves the configuration for the chosen LLM provider.
    Reads all credentials, models, and base URLs dynamically from settings (.env).
    """
    name = get_active_provider_name(provider_name)

    if name == "groq":
        api_key = settings.groq_api_key or ""
        if require_api_key and not api_key:
            raise ValueError("Missing GROQ_API_KEY in .env. Please configure GROQ_API_KEY to use Groq.")
        model = settings.groq_model or "openai/gpt-oss-120b"
        base_url = (settings.groq_base_url or "https://api.groq.com/openai/v1").rstrip("/")
        return ProviderConfig(name=name, model=model, base_url=base_url, api_key=api_key)

    elif name == "hf":
        api_key = settings.hf_token or ""
        if require_api_key and not api_key:
            raise ValueError("Missing HF_TOKEN in .env. Please configure HF_TOKEN to use Hugging Face.")
        model = settings.hf_model or "Qwen/Qwen3-Coder-30B-A3B-Instruct"
        base_url = (settings.hf_base_url or "https://router.huggingface.co/v1").rstrip("/")
        return ProviderConfig(name=name, model=model, base_url=base_url, api_key=api_key)

    elif name == "mistral":
        api_key = settings.mistral_api_key or ""
        if require_api_key and not api_key:
            raise ValueError("Missing MISTRAL_API_KEY in .env. Please configure MISTRAL_API_KEY to use Mistral.")
        model = settings.mistral_model or "mistral-small-latest"
        base_url = (settings.mistral_base_url or "https://api.mistral.ai/v1").rstrip("/")
        return ProviderConfig(name=name, model=model, base_url=base_url, api_key=api_key)

    elif name == "gemini":
        api_key = settings.gemini_api_key or ""
        if require_api_key and not api_key:
            raise ValueError("Missing GEMINI_API_KEY in .env. Please configure GEMINI_API_KEY to use Gemini.")
        model = settings.gemini_model or "gemini-2.5-flash"
        base_url = (settings.gemini_base_url or "https://generativelanguage.googleapis.com/v1beta/openai").rstrip("/")
        return ProviderConfig(name=name, model=model, base_url=base_url, api_key=api_key)

    else:
        raise ValueError(
            f"Unsupported LLM provider: '{name}'. Supported providers are: groq, hf, mistral, gemini."
        )


async def execute_provider_call(
    messages: list,
    max_tokens: int = 4000,
    temperature: float = 0.1,
    analysis_id: str = None,
    stage_name: str = "LLM_REQUEST",
    attempt: int = 1,
    metrics_info: dict = None,
    provider: Optional[str] = None,
) -> Tuple[str, int, float]:
    """
    Common LLM provider caller supporting Groq, Hugging Face, Mistral, and Gemini.
    Zero OpenRouter calls.
    Logs provider routing information without exposing secrets.
    """
    cfg = get_provider_config(provider)

    # Required logging format
    backend_logger.info(f"LLM PROVIDER: {cfg.name}")
    backend_logger.info(f"LLM MODEL: {cfg.model}")
    backend_logger.info(f"LLM REQUEST STARTED [{stage_name}] attempt={attempt}")
    log_stage(stage_name, "START", analysis_id=analysis_id, extra={"provider": cfg.name, "model": cfg.model, "attempt": attempt})

    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": cfg.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    timeout = httpx.Timeout(
        connect=settings.llm_connect_timeout,
        read=settings.llm_read_timeout,
        write=settings.llm_write_timeout,
        pool=settings.llm_pool_timeout
    )

    t0 = time.time()
    url = f"{cfg.base_url}/chat/completions"

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(url, headers=headers, json=payload)
            status_code = response.status_code
            response.raise_for_status()
            data = response.json()
            raw_content = data["choices"][0]["message"]["content"]

            duration_s = time.time() - t0
            backend_logger.info("LLM RESPONSE RECEIVED")
            backend_logger.info(f"LLM RESPONSE STATUS: {status_code}")
            log_stage(stage_name, "RESPONSE_RECEIVED", analysis_id=analysis_id, duration_ms=duration_s * 1000, extra={"attempt": attempt, "provider": cfg.name})

            if metrics_info:
                log_llm_metrics(
                    analysis_id=analysis_id,
                    model=cfg.model,
                    file_count=metrics_info.get("file_count", 0),
                    code_map_bytes=metrics_info.get("code_map_bytes", 0),
                    prompt_chars=metrics_info.get("prompt_chars", 0),
                    start_time=t0,
                    end_time=time.time(),
                    duration_s=duration_s,
                    status_code=status_code,
                    success=True,
                    error_type=None,
                    error_message=None
                )

            return raw_content, status_code, duration_s

        except httpx.TimeoutException as exc:
            duration_s = time.time() - t0
            err_msg = f"{cfg.name.upper()} LLM request timed out after {duration_s:.2f}s (configured read timeout: {settings.llm_read_timeout}s)"
            backend_logger.error(err_msg)
            log_stage(stage_name, "FAILURE", message=err_msg, analysis_id=analysis_id, duration_ms=duration_s * 1000)
            log_error(stage_name, "TimeoutException", err_msg, stack_trace=str(exc), analysis_id=analysis_id)

            # Check configured fallback if available
            fallback = getattr(settings, "llm_fallback_provider", None)
            if fallback and get_active_provider_name(fallback) != cfg.name:
                backend_logger.warning(f"Provider {cfg.name} timed out. Attempting fallback provider: {fallback}")
                return await execute_provider_call(
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    analysis_id=analysis_id,
                    stage_name=stage_name,
                    attempt=attempt,
                    metrics_info=metrics_info,
                    provider=fallback
                )

            raise TimeoutError(err_msg) from exc

        except httpx.HTTPStatusError as exc:
            duration_s = time.time() - t0
            status_code = exc.response.status_code
            err_body = exc.response.text[:300] if exc.response.text else ""
            err_msg = f"{cfg.name.upper()} returned HTTP {status_code}: {err_body}"
            backend_logger.error(err_msg)
            log_stage(stage_name, "FAILURE", message=err_msg, analysis_id=analysis_id, duration_ms=duration_s * 1000)
            log_error(stage_name, "HTTPStatusError", err_msg, analysis_id=analysis_id)

            # Check configured fallback if available
            fallback = getattr(settings, "llm_fallback_provider", None)
            if fallback and get_active_provider_name(fallback) != cfg.name:
                backend_logger.warning(f"Provider {cfg.name} failed with HTTP {status_code}. Attempting fallback provider: {fallback}")
                return await execute_provider_call(
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    analysis_id=analysis_id,
                    stage_name=stage_name,
                    attempt=attempt,
                    metrics_info=metrics_info,
                    provider=fallback
                )

            raise RuntimeError(err_msg) from exc

        except httpx.HTTPError as exc:
            duration_s = time.time() - t0
            err_msg = f"{cfg.name.upper()} network error: {str(exc)}"
            backend_logger.error(err_msg)
            log_stage(stage_name, "FAILURE", message=err_msg, analysis_id=analysis_id, duration_ms=duration_s * 1000)
            log_error(stage_name, type(exc).__name__, err_msg, analysis_id=analysis_id)

            fallback = getattr(settings, "llm_fallback_provider", None)
            if fallback and get_active_provider_name(fallback) != cfg.name:
                backend_logger.warning(f"Provider {cfg.name} network error. Attempting fallback provider: {fallback}")
                return await execute_provider_call(
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    analysis_id=analysis_id,
                    stage_name=stage_name,
                    attempt=attempt,
                    metrics_info=metrics_info,
                    provider=fallback
                )

            raise RuntimeError(err_msg) from exc
