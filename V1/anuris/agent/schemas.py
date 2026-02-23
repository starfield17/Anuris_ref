from typing import Any, Dict, List


def _bash_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    }


def _read_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    }


def _write_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    }


def _edit_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace one exact text occurrence in a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    }


def _todo_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "TodoWrite",
            "description": "Update task tracking list for multi-step work.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string"},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                },
                                "activeForm": {"type": "string"},
                            },
                            "required": ["content", "status", "activeForm"],
                        },
                    }
                },
                "required": ["items"],
            },
        },
    }


def _task_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "task",
            "description": "Spawn a subagent with fresh context to handle a subtask.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "agent_type": {
                        "type": "string",
                        "enum": ["Explore", "general-purpose"],
                    },
                },
                "required": ["prompt"],
            },
        },
    }


def _task_create_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "task_create",
            "description": "Create a persistent task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["subject"],
            },
        },
    }


def _task_get_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "task_get",
            "description": "Get details of a persistent task by ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer"},
                },
                "required": ["task_id"],
            },
        },
    }


def _task_update_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "task_update",
            "description": "Update status, owner, or dependencies for a persistent task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer"},
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed", "deleted"],
                    },
                    "owner": {"type": "string"},
                    "add_blocked_by": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                    "add_blocks": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
                "required": ["task_id"],
            },
        },
    }


def _task_list_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "task_list",
            "description": "List persistent tasks with status summary.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    }


def build_tool_schemas(
    include_write_edit: bool = True,
    include_todo: bool = True,
    include_task: bool = True,
    include_task_board: bool = True,
) -> List[Dict[str, Any]]:
    """Build tool schema list by feature flags."""
    schemas = [_bash_schema(), _read_schema()]
    if include_write_edit:
        schemas.extend([_write_schema(), _edit_schema()])
    if include_todo:
        schemas.append(_todo_schema())
    if include_task:
        schemas.append(_task_schema())
    if include_task_board:
        schemas.extend(
            [
                _task_create_schema(),
                _task_get_schema(),
                _task_update_schema(),
                _task_list_schema(),
            ]
        )
    return schemas


TOOL_SCHEMAS = build_tool_schemas()
