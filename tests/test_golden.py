from __future__ import annotations

import json
import unittest
from pathlib import Path

from bordernec.planner import enumerate_successful_plans
from bordernec.schema import Plan, load_benchmark
from bordernec.semantics import (
    TransferEvent,
    analyze_plan,
    compare_footprints,
    footprint_signature,
    jurisdictional_frontier,
)


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "examples/pilot_12.json"
GOLDEN = ROOT / "examples/pilot_12_golden.json"


def golden_observations(
    rows: list[dict[str, object]],
) -> frozenset[TransferEvent]:
    result: set[TransferEvent] = set()
    for row in rows:
        values = dict(row)
        values.pop("count", None)
        result.add(TransferEvent(**values))  # type: ignore[arg-type]
    return frozenset(result)


class GoldenIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        benchmark = load_benchmark(BENCHMARK)
        cls.tasks = {task.id: task for task in benchmark.tasks}
        cls.golden = json.loads(GOLDEN.read_text(encoding="utf-8"))["tasks"]

    def test_all_tasks_have_independent_golden_fixture(self) -> None:
        self.assertEqual(set(self.tasks), set(self.golden))

    def test_golden_events_outcomes_relations_and_frontier(self) -> None:
        for task_id, fixture in self.golden.items():
            task = self.tasks[task_id]
            analyses = {}
            for plan_name, steps in fixture["plans"].items():
                analysis = analyze_plan(
                    task, Plan(tuple(steps), source="golden")
                )
                analyses[plan_name] = analysis
                with self.subTest(task=task_id, plan=plan_name, kind="events"):
                    self.assertEqual(
                        analysis.footprint,
                        golden_observations(fixture["events"][plan_name]),
                    )
                expected_success = fixture["task_success"][plan_name]
                with self.subTest(task=task_id, plan=plan_name, kind="outcome"):
                    self.assertEqual(
                        analysis.execution.success, expected_success
                    )

            for relation in fixture["relations"]:
                actual = compare_footprints(
                    analyses[relation["left"]].footprint,
                    analyses[relation["right"]].footprint,
                )
                with self.subTest(task=task_id, relation=relation):
                    self.assertEqual(actual, relation["label"])

            reference = enumerate_successful_plans(task)
            reference_analyses = [
                analyze_plan(task, plan) for plan in reference.plans
            ]
            frontier_signatures = {
                footprint_signature(item.footprint)
                for item in jurisdictional_frontier(reference_analyses)
            }
            expected_frontier = set(fixture["frontier"])
            for plan_name, analysis in analyses.items():
                if not fixture["task_success"][plan_name]:
                    continue
                with self.subTest(
                    task=task_id, plan=plan_name, kind="frontier"
                ):
                    self.assertEqual(
                        footprint_signature(analysis.footprint)
                        in frontier_signatures,
                        plan_name in expected_frontier,
                    )


if __name__ == "__main__":
    unittest.main()
