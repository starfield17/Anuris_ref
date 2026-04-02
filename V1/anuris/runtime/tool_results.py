from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


DEFAULT_INLINE_LIMIT = 4000
DEFAULT_PREVIEW_CHARS = 1200
PERSIST_POLICY_DEFAULT = "persist"
PERSIST_POLICY_NEVER = "never_persist"


@dataclass(frozen=True)
class PersistedToolResult:
    content_for_model: str
    content_for_display: str
    metadata: Dict[str, Any]


class ToolResultStore:
    """Persist oversized tool results and replace them with compact references."""

    def __init__(
        self,
        root: Path,
        *,
        inline_limit: int = DEFAULT_INLINE_LIMIT,
        preview_chars: int = DEFAULT_PREVIEW_CHARS,
    ):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.inline_limit = int(inline_limit)
        self.preview_chars = int(preview_chars)

    def prepare(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        model_content: str,
        display_content: str | None = None,
        persist_policy: str = PERSIST_POLICY_DEFAULT,
    ) -> PersistedToolResult:
        display = display_content if display_content is not None else model_content
        if persist_policy == PERSIST_POLICY_NEVER:
            return self._inline_result(tool_name, model_content, display, persist_policy)
        if len(model_content) <= self.inline_limit and len(display) <= self.inline_limit:
            return self._inline_result(tool_name, model_content, display, persist_policy)
        safe_name = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in tool_name)
        path = self.root / f"{tool_call_id}_{safe_name}.txt"
        payload = model_content if len(model_content) >= len(display) else display
        path.write_text(payload, encoding="utf-8")
        preview = payload[: self.preview_chars]
        if len(payload) > self.preview_chars:
            preview += "\n...[stored externally]"
        message = (
            f"Tool output stored externally at {path}.\n"
            f"Preview:\n{preview}"
        )
        metadata = {
            "stored_externally": True,
            "artifact_path": str(path),
            "preview": preview,
            "size_bytes": len(payload.encode("utf-8")),
            "tool_name": tool_name,
            "persistence_policy": persist_policy,
        }
        return PersistedToolResult(
            content_for_model=message,
            content_for_display=message,
            metadata=metadata,
        )

    def _inline_result(
        self,
        tool_name: str,
        model_content: str,
        display: str,
        persist_policy: str,
    ) -> PersistedToolResult:
        return PersistedToolResult(
            content_for_model=model_content,
            content_for_display=display,
            metadata={
                "stored_externally": False,
                "size_bytes": len(model_content.encode("utf-8")),
                "tool_name": tool_name,
                "persistence_policy": persist_policy,
            },
        )

    def read_artifact(self, artifact_path: str) -> str:
        return Path(artifact_path).read_text(encoding="utf-8")
