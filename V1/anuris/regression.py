from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-._")
    return slug or "case"


@dataclass
class RegressionCase:
    name: str
    request_kind: str
    input: str
    assertions: Dict[str, Any]
    agent_mode: Optional[bool] = None
    session_overrides: Dict[str, Any] = field(default_factory=dict)
    attachment_paths: List[str] = field(default_factory=list)
    source_path: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any], source_path: str = "") -> "RegressionCase":
        if not isinstance(data, dict):
            raise ValueError("Case file must contain a JSON object")

        name = str(data.get("name", "") or "").strip()
        request_kind = str(data.get("request_kind", "") or "").strip()
        input_text = str(data.get("input", "") or "")
        assertions = data.get("assertions")

        if not name:
            raise ValueError("Case is missing required field: name")
        if request_kind not in {"message", "task"}:
            raise ValueError("Case request_kind must be 'message' or 'task'")
        if not input_text:
            raise ValueError("Case is missing required field: input")
        if not isinstance(assertions, dict) or not assertions:
            raise ValueError("Case assertions must be a non-empty object")

        allowed_assertions = {
            "final_text_contains",
            "final_text_not_contains",
            "transcript_contains",
            "event_type_present",
            "status_equals",
            "round_count_max",
        }
        unknown_assertions = sorted(set(assertions) - allowed_assertions)
        if unknown_assertions:
            raise ValueError(f"Unknown assertion types: {', '.join(unknown_assertions)}")

        session_overrides = data.get("session_overrides") or {}
        if not isinstance(session_overrides, dict):
            raise ValueError("session_overrides must be an object when provided")

        attachment_paths = data.get("attachments") or []
        if not isinstance(attachment_paths, list) or any(not isinstance(item, str) for item in attachment_paths):
            raise ValueError("attachments must be a list of strings when provided")

        agent_mode = data.get("agent_mode")
        if agent_mode is not None and not isinstance(agent_mode, bool):
            raise ValueError("agent_mode must be a boolean when provided")

        return cls(
            name=name,
            request_kind=request_kind,
            input=input_text,
            assertions=assertions,
            agent_mode=agent_mode,
            session_overrides=session_overrides,
            attachment_paths=attachment_paths,
            source_path=source_path,
        )

    @classmethod
    def from_file(cls, path: Path) -> "RegressionCase":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data, source_path=str(path))


@dataclass
class CaseResult:
    name: str
    request_kind: str
    passed: bool
    session_id: str
    session_status: str
    round_count: int
    final_text: str
    failures: List[str] = field(default_factory=list)
    events_path: str = ""
    transcript_path: str = ""
    source_path: str = ""
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RegressionRunResult:
    run_id: str
    total_cases: int
    passed_cases: int
    failed_cases: int
    results: List[CaseResult]
    run_dir: Path
    summary_path: Path
    report_path: Path


class HttpDebugAPIClient:
    """HTTP client for the local debug server."""

    def __init__(self, server_url: str):
        self.server_url = server_url.rstrip("/")

    def create_session(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/sessions", payload)

    def send_message(self, session_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", f"/sessions/{session_id}/message", payload)

    def send_task(self, session_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", f"/sessions/{session_id}/task", payload)

    def get_session(self, session_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/sessions/{session_id}")

    def get_events(self, session_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/sessions/{session_id}/events")

    def get_transcript(self, session_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/sessions/{session_id}/transcript")

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(f"{self.server_url}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} for {path}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Failed to reach debug server at {self.server_url}: {exc.reason}") from exc


class RegressionHarness:
    """Runs regression cases against the debug server and writes reports."""

    def __init__(self, client: Any, output_root: Path, fail_fast: bool = False):
        self.client = client
        self.output_root = Path(output_root)
        self.fail_fast = fail_fast

    def run(self, cases: Iterable[RegressionCase]) -> RegressionRunResult:
        case_list = list(cases)
        run_id = _utc_stamp()
        run_dir = self.output_root / run_id
        case_dir = run_dir / "cases"
        case_dir.mkdir(parents=True, exist_ok=True)

        results: List[CaseResult] = []
        for case in case_list:
            result = self._run_case(case)
            results.append(result)
            case_output = case_dir / f"{_slugify(case.name)}.json"
            case_output.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            if self.fail_fast and not result.passed:
                break

        passed_cases = sum(1 for result in results if result.passed)
        failed_cases = len(results) - passed_cases
        summary = {
            "run_id": run_id,
            "total_cases": len(results),
            "passed_cases": passed_cases,
            "failed_cases": failed_cases,
            "results": [result.to_dict() for result in results],
        }
        summary_path = run_dir / "summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        report_path = run_dir / "report.md"
        report_path.write_text(self._render_report(run_id, results), encoding="utf-8")

        return RegressionRunResult(
            run_id=run_id,
            total_cases=len(results),
            passed_cases=passed_cases,
            failed_cases=failed_cases,
            results=results,
            run_dir=run_dir,
            summary_path=summary_path,
            report_path=report_path,
        )

    def _run_case(self, case: RegressionCase) -> CaseResult:
        session_id = ""
        try:
            session_payload: Dict[str, Any] = {"session_name": case.name}
            if case.agent_mode is not None:
                session_payload["agent_mode"] = case.agent_mode
            session_payload.update(case.session_overrides)
            created = self.client.create_session(session_payload)
            session_id = str(created.get("session_id", "") or "")
            if not session_id:
                raise RuntimeError("Debug server did not return session_id")

            request_payload: Dict[str, Any] = {"attachments": case.attachment_paths}
            if case.request_kind == "message":
                request_payload["message"] = case.input
                response = self.client.send_message(session_id, request_payload)
            else:
                request_payload["task"] = case.input
                response = self.client.send_task(session_id, request_payload)

            session = self.client.get_session(session_id)
            events = self.client.get_events(session_id)
            transcript = self.client.get_transcript(session_id)
            failures = evaluate_assertions(case, response, session, events, transcript)
            return CaseResult(
                name=case.name,
                request_kind=case.request_kind,
                passed=not failures,
                session_id=session_id,
                session_status=str(session.get("status", "unknown")),
                round_count=int(response.get("round_count", 0) or 0),
                final_text=str(response.get("final_text", "") or ""),
                failures=failures,
                events_path=str(response.get("events_path", events.get("events_path", "")) or ""),
                transcript_path=str(response.get("transcript_path", transcript.get("transcript_path", "")) or ""),
                source_path=case.source_path,
            )
        except Exception as exc:
            return CaseResult(
                name=case.name,
                request_kind=case.request_kind,
                passed=False,
                session_id=session_id,
                session_status="error",
                round_count=0,
                final_text="",
                failures=[str(exc)],
                source_path=case.source_path,
                error=str(exc),
            )

    def _render_report(self, run_id: str, results: List[CaseResult]) -> str:
        passed = [result for result in results if result.passed]
        failed = [result for result in results if not result.passed]
        lines = [
            f"# Regression Run {run_id}",
            "",
            f"- Total: {len(results)}",
            f"- Passed: {len(passed)}",
            f"- Failed: {len(failed)}",
            "",
            "## Failed Cases",
            "",
        ]
        if not failed:
            lines.append("- None")
            lines.append("")
        else:
            for result in failed:
                lines.append(f"### {result.name}")
                lines.append("")
                lines.append(f"- Request kind: `{result.request_kind}`")
                lines.append(f"- Session ID: `{result.session_id or 'n/a'}`")
                lines.append(f"- Status: `{result.session_status}`")
                if result.transcript_path:
                    lines.append(f"- Transcript: `{result.transcript_path}`")
                if result.events_path:
                    lines.append(f"- Events: `{result.events_path}`")
                for failure in result.failures:
                    lines.append(f"- Failure: {failure}")
                lines.append("")
        lines.append("## Passed Cases")
        lines.append("")
        if not passed:
            lines.append("- None")
            lines.append("")
        else:
            for result in passed:
                lines.append(f"- {result.name} (`{result.request_kind}`) session=`{result.session_id}`")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def discover_case_files(cases_dir: Path, case_name: Optional[str] = None) -> List[Path]:
    base_dir = Path(cases_dir)
    if case_name:
        direct = base_dir / f"{case_name}.json"
        if direct.exists():
            return [direct]
        matches = sorted(path for path in base_dir.glob("*.json") if path.stem == case_name)
        if matches:
            return matches
        raise FileNotFoundError(f"Regression case not found: {case_name}")

    case_files = sorted(base_dir.glob("*.json"))
    if not case_files:
        raise FileNotFoundError(f"No regression cases found in {base_dir}")
    return case_files


def load_cases(cases_dir: Path, case_name: Optional[str] = None) -> List[RegressionCase]:
    return [RegressionCase.from_file(path) for path in discover_case_files(cases_dir, case_name=case_name)]


def _normalize_to_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise ValueError("Assertion values must be a string or list of strings")


def evaluate_assertions(
    case: RegressionCase,
    response: Dict[str, Any],
    session: Dict[str, Any],
    events: Dict[str, Any],
    transcript: Dict[str, Any],
) -> List[str]:
    failures: List[str] = []
    assertions = case.assertions
    final_text = str(response.get("final_text", "") or "")
    transcript_text = str(transcript.get("transcript", "") or "")
    event_types = [str(item.get("type", "")) for item in events.get("events", []) if isinstance(item, dict)]
    session_status = str(session.get("status", "") or "")
    round_count = int(response.get("round_count", 0) or 0)

    for expected in _normalize_to_list(assertions.get("final_text_contains")):
        if expected not in final_text:
            failures.append(f"final_text must contain: {expected}")
    for blocked in _normalize_to_list(assertions.get("final_text_not_contains")):
        if blocked in final_text:
            failures.append(f"final_text must not contain: {blocked}")
    for expected in _normalize_to_list(assertions.get("transcript_contains")):
        if expected not in transcript_text:
            failures.append(f"transcript must contain: {expected}")
    for expected in _normalize_to_list(assertions.get("event_type_present")):
        if expected not in event_types:
            failures.append(f"event type must be present: {expected}")

    if "status_equals" in assertions:
        expected_status = str(assertions["status_equals"])
        if session_status != expected_status:
            failures.append(f"status must equal: {expected_status} (actual: {session_status})")

    if "round_count_max" in assertions:
        try:
            maximum = int(assertions["round_count_max"])
        except (TypeError, ValueError) as exc:
            raise ValueError("round_count_max must be an integer") from exc
        if round_count > maximum:
            failures.append(f"round_count must be <= {maximum} (actual: {round_count})")

    return failures


def build_arg_parser() -> argparse.ArgumentParser:
    root_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Run regression cases against the Anuris debug server")
    parser.add_argument("--server-url", default="http://127.0.0.1:8765", help="Base URL of the debug server")
    parser.add_argument(
        "--cases-dir",
        default=str(root_dir / "regression_cases"),
        help="Directory containing regression case JSON files",
    )
    parser.add_argument("--case", help="Run only one named case (file stem)")
    parser.add_argument(
        "--output-dir",
        default=str(root_dir / ".anuris_regression_runs"),
        help="Directory where regression reports are written",
    )
    parser.add_argument("--fail-fast", action="store_true", help="Stop after the first failing case")
    return parser


def main(argv: Optional[List[str]] = None, client: Optional[Any] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    cases = load_cases(Path(args.cases_dir), case_name=args.case)
    harness = RegressionHarness(
        client=client or HttpDebugAPIClient(args.server_url),
        output_root=Path(args.output_dir),
        fail_fast=args.fail_fast,
    )
    result = harness.run(cases)

    print(
        f"Regression run {result.run_id}: total={result.total_cases} passed={result.passed_cases} failed={result.failed_cases}"
    )
    print(f"Summary: {result.summary_path}")
    print(f"Report: {result.report_path}")
    return 1 if result.failed_cases else 0


if __name__ == "__main__":
    raise SystemExit(main())
