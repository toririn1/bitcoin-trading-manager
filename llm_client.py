from __future__ import annotations

import json
import importlib
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

import config


JSONExtractor = Callable[[str], dict]


@dataclass
class LLMResult:
    raw_text: str
    raw_response: Any
    analysis_json: dict | None = None
    structured_output_used: bool = False


class LLMClientError(RuntimeError):
    pass


def _provider() -> str:
    provider = (config.LLM_PROVIDER or "openai_compatible").lower().strip()
    if provider in ("openai-compatible", "openai_compatible", "compatible"):
        return "openai_compatible"
    if provider in ("openai", "anthropic", "openai_oauth"):
        return provider
    raise LLMClientError(f"지원하지 않는 LLM_PROVIDER입니다: {config.LLM_PROVIDER}")


def _is_openai_official(provider: str, base_url: str) -> bool:
    if provider == "openai_oauth":
        return False
    return provider == "openai" or base_url.rstrip("/").lower() == "https://api.openai.com/v1"


def _uses_max_completion_tokens(model: str) -> bool:
    m = (model or "").lower()
    return m.startswith(("o1", "o3", "o4", "gpt-5"))


def _supports_temperature(model: str) -> bool:
    m = (model or "").lower()
    return not m.startswith(("o1", "o3", "o4", "gpt-5"))


def _backoff_sleep(attempt: int) -> None:
    time.sleep(min(10 * (2 ** attempt), 80))


def _compact_schema(tool_schema: dict) -> dict:
    schema = dict(tool_schema.get("input_schema") or {})
    schema.setdefault("type", "object")
    return schema


def _build_openai_payload(
    *,
    system_prompt: str,
    user_prompt: str,
    schema: dict,
    use_response_format: bool,
    force_json_prompt: bool = False,
    max_token_param: str | None = None,
    include_temperature: bool = True,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> dict:
    model = config.LLM_MODEL
    role = "developer" if _is_openai_official(_provider(), config.LLM_BASE_URL) else "system"
    prompt = user_prompt
    if force_json_prompt:
        prompt = (
            user_prompt
            + "\n\n반드시 응답 맨 앞에 <analysis_json>...</analysis_json> JSON 블록을 넣고, "
            + "그 뒤에 지정된 한국어 리포트 섹션을 이어서 작성하세요. JSON은 유효한 객체여야 합니다."
        )

    token_param = max_token_param or (
        "max_completion_tokens" if _uses_max_completion_tokens(model) else "max_tokens"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": role, "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        token_param: max_tokens if max_tokens is not None else config.LLM_MAX_TOKENS,
    }
    if include_temperature and _supports_temperature(model):
        payload["temperature"] = config.LLM_TEMPERATURE if temperature is None else temperature
    if use_response_format:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "record_analysis",
                "strict": True,
                "schema": _compact_schema(schema),
            },
        }
    return payload


def _post_chat_completions(payload: dict) -> dict:
    url = f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if _provider() != "openai_oauth":
        headers["Authorization"] = f"Bearer {config.LLM_API_KEY}"
    last_exc: Exception | None = None
    for attempt in range(4):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=config.LLM_TIMEOUT_SECS)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                _backoff_sleep(attempt)
                continue
            if resp.status_code >= 400:
                raise LLMClientError(f"LLM HTTP {resp.status_code}: {resp.text[:500]}")
            return resp.json()
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < 3:
                _backoff_sleep(attempt)
                continue
            raise LLMClientError(f"LLM 요청 실패: {exc}") from exc
    raise LLMClientError(f"LLM 요청 실패: {last_exc}")


def _message_text_from_chat_response(data: dict) -> str:
    try:
        content = data["choices"][0]["message"].get("content") or ""
    except Exception as exc:
        raise LLMClientError(f"Chat Completions 응답 형식 오류: {data!r}") from exc
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return str(content)


def _call_openai_compatible(
    *,
    system_prompt: str,
    user_prompt: str,
    schema: dict,
    extract_json: JSONExtractor,
) -> LLMResult:
    provider = _provider()
    official = _is_openai_official(provider, config.LLM_BASE_URL)
    use_response_format = official
    force_json_prompt = not official
    max_token_param = None
    include_temperature = True
    last_error: Exception | None = None

    for fallback_round in range(3):
        payload = _build_openai_payload(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            use_response_format=use_response_format,
            force_json_prompt=force_json_prompt,
            max_token_param=max_token_param,
            include_temperature=include_temperature,
        )
        try:
            data = _post_chat_completions(payload)
        except LLMClientError as exc:
            msg = str(exc).lower()
            last_error = exc
            if use_response_format and "response_format" in msg:
                use_response_format = False
                force_json_prompt = True
                continue
            if "max_completion_tokens" in msg:
                max_token_param = "max_tokens"
                continue
            if "max_tokens" in msg:
                max_token_param = "max_completion_tokens"
                continue
            if "temperature" in msg:
                include_temperature = False
                continue
            raise

        raw_text = _message_text_from_chat_response(data)
        if use_response_format:
            try:
                parsed = json.loads(raw_text)
                if isinstance(parsed, dict):
                    return LLMResult(raw_text=raw_text, raw_response=data, analysis_json=parsed, structured_output_used=True)
            except Exception as exc:
                last_error = exc
                use_response_format = False
                force_json_prompt = True
                continue

        parsed = extract_json(raw_text)
        if parsed:
            return LLMResult(raw_text=raw_text, raw_response=data, analysis_json=parsed, structured_output_used=False)
        force_json_prompt = True
        last_error = LLMClientError("LLM 응답에서 analysis_json을 찾지 못했습니다.")

    raise LLMClientError(f"구조화 분석 JSON 생성 실패: {last_error}")


def _system_prompt_param(system_prompt: str, prompt_cache_enabled: bool):
    if not prompt_cache_enabled:
        return system_prompt
    return [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]


def _call_anthropic(
    *,
    system_prompt: str,
    user_prompt: str,
    schema: dict,
    prompt_cache_enabled: bool,
) -> LLMResult:
    anthropic_mod = importlib.import_module("anthropic")

    client = getattr(anthropic_mod, "Anthropic")(api_key=config.ANTHROPIC_API_KEY)
    request_kwargs = {
        "model": config.ANTHROPIC_MODEL,
        "max_tokens": config.LLM_MAX_TOKENS,
        "system": _system_prompt_param(system_prompt, prompt_cache_enabled),
        "messages": [{"role": "user", "content": user_prompt}],
        "tools": [schema],
    }
    message = None
    for attempt in range(4):
        try:
            message = client.messages.create(**request_kwargs)
            break
        except getattr(anthropic_mod, "APIStatusError") as exc:
            if exc.status_code == 400 and request_kwargs.get("system") != system_prompt:
                request_kwargs["system"] = system_prompt
                continue
            if exc.status_code in (429, 529, 500, 502, 503, 504) and attempt < 3:
                _backoff_sleep(attempt)
                continue
            raise

    if message is None or not hasattr(message, "content") or not isinstance(message.content, list):
        raise LLMClientError(f"Anthropic 응답 형식 오류: {message!r}")

    raw_text = next((b.text for b in message.content if getattr(b, "type", None) == "text"), "")
    tool_json = None
    for block in message.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "record_analysis":
            inp = getattr(block, "input", None)
            if isinstance(inp, dict):
                tool_json = inp
                break
    return LLMResult(
        raw_text=raw_text,
        raw_response=message,
        analysis_json=tool_json,
        structured_output_used=tool_json is not None,
    )


def call_text_llm(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> str:
    provider = _provider()
    max_tokens = max_tokens if max_tokens is not None else config.LLM_MAX_TOKENS

    if provider == "anthropic":
        if not config.ANTHROPIC_API_KEY:
            raise LLMClientError("LLM 설정 미완료")
        anthropic_mod = importlib.import_module("anthropic")

        client = getattr(anthropic_mod, "Anthropic")(api_key=config.ANTHROPIC_API_KEY)
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                msg = client.messages.create(
                    model=config.ANTHROPIC_MODEL,
                    max_tokens=max_tokens,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                if not hasattr(msg, "content") or not isinstance(msg.content, list):
                    raise LLMClientError(f"Anthropic 응답 형식 오류: {msg!r}")
                return next(
                    (b.text for b in msg.content if getattr(b, "type", None) == "text"),
                    "",
                ).strip()
            except getattr(anthropic_mod, "APIStatusError") as exc:
                last_exc = exc
                if exc.status_code in (429, 529, 500, 502, 503, 504) and attempt < 2:
                    _backoff_sleep(attempt)
                    continue
                raise
        raise LLMClientError(f"LLM 요청 실패: {last_exc}")

    if provider != "openai_oauth" and not config.LLM_API_KEY:
        raise LLMClientError("LLM 설정 미완료")

    max_token_param = None
    include_temperature = True
    last_error: Exception | None = None
    for _ in range(3):
        payload = _build_openai_payload(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema={"input_schema": {"type": "object"}},
            use_response_format=False,
            force_json_prompt=False,
            max_token_param=max_token_param,
            include_temperature=include_temperature,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        try:
            data = _post_chat_completions(payload)
            return _message_text_from_chat_response(data).strip()
        except LLMClientError as exc:
            msg = str(exc).lower()
            last_error = exc
            if "max_completion_tokens" in msg:
                max_token_param = "max_tokens"
                continue
            if "max_tokens" in msg:
                max_token_param = "max_completion_tokens"
                continue
            if "temperature" in msg:
                include_temperature = False
                continue
            raise
    raise LLMClientError(f"LLM 요청 실패: {last_error}")


def call_analysis_llm(
    *,
    system_prompt: str,
    user_prompt: str,
    schema: dict,
    extract_json: JSONExtractor,
    prompt_cache_enabled: bool = True,
) -> LLMResult:
    provider = _provider()
    if provider == "anthropic":
        if not config.ANTHROPIC_API_KEY:
            raise LLMClientError("Anthropic API 키가 없습니다. ANTHROPIC_API_KEY를 설정하세요.")
        return _call_anthropic(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            prompt_cache_enabled=prompt_cache_enabled,
        )
    if provider != "openai_oauth" and not config.LLM_API_KEY:
        raise LLMClientError("LLM API 키가 없습니다. LLM_API_KEY를 설정하세요.")
    return _call_openai_compatible(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema=schema,
        extract_json=extract_json,
    )
