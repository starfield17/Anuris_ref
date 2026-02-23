from typing import Any, Dict, List


class TodoManager:
    """In-memory todo list manager (s03 style)."""

    def __init__(self):
        self.items: List[Dict[str, str]] = []

    def update(self, items: List[Dict[str, Any]]) -> str:
        if len(items) > 20:
            raise ValueError("Max 20 todos")

        validated: List[Dict[str, str]] = []
        in_progress_count = 0
        for index, item in enumerate(items):
            content = str(item.get("content", item.get("text", ""))).strip()
            status = str(item.get("status", "pending")).lower()
            active_form = str(item.get("activeForm", content)).strip()
            if not content:
                raise ValueError(f"Item {index}: content required")
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"Item {index}: invalid status '{status}'")
            if status == "in_progress":
                in_progress_count += 1
                if not active_form:
                    raise ValueError(f"Item {index}: activeForm required for in_progress")
            validated.append(
                {
                    "content": content,
                    "status": status,
                    "activeForm": active_form,
                }
            )

        if in_progress_count > 1:
            raise ValueError("Only one in_progress allowed")

        self.items = validated
        return self.render()

    def render(self) -> str:
        if not self.items:
            return "No todos."

        lines: List[str] = []
        for item in self.items:
            marker = {
                "pending": "[ ]",
                "in_progress": "[>]",
                "completed": "[x]",
            }.get(item["status"], "[?]")
            suffix = f" <- {item['activeForm']}" if item["status"] == "in_progress" else ""
            lines.append(f"{marker} {item['content']}{suffix}")

        done = sum(1 for item in self.items if item["status"] == "completed")
        lines.append(f"\n({done}/{len(self.items)} completed)")
        return "\n".join(lines)
