"""BorderNec same-result strict-subset witness search."""

from .local_model import LocalBackend, CommandBackend, generate_counterplan_descent
from .planner import enumerate_successful_plans
from .schema import Benchmark, Plan, TaskDomain, load_benchmark
from .semantics import (
    TransferEvent, analyze_plan, find_dominating_witness,
    is_same_result_witness, jurisdictional_frontier,
)

__version__ = "0.1.0"
__all__ = [
    "Benchmark", "CommandBackend", "LocalBackend", "Plan", "TaskDomain",
    "TransferEvent", "analyze_plan", "enumerate_successful_plans",
    "find_dominating_witness", "generate_counterplan_descent",
    "is_same_result_witness", "jurisdictional_frontier", "load_benchmark",
]
