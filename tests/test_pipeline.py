"""Offline verification: synthetic SBA rows and mocked HTTP/model boundaries only."""
from contextlib import ExitStack, redirect_stdout
from datetime import date
from io import StringIO
import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "examples" / "fixtures"


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 11, 6)


class OfflinePipelineTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(
            socket.socket, "connect", side_effect=AssertionError("Live network forbidden")
        ))
        self.stack.enter_context(patch.dict(os.environ, {"OPENROUTER": "offline-placeholder"}))
        # Never load a developer's .env; this test uses no genuine credentials.
        self.stack.enter_context(patch("dotenv.load_dotenv", return_value=False))
        spec = importlib.util.spec_from_file_location("pipeline_under_test", ROOT / "pipeline.py")
        self.pipeline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.pipeline)
        self.stack.enter_context(patch.object(self.pipeline, "date", FixedDate))
        self.stack.enter_context(redirect_stdout(StringIO()))

    def download(self, url, timeout):
        self.assertEqual(timeout, 120)
        csv = (ROOT / "examples" / "synthetic_loans.csv").read_text()
        if url != next(iter(self.pipeline.SBA_URLS.values())):
            csv = csv.splitlines()[0] + "\n"
        return SimpleNamespace(text=csv, raise_for_status=lambda: None)

    def test_market_and_refinance_filters(self):
        with patch.object(self.pipeline.requests, "get", side_effect=self.download) as http:
            market = self.pipeline.fetch_and_filter()
        self.assertEqual(http.call_count, 2)
        self.assertEqual(len(market), 7)
        payload = self.pipeline.build_ingest_payload(market)
        self.assertEqual(payload["new_loans_count"], 1)
        self.assertEqual(payload["refi_candidates_count"], 1)
        refi = payload["refi_candidates"][0]
        self.assertEqual(refi["borrower_name"], "SYNTHETIC CEDAR TOOLWORKS LLC")
        self.assertEqual(refi["months_since_approval"], 26)
        self.assertEqual(refi["rate_delta"], 2.0)
        self.assertEqual(refi["estimated_maturity_date"], "2034-09-06")
        self.assertEqual(payload["new_loans"][0]["county"], "ST. LOUIS")

    def test_fenced_json_and_empty_market_halt(self):
        self.assertEqual(self.pipeline.extract_json('```json\n{"synthetic": true}\n```'), {"synthetic": True})
        with self.assertRaises(SystemExit) as error:
            self.pipeline.halt_if_empty({"new_loans": [], "refi_candidates": []})
        self.assertEqual(error.exception.code, 1)

    def test_offline_end_to_end_writes_three_artifacts(self):
        contract2 = json.loads((FIXTURES / "contract2_synthetic.json").read_text())
        report = (ROOT / "examples" / "report_synthetic.md").read_text()
        stages = []

        def model_response(**kwargs):
            system, user = [message["content"] for message in kwargs["messages"]]
            self.assertEqual(kwargs["temperature"], 0.1)
            if "Ingest & Filter Agent" in system:
                stages.append("ingest")
                chunk = json.JSONDecoder().raw_decode(user[user.index("{"):])[0]
                # Return the structured synthetic records, as an ingest model would.
                for record in chunk["new_loans"] + chunk["refi_candidates"]:
                    self.assertTrue(record["borrower_name"].startswith("SYNTHETIC "))
                    self.assertGreaterEqual(record["gross_approval"], 500000)
                content = json.dumps({key: chunk[key] for key in ("new_loans", "refi_candidates")})
            elif "Analysis Agent" in system:
                stages.append("analysis")
                c1 = json.loads(user.split("\n\n", 1)[1])
                self.assertEqual(c1["meta"]["total_new_loans"], 1)
                self.assertEqual(c1["meta"]["total_refi_candidates"], 1)
                self.assertEqual(c1["refi_candidates"][0]["gross_approval"], 750000)
                content = "```json\n" + json.dumps(contract2) + "\n```"
            elif "Synthesis Agent" in system:
                stages.append("synthesis")
                c2 = json.loads(user.split("\n\n", 1)[1])
                self.assertEqual(c2, contract2)
                content = report
            else:
                self.fail("Unexpected model stage")
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=content), finish_reason="stop"
            )])

        with tempfile.TemporaryDirectory(prefix="pipeline-offline-") as workdir:
            previous = Path.cwd()
            try:
                os.chdir(workdir)
                with patch.object(self.pipeline.requests, "get", side_effect=self.download) as http, \
                     patch.object(self.pipeline.client.chat.completions, "create", side_effect=model_response) as model:
                    self.pipeline.run_pipeline("SYNTHETIC-2026-Q4")
                output = Path("pipeline_runs/SYNTHETIC-2026-Q4")
                self.assertEqual(sorted(p.name for p in output.iterdir()), ["contract1.json", "contract2.json", "report.md"])
                c1 = json.loads((output / "contract1.json").read_text())
                self.assertEqual(c1["meta"]["total_refi_candidates"], 1)
                self.assertEqual(json.loads((output / "contract2.json").read_text()), contract2)
                self.assertEqual((output / "report.md").read_text(), report)
                self.assertIn("SYNTHETIC CEDAR TOOLWORKS LLC", report)
                self.assertEqual(http.call_count, 2)
                self.assertEqual(model.call_count, 4)
                self.assertEqual(stages, ["ingest", "ingest", "analysis", "synthesis"])
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
