from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from typing import Any

from . import __version__
from .local_model import (
    CommandBackend, LocalBackend, _tool_view, generate_counterplan_descent,
)
from .planner import enumerate_successful_plans
from .schema import Plan, SchemaError, TaskDomain, load_benchmark
from .semantics import (
    SEMANTICS_VERSION, analyze_plan, find_dominating_witness,
    footprint_to_rows, jurisdictional_frontier,
)


class ScriptedDemoBackend(LocalBackend):
    """Replay five fixed proposals to demonstrate acceptance and rejection."""

    def __init__(self, domain: TaskDomain) -> None:
        self.domain = domain
        self.outputs = iter([
            ("search_with_identity",),
            ("book_instead_of_search",),
            ("search_minimal",),
            ("search_with_identity",),
            ("search_minimal",),
        ])

    def generate(self, prompt: str, seed: int) -> str:
        _, aliases = _tool_view(self.domain, seed)
        inverse = {action: alias for alias, action in aliases.items()}
        return json.dumps({"steps": [inverse[action] for action in next(self.outputs)]})


def _load_results(path: Path, domains: dict[str, TaskDomain]) -> dict[str, dict[tuple[str, ...], str]]:
    """Read a trusted per-task result registry supplied by an executor."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("results"), list):
        raise ValueError("result registry must contain a 'results' list")
    result: dict[str, dict[tuple[str, ...], str]] = {}
    for row in raw["results"]:
        if not isinstance(row, dict):
            raise ValueError("each result registry entry must be an object")
        task_id, steps, signature = row.get("task_id"), row.get("steps"), row.get("signature")
        if not isinstance(task_id, str) or task_id not in domains:
            raise ValueError("result registry contains an unknown task_id")
        if not isinstance(steps, list) or not all(isinstance(step, str) for step in steps):
            raise ValueError("result registry steps must be a list of strings")
        if not isinstance(signature, str) or not signature:
            raise ValueError("result registry signature must be a non-empty string")
        if not analyze_plan(domains[task_id], Plan(tuple(steps))).execution.success:
            raise ValueError(f"{task_id}: registry plan fails the task contract")
        entries = result.setdefault(task_id, {})
        if tuple(steps) in entries:
            raise ValueError(f"{task_id}: duplicate result registry plan")
        entries[tuple(steps)] = signature
    return result


def _analysis_rows(domain: TaskDomain, plan: Plan | None) -> dict[str, Any] | None:
    if plan is None:
        return None
    analysis = analyze_plan(domain, plan)
    return {
        "steps": list(plan.steps),
        "success": analysis.execution.success,
        "observations": footprint_to_rows(analysis.footprint),
        "observation_count": len(analysis.footprint),
    }


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main() -> None:
    parser = argparse.ArgumentParser(description="Reproduce BorderNec witness search and exact pruning.")
    parser.add_argument("mode", choices=("demo", "exact", "search"))
    parser.add_argument("--benchmark", type=Path, default=Path("examples/pilot_12.json"))
    parser.add_argument("--task")
    parser.add_argument("--verified-results", type=Path)
    parser.add_argument("--budget", type=_positive, default=5)
    parser.add_argument("--seed", type=int, default=19)
    parser.add_argument("--generation-mode", choices=("plan", "stepwise"), default="plan")
    parser.add_argument("--max-nodes", type=_positive, default=100_000)
    parser.add_argument("--command", help='JSON argument list for a local generator, e.g. ["python", "generator.py"]')
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        benchmark = load_benchmark(args.benchmark)
        domains = {domain.id: domain for domain in benchmark.tasks}
        registries = _load_results(args.verified_results, domains) if args.verified_results else None
        task_id = args.task or ("B02_hotel_search_oversharing" if args.mode == "demo" else None)
        if task_id is not None and task_id not in domains:
            raise ValueError(f"unknown task {task_id!r}")
        tasks = [domains[task_id]] if task_id else list(benchmark.tasks)
        backend: LocalBackend | None = None
        if args.mode == "search":
            if not args.command:
                raise ValueError("search requires --command")
            command = json.loads(args.command)
            if not isinstance(command, list) or not command or not all(isinstance(part, str) for part in command):
                raise ValueError("--command must be a non-empty JSON list of strings")
            backend = CommandBackend(command)
        if args.mode == "demo":
            if task_id != "B02_hotel_search_oversharing" or args.budget != 5 or args.generation_mode != "plan":
                raise ValueError("demo uses B02, five calls, and plan generation; use search for custom settings")
            backend = ScriptedDemoBackend(tasks[0])
        rows = []
        for domain in tasks:
            results = registries.get(domain.id, {}) if registries is not None else None
            if args.mode == "exact":
                enumeration = enumerate_successful_plans(domain, max_nodes=args.max_nodes)
                analyses = [analyze_plan(domain, plan) for plan in enumeration.plans]
                frontier = jurisdictional_frontier(analyses, verified_results=results)
                candidates = []
                for analysis in analyses:
                    witness = find_dominating_witness(analysis, analyses, verified_results=results)
                    row = _analysis_rows(domain, analysis.plan)
                    assert row is not None
                    row.update({
                        "verified": results is None or analysis.plan.steps in results,
                        "result_signature": results.get(analysis.plan.steps) if results is not None else "symbolic_outcome_contract",
                        "kept": analysis in frontier,
                        "witness_steps": list(witness.plan.steps) if witness else None,
                        "removed_observations": footprint_to_rows(analysis.footprint - witness.footprint) if witness else [],
                    })
                    candidates.append(row)
                rows.append({"task_id": domain.id, "enumeration": asdict(enumeration.stats),
                             "frontier": [list(item.plan.steps) for item in frontier], "plans": candidates})
            else:
                assert backend is not None
                run = generate_counterplan_descent(domain, backend, args.budget, args.seed,
                    generation_mode=args.generation_mode, verified_results=results)
                rows.append({"task_id": domain.id, "run": asdict(run),
                    "initial": _analysis_rows(domain, run.initial_incumbent),
                    "final": _analysis_rows(domain, run.final_incumbent),
                    "model_calls_used": sum(attempt.model_calls for attempt in run.attempts)})
        report = {"bordernec_version": __version__, "semantics_version": SEMANTICS_VERSION,
                  "mode": args.mode, "seed": args.seed,
                  "generator": backend.metadata() if backend else None,
                  "result_verification": "trusted_result_registry" if registries is not None else "symbolic_outcome_contract",
                  "benchmark_sha256": hashlib.sha256(args.benchmark.read_bytes()).hexdigest(),
                  "result_registry_sha256": hashlib.sha256(args.verified_results.read_bytes()).hexdigest() if args.verified_results else None,
                  "tasks": rows}
        serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(serialized, encoding="utf-8")
            print(args.out)
        else:
            print(serialized, end="")
    except (OSError, ValueError, SchemaError, RuntimeError) as exc:
        parser.exit(2, f"bordernec: {exc}\n")
