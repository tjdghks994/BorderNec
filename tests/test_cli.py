from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from bordernec.cli import _load_results
from bordernec.schema import load_benchmark

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> dict:
        completed = subprocess.run([sys.executable, "-m", "bordernec", *args],
            cwd=ROOT, text=True, capture_output=True, check=True)
        return json.loads(completed.stdout)

    def test_demo_result_and_rejection_trace(self) -> None:
        report = self.run_cli("demo", "--verified-results", "examples/verified_results.json")
        task = report["tasks"][0]
        self.assertEqual(task["model_calls_used"], 5)
        self.assertEqual(task["initial"]["observation_count"], 2)
        self.assertEqual(task["final"]["observation_count"], 0)
        self.assertTrue(task["final"]["success"])

    def test_exact_all_tasks_and_signature_filter(self) -> None:
        report = self.run_cli("exact")
        self.assertEqual(len(report["tasks"]), 12)
        task = next(task for task in report["tasks"] if task["task_id"] == "B02_hotel_search_oversharing")
        self.assertEqual(task["frontier"], [["search_minimal"]])
        report = self.run_cli("exact", "--task", "B02_hotel_search_oversharing",
                              "--verified-results", "examples/verified_results.json")
        self.assertEqual(report["result_verification"], "trusted_result_registry")
        self.assertEqual(report["tasks"][0]["frontier"], [["search_minimal"]])

    def test_local_command_backend_plan_and_stepwise(self) -> None:
        command = json.dumps([sys.executable, "examples/local_generator.py"])
        for mode in ("plan", "stepwise"):
            report = self.run_cli("search", "--task", "B02_hotel_search_oversharing",
                                  "--command", command, "--budget", "3", "--generation-mode", mode)
            self.assertEqual(report["tasks"][0]["model_calls_used"], 3)
            self.assertEqual(report["tasks"][0]["final"]["steps"], ["search_minimal"])

    def test_invalid_configuration_reports_error(self) -> None:
        for args in (("search",), ("exact", "--task", "unknown"),
                     ("demo", "--budget", "0"), ("search", "--command", '"python"')):
            completed = subprocess.run([sys.executable, "-m", "bordernec", *args],
                cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(completed.returncode, 2)
            self.assertNotIn("Traceback", completed.stderr)

    def test_registry_duplicate_and_unknown_task_are_rejected(self) -> None:
        domains = {task.id: task for task in load_benchmark(ROOT / "examples/pilot_12.json").tasks}
        row = dict(task_id="B02_hotel_search_oversharing", steps=["search_minimal"], signature="A")
        for rows in ([row, row], [row | {"task_id": "unknown"}], [row | {"signature": ""}]):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "results.json"
                path.write_text(json.dumps({"results": rows}))
                with self.assertRaises(ValueError):
                    _load_results(path, domains)


if __name__ == "__main__":
    unittest.main()
