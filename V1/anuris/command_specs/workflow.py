from __future__ import annotations

import subprocess


def register_workflow_commands(dispatcher) -> None:
    dispatcher._register("commit", "Stage all changes and create a git commit.", "/commit [message]", lambda args: _handle_commit(dispatcher, args))


def _handle_commit(dispatcher, args: str) -> None:
    message = args.strip() or _suggest_commit_message(dispatcher)
    if not message:
        dispatcher.ui.display_message("Commit message is required.", style="yellow")
        return

    try:
        status = subprocess.run(
            ["git", "status", "--short"],
            cwd=dispatcher.session.workspace_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except Exception as exc:
        dispatcher.ui.display_message(f"Commit failed: {exc}", style="red")
        return

    if not status:
        dispatcher.ui.display_message("No changes to commit.", style="yellow")
        return

    try:
        subprocess.run(
            ["git", "add", "-A"],
            cwd=dispatcher.session.workspace_root,
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        completed = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=dispatcher.session.workspace_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        dispatcher.ui.display_message(f"Commit failed: {detail}", style="red")
        return

    output = completed.stdout.strip() or completed.stderr.strip() or f"Committed: {message}"
    dispatcher.ui.display_message(output, style="green")


def _suggest_commit_message(dispatcher) -> str:
    title = dispatcher.session.session_store.title
    if title:
        return f"refactor: {title[:64].strip()}"

    try:
        status = subprocess.run(
            ["git", "diff", "--stat", "--cached"],
            cwd=dispatcher.session.workspace_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout.strip()
        if not status:
            status = subprocess.run(
                ["git", "diff", "--stat"],
                cwd=dispatcher.session.workspace_root,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            ).stdout.strip()
    except Exception:
        status = ""

    first_line = status.splitlines()[0].strip() if status else ""
    if first_line:
        return f"chore: update {first_line[:48]}"
    return "chore: update workspace"
