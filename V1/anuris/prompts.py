import os
from pathlib import Path


class PromptManager:
    def __init__(self, filename: str = "prompt_v2.md"):
        self.project_dir = Path(__file__).resolve().parent.parent
        self.filename = Path("prompts") / filename
        self._cached_prompt = None

    def get_prompt(self, force_reload: bool = False) -> str:
        if self._cached_prompt and not force_reload:
            return self._cached_prompt

        prompt_file = self.project_dir / self.filename
        try:
            if prompt_file.exists():
                self._cached_prompt = prompt_file.read_text(encoding="utf-8")
                return self._cached_prompt
        except Exception as exc:
            print(f"Error loading prompt from {prompt_file}: {exc}")

        self._cached_prompt = self._get_default_prompt()
        return self._cached_prompt

    def resolve_prompt_source(self, source: str) -> str:
        if not source or not source.strip():
            return self.get_prompt()

        try:
            path = Path(os.path.expanduser(source)).resolve()
            if path.exists() and path.is_file():
                try:
                    return path.read_text(encoding="utf-8")
                except Exception as exc:
                    print(f"Warning: System prompt file exists but readable failed: {exc}")
                    return source
        except Exception:
            pass

        return source

    def _get_default_prompt(self) -> str:
        return ""

    def save_prompt(self, content: str) -> bool:
        prompt_file = self.project_dir / self.filename
        try:
            prompt_file.write_text(content, encoding="utf-8")
            self._cached_prompt = content
            return True
        except Exception as exc:
            print(f"Error saving prompt: {exc}")
            return False


prompt_manager = PromptManager()
DEFAULT_SYSTEM_PROMPT = prompt_manager.get_prompt()
