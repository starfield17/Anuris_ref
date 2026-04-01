from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from ..engine.messages import extract_text_content


@dataclass
class ContextSlice:
    name: str
    kind: str
    char_count: int
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "char_count": self.char_count,
            "details": self.details,
        }


class ContextVisualizer:
    """Builds grouped context views inspired by Claude Code context panels."""

    def __init__(self, session: Any):
        self.session = session

    def analyze(self) -> Dict[str, Any]:
        store = self.session.session_store
        context_files = self.session.services.context_files
        memory_manager = self.session.services.memory_manager
        notification_center = self.session.services.notification_center

        grouped: Dict[str, List[ContextSlice]] = {
            "conversation": [],
            "file_reads": [],
            "mcp": [],
            "memory": [],
            "attachments": [],
            "compact_summaries": [],
            "runtime_injections": [],
        }

        for message in store.messages:
            text = extract_text_content(message.content)
            size = len(text)
            if message.kind == "compact_boundary":
                grouped["compact_summaries"].append(
                    ContextSlice("compact_boundary", "compact_summary", size, message.metadata.get("focus", ""))
                )
                continue
            if message.role in {"user", "assistant"}:
                grouped["conversation"].append(ContextSlice(message.role, message.kind, size, message.preview(120)))
            elif message.role == "tool" and message.name == "read_mcp_resource":
                grouped["mcp"].append(ContextSlice(message.name or "mcp", "mcp", size, message.preview(120)))

            attachments = message.metadata.get("attachments", []) if isinstance(message.metadata, dict) else []
            for attachment in attachments:
                label = str(attachment.get("name", attachment.get("path", "attachment")))
                grouped["attachments"].append(ContextSlice(label, "attachment", 0, str(attachment.get("type", ""))))

        for path in context_files.list_paths():
            grouped["file_reads"].append(ContextSlice(self._label(path), "file", self._safe_char_count(path), self._label(path)))

        memory_text = memory_manager.read() if memory_manager else "No memory saved."
        if memory_text and memory_text != "No memory saved.":
            grouped["memory"].append(ContextSlice("workspace memory", "memory", len(memory_text), memory_text[:120]))

        if notification_center is not None:
            for notice in notification_center.recent(limit=10):
                grouped["runtime_injections"].append(
                    ContextSlice(
                        notice.get("kind", "runtime"),
                        "runtime_notice",
                        len(str(notice.get("message", ""))),
                        str(notice.get("message", ""))[:120],
                    )
                )

        totals = {key: sum(item.char_count for item in values) for key, values in grouped.items()}
        top_items = sorted(
            [item.to_dict() for values in grouped.values() for item in values],
            key=lambda item: item["char_count"],
            reverse=True,
        )
        recent_files = [self._label(path) for path in context_files.list_paths()[-10:]][::-1]
        return {
            "totals": totals,
            "groups": {key: [item.to_dict() for item in values] for key, values in grouped.items()},
            "top_items": top_items[:20],
            "recent_files": recent_files,
            "approx_chars": store.approximate_size(),
            "compact_count": len(grouped["compact_summaries"]),
        }

    def render(self) -> str:
        data = self.analyze()
        lines = [
            "Context visualization:",
            f"- approx_chars: {data['approx_chars']}",
            f"- compact_boundaries: {data['compact_count']}",
            "",
            "Sources:",
        ]
        for key, total in data["totals"].items():
            count = len(data["groups"][key])
            lines.append(f"- {key}: {count} item(s), {total} chars")
        if data["recent_files"]:
            lines.extend(["", "Recent files:"])
            lines.extend(f"- {item}" for item in data["recent_files"])
        if data["top_items"]:
            lines.extend(["", "Top context items:"])
            for item in data["top_items"][:8]:
                details = f" ({item['details']})" if item["details"] else ""
                lines.append(f"- {item['kind']}::{item['name']} -> {item['char_count']} chars{details}")
        return "\n".join(lines)

    def render_top(self, limit: int = 10) -> str:
        data = self.analyze()
        items = data["top_items"][:limit]
        if not items:
            return "No context items."
        return "\n".join(
            f"- {item['kind']}::{item['name']} -> {item['char_count']} chars"
            for item in items
        )

    def render_recent(self) -> str:
        data = self.analyze()
        lines = ["Recent context entries:"]
        if data["recent_files"]:
            lines.extend(f"- file::{item}" for item in data["recent_files"])
        else:
            lines.append("- (no recent files)")
        compact_items = data["groups"]["compact_summaries"][-3:]
        for item in compact_items:
            lines.append(f"- compact::{item['details'] or 'automatic'}")
        runtime_items = data["groups"]["runtime_injections"][-5:]
        for item in runtime_items:
            lines.append(f"- runtime::{item['details']}")
        return "\n".join(lines)

    @staticmethod
    def _safe_char_count(path: Path) -> int:
        try:
            if path.is_file():
                return len(path.read_text(encoding="utf-8"))
        except Exception:
            return 0
        return 0

    def _label(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.session.workspace_root))
        except ValueError:
            return str(path)
