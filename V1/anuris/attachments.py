import base64
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class Attachment:
    """Represents a file attachment."""

    path: str
    name: str
    mime_type: str
    size: int
    base64_data: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "path": self.path,
            "name": self.name,
            "mime_type": self.mime_type,
            "size": self.size,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Attachment":
        """Create from dictionary."""
        return cls(**data)


class AttachmentManager:
    """Manages file attachments."""

    def __init__(self):
        self.attachments: List[Attachment] = []
        self.max_file_size = 20 * 1024 * 1024
        self.supported_image_types = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
        self.supported_text_types = {".txt", ".md", ".json", ".csv", ".xml", ".yaml", ".yml"}
        self.supported_doc_types = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}

    def add_attachment(self, file_path: str) -> Tuple[bool, str]:
        """Add a file as attachment."""
        try:
            path = Path(file_path).resolve()

            if not path.exists():
                return False, f"File not found: {file_path}"

            if not path.is_file():
                return False, f"Not a file: {file_path}"

            size = path.stat().st_size
            if size > self.max_file_size:
                return (
                    False,
                    f"File too large: {size / 1024 / 1024:.1f}MB (max: {self.max_file_size / 1024 / 1024}MB)",
                )

            mime_type, _ = mimetypes.guess_type(str(path))
            if not mime_type:
                mime_type = "application/octet-stream"

            attachment = Attachment(path=str(path), name=path.name, mime_type=mime_type, size=size)

            if path.suffix.lower() in self.supported_image_types:
                with open(path, "rb") as file_obj:
                    attachment.base64_data = base64.b64encode(file_obj.read()).decode("utf-8")

            self.attachments.append(attachment)
            return True, f"Added: {path.name} ({mime_type}, {size / 1024:.1f}KB)"

        except Exception as exc:
            return False, f"Error adding attachment: {str(exc)}"

    def remove_attachment(self, index: int) -> Tuple[bool, str]:
        """Remove attachment by index."""
        if 0 <= index < len(self.attachments):
            removed = self.attachments.pop(index)
            return True, f"Removed: {removed.name}"
        return False, "Invalid attachment index"

    def clear_attachments(self) -> None:
        """Clear all attachments."""
        self.attachments.clear()

    def list_attachments(self) -> List[Dict[str, Any]]:
        """Get list of attachments with details."""
        return [
            {
                "index": index,
                "name": attachment.name,
                "type": attachment.mime_type,
                "size": (
                    f"{attachment.size / 1024:.1f}KB"
                    if attachment.size < 1024 * 1024
                    else f"{attachment.size / 1024 / 1024:.1f}MB"
                ),
            }
            for index, attachment in enumerate(self.attachments)
        ]

    def prepare_for_api(self) -> List[Dict[str, Any]]:
        """Prepare attachments for API request."""
        api_attachments = []

        for attachment in self.attachments:
            if attachment.base64_data:
                api_attachments.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{attachment.mime_type};base64,{attachment.base64_data}",
                        },
                    }
                )
            elif Path(attachment.path).suffix.lower() in self.supported_text_types:
                try:
                    with open(attachment.path, "r", encoding="utf-8") as file_obj:
                        content = file_obj.read()
                        api_attachments.append(
                            {
                                "type": "text",
                                "text": f"[File: {attachment.name}]\n{content}",
                            }
                        )
                except Exception as exc:
                    api_attachments.append(
                        {
                            "type": "text",
                            "text": f"[Error reading {attachment.name}: {str(exc)}]",
                        }
                    )
            else:
                api_attachments.append(
                    {
                        "type": "text",
                        "text": f"[Attached file: {attachment.name} ({attachment.mime_type})]",
                    }
                )

        return api_attachments
