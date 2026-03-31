from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List


class PluginManager:
    """Discover local plugin manifests from workspace-controlled directories."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()
        self.plugins: List[Dict[str, str]] = []
        self.reload(self.workspace_root)

    def reload(self, workspace_root: Path | None = None) -> List[Dict[str, str]]:
        if workspace_root is not None:
            self.workspace_root = Path(workspace_root).resolve()
        discovered: List[Dict[str, str]] = []
        for directory in self._plugin_dirs():
            if not directory.exists():
                continue
            for manifest in sorted(directory.rglob("plugin.json")):
                try:
                    payload = json.loads(manifest.read_text(encoding="utf-8"))
                except Exception:
                    continue
                root = manifest.parent
                if root.name == ".codex-plugin":
                    root = root.parent
                discovered.append(
                    {
                        "name": str(payload.get("name", root.name)),
                        "description": str(payload.get("description", "")),
                        "version": str(payload.get("version", "")),
                        "path": str(root),
                    }
                )
        self.plugins = discovered
        return list(self.plugins)

    def skill_dirs(self) -> List[Path]:
        result: List[Path] = []
        for plugin in self.plugins:
            path = Path(plugin["path"]) / "skills"
            if path.exists() and path.is_dir():
                result.append(path.resolve())
        return result

    def render(self) -> str:
        if not self.plugins:
            return "No plugins found."
        lines = []
        for plugin in self.plugins:
            version = f" v{plugin['version']}" if plugin["version"] else ""
            description = f" - {plugin['description']}" if plugin["description"] else ""
            lines.append(f"- {plugin['name']}{version}{description} ({plugin['path']})")
        return "\n".join(lines)

    def _plugin_dirs(self) -> List[Path]:
        return [
            self.workspace_root / ".anuris" / "plugins",
            self.workspace_root / ".claude" / "plugins",
            self.workspace_root / ".agents" / "plugins",
        ]
