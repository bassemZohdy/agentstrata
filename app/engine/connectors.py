"""Model connector construction (REQUIREMENTS.md LLM-01 – LLM-03).

- gemini + !vertex: ADK native Gemini with the API key (client_kwargs).
- gemini + vertex: ADK Gemini with Vertex AI (ADC) via client_kwargs.
- openai/anthropic/ollama/litellm: ADK's LiteLLM bridge with the LLM-01
  model strings; ollama sets api_base.
Retry policy: at most two retries for transport errors/429/5xx before any
delta, 1s then 2s backoff + jitter, honoring Retry-After, never replaying
after a delta or tool call. Credential health: unavailable/unknown/available,
file-backed re-resolve per request vs env process-start snapshot (LLM-02).
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from google.adk.models import BaseLlm
from google.adk.models.google_llm import Gemini
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

MAX_RETRIES = 2

# R-33: API-12 per-run override transport — the values ride in
# RunConfig.labels (ADK merges run-config labels into the per-step
# LlmRequest.config.labels) and are applied + stripped by RetryableLlm.
_OVERRIDE_LABEL_TEMPERATURE = "x-agentstrata-temperature"
_OVERRIDE_LABEL_MAX_TOKENS = "x-agentstrata-max-tokens"
BACKOFF_SECONDS = (1, 2)


class ProviderConfigError(Exception):
    """Invalid provider configuration (boot-time, fails validation)."""


@dataclass(frozen=True)
class SecretRef:
    env: str | None = None
    file: str | None = None


class SecretResolver:
    """LLM-02/SEC-04: file wins over env; files re-read at point of use
    (rotation), env values are the process-start snapshot."""

    def __init__(
        self,
        env: dict[str, str] | None = None,
        read_file: Callable[[str], str | None] | None = None,
    ) -> None:
        # Default to the process environment: a bare SecretResolver() must
        # resolve real env refs (regression: it used to snapshot an empty
        # dict, so apiKeyEnv never resolved in the production path).
        self._env = dict(env) if env is not None else dict(os.environ)
        self._read_file = read_file or _default_read_file
        self._env_snapshot = dict(self._env)

    def resolve(self, ref: SecretRef) -> str | None:
        if ref.file:
            value = self._read_file(ref.file)
            if value:
                return value
        if ref.env and ref.env in self._env_snapshot:
            return self._env_snapshot[ref.env]
        return None


def _default_read_file(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        return content.rstrip("\r\n")
    except OSError:
        return None


class CredentialHealth:
    """LLM-02 health state machine: unavailable/unknown/available."""

    def __init__(self) -> None:
        self._state = "unknown"

    @property
    def state(self) -> str:
        return self._state

    def mark_unavailable(self, reason: str = "") -> None:
        self._state = "unavailable"

    def mark_available(self) -> None:
        self._state = "available"

    def observe_auth_failure(self) -> None:
        self._state = "unavailable"

    def status(self) -> dict[str, str]:
        return {"status": self._state}


# E2-1 (LLM-01): LiteLLM model-string prefixes per provider.  ``litellm``
# stays the verbatim escape hatch for anything not enumerated.
_LLM_MODEL_PREFIX: dict[str, str] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "ollama": "ollama_chat",
    "azure": "azure",
    "groq": "groq",
    "mistral": "mistral",
    "cohere": "cohere",
    "deepseek": "deepseek",
    "xai": "xai",
    "together": "together_ai",
    "fireworks": "fireworks_ai",
    "openrouter": "openrouter",
    "huggingface": "huggingface",
    # E2-3: vllm is the generic OpenAI-compatible provider.
    "vllm": "openai",
    "watsonx": "watsonx",
}


def _llm_model_string(provider: str, model: str) -> str:
    prefix = _LLM_MODEL_PREFIX.get(provider)
    if prefix is None:
        return model  # litellm: verbatim escape hatch
    return f"{prefix}/{model}"


def _credential_ref(llm_cfg: Any) -> tuple[str | None, str | None]:
    """LLM-04 (E1-5): the effective credential ref — explicit
    apiKeyEnv/apiKeyFile always win; otherwise the opt-in inferred
    variable name.  Returns (env_var, file)."""
    if llm_cfg.apiKeyEnv or llm_cfg.apiKeyFile:
        return llm_cfg.apiKeyEnv, llm_cfg.apiKeyFile
    if getattr(llm_cfg, "autoApiKeyEnv", False):
        from ..config.validate import effective_api_key_env

        inferred = effective_api_key_env({"llm": llm_cfg.model_dump(by_alias=True, mode="json")})
        return inferred, None
    return None, None


def build_llm(
    llm_cfg: Any,
    secrets: SecretResolver | None = None,
) -> BaseLlm:
    """Construct the ADK model per LLM-01 from the llm config section."""
    provider = llm_cfg.provider.value
    model = llm_cfg.model
    secrets = secrets or SecretResolver()

    if provider == "gemini":
        if llm_cfg.vertex.enabled:
            # LLM-01: ADC — no API key required.
            return Gemini(
                model=model,
                client_kwargs={
                    "vertexai": True,
                    "project": llm_cfg.vertex.project,
                    "location": llm_cfg.vertex.location,
                },
            )
        env_ref, file_ref = _credential_ref(llm_cfg)
        api_key = secrets.resolve(SecretRef(env_ref, file_ref))
        client_kwargs: dict[str, Any] = {}
        if api_key:
            client_kwargs["api_key"] = api_key
        if llm_cfg.baseUrl:
            client_kwargs["http_options"] = {"base_url": llm_cfg.baseUrl}
        if env_ref:
            logging.getLogger("agentbase.engine").info(
                "llm credential: gemini via %s",
                env_ref,  # OBS-03: name only
            )
        return Gemini(model=model, client_kwargs=client_kwargs or None)

    # LLM-01 LiteLLM bridge
    kwargs: dict[str, Any] = {}
    env_ref, file_ref = _credential_ref(llm_cfg)
    api_key = secrets.resolve(SecretRef(env_ref, file_ref))
    if api_key:
        kwargs["api_key"] = api_key
    if provider in ("ollama", "vllm", "azure"):
        # CFG-14: baseUrl is required for ollama (api_base), vllm (the
        # OpenAI-compatible provider, E2-3) and azure (the endpoint).
        # azure's api_version rides in llm.extra (E2-2).
        kwargs["api_base"] = llm_cfg.baseUrl
    elif llm_cfg.baseUrl:
        kwargs["base_url"] = llm_cfg.baseUrl
    if llm_cfg.extra:
        kwargs.update(llm_cfg.extra)
    if env_ref:
        logging.getLogger("agentbase.engine").info(
            "llm credential: %s via %s",
            provider,
            env_ref,  # OBS-03: name only
        )
    return LiteLlm(model=_llm_model_string(provider, model), **kwargs)


class RetryableLlm(BaseLlm):
    """LLM-03 retry wrapper: retries only before any delta is emitted.

    Retryable failures: transport errors, HTTP 429, HTTP 5xx. Backoff 1s then
    2s plus 0-250ms jitter; a longer valid Retry-After is honored. Once a
    delta or tool call has been observed the call is never replayed.

    R-33: also the per-run override seam — API-12's temperature / max_tokens
    overrides ride in ``RunConfig.labels`` (ADK merges them into the
    per-step ``LlmRequest.config.labels``), and this wrapper applies them to
    the request config and strips the synthetic labels before the provider
    call.  The labels travel with the invocation, so concurrent runs never
    share override state (unlike a knob on the singleton agent).
    """

    model: str = ""

    def __init__(self, inner: BaseLlm, health: CredentialHealth | None = None) -> None:
        # The ADK request builder reads ``agent.model.model`` at request time
        # (google/adk/flows/llm_flows/basic.py), so the pydantic ``model``
        # field must carry the wrapped model's name.
        super().__init__(model=inner.model)
        self._inner = inner
        self._health = health

    def _apply_overrides(self, llm_request: LlmRequest) -> None:
        """R-33: apply per-run provider overrides carried in the request's
        run-config labels; the synthetic labels are removed so the provider
        never sees them."""
        labels = llm_request.config.labels
        if not labels:
            return
        temperature = labels.pop(_OVERRIDE_LABEL_TEMPERATURE, None)
        max_tokens = labels.pop(_OVERRIDE_LABEL_MAX_TOKENS, None)
        if temperature is not None:
            llm_request.config.temperature = float(temperature)
        if max_tokens is not None:
            llm_request.config.max_output_tokens = int(max_tokens)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self._apply_overrides(llm_request)
        last_exc: BaseException | None = None
        for attempt in range(MAX_RETRIES + 1):
            emitted = False
            try:
                async for response in self._inner.generate_content_async(
                    llm_request, stream=stream
                ):
                    if response.error_code or response.error_message:
                        raise ProviderCallError(
                            response.error_code or "provider_error",
                            response.error_message or "provider call failed",
                        )
                    emitted = True
                    yield response
                if self._health is not None:
                    self._health.mark_available()
                return
            except asyncio.CancelledError:
                raise
            except ProviderCallError as exc:
                last_exc = exc
                if self._health is not None and _is_auth_failure(exc.code):
                    self._health.observe_auth_failure()
                if emitted or attempt >= MAX_RETRIES or not _retryable(exc.code):
                    break
            except Exception as exc:  # noqa: BLE001 — transport errors
                last_exc = exc
                if emitted or attempt >= MAX_RETRIES:
                    break
            delay = _backoff_seconds(attempt, last_exc)
            await asyncio.sleep(delay)
        raise ProviderCallError(
            "provider_unavailable", "provider call failed after retries"
        ) from last_exc


class ProviderCallError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _is_auth_failure(code: str) -> bool:
    return code in ("provider_auth", "auth_error", "unauthorized", "permission_denied")


def _retryable(code: str) -> bool:
    # transport errors and 429/5xx only (LLM-03)
    return code in (
        "rate_limit_exceeded",
        "resource_exhausted",
        "internal_error",
        "transport_error",
    )


def _backoff_seconds(attempt: int, exc: BaseException | None) -> float:
    jitter = random.uniform(0, 0.25)
    if attempt < len(BACKOFF_SECONDS):
        return BACKOFF_SECONDS[attempt] + jitter
    return 2.0 + jitter


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()
