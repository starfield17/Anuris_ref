from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import yaml

PACKAGE_SKILL_FILE = "SKILL.md"
RESOURCE_DIR_NAMES = ("scripts", "references", "assets")


def discover_skill_candidates(directory: Path) -> List[Tuple[str, Path]]:
    candidates: List[Tuple[str, Path]] = []
    for child in sorted(directory.iterdir()):
        entry = child / PACKAGE_SKILL_FILE
        if child.is_dir() and entry.is_file():
            candidates.append(("package", entry))
    for skill_file in sorted(directory.glob("*.md")):
        candidates.append(("flat", skill_file))
    return candidates


def parse_skill_file(path: Path) -> tuple[Dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n?(.*)", text, re.DOTALL)
    if not match:
        return {}, text.strip()
    try:
        raw_meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        raw_meta = {}
    meta = raw_meta if isinstance(raw_meta, dict) else {}
    return meta, match.group(2).strip()


def build_skill_record(
    *,
    kind: str,
    key: str,
    entry_path: Path,
    workspace_root: Path,
) -> Dict[str, Any]:
    meta, body = parse_skill_file(entry_path)
    skill_dir = entry_path.parent if kind == "package" else None
    return {
        "name": key,
        "kind": kind,
        "body": body,
        "description": _as_text(meta.get("description"), "No description"),
        "tags": _as_csv(meta.get("tags")),
        "path": str(entry_path),
        "skill_dir": str(skill_dir) if skill_dir else "",
        "skill_dir_display": display_path(skill_dir, workspace_root) if skill_dir else "",
        "paths": _as_csv(meta.get("paths")),
        "allowed_tools": _as_csv(meta.get("allowed_tools")),
        "agent": _as_text(meta.get("agent")),
        "effort": _as_text(meta.get("effort")),
        "when_to_use": _as_text(meta.get("when_to_use") or meta.get("when-to-use")),
        "user_invocable": _as_text(meta.get("user_invocable") or meta.get("user-invocable"), "true"),
        "source": _as_text(meta.get("source"), "workspace"),
        "aliases": _as_list(meta.get("aliases")),
        "resources": collect_skill_resources(skill_dir, workspace_root),
        "display_path": display_path(entry_path, workspace_root),
    }


def collect_skill_resources(skill_dir: Path | None, workspace_root: Path) -> Dict[str, List[str]]:
    resources = {name: [] for name in RESOURCE_DIR_NAMES}
    if skill_dir is None:
        return resources
    for directory_name in RESOURCE_DIR_NAMES:
        resource_dir = skill_dir / directory_name
        if not resource_dir.is_dir():
            continue
        resources[directory_name] = [
            display_path(path, workspace_root)
            for path in sorted(resource_dir.rglob("*"))
            if path.is_file()
        ]
    return resources


def render_skill_load_output(skill: Dict[str, Any], resolved_name: str) -> str:
    lines = [f"<skill name=\"{resolved_name}\">", skill["body"]]
    resource_block = render_resource_manifest(skill)
    if resource_block:
        lines.extend(["", resource_block])
    lines.append("</skill>")
    return "\n".join(item for item in lines if item)


def render_resource_manifest(skill: Dict[str, Any]) -> str:
    resources = skill.get("resources", {})
    has_resources = any(resources.get(name) for name in RESOURCE_DIR_NAMES)
    if skill.get("kind") != "package" and not has_resources:
        return ""
    lines = [
        "Bundled skill resources:",
        f"- entry: {skill.get('display_path', skill.get('path', ''))}",
    ]
    if skill.get("skill_dir_display"):
        lines.append(f"- skill_dir: {skill['skill_dir_display']}")
    for name in RESOURCE_DIR_NAMES:
        items = list(resources.get(name, []))
        if not items:
            lines.append(f"- {name}: (none)")
            continue
        lines.append(f"- {name}:")
        lines.extend(f"  - {item}" for item in items)
    lines.append("Read or execute these resources explicitly as needed; they are not auto-loaded.")
    return "\n".join(lines)


def display_path(path: Path, workspace_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(workspace_root))
    except Exception:
        return str(path.resolve())


def alias_tokens(skill_name: str, aliases: Iterable[str], tags: Iterable[str]) -> List[str]:
    values = [skill_name, *aliases, *tags]
    results: List[str] = []
    for value in values:
        normalized = normalize_token(value)
        if normalized:
            results.append(normalized)
    return results


def normalize_token(raw: str) -> str:
    normalized = raw.strip().lower().replace("\\", "/")
    normalized = normalized.split("/")[-1]
    if normalized.endswith(".md"):
        normalized = normalized[:-3]
    normalized = re.sub(r"[^a-z0-9_-]+", "-", normalized)
    normalized = normalized.strip("-").replace("_", "-")
    while "--" in normalized:
        normalized = normalized.replace("--", "-")
    return normalized


def token_signature(token: str) -> str:
    parts = [item for item in token.split("-") if item]
    if len(parts) < 2:
        return ""
    return "-".join(sorted(parts))


def _as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    text = str(value).strip()
    return text or default


def _as_csv(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]
