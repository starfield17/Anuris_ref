from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional


class MCPManager:
    """Local file-backed MCP resource catalog for the Python runtime."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()
        self.config_path = self.workspace_root / ".anuris" / "mcp.json"
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.payload = {"servers": []}
        self.reload(self.workspace_root)

    def reload(self, workspace_root: Path | None = None) -> None:
        if workspace_root is not None:
            self.workspace_root = Path(workspace_root).resolve()
            self.config_path = self.workspace_root / ".anuris" / "mcp.json"
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
        if self.config_path.exists():
            try:
                self.payload = json.loads(self.config_path.read_text(encoding="utf-8"))
            except Exception:
                self.payload = {"servers": []}
        else:
            self.payload = {"servers": []}

    def save(self) -> None:
        self.config_path.write_text(json.dumps(self.payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_servers(self) -> List[Dict[str, object]]:
        return list(self.payload.get("servers", []))

    def list_resources(self, server_name: Optional[str] = None) -> List[Dict[str, str]]:
        resources: List[Dict[str, str]] = []
        for server in self.payload.get("servers", []):
            if server_name and server.get("name") != server_name:
                continue
            for resource in server.get("resources", []):
                item = dict(resource)
                item.setdefault("server", str(server.get("name", "local")))
                resources.append(item)
        return resources

    def add_resource(self, name: str, path: str, description: str = "", server_name: str = "local") -> Dict[str, str]:
        resolved = Path(path).expanduser()
        if not resolved.is_absolute():
            resolved = (self.workspace_root / resolved).resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"Resource path not found: {path}")

        servers = self.payload.setdefault("servers", [])
        for server in servers:
            if server.get("name") == server_name:
                target_server = server
                break
        else:
            target_server = {"name": server_name, "resources": []}
            servers.append(target_server)

        resource = {
            "name": name.strip(),
            "path": str(resolved),
            "description": description.strip(),
        }
        target_server.setdefault("resources", [])
        target_server["resources"] = [item for item in target_server["resources"] if item.get("name") != resource["name"]]
        target_server["resources"].append(resource)
        self.save()
        return resource

    def read_resource(self, name: str, server_name: Optional[str] = None) -> str:
        for resource in self.list_resources(server_name):
            if resource.get("name") != name:
                continue
            path = Path(str(resource["path"]))
            if path.is_file():
                return path.read_text(encoding="utf-8")
            if path.is_dir():
                entries = sorted(path.iterdir())
                return "\n".join(item.name for item in entries[:200]) or "(empty directory)"
        raise ValueError(f"MCP resource not found: {name}")

    def render_servers(self) -> str:
        servers = self.list_servers()
        if not servers:
            return "No MCP servers configured."
        return "\n".join(
            f"- {server.get('name', 'unnamed')}: {len(server.get('resources', []))} resource(s)"
            for server in servers
        )

    def render_resources(self, server_name: Optional[str] = None) -> str:
        resources = self.list_resources(server_name)
        if not resources:
            return "No MCP resources configured."
        return "\n".join(
            f"- {item.get('server', 'local')}::{item.get('name')} -> {item.get('path')}"
            for item in resources
        )
