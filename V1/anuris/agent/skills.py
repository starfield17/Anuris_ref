import difflib
import fnmatch
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set


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
        self.alias_map: Dict[str, str] = {}
        self.refresh()

    def refresh(self) -> None:
        """Rescan skill directories so runtime edits are visible."""
        loaded: Dict[str, Dict[str, str]] = {}
        aliases: Dict[str, str] = {}
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
                    "paths": meta.get("paths", ""),
                    "allowed_tools": meta.get("allowed_tools", ""),
                    "agent": meta.get("agent", ""),
                    "effort": meta.get("effort", ""),
                    "when_to_use": meta.get("when_to_use", meta.get("when-to-use", "")),
                    "user_invocable": meta.get("user_invocable", meta.get("user-invocable", "true")),
                    "source": meta.get("source", "workspace"),
                }
                for alias in self._build_aliases(
                    name=name,
                    aliases_raw=meta.get("aliases", ""),
                    tags_raw=meta.get("tags", ""),
                ):
                    aliases.setdefault(alias, name)
        self.skills = loaded
        self.alias_map = aliases

    def descriptions(self, current_paths: Optional[Iterable[Path]] = None) -> str:
        """Compact metadata to inject into the system prompt."""
        self.refresh()
        visible = self._visible_skill_names(current_paths)
        if not visible:
            return "(no skills available)"
        lines = []
        for name in visible:
            skill = self.skills[name]
            line = f"- {name}: {skill['description']}"
            if skill["tags"]:
                line += f" [{skill['tags']}]"
            lines.append(line)
        return "\n".join(lines)

    def load(self, name: str) -> str:
        """Full skill body returned via tool_result."""
        self.refresh()
        resolved_name = self._resolve_name(name)
        skill = self.skills.get(resolved_name)
        if not skill:
            available = ", ".join(sorted(self.skills.keys())) or "(none)"
            hint = self._suggest(name)
            if hint:
                return f"Error: Unknown skill '{name}'. Did you mean: {hint}? Available: {available}"
            return f"Error: Unknown skill '{name}'. Available: {available}"
        return f"<skill name=\"{resolved_name}\">\n{skill['body']}\n</skill>"

    def render_catalog(self) -> str:
        """Human-readable skill catalog for CLI command output."""
        self.refresh()
        if not self.skills:
            return "No skills found. Add Markdown files under .anuris_skills/ or skills/."
        lines = []
        for name in sorted(self.skills):
            skill = self.skills[name]
            scope = f" paths={skill['paths']}" if skill.get("paths") else ""
            lines.append(f"- {name}: {skill['description']} ({skill['path']}){scope}")
        return "\n".join(lines)

    def prefetch(
        self,
        prompt: str,
        limit: int = 3,
        current_paths: Optional[Iterable[Path]] = None,
    ) -> List[Dict[str, str]]:
        self.refresh()
        normalized_prompt = self._normalize(prompt)
        raw_tokens = {token for token in re.split(r"[^a-zA-Z0-9_-]+", prompt.lower()) if token}
        scored: List[tuple[int, str]] = []
        visible = set(self._visible_skill_names(current_paths))
        for name, skill in self.skills.items():
            if name not in visible:
                continue
            score = 0
            aliases = {name, self._normalize(name)}
            aliases.update(alias for alias, target in self.alias_map.items() if target == name)
            for alias in aliases:
                if not alias:
                    continue
                if alias in normalized_prompt:
                    score += 5
                alias_tokens = {token for token in alias.split("-") if token}
                score += len(alias_tokens & raw_tokens)
            description_tokens = {token for token in re.split(r"[^a-zA-Z0-9_-]+", skill["description"].lower()) if token}
            score += min(2, len(description_tokens & raw_tokens))
            if score > 0:
                scored.append((score, name))
        scored.sort(key=lambda item: (-item[0], item[1]))
        results: List[Dict[str, str]] = []
        for _, name in scored[:limit]:
            skill = self.skills[name]
            results.append(
                {
                    "name": name,
                    "description": skill["description"],
                    "path": skill["path"],
                    "when_to_use": skill.get("when_to_use", ""),
                }
            )
        return results

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

    @staticmethod
    def _normalize(raw: str) -> str:
        normalized = raw.strip().lower()
        normalized = normalized.replace("\\", "/")
        normalized = normalized.split("/")[-1]
        if normalized.endswith(".md"):
            normalized = normalized[:-3]
        normalized = re.sub(r"[^a-z0-9_-]+", "-", normalized)
        normalized = normalized.strip("-")
        normalized = normalized.replace("_", "-")
        while "--" in normalized:
            normalized = normalized.replace("--", "-")
        return normalized

    def _build_aliases(self, name: str, aliases_raw: str, tags_raw: str) -> Set[str]:
        aliases: Set[str] = set()
        canonical = self._normalize(name)
        if canonical:
            aliases.add(canonical)
            if canonical.startswith("nb-"):
                aliases.add(canonical[3:])
            aliases.add(canonical.replace("-", ""))
            signature = self._token_signature(canonical)
            if signature:
                aliases.add(signature)

        for tag in tags_raw.split(","):
            token = self._normalize(tag)
            if token:
                aliases.add(token)
                signature = self._token_signature(token)
                if signature:
                    aliases.add(signature)

        for token in aliases_raw.split(","):
            alias = self._normalize(token)
            if alias:
                aliases.add(alias)
                signature = self._token_signature(alias)
                if signature:
                    aliases.add(signature)
        return aliases

    def _resolve_name(self, requested: str) -> str:
        exact = requested.strip()
        if exact in self.skills:
            return exact

        normalized = self._normalize(requested)
        if not normalized:
            return exact

        if normalized in self.skills:
            return normalized
        if normalized in self.alias_map:
            return self.alias_map[normalized]
        signature = self._token_signature(normalized)
        if signature and signature in self.alias_map:
            return self.alias_map[signature]

        if normalized.startswith("nb-"):
            short = normalized[3:]
            if short in self.alias_map:
                return self.alias_map[short]
        else:
            prefixed = f"nb-{normalized}"
            if prefixed in self.skills:
                return prefixed
        return exact

    def _suggest(self, requested: str) -> str:
        normalized = self._normalize(requested)
        candidates = sorted(set(list(self.skills.keys()) + list(self.alias_map.keys())))
        matches = difflib.get_close_matches(normalized, candidates, n=3, cutoff=0.5)
        if not matches:
            return ""
        canonical = []
        for match in matches:
            canonical_name = self.alias_map.get(match, match)
            if canonical_name not in canonical:
                canonical.append(canonical_name)
        return ", ".join(canonical)

    @staticmethod
    def _token_signature(token: str) -> str:
        parts = [item for item in token.split("-") if item]
        if len(parts) < 2:
            return ""
        return "-".join(sorted(parts))

    def _visible_skill_names(self, current_paths: Optional[Iterable[Path]]) -> List[str]:
        visible: List[str] = []
        for name in sorted(self.skills):
            if self._is_in_scope(self.skills[name], current_paths):
                visible.append(name)
        return visible

    def _is_in_scope(self, skill: Dict[str, str], current_paths: Optional[Iterable[Path]]) -> bool:
        patterns = [item.strip() for item in str(skill.get("paths", "") or "").split(",") if item.strip()]
        if not patterns:
            return True
        if not current_paths:
            return False
        for path in current_paths:
            try:
                relative = path.resolve().relative_to(self.workspace_root)
            except Exception:
                continue
            relative_text = relative.as_posix()
            if any(
                fnmatch.fnmatch(relative_text, candidate)
                for pattern in patterns
                for candidate in {
                    pattern.replace("\\", "/"),
                    pattern.replace("\\", "/").replace("**/", ""),
                }
            ):
                return True
        return False
