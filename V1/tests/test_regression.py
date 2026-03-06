import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from anuris.regression import (
    RegressionCase,
    RegressionHarness,
    build_arg_parser,
    discover_case_files,
    evaluate_assertions,
    load_cases,
    main,
)


class FakeClient:
    def __init__(self, scenarios):
        self.scenarios = scenarios
        self.created_sessions = []

    def create_session(self, payload):
        name = payload.get("session_name", "case")
        session_id = f"sess_{len(self.created_sessions) + 1}"
        self.created_sessions.append((session_id, name, payload))
        scenario = self.scenarios[name]
        scenario["session_id"] = session_id
        return {"session_id": session_id}

    def send_message(self, session_id, payload):
        return self._response_for(session_id, payload)

    def send_task(self, session_id, payload):
        return self._response_for(session_id, payload)

    def get_session(self, session_id):
        scenario = self._scenario_by_session(session_id)
        return {"session_id": session_id, "status": scenario.get("status", "idle")}

    def get_events(self, session_id):
        scenario = self._scenario_by_session(session_id)
        return {"session_id": session_id, "events": scenario.get("events", [])}

    def get_transcript(self, session_id):
        scenario = self._scenario_by_session(session_id)
        return {"session_id": session_id, "transcript": scenario.get("transcript", "")}

    def _response_for(self, session_id, payload):
        scenario = self._scenario_by_session(session_id)
        response = dict(scenario.get("response", {}))
        response.setdefault("request_kind", "message")
        response.setdefault("events_path", scenario.get("events_path", "/tmp/events.jsonl"))
        response.setdefault("transcript_path", scenario.get("transcript_path", "/tmp/transcript.md"))
        response.setdefault("received_payload", payload)
        return response

    def _scenario_by_session(self, session_id):
        for scenario in self.scenarios.values():
            if scenario.get("session_id") == session_id:
                return scenario
        raise AssertionError(f"Unknown session id: {session_id}")


class RegressionHarnessTests(unittest.TestCase):
    def test_case_validation_rejects_missing_fields(self):
        with self.assertRaises(ValueError) as ctx:
            RegressionCase.from_dict({"request_kind": "message", "input": "hi", "assertions": {}})
        self.assertIn("name", str(ctx.exception))

    def test_evaluate_assertions_reports_multiple_failures(self):
        case = RegressionCase.from_dict(
            {
                "name": "bad_case",
                "request_kind": "message",
                "input": "hello",
                "assertions": {
                    "final_text_contains": ["ok"],
                    "event_type_present": ["assistant_message"],
                    "status_equals": "idle",
                    "round_count_max": 1,
                },
            }
        )
        failures = evaluate_assertions(
            case,
            response={"final_text": "nope", "round_count": 3},
            session={"status": "failed"},
            events={"events": [{"type": "request_started"}]},
            transcript={"transcript": "trace"},
        )
        self.assertEqual(len(failures), 4)

    def test_harness_writes_summary_and_case_reports(self):
        cases = [
            RegressionCase.from_dict(
                {
                    "name": "status_ok",
                    "request_kind": "message",
                    "input": "/agent status",
                    "assertions": {
                        "final_text_contains": "Agent mode: ON",
                        "event_type_present": "assistant_message",
                        "status_equals": "idle",
                    },
                }
            ),
            RegressionCase.from_dict(
                {
                    "name": "status_fail",
                    "request_kind": "task",
                    "input": "inspect",
                    "assertions": {"final_text_contains": "finished"},
                }
            ),
        ]
        client = FakeClient(
            {
                "status_ok": {
                    "response": {
                        "final_text": "Agent mode: ON",
                        "round_count": 0,
                        "request_kind": "message",
                    },
                    "events": [{"type": "assistant_message"}],
                    "transcript": "### Assistant\n\nAgent mode: ON",
                    "status": "idle",
                },
                "status_fail": {
                    "response": {
                        "final_text": "not done",
                        "round_count": 2,
                        "request_kind": "task",
                    },
                    "events": [{"type": "assistant_message"}],
                    "transcript": "### Assistant\n\nnot done",
                    "status": "idle",
                },
            }
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            harness = RegressionHarness(client=client, output_root=Path(tmp_dir), fail_fast=False)
            result = harness.run(cases)

            self.assertEqual(result.total_cases, 2)
            self.assertEqual(result.passed_cases, 1)
            self.assertEqual(result.failed_cases, 1)
            self.assertTrue(result.summary_path.exists())
            self.assertTrue(result.report_path.exists())
            self.assertTrue((result.run_dir / "cases" / "status_ok.json").exists())
            summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["failed_cases"], 1)
            report = result.report_path.read_text(encoding="utf-8")
            self.assertIn("status_fail", report)
            self.assertIn("Failure:", report)

    def test_fail_fast_stops_after_first_failure(self):
        cases = [
            RegressionCase.from_dict(
                {
                    "name": "first_fail",
                    "request_kind": "message",
                    "input": "hello",
                    "assertions": {"final_text_contains": "done"},
                }
            ),
            RegressionCase.from_dict(
                {
                    "name": "second_case",
                    "request_kind": "message",
                    "input": "world",
                    "assertions": {"final_text_contains": "world"},
                }
            ),
        ]
        client = FakeClient(
            {
                "first_fail": {
                    "response": {"final_text": "nope", "round_count": 0},
                    "events": [],
                    "transcript": "",
                    "status": "idle",
                },
                "second_case": {
                    "response": {"final_text": "world", "round_count": 0},
                    "events": [],
                    "transcript": "world",
                    "status": "idle",
                },
            }
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            result = RegressionHarness(client=client, output_root=Path(tmp_dir), fail_fast=True).run(cases)

        self.assertEqual(result.total_cases, 1)
        self.assertEqual(len(client.created_sessions), 1)

    def test_load_cases_and_main_support_case_filter(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cases_dir = Path(tmp_dir) / "cases"
            cases_dir.mkdir()
            (cases_dir / "one.json").write_text(
                json.dumps(
                    {
                        "name": "one",
                        "request_kind": "message",
                        "input": "/agent status",
                        "assertions": {"final_text_contains": "Agent mode: ON"},
                    }
                ),
                encoding="utf-8",
            )
            (cases_dir / "two.json").write_text(
                json.dumps(
                    {
                        "name": "two",
                        "request_kind": "message",
                        "input": "/agent status",
                        "assertions": {"final_text_contains": "Agent mode: ON"},
                    }
                ),
                encoding="utf-8",
            )
            output_dir = Path(tmp_dir) / "runs"
            client = FakeClient(
                {
                    "one": {
                        "response": {"final_text": "Agent mode: ON", "round_count": 0},
                        "events": [{"type": "assistant_message"}],
                        "transcript": "Agent mode: ON",
                        "status": "idle",
                    }
                }
            )

            files = discover_case_files(cases_dir, case_name="one")
            self.assertEqual([path.name for path in files], ["one.json"])
            loaded = load_cases(cases_dir, case_name="one")
            self.assertEqual([case.name for case in loaded], ["one"])

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(
                    [
                        "--cases-dir",
                        str(cases_dir),
                        "--case",
                        "one",
                        "--output-dir",
                        str(output_dir),
                    ],
                    client=client,
                )
            self.assertEqual(rc, 0)
            self.assertIn("total=1 passed=1 failed=0", stdout.getvalue())
            self.assertTrue(any(output_dir.iterdir()))

    def test_build_arg_parser_has_expected_defaults(self):
        parser = build_arg_parser()
        args = parser.parse_args([])
        self.assertEqual(args.server_url, "http://127.0.0.1:8765")
        self.assertTrue(str(args.cases_dir).endswith("regression_cases"))


if __name__ == "__main__":
    unittest.main()
