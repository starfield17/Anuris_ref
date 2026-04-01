from __future__ import annotations

import shlex
import shutil
import subprocess


def _git_command(dispatcher, args: list[str], fallback: str) -> str:
    try:
        completed = subprocess.run(
            args,
            cwd=dispatcher.session.workspace_root,
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        output = completed.stdout.strip() or completed.stderr.strip()
        return output or fallback
    except Exception:
        return fallback


def register_analysis_commands(dispatcher) -> None:
    dispatcher._register("cost", "Show local session usage accounting.", "/cost", lambda args: _handle_cost(dispatcher, args))
    dispatcher._register("diff", "Show the current git diff.", "/diff [full|pathspec]", lambda args: _handle_diff(dispatcher, args))
    dispatcher._register("review", "Run a local AI code review over the current diff or PR.", "/review [pr_number]", lambda args: _handle_review(dispatcher, args))
    dispatcher._register("plan", "Enter plan mode or ask the model to draft a plan.", "/plan [open|show|description]", lambda args: _handle_plan(dispatcher, args))


def _handle_cost(dispatcher, args: str) -> None:
    del args
    dispatcher.ui.display_message(dispatcher.session.services.usage_tracker.render(), style="cyan")


def _handle_diff(dispatcher, args: str) -> None:
    raw = args.strip()
    if raw in {"full", "--full"}:
        output = _git_command(dispatcher, ["git", "diff", "--no-ext-diff"], "(no diff)")
    elif raw:
        pathspec = shlex.split(raw)
        output = _git_command(dispatcher, ["git", "diff", "--stat", "--no-ext-diff", "--", *pathspec], "(no diff)")
    else:
        output = _git_command(dispatcher, ["git", "diff", "--stat", "--no-ext-diff"], "(no diff)")
    dispatcher.ui.display_message(output, style="cyan")


def _handle_review(dispatcher, args: str) -> None:
    raw = args.strip()
    if raw.isdigit() and shutil.which("gh"):
        diff_text = _git_command(dispatcher, ["gh", "pr", "diff", raw], "(no PR diff)")
        review_target = f"pull request #{raw}"
    else:
        diff_text = _git_command(dispatcher, ["git", "diff", "--no-ext-diff"], "(no diff)")
        review_target = "the current working tree diff"

    prompt = f"""
You are performing a local code review for {review_target}.

Review priorities:
- correctness
- regressions
- missing tests
- security or data-loss risk
- interface or behavior changes

If there are no findings, say so explicitly and mention residual risk.

Diff:
```diff
{diff_text[:12000]}
```
""".strip()
    dispatcher.session.run_prompt_command("review", prompt)


def _handle_plan(dispatcher, args: str) -> None:
    raw = args.strip()
    if not raw or raw == "open":
        dispatcher.session.services.permission_manager.set_mode("plan")
        dispatcher.ui.display_message("Plan mode enabled.", style="green")
        return
    if raw == "show":
        dispatcher.ui.display_message(dispatcher.session.services.permission_manager.render(), style="cyan")
        return

    dispatcher.session.services.permission_manager.set_mode("plan")
    prompt = f"""
Produce a concrete implementation plan for this task in the current repository.

Task:
{raw}

The plan should focus on:
- major code changes
- key interfaces
- tests to add or update
- main risks or edge cases
""".strip()
    dispatcher.session.run_prompt_command("plan", prompt)
