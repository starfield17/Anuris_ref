import re
from pathlib import Path
from typing import Dict, List, Optional


class SkillLoader:
    """Two-layer skill loader (metadata in prompt, body on demand)."""

    def __init__(self, workspace_root: Path, skills_dirs: Optional[List[Path]] = None):
        self.workspace_root = workspace_root.resolve()
        if skills_dirs is None:
            skills_dirs = [
                self.workspace_root / ".anuris_skills",
                self.workspace_root / "skills",
            ]
        self.skills_dirs = [path.resolve() for path in skills_dirs]
        self.skills: Dict[str, Dict[str, str]] = {}
        self.refresh()

    def refresh(self) -> None:
        """Rescan skill directories so runtime edits are visible."""
        loaded: Dict[str, Dict[str, str]] = {}
        for directory in self.skills_dirs:
            if not directory.exists():
                continue
            for skill_file in sorted(directory.glob("*.md")):
                name = skill_file.stem
                # Earlier directories take precedence (e.g. .anuris_skills over skills).
                if name in loaded:
                    continue
                text = skill_file.read_text()
                meta, body = self._parse_frontmatter(text)
                loaded[name] = {
                    "body": body,
                    "description": meta.get("description", "No description"),
                    "tags": meta.get("tags", ""),
                    "path": str(skill_file),
                }
        self.skills = loaded

    def descriptions(self) -> str:
        """Compact metadata to inject into the system prompt."""
        self.refresh()
        if not self.skills:
            return "(no skills available)"
        lines = []
        for name in sorted(self.skills):
            skill = self.skills[name]
            line = f"- {name}: {skill['description']}"
            if skill["tags"]:
                line += f" [{skill['tags']}]"
            lines.append(line)
        return "\n".join(lines)

    def load(self, name: str) -> str:
        """Full skill body returned via tool_result."""
        self.refresh()
        skill = self.skills.get(name)
        if not skill:
            available = ", ".join(sorted(self.skills.keys())) or "(none)"
            return f"Error: Unknown skill '{name}'. Available: {available}"
        return f"<skill name=\"{name}\">\n{skill['body']}\n</skill>"

    def render_catalog(self) -> str:
        """Human-readable skill catalog for CLI command output."""
        self.refresh()
        if not self.skills:
            return "No skills found. Add Markdown files under .anuris_skills/ or skills/."
        lines = []
        for name in sorted(self.skills):
            skill = self.skills[name]
            lines.append(f"- {name}: {skill['description']} ({skill['path']})")
        return "\n".join(lines)

    @staticmethod
    def _parse_frontmatter(text: str) -> tuple[Dict[str, str], str]:
        match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
        if not match:
            return {}, text.strip()
        meta: Dict[str, str] = {}
        for line in match.group(1).strip().splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
        return meta, match.group(2).strip()
