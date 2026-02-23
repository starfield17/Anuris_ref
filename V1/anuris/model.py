import json
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

        if config.proxy and config.proxy.startswith("socks"):
            transport = SyncProxyTransport.from_url(config.proxy)
            http_client = httpx.Client(transport=transport)
            self.client = OpenAI(
                api_key=config.api_key,
                base_url=config.base_url,
                http_client=http_client,
                timeout=30.0,
            )
        else:
            self.client = OpenAI(
                api_key=config.api_key,
                base_url=config.base_url,
                timeout=30.0,
            )

        if self.debug:
            self._debug_print(
                f"Initialized ChatModel with model={config.model}, base_url={config.base_url}, proxy={config.proxy}"
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

            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=api_messages,
                temperature=self.config.temperature,
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
