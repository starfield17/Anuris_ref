from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .messages import ConversationMessage, extract_text_content

MAX_TRACKED_FILE_READS = 12
MAX_TRACKED_TOOL_RESULTS = 8
MAX_CONTEXT_SUMMARY_ITEMS = 4
MAX_RESTORED_FILE_CONTEXTS = 3
MAX_RESTORED_TOOL_CONTEXTS = 2
MAX_RESTORED_FILE_CHARS = 6000
MAX_RESTORED_TOOL_CHARS = 1600
MAX_PREVIEW_CHARS = 320
STORED_TOOL_RESULT_PREFIX = "[Stored tool result retained at "


@dataclass(frozen=True)
class FileContextEntry:
    path: str
    start_line: int
    end_line: int
    mtime_ns: int
    content_hash: str
    context_generation: int
    source: str = "read_file"
    restored_after_compact: bool = False

    @property
    def key(self) -> str:
        return file_context_key(self.path, self.start_line, self.end_line)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "mtime_ns": self.mtime_ns,
            "content_hash": self.content_hash,
            "context_generation": self.context_generation,
            "source": self.source,
            "restored_after_compact": self.restored_after_compact,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "FileContextEntry":
        return cls(
            path=str(payload.get("path", "")),
            start_line=int(payload.get("start_line", 1) or 1),
            end_line=int(payload.get("end_line", 0) or 0),
            mtime_ns=int(payload.get("mtime_ns", 0) or 0),
            content_hash=str(payload.get("content_hash", "")),
            context_generation=int(payload.get("context_generation", 0) or 0),
            source=str(payload.get("source", "read_file") or "read_file"),
            restored_after_compact=bool(payload.get("restored_after_compact", False)),
        )


@dataclass(frozen=True)
class ToolContextEntry:
    tool_name: str
    tool_call_id: str
    summary: str
    availability: str
    artifact_path: str = ""
    context_generation: int = 0

    @property
    def key(self) -> str:
        return self.tool_call_id or f"{self.tool_name}:{self.summary}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "tool_call_id": self.tool_call_id,
            "summary": self.summary,
            "availability": self.availability,
            "artifact_path": self.artifact_path,
            "context_generation": self.context_generation,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ToolContextEntry":
        return cls(
            tool_name=str(payload.get("tool_name", "")),
            tool_call_id=str(payload.get("tool_call_id", "")),
            summary=str(payload.get("summary", "")),
            availability=str(payload.get("availability", "full")),
            artifact_path=str(payload.get("artifact_path", "")),
            context_generation=int(payload.get("context_generation", 0) or 0),
        )


@dataclass(frozen=True)
class ContextAnchor:
    file_reads: List[FileContextEntry] = field(default_factory=list)
    tool_results: List[ToolContextEntry] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_reads": [item.to_dict() for item in self.file_reads],
            "tool_results": [item.to_dict() for item in self.tool_results],
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any] | None) -> "ContextAnchor":
        raw = payload or {}
        return cls(
            file_reads=[FileContextEntry.from_dict(item) for item in raw.get("file_reads", [])],
            tool_results=[ToolContextEntry.from_dict(item) for item in raw.get("tool_results", [])],
        )


def build_context_anchor(messages: Iterable[ConversationMessage], context_generation: int) -> Dict[str, Any]:
    anchor: Dict[str, Any] = {}
    for message in messages:
        anchor = remember_context_message(anchor, message, context_generation)
    return anchor


def remember_context_message(
    anchor_payload: Dict[str, Any] | None,
    message: ConversationMessage,
    context_generation: int,
) -> Dict[str, Any]:
    anchor = ContextAnchor.from_dict(anchor_payload)
    file_reads = OrderedDict((item.key, item) for item in anchor.file_reads)
    tool_results = OrderedDict((item.key, item) for item in anchor.tool_results)
    file_entry = _file_entry_from_message(message, context_generation)
    if file_entry is not None:
        _remember(file_reads, file_entry.key, file_entry, MAX_TRACKED_FILE_READS)
    tool_entry = _tool_entry_from_message(message, context_generation)
    if tool_entry is not None:
        _remember(tool_results, tool_entry.key, tool_entry, MAX_TRACKED_TOOL_RESULTS)
    return ContextAnchor(
        file_reads=list(file_reads.values()),
        tool_results=list(tool_results.values()),
    ).to_dict()


def can_reuse_file_context(
    anchor_payload: Dict[str, Any] | None,
    *,
    path: Path,
    start_line: int,
    end_line: int,
    mtime_ns: int,
    context_generation: int,
) -> bool:
    anchor = ContextAnchor.from_dict(anchor_payload)
    target_key = file_context_key(str(path), start_line, end_line)
    for entry in anchor.file_reads:
        if entry.key != target_key:
            continue
        return entry.context_generation == context_generation and entry.mtime_ns == mtime_ns
    return False


def render_context_anchor_message(anchor_payload: Dict[str, Any] | None, *, compact: bool = False) -> str:
    anchor = ContextAnchor.from_dict(anchor_payload)
    lines = _summary_lines(anchor, compact=compact)
    if not lines:
        return ""
    heading = "Working context currently available in this conversation."
    if compact:
        heading = "Current working context:"
    return "\n".join([heading, *lines])


def build_post_compact_restore_messages(
    anchor_payload: Dict[str, Any] | None,
    workspace_root: Path,
    context_generation: int,
    preserved_messages: List[ConversationMessage],
) -> List[ConversationMessage]:
    anchor = ContextAnchor.from_dict(anchor_payload)
    restored_messages = _restore_file_messages(anchor, workspace_root, context_generation, preserved_messages)
    restored_messages.extend(_restore_tool_messages(anchor, context_generation))
    return restored_messages


def file_context_key(path: str, start_line: int, end_line: int) -> str:
    return f"{path}:{int(start_line)}:{int(end_line)}"


def _remember(items: OrderedDict[str, Any], key: str, value: Any, limit: int) -> None:
    if key in items:
        items.pop(key)
    items[key] = value
    while len(items) > limit:
        items.popitem(last=False)


def _summary_lines(anchor: ContextAnchor, *, compact: bool) -> List[str]:
    lines: List[str] = []
    if anchor.file_reads:
        label = "Files with in-context content" if not compact else "Files"
        file_items = [f"- {label}:"] if not compact else []
        for item in anchor.file_reads[-MAX_CONTEXT_SUMMARY_ITEMS:]:
            suffix = _line_suffix(item.start_line, item.end_line)
            restored = " (restored after compact)" if item.restored_after_compact else ""
            file_items.append(f"  - {item.path}{suffix}{restored}")
        lines.extend(file_items)
    if anchor.tool_results:
        label = "Tool outputs still available by preview/reference" if not compact else "Tool references"
        tool_items = [f"- {label}:"] if not compact else []
        for item in anchor.tool_results[-MAX_CONTEXT_SUMMARY_ITEMS:]:
            descriptor = item.availability.replace("_", " ")
            tool_items.append(f"  - {item.tool_name}#{item.tool_call_id or 'unknown'} ({descriptor})")
        lines.extend(tool_items)
    return lines


def _restore_file_messages(
    anchor: ContextAnchor,
    workspace_root: Path,
    context_generation: int,
    preserved_messages: List[ConversationMessage],
) -> List[ConversationMessage]:
    preserved_keys = {
        entry.key
        for entry in (
            _file_entry_from_message(message, context_generation)
            for message in preserved_messages
        )
        if entry is not None
    }
    used_chars = 0
    restored: List[ConversationMessage] = []
    for entry in reversed(anchor.file_reads):
        if entry.key in preserved_keys or len(restored) >= MAX_RESTORED_FILE_CONTEXTS:
            continue
        path = _resolve_workspace_path(workspace_root, entry.path)
        if path is None or not path.exists():
            continue
        content, mtime_ns, content_hash = _read_range(path, entry.start_line, entry.end_line)
        if not content:
            continue
        if used_chars + len(content) > MAX_RESTORED_FILE_CHARS:
            continue
        used_chars += len(content)
        restored.append(
            ConversationMessage(
                role="system",
                kind="context_restore",
                content=_render_file_restore(entry.path, entry.start_line, entry.end_line, content),
                metadata={
                    "anchor_type": "file_read_restore",
                    "path": entry.path,
                    "start_line": entry.start_line,
                    "end_line": entry.end_line,
                    "mtime_ns": mtime_ns,
                    "content_hash": content_hash,
                    "context_generation": context_generation,
                    "restored_after_compact": True,
                },
            )
        )
    return restored


def _restore_tool_messages(anchor: ContextAnchor, context_generation: int) -> List[ConversationMessage]:
    used_chars = 0
    restored: List[ConversationMessage] = []
    for entry in reversed(anchor.tool_results):
        if len(restored) >= MAX_RESTORED_TOOL_CONTEXTS:
            break
        if not entry.summary:
            continue
        summary = entry.summary[:MAX_PREVIEW_CHARS]
        if used_chars + len(summary) > MAX_RESTORED_TOOL_CHARS:
            continue
        used_chars += len(summary)
        restored.append(
            ConversationMessage(
                role="system",
                kind="context_restore",
                content=(
                    "Recent tool context retained after compaction:\n"
                    f"- Tool: {entry.tool_name}\n"
                    f"- Call ID: {entry.tool_call_id or 'unknown'}\n"
                    f"- Availability: {entry.availability}\n"
                    f"- Summary: {summary}"
                ),
                metadata={
                    "anchor_type": "tool_result_restore",
                    "tool_name": entry.tool_name,
                    "tool_call_id": entry.tool_call_id,
                    "availability": entry.availability,
                    "artifact_path": entry.artifact_path,
                    "context_generation": context_generation,
                },
            )
        )
    return restored


def _file_entry_from_message(
    message: ConversationMessage,
    context_generation: int,
) -> FileContextEntry | None:
    metadata = dict(message.metadata or {})
    if message.kind == "context_restore" and metadata.get("anchor_type") == "file_read_restore":
        path = str(metadata.get("path", "")).strip()
        if not path:
            return None
        return FileContextEntry(
            path=path,
            start_line=int(metadata.get("start_line", 1) or 1),
            end_line=int(metadata.get("end_line", 0) or 0),
            mtime_ns=int(metadata.get("mtime_ns", 0) or 0),
            content_hash=str(metadata.get("content_hash", "")),
            context_generation=context_generation,
            source="context_restore",
            restored_after_compact=True,
        )
    if message.role != "tool" or message.name != "read_file":
        return None
    if bool(metadata.get("unchanged_since_last_read", False)):
        return None
    path = str(metadata.get("path", "")).strip()
    if not path:
        return None
    content = extract_text_content(message.content)
    if not content or _is_reference_only(content):
        return None
    return FileContextEntry(
        path=path,
        start_line=int(metadata.get("start_line", 1) or 1),
        end_line=int(metadata.get("end_line", 0) or 0),
        mtime_ns=int(metadata.get("mtime_ns", 0) or 0),
        content_hash=str(metadata.get("content_hash", "") or _hash_text(content)),
        context_generation=context_generation,
        source="read_file",
        restored_after_compact=False,
    )


def _tool_entry_from_message(
    message: ConversationMessage,
    context_generation: int,
) -> ToolContextEntry | None:
    if message.role != "tool" or message.name == "read_file":
        return None
    metadata = dict(message.metadata or {})
    content = extract_text_content(message.content)
    availability = _tool_availability(metadata, content)
    if availability == "full" and not metadata.get("stored_externally", False):
        return None
    summary = str(metadata.get("preview", "") or content[:MAX_PREVIEW_CHARS]).strip()
    if not summary:
        return None
    return ToolContextEntry(
        tool_name=str(message.name or ""),
        tool_call_id=str(message.tool_call_id or ""),
        summary=summary,
        availability=availability,
        artifact_path=str(metadata.get("artifact_path", "") or ""),
        context_generation=context_generation,
    )


def _tool_availability(metadata: Dict[str, Any], content: str) -> str:
    if metadata.get("stored_externally", False):
        return "preview_only"
    if _is_reference_only(content):
        return "reference_only"
    return "full"


def _is_reference_only(content: str) -> bool:
    text = str(content or "").strip()
    return text.startswith(STORED_TOOL_RESULT_PREFIX)


def _render_file_restore(path: str, start_line: int, end_line: int, content: str) -> str:
    line_suffix = _line_suffix(start_line, end_line)
    return (
        "Restored file context after compaction for continued work.\n"
        f"Path: {path}{line_suffix}\n"
        "Content:\n"
        f"{content}"
    )


def _line_suffix(start_line: int, end_line: int) -> str:
    if end_line > 0:
        return f" (lines {start_line}-{end_line})"
    return f" (from line {start_line})"


def _resolve_workspace_path(workspace_root: Path, raw_path: str) -> Path | None:
    try:
        candidate = Path(raw_path).expanduser()
        path = candidate if candidate.is_absolute() else (workspace_root / candidate)
        return path.resolve()
    except Exception:
        return None


def _read_range(path: Path, start_line: int, end_line: int) -> tuple[str, int, str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    snippet = lines[start_line - 1 : end_line] if end_line > 0 else lines[start_line - 1 :]
    content = "\n".join(snippet)
    return content, int(path.stat().st_mtime_ns), _hash_text(content)


def _hash_text(content: str) -> str:
    return sha256(str(content).encode("utf-8")).hexdigest()
