import json
from urllib.parse import urlsplit, urlunsplit
from typing import Any, Dict, List, Optional

import httpx
from httpx_socks import SyncProxyTransport
from openai import OpenAI

from .config import Config


class ChatModel:
    """API interaction layer with state-focused design."""

    def __init__(self, config: Config):
        self.config = config
        self.debug = config.debug
        self.base_url = self._normalize_base_url(config.base_url)

        if config.proxy and config.proxy.startswith("socks"):
            transport = SyncProxyTransport.from_url(config.proxy)
            http_client = httpx.Client(transport=transport)
            self.client = OpenAI(
                api_key=config.api_key,
                base_url=self.base_url,
                http_client=http_client,
                timeout=30.0,
            )
        else:
            self.client = OpenAI(
                api_key=config.api_key,
                base_url=self.base_url,
                timeout=30.0,
            )

        if self.debug:
            self._debug_print(
                "Initialized ChatModel with "
                f"model={config.model}, base_url={self.base_url}, proxy={config.proxy}, reasoning={config.reasoning}"
            )

    def _debug_print(self, message: str) -> None:
        """Print debug message if debug mode is enabled."""
        if self.debug:
            print(f"\nDebug - {message}")

    def get_response(
        self,
        messages: List[Dict],
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Any:
        """Get streaming response from API with attachment support."""
        try:
            if not messages or not isinstance(messages, list):
                raise ValueError("Invalid messages format")

            api_messages = messages.copy()
            if attachments and api_messages and api_messages[-1]["role"] == "user":
                content = [{"type": "text", "text": api_messages[-1]["content"]}]
                content.extend(attachments)
                api_messages[-1]["content"] = content

            if self.debug:
                self._debug_print(f"Sending request with messages: {json.dumps(api_messages[-2:], indent=2)}")

            response = self.create_completion(
                messages=api_messages,
                stream=True,
            )

            if not response:
                raise Exception("Empty response from API")

            if self.debug:
                self._debug_print("Response received successfully")

            return response

        except httpx.TimeoutException as exc:
            self._debug_print("Timeout Exception occurred")
            raise Exception("Request timed out - API server not responding") from exc

        except httpx.ConnectError as exc:
            self._debug_print("Connection Error occurred")
            raise Exception("Connection failed - Please check your internet connection") from exc

        except Exception as exc:
            self._debug_print(f"Exception occurred: {type(exc).__name__}: {str(exc)}")
            raise Exception(f"API Error ({type(exc).__name__}): {str(exc)}") from exc

    def create_completion(
        self,
        messages: List[Dict[str, Any]],
        stream: bool,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
    ) -> Any:
        """Create a chat completion with provider-specific reasoning toggle support."""
        request_kwargs: Dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "stream": stream,
        }
        if tools is not None:
            request_kwargs["tools"] = tools
        if tool_choice is not None:
            request_kwargs["tool_choice"] = tool_choice

        extra_body = self._build_reasoning_extra_body()
        if extra_body:
            request_kwargs["extra_body"] = extra_body

        return self._create_with_fallback(request_kwargs)

    def _build_reasoning_extra_body(self) -> Optional[Dict[str, Any]]:
        """
        Build provider-specific payload for reasoning mode.
        DeepSeek expects `thinking.type = enabled|disabled`.
        """
        if self._detect_provider() == "deepseek":
            thinking_type = "enabled" if self.config.reasoning else "disabled"
            return {"thinking": {"type": thinking_type}}
        return None

    def _supports_reasoning_switch(self) -> bool:
        return self._detect_provider() == "deepseek"

    def _detect_provider(self) -> str:
        base_url = (self.base_url or self.config.base_url or "").lower()
        model_name = (self.config.model or "").lower()
        if "openrouter" in base_url:
            return "openrouter"
        if "api.openai.com" in base_url or "openai.com" in base_url:
            return "openai"
        if "deepseek" in base_url or "deepseek" in model_name:
            return "deepseek"
        if "anthropic" in base_url:
            return "anthropic"
        return "generic"

    @staticmethod
    def _normalize_base_url(raw_base_url: str) -> str:
        """
        Normalize OpenAI-compatible base URL.

        Some OpenAI-compatible providers return 404 when `/v1` is omitted.
        """
        base_url = (raw_base_url or "").strip()
        if not base_url:
            return base_url

        parsed = urlsplit(base_url)
        path = (parsed.path or "").rstrip("/")
        if path in {"", "/"}:
            path = "/v1"
        normalized = urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))
        return normalized.rstrip("/")

    def _create_with_fallback(self, request_kwargs: Dict[str, Any]) -> Any:
        """
        Run chat completion and apply payload-shape fallback on retriable request errors.
        This is provider-agnostic and avoids hardcoding vendor-specific error codes.
        """
        active_kwargs = dict(request_kwargs)

        try:
            return self.client.chat.completions.create(**active_kwargs)
        except Exception as exc:
            while self._is_retriable_request_shape_error(exc):
                next_kwargs, label = self._build_retry_kwargs(active_kwargs)
                if next_kwargs is None:
                    raise
                active_kwargs = next_kwargs
                if self.debug:
                    self._debug_print(f"Retrying with reduced payload ({label})")
                try:
                    return self.client.chat.completions.create(**active_kwargs)
                except Exception as retry_exc:
                    exc = retry_exc
            raise

    def _build_retry_kwargs(self, request_kwargs: Dict[str, Any]) -> tuple[Optional[Dict[str, Any]], str]:
        for field in ("extra_body", "tools+tool_choice", "temperature"):
            candidate = dict(request_kwargs)
            changed = False
            if field == "extra_body" and "extra_body" in candidate:
                candidate.pop("extra_body", None)
                changed = True
            elif field == "tools+tool_choice" and (
                "tools" in candidate or "tool_choice" in candidate
            ):
                candidate.pop("tools", None)
                candidate.pop("tool_choice", None)
                changed = True
            elif field == "temperature" and "temperature" in candidate:
                candidate.pop("temperature", None)
                changed = True
            if changed:
                return candidate, field
        return None, ""

    def _is_retriable_request_shape_error(self, exc: Exception) -> bool:
        status_code = self._extract_status_code(exc)
        text = self._extract_error_text(exc).lower()

        if any(token in text for token in ("api key", "unauthorized", "forbidden", "quota", "rate limit")):
            return False

        indicators = (
            "invalid",
            "unsupported",
            "unknown",
            "unrecognized",
            "unexpected",
            "not allowed",
            "bad request",
            "parameter",
            "params",
            "setting",
            "schema",
            "tool",
            "temperature",
            "extra_body",
        )
        has_shape_hint = any(token in text for token in indicators)
        if status_code is None:
            return has_shape_hint
        return status_code in {400, 415, 422} and has_shape_hint

    @staticmethod
    def _extract_status_code(exc: Exception) -> Optional[int]:
        for attr in ("status_code", "status"):
            value = getattr(exc, attr, None)
            if isinstance(value, int):
                return value
        response = getattr(exc, "response", None)
        value = getattr(response, "status_code", None)
        if isinstance(value, int):
            return value
        return None

    @staticmethod
    def _extract_error_text(exc: Exception) -> str:
        parts = [str(exc)]
        body = getattr(exc, "body", None)
        if body is not None:
            try:
                parts.append(json.dumps(body, ensure_ascii=False))
            except Exception:
                parts.append(str(body))
        response = getattr(exc, "response", None)
        text = getattr(response, "text", None)
        if isinstance(text, str) and text:
            parts.append(text)
        return "\n".join(parts)
