from __future__ import annotations

import difflib
import fnmatch
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from .skill_support import (
    alias_tokens,
    build_skill_record,
    discover_skill_candidates,
    normalize_token,
    render_skill_load_output,
    token_signature,
)


class SkillLoader:
    """Two-layer skill loader (metadata in prompt, body on demand)."""

    def __init__(self, workspace_root: Path, skills_dirs: Optional[List[Path]] = None):
        self.workspace_root = workspace_root.resolve()
        self.skills_dirs = [path.resolve() for path in (skills_dirs or self._default_skill_dirs())]
        self.skills: Dict[str, Dict[str, Any]] = {}
        self.alias_map: Dict[str, str] = {}
        self.refresh()

    def refresh(self) -> None:
        loaded: Dict[str, Dict[str, Any]] = {}
        aliases: Dict[str, str] = {}
        for directory in self.skills_dirs:
            if not directory.exists():
                continue
            for kind, entry_path in discover_skill_candidates(directory):
                key = self._skill_key(kind, entry_path)
                if key in loaded:
                    continue
                skill = build_skill_record(
                    kind=kind,
                    key=key,
                    entry_path=entry_path,
                    workspace_root=self.workspace_root,
                )
                loaded[key] = skill
                self._register_aliases(aliases, key, skill)
        self.skills = loaded
        self.alias_map = aliases

    def descriptions(self, current_paths: Optional[Iterable[Path]] = None) -> str:
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
        self.refresh()
        resolved_name = self._resolve_name(name)
        skill = self.skills.get(resolved_name)
        if not skill:
            return self._unknown_skill(name)
        return render_skill_load_output(skill, resolved_name)

    def render_catalog(self) -> str:
        self.refresh()
        if not self.skills:
            return "No skills found. Add flat .md skills or <skill>/SKILL.md packages under .anuris_skills/ or skills/."
        lines = []
        for name in sorted(self.skills):
            skill = self.skills[name]
            scope = f" paths={skill['paths']}" if skill.get("paths") else ""
            kind = skill.get("kind", "flat")
            lines.append(f"- {name}: {skill['description']} ({skill['display_path']}) kind={kind}{scope}")
        return "\n".join(lines)

    def prefetch(
        self,
        prompt: str,
        limit: int = 3,
        current_paths: Optional[Iterable[Path]] = None,
    ) -> List[Dict[str, str]]:
        self.refresh()
        normalized_prompt = normalize_token(prompt)
        raw_tokens = {token for token in re.split(r"[^a-zA-Z0-9_-]+", prompt.lower()) if token}
        visible = set(self._visible_skill_names(current_paths))
        scored = self._score_visible_skills(normalized_prompt, raw_tokens, visible)
        results: List[Dict[str, str]] = []
        for _, name in scored[:limit]:
            skill = self.skills[name]
            results.append(
                {
                    "name": name,
                    "description": skill["description"],
                    "path": skill["display_path"],
                    "when_to_use": skill.get("when_to_use", ""),
                }
            )
        return results

    def _score_visible_skills(
        self,
        normalized_prompt: str,
        raw_tokens: Set[str],
        visible: Set[str],
    ) -> List[tuple[int, str]]:
        scored: List[tuple[int, str]] = []
        for name, skill in self.skills.items():
            if name not in visible:
                continue
            score = 0
            aliases = {name, normalize_token(name)}
            aliases.update(alias for alias, target in self.alias_map.items() if target == name)
            for alias in aliases:
                if alias and alias in normalized_prompt:
                    score += 5
                score += len({token for token in alias.split("-") if token} & raw_tokens)
            description_tokens = {token for token in re.split(r"[^a-zA-Z0-9_-]+", skill["description"].lower()) if token}
            score += min(2, len(description_tokens & raw_tokens))
            if score > 0:
                scored.append((score, name))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return scored

    def _register_aliases(self, aliases: Dict[str, str], key: str, skill: Dict[str, Any]) -> None:
        tags = [item.strip() for item in str(skill.get("tags", "")).split(",") if item.strip()]
        for alias in alias_tokens(key, skill.get("aliases", []), tags):
            aliases.setdefault(alias, key)
            signature = token_signature(alias)
            if signature:
                aliases.setdefault(signature, key)
            if alias.startswith("nb-"):
                aliases.setdefault(alias[3:], key)
            aliases.setdefault(alias.replace("-", ""), key)
        declared_name = normalize_token(str(skill.get("name", "")))
        if declared_name:
            aliases.setdefault(declared_name, key)

    def _resolve_name(self, requested: str) -> str:
        exact = requested.strip()
        if exact in self.skills:
            return exact
        normalized = normalize_token(requested)
        if not normalized:
            return exact
        if normalized in self.skills:
            return normalized
        if normalized in self.alias_map:
            return self.alias_map[normalized]
        signature = token_signature(normalized)
        if signature and signature in self.alias_map:
            return self.alias_map[signature]
        prefixed = normalized[3:] if normalized.startswith("nb-") else f"nb-{normalized}"
        if prefixed in self.skills:
            return prefixed
        if prefixed in self.alias_map:
            return self.alias_map[prefixed]
        return exact

    def _suggest(self, requested: str) -> str:
        normalized = normalize_token(requested)
        candidates = sorted(set(list(self.skills.keys()) + list(self.alias_map.keys())))
        matches = difflib.get_close_matches(normalized, candidates, n=3, cutoff=0.5)
        canonical: List[str] = []
        for match in matches:
            resolved = self.alias_map.get(match, match)
            if resolved not in canonical:
                canonical.append(resolved)
        return ", ".join(canonical)

    def _unknown_skill(self, requested: str) -> str:
        available = ", ".join(sorted(self.skills.keys())) or "(none)"
        hint = self._suggest(requested)
        if hint:
            return f"Error: Unknown skill '{requested}'. Did you mean: {hint}? Available: {available}"
        return f"Error: Unknown skill '{requested}'. Available: {available}"

    def _visible_skill_names(self, current_paths: Optional[Iterable[Path]]) -> List[str]:
        return [name for name in sorted(self.skills) if self._is_in_scope(self.skills[name], current_paths)]

    def _is_in_scope(self, skill: Dict[str, Any], current_paths: Optional[Iterable[Path]]) -> bool:
        patterns = [item.strip() for item in str(skill.get("paths", "") or "").split(",") if item.strip()]
        if not patterns:
            return True
        if not current_paths:
            return False
        for path in current_paths:
            try:
                relative = path.resolve().relative_to(self.workspace_root).as_posix()
            except Exception:
                continue
            if self._matches_any_scope(relative, patterns):
                return True
        return False

    @staticmethod
    def _matches_any_scope(relative_path: str, patterns: List[str]) -> bool:
        for pattern in patterns:
            normalized = pattern.replace("\\", "/")
            candidates = {normalized, normalized.replace("**/", "")}
            if any(fnmatch.fnmatch(relative_path, candidate) for candidate in candidates):
                return True
        return False

    def _skill_key(self, kind: str, entry_path: Path) -> str:
        if kind == "package":
            return normalize_token(entry_path.parent.name)
        return normalize_token(entry_path.stem)

    def _default_skill_dirs(self) -> List[Path]:
        return [
            self.workspace_root / ".anuris_skills",
            self.workspace_root / "skills",
        ]
