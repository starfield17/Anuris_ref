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


def _claim_task_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "claim_task",
            "description": "Claim a persistent task for an owner and mark it in progress.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer"},
                    "owner": {"type": "string"},
                },
                "required": ["task_id"],
            },
        },
    }


def _load_skill_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "load_skill",
            "description": "Load specialized knowledge by skill name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    }


def _background_run_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "background_run",
            "description": "Run a command in background and return a task id immediately.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer"},
                },
                "required": ["command"],
            },
        },
    }


def _check_background_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "check_background",
            "description": "Check one background task status or list all tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                },
            },
        },
    }


def _spawn_teammate_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "spawn_teammate",
            "description": "Spawn a persistent teammate worker.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "prompt": {"type": "string"},
                },
                "required": ["name", "prompt"],
            },
        },
    }


def _list_teammates_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "list_teammates",
            "description": "List teammate statuses.",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _send_message_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": "Send a message from lead to one teammate inbox.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "content": {"type": "string"},
                    "msg_type": {
                        "type": "string",
                        "enum": ["message", "broadcast"],
                    },
                },
                "required": ["to", "content"],
            },
        },
    }


def _read_inbox_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "read_inbox",
            "description": "Read and drain an inbox (defaults to lead inbox).",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
            },
        },
    }


def _broadcast_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "broadcast",
            "description": "Broadcast a message from lead to all teammates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                },
                "required": ["content"],
            },
        },
    }


def _shutdown_request_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "shutdown_request",
            "description": "Ask one teammate to shutdown gracefully.",
            "parameters": {
                "type": "object",
                "properties": {
                    "teammate": {"type": "string"},
                },
                "required": ["teammate"],
            },
        },
    }


def _shutdown_status_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "shutdown_status",
            "description": "Check a shutdown request by request_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "string"},
                },
                "required": ["request_id"],
            },
        },
    }


def _shutdown_list_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "shutdown_list",
            "description": "List all shutdown request statuses.",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _plan_review_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "plan_review",
            "description": "Approve or reject a teammate plan request.",
            "parameters": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "string"},
                    "approve": {"type": "boolean"},
                    "feedback": {"type": "string"},
                },
                "required": ["request_id", "approve"],
            },
        },
    }


def _plan_list_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "plan_list",
            "description": "List tracked teammate plan requests.",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def build_tool_schemas(
    include_write_edit: bool = True,
    include_todo: bool = True,
    include_task: bool = True,
    include_task_board: bool = True,
    include_skill_loading: bool = True,
    include_background_tasks: bool = True,
    include_team_ops: bool = False,
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
                _claim_task_schema(),
            ]
        )
    if include_skill_loading:
        schemas.append(_load_skill_schema())
    if include_background_tasks:
        schemas.extend([_background_run_schema(), _check_background_schema()])
    if include_team_ops:
        schemas.extend(
            [
                _spawn_teammate_schema(),
                _list_teammates_schema(),
                _send_message_schema(),
                _read_inbox_schema(),
                _broadcast_schema(),
                _shutdown_request_schema(),
                _shutdown_status_schema(),
                _shutdown_list_schema(),
                _plan_review_schema(),
                _plan_list_schema(),
            ]
        )
    return schemas


TOOL_SCHEMAS = build_tool_schemas()
