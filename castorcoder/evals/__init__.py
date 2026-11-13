"""Local, reproducible evaluation harness for CastorCoder."""

from .metrics import evaluate_ranking, gold_matches_code, gold_matches_memory
from .models import EvalSuite, EvalTask, ScriptedStep, TaskResult, load_suite
from .report import print_summary, write_report
from .runner import EvalOptions, run_suite

__all__ = [
    "EvalOptions",
    "EvalSuite",
    "EvalTask",
    "ScriptedStep",
    "TaskResult",
    "evaluate_ranking",
    "gold_matches_code",
    "gold_matches_memory",
    "load_suite",
    "print_summary",
    "run_suite",
    "write_report",
]
