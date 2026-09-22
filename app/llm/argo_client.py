"""Strict client for the active OpenAI-compatible chat-completions provider."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from time import monotonic, perf_counter
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings
from app.database.sqlite import Database
from app.llm.contracts import (
    GenerationMessage,
    GenerationMetrics,
    GenerationResponse,
)
from app.llm.providers import LlmProviderId, LlmProviderStore
from app.memory import MemoryGuard
from app.services.argo_quota import ArgoQuotaService
from app.telemetry import measured

_MODEL_VALIDATION_CACHE: dict[tuple[str, str, str], float] = {}
_MODEL_VALIDATION_CACHE_LOCK = threading.Lock()


def clear_model_validation_cache() -> None:
    """Invalidate safe process-local model validations after credential changes."""

    with _MODEL_VALIDATION_CACHE_LOCK:
        _MODEL_VALIDATION_CACHE.clear()


class ArgoError(RuntimeError):
    """Base error for INRAE ARGO operations."""


class ArgoUnavailableError(ArgoError):
    """Base error for retryable provider availability failures."""


class ArgoRequestTimeoutError(ArgoUnavailableError):
    """The provider request exceeded its configured timeout."""


class ArgoTransportError(ArgoUnavailableError):
    """The provider request failed before a usable HTTP response was received."""


class ArgoAuthenticationError(ArgoError):
    """The API key is absent or rejected."""


class ArgoAuthorizationError(ArgoError):
    """ARGO accepted the credential but denied the requested operation."""


class ArgoQuotaError(ArgoError):
    """ARGO rejected a request because the account quota was reached."""


class ArgoLocalQuotaError(ArgoQuotaError):
    def __init__(self, retry_at: datetime) -> None:
        self.retry_at = retry_at
        super().__init__(f"Local ARGO quota is reached until {retry_at.isoformat()}")


class ArgoProtocolError(ArgoError):
    """ARGO returned an unusable response."""


class ArgoGenerationError(ArgoError):
    """ARGO rejected or failed a generation request."""


class ScientificValidationReason(StrEnum):
    """Stable, non-sensitive cause codes for scientific generation failures."""

    EMPTY_ANSWERABLE_STATEMENTS = "empty_answerable_statements"
    UNSUPPORTED_EVALUATIVE_CLAIM = "unsupported_evaluative_claim"
    UNSUPPORTED_NUMERIC_CLAIM = "unsupported_numeric_claim"
    UNSUPPORTED_CAUSAL_CLAIM = "unsupported_causal_claim"
    UNSUPPORTED_SAFETY_CLAIM = "unsupported_safety_claim"
    UNSUPPORTED_NORMATIVE_CLAIM = "unsupported_normative_claim"
    INVALID_EVIDENCE_REFERENCE = "invalid_evidence_reference"
    MISSING_REQUIRED_EVIDENCE = "missing_required_evidence"
    UNJUSTIFIED_ABSTENTION = "unjustified_abstention"
    MISSING_CONTEXTUAL_INTRODUCTION = "missing_contextual_introduction"
    PARAGRAPH_TOO_SHORT = "paragraph_too_short"
    SYNTHESIS_TOO_SHORT = "synthesis_too_short"
    INVALID_RESPONSE_STYLE = "invalid_response_style"
    INVALID_PROSE_STRUCTURE = "invalid_prose_structure"
    EVIDENCE_ID_LEAK = "evidence_id_leak"
    UNSUPPORTED_EVIDENCE_GRADE = "unsupported_evidence_grade"
    MISSING_INDIRECT_EVIDENCE_LABEL = "missing_indirect_evidence_label"
    LANGUAGE_MISMATCH = "language_mismatch"
    INTERNAL_PROCESS_LEAK = "internal_process_leak"
    MISSING_DOCUMENTED_FACET = "missing_documented_facet"
    PROMPT_BUDGET_EXCEEDED = "prompt_budget_exceeded"
    INVALID_DIRECT_ANSWER_COUNT = "invalid_direct_answer_count"
    INVALID_SCHEMA = "invalid_schema"
    QUESTION_INTEGRITY = "question_integrity"
    UNUSABLE_OUTPUT = "unusable_output"
    UNKNOWN = "unknown"


def classify_scientific_validation_failure(message: str) -> ScientificValidationReason:
    normalized = " ".join(message.casefold().split())
    rules = (
        (
            "answerable response requires cited statements",
            ScientificValidationReason.EMPTY_ANSWERABLE_STATEMENTS,
        ),
        ("unsupported evaluative", ScientificValidationReason.UNSUPPORTED_EVALUATIVE_CLAIM),
        ("unsupported numeric", ScientificValidationReason.UNSUPPORTED_NUMERIC_CLAIM),
        ("numeric value", ScientificValidationReason.UNSUPPORTED_NUMERIC_CLAIM),
        ("causal language", ScientificValidationReason.UNSUPPORTED_CAUSAL_CLAIM),
        ("unsupported safety", ScientificValidationReason.UNSUPPORTED_SAFETY_CLAIM),
        ("unsupported norm", ScientificValidationReason.UNSUPPORTED_NORMATIVE_CLAIM),
        ("outside the supplied", ScientificValidationReason.INVALID_EVIDENCE_REFERENCE),
        ("omitted one or more", ScientificValidationReason.MISSING_REQUIRED_EVIDENCE),
        ("abstained despite", ScientificValidationReason.UNJUSTIFIED_ABSTENTION),
        ("contextual introduction", ScientificValidationReason.MISSING_CONTEXTUAL_INTRODUCTION),
        ("paragraph that is too short", ScientificValidationReason.PARAGRAPH_TOO_SHORT),
        ("synthesis is too short", ScientificValidationReason.SYNTHESIS_TOO_SHORT),
        ("response style", ScientificValidationReason.INVALID_RESPONSE_STYLE),
        ("list marker", ScientificValidationReason.INVALID_PROSE_STRUCTURE),
        ("heading or fragment", ScientificValidationReason.INVALID_PROSE_STRUCTURE),
        ("leaked an evidence id", ScientificValidationReason.EVIDENCE_ID_LEAK),
        (
            "peripheral or irrelevant evidence",
            ScientificValidationReason.UNSUPPORTED_EVIDENCE_GRADE,
        ),
        (
            "label indirect evidence",
            ScientificValidationReason.MISSING_INDIRECT_EVIDENCE_LABEL,
        ),
        ("answer language", ScientificValidationReason.LANGUAGE_MISMATCH),
        ("internal generation or retrieval", ScientificValidationReason.INTERNAL_PROCESS_LEAK),
        ("documented facet", ScientificValidationReason.MISSING_DOCUMENTED_FACET),
        ("prompt content exceeds", ScientificValidationReason.PROMPT_BUDGET_EXCEEDED),
        ("prompt has no evidence", ScientificValidationReason.PROMPT_BUDGET_EXCEEDED),
        ("direct-answer statements", ScientificValidationReason.INVALID_DIRECT_ANSWER_COUNT),
        ("validation error", ScientificValidationReason.INVALID_SCHEMA),
        ("invalid faceted evidence answer", ScientificValidationReason.INVALID_SCHEMA),
        ("question integrity", ScientificValidationReason.QUESTION_INTEGRITY),
        ("did not return a usable", ScientificValidationReason.UNUSABLE_OUTPUT),
    )
    return next(
        (reason for marker, reason in rules if marker in normalized),
        ScientificValidationReason.UNKNOWN,
    )


class ArgoScientificValidationError(ArgoError):
    """ARGO exhausted correction attempts without producing grounded scientific output."""

    def __init__(
        self,
        message: str,
        *,
        reason: ScientificValidationReason | None = None,
        reasons: Sequence[ScientificValidationReason] = (),
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        generation_traces: Sequence[object] = (),
    ) -> None:
        explicit_reasons = [*([] if reason is None else [reason]), *reasons]
        self.reasons = tuple(
            dict.fromkeys(explicit_reasons or [classify_scientific_validation_failure(message)])
        )
        self.reason = self.reasons[0]
        self.prompt_tokens = max(0, int(prompt_tokens))
        self.completion_tokens = max(0, int(completion_tokens))
        self.generation_traces = tuple(generation_traces)
        super().__init__(message)


class ArgoHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reachable: bool
    provider: str = "argo"
    base_url: str
    configured_model: str
    model_available: bool
    available_models: list[str]
    api_key_configured: bool
    error: str | None = None


class _ArgoModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)


class _ArgoModelList(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: list[_ArgoModel]


class _ArgoMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: str
    content: str | None = None
    reasoning_content: str | None = None


class _ArgoChoice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: _ArgoMessage
    finish_reason: str | None = None


class _ArgoUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)


class _ArgoChatResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str
    choices: list[_ArgoChoice] = Field(min_length=1)
    usage: _ArgoUsage = Field(default_factory=_ArgoUsage)


class OpenAICompatibleClient:
    """Synchronous bounded client resolved from the current-user active profile."""

    def __init__(
        self,
        settings: Settings,
        *,
        api_key: str | None = None,
        provider_id: LlmProviderId | None = None,
        transport: httpx.BaseTransport | None = None,
        quota_service: ArgoQuotaService | None = None,
        request_timeout_seconds: float | None = None,
    ) -> None:
        self.settings = settings
        self.config = settings.argo
        provider_store = LlmProviderStore(settings)
        self.profile = provider_store.profile(provider_id or provider_store.active_provider())
        self.provider = self.profile.id
        self.provider_label = "ARGO" if self.provider == "argo" else self.profile.label
        self.base_url = self.profile.base_url
        self.model = self.profile.model
        self.memory = MemoryGuard(settings.memory)
        self._quota_service = (
            quota_service or ArgoQuotaService(Database(settings.paths.database_path))
            if self.provider == "argo"
            else None
        )
        if api_key is None:
            api_key = provider_store.api_key(self.provider)
        cleaned_api_key = (api_key or "").strip()
        self._api_key_configured = bool(cleaned_api_key)
        self._credential_fingerprint = sha256(cleaned_api_key.encode("utf-8")).hexdigest()
        headers = {"Accept": "application/json"}
        if cleaned_api_key:
            headers["Authorization"] = f"Bearer {cleaned_api_key}"
        timeout_seconds = min(
            float(self.config.request_timeout_seconds),
            float(request_timeout_seconds or self.config.request_timeout_seconds),
        )
        if timeout_seconds <= 0:
            raise ValueError("LLM request timeout must be positive")
        self._http = httpx.Client(
            base_url=f"{self.base_url}/",
            timeout=httpx.Timeout(
                timeout_seconds,
                connect=min(10.0, timeout_seconds),
            ),
            follow_redirects=False,
            trust_env=False,
            verify=self.config.verify_tls,
            transport=transport,
            headers=headers,
        )
        del cleaned_api_key
        self._generation_lock = threading.Lock()
        self._verified_models: set[str] = set()
        self._closed = False

    @staticmethod
    def _selected_model(model: str) -> str:
        cleaned = model.strip()
        if not cleaned or any(character.isspace() for character in cleaned):
            raise ValueError("LLM model name is invalid")
        return cleaned

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        on_reserved: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("LLM client is closed")
        if not self._api_key_configured:
            if self.provider == "argo":
                raise ArgoAuthenticationError(
                    f"ARGO API key is missing from environment variable {self.config.api_key_env}"
                )
            raise ArgoAuthenticationError("Custom LLM API key is missing")
        if self._quota_service is not None:
            reservation = self._quota_service.reserve(path)
            if not reservation.allowed:
                raise ArgoLocalQuotaError(reservation.next_allowed_at)
        if on_reserved is not None:
            on_reserved()
        try:
            response = self._http.request(method, path, json=json_body)
        except httpx.TimeoutException as exc:
            raise ArgoRequestTimeoutError(f"{self.provider_label} request timed out") from exc
        except httpx.HTTPError as exc:
            raise ArgoTransportError(f"{self.provider_label} service is unavailable") from exc
        if response.is_redirect:
            raise ArgoProtocolError(f"{self.provider_label} redirects are forbidden")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ArgoProtocolError(
                f"{self.provider_label} returned non-JSON data with HTTP {response.status_code}"
            ) from exc
        if not isinstance(payload, dict):
            raise ArgoProtocolError(f"{self.provider_label} returned a non-object JSON response")
        if response.status_code == 401:
            raise ArgoAuthenticationError(f"{self.provider_label} rejected the configured API key")
        if response.status_code == 403:
            raise ArgoAuthorizationError(
                f"{self.provider_label} denied access to the requested model or operation"
            )
        if response.status_code == 429:
            raise ArgoQuotaError(f"{self.provider_label} request quota has been reached")
        if response.is_error:
            raw_detail = payload.get("detail") or payload.get("error")
            detail = "request rejected" if raw_detail is None else str(raw_detail)[:500]
            raise ArgoGenerationError(
                f"{self.provider_label} request failed with HTTP {response.status_code}: {detail}"
            )
        return payload

    def list_models(self) -> list[str]:
        payload = self._request("GET", "models")
        try:
            result = _ArgoModelList.model_validate(payload)
        except ValidationError as exc:
            raise ArgoProtocolError(
                f"{self.provider_label} returned invalid model metadata"
            ) from exc
        return sorted({model.id for model in result.data})

    def health(self, *, model: str | None = None) -> ArgoHealth:
        selected_model = self._selected_model(model or self.model)
        try:
            models = self.list_models()
        except ArgoAuthorizationError as exc:
            return ArgoHealth(
                reachable=True,
                provider=self.provider,
                base_url=self.base_url,
                configured_model=selected_model,
                model_available=False,
                available_models=[],
                api_key_configured=self._api_key_configured,
                error=str(exc),
            )
        except ArgoError as exc:
            return ArgoHealth(
                reachable=False,
                provider=self.provider,
                base_url=self.base_url,
                configured_model=selected_model,
                model_available=False,
                available_models=[],
                api_key_configured=self._api_key_configured,
                error=str(exc),
            )
        return ArgoHealth(
            reachable=True,
            provider=self.provider,
            base_url=self.base_url,
            configured_model=selected_model,
            model_available=selected_model in models,
            available_models=models,
            api_key_configured=True,
            error=None,
        )

    def ensure_model(self, model: str | None = None) -> str:
        selected_model = self._selected_model(model or self.model)
        cache_key = (
            self.base_url,
            selected_model,
            self._credential_fingerprint,
        )
        now = monotonic()
        with _MODEL_VALIDATION_CACHE_LOCK:
            expires_at = _MODEL_VALIDATION_CACHE.get(cache_key, 0.0)
            if expires_at > now:
                self._verified_models.add(selected_model)
                return selected_model
        if selected_model not in self.list_models():
            raise ArgoGenerationError(
                f"{self.provider_label} model is unavailable for this account: {selected_model}"
            )
        with _MODEL_VALIDATION_CACHE_LOCK:
            expired_keys = [
                key for key, expiration in _MODEL_VALIDATION_CACHE.items() if expiration <= now
            ]
            for key in expired_keys:
                del _MODEL_VALIDATION_CACHE[key]
            _MODEL_VALIDATION_CACHE[cache_key] = now + self.config.model_validation_ttl_seconds
        self._verified_models.add(selected_model)
        return selected_model

    def _messages(
        self, messages: Sequence[GenerationMessage | Mapping[str, str]]
    ) -> list[GenerationMessage]:
        try:
            validated = [GenerationMessage.model_validate(message) for message in messages]
        except ValidationError as exc:
            raise ValueError("invalid LLM chat message") from exc
        if not validated:
            raise ValueError("at least one LLM message is required")
        character_count = sum(len(message.content) for message in validated)
        if character_count > self.config.max_input_characters:
            raise ValueError(
                "LLM input exceeds the configured character limit "
                f"({character_count} > {self.config.max_input_characters})"
            )
        return validated

    @measured("llm_request")
    def chat(
        self,
        messages: Sequence[GenerationMessage | Mapping[str, str]],
        *,
        model: str | None = None,
        json_schema: Mapping[str, Any] | None = None,
        temperature: float | None = None,
        num_ctx: int | None = None,
        max_output_tokens: int | None = None,
        on_request_reserved: Callable[[], None] | None = None,
    ) -> GenerationResponse:
        del num_ctx  # ARGO controls the server-side context window.
        selected_model = self._selected_model(model or self.model)
        validated_messages = self._messages(messages)
        selected_temperature = self.config.temperature if temperature is None else temperature
        selected_output_tokens = (
            self.config.max_output_tokens if max_output_tokens is None else max_output_tokens
        )
        if not 0.0 <= selected_temperature <= 2.0:
            raise ValueError("LLM temperature must be between 0 and 2")
        if not 1 <= selected_output_tokens <= self.config.max_output_tokens:
            raise ValueError("LLM output token limit exceeds configuration")
        if json_schema is not None and not json_schema:
            raise ValueError("LLM JSON schema cannot be empty")

        request_body: dict[str, Any] = {
            "model": selected_model,
            "messages": [message.model_dump(mode="json") for message in validated_messages],
            "stream": False,
            "temperature": selected_temperature,
            "max_tokens": selected_output_tokens,
        }
        if json_schema is not None:
            request_body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "local_science_rag_response",
                    "strict": True,
                    "schema": dict(json_schema),
                },
            }

        with self._generation_lock:
            self.memory.check(f"{self.provider_label} request")
            if selected_model not in self._verified_models:
                self.ensure_model(selected_model)
            started = perf_counter()
            payload = self._request(
                "POST",
                "chat/completions",
                json_body=request_body,
                on_reserved=on_request_reserved,
            )
            duration = perf_counter() - started
            try:
                raw = _ArgoChatResponse.model_validate(payload)
            except ValidationError as exc:
                raise ArgoProtocolError(
                    f"{self.provider_label} returned an invalid chat response"
                ) from exc
            choice = raw.choices[0]
            content = choice.message.content
            if raw.model != selected_model:
                raise ArgoProtocolError(
                    f"{self.provider_label} answered with unexpected model: {raw.model}"
                )
            if not isinstance(content, str) or not content.strip():
                reason = choice.finish_reason or "unknown"
                raise ArgoProtocolError(
                    f"{self.provider_label} returned no answer content (finish_reason={reason})"
                )
            return GenerationResponse(
                model=raw.model,
                content=content,
                done_reason=choice.finish_reason,
                metrics=GenerationMetrics(
                    total_duration_seconds=duration,
                    load_duration_seconds=0.0,
                    prompt_eval_count=raw.usage.prompt_tokens,
                    prompt_eval_duration_seconds=0.0,
                    eval_count=raw.usage.completion_tokens,
                    eval_duration_seconds=duration,
                ),
            )

    def close(self) -> None:
        if self._closed:
            return
        self._http.close()
        self._api_key_configured = False
        self._closed = True

    def __enter__(self) -> OpenAICompatibleClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# Compatibility aliases keep existing integrations and tests stable while all new
# runtime clients resolve the selected provider.
ArgoClient = OpenAICompatibleClient
LlmHealth = ArgoHealth
LlmError = ArgoError
LlmUnavailableError = ArgoUnavailableError
LlmRequestTimeoutError = ArgoRequestTimeoutError
LlmTransportError = ArgoTransportError
LlmAuthenticationError = ArgoAuthenticationError
LlmAuthorizationError = ArgoAuthorizationError
LlmQuotaError = ArgoQuotaError
LlmProtocolError = ArgoProtocolError
LlmGenerationError = ArgoGenerationError
