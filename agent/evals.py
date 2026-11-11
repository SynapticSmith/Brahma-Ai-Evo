"""Scenario evaluation engine and capability scorecard for the Brahma Agent Harness.

Standard-library only: imports nothing from PyQt, provider SDKs, or Windows COM.
Evaluates agent decision quality, tool choice, multi-step execution, recovery,
and safety constraints against deterministic or model-backed providers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional

from agent.permissions import PermissionPolicy
from agent.providers import build_completer
from agent.runtime import AgentRuntime
from agent.trace import TraceCollector, TraceSink


# -- Scenario Definition ----------------------------------------------------

VALID_CATEGORIES = ("tool_choice", "multi_step", "recovery", "safety", "memory")


@dataclass
class Scenario:
    """A single deterministic evaluation scenario."""

    id: str
    name: str
    category: str
    prompt: str
    description: str = ""
    expected_tools: List[str] = field(default_factory=list)
    forbidden_tools: List[str] = field(default_factory=list)
    expected_text: List[str] = field(default_factory=list)
    forbidden_text: List[str] = field(default_factory=list)
    expected_stop_reason: Optional[str] = "completed"
    max_turns: int = 5
    setup_files: Dict[str, str] = field(default_factory=dict)
    verify_files: Dict[str, str] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.category not in VALID_CATEGORIES:
            raise ValueError(f"Scenario category {self.category!r} must be one of {VALID_CATEGORIES}")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Scenario:
        clean = dict(data)
        return cls(**clean)


@dataclass
class ScenarioResult:
    """Outcome of running a single scenario."""

    scenario_id: str
    category: str
    passed: bool
    duration_ms: float
    turns: int
    tools_called: List[str]
    failures: List[str] = field(default_factory=list)
    trace_id: Optional[str] = None
    stop_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EvalSuiteResult:
    """Aggregated results across all executed scenarios."""

    total: int
    passed: int
    failed: int
    score: float
    duration_ms: float
    by_category: Dict[str, Dict[str, Any]]
    results: List[ScenarioResult]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "score": round(self.score, 4),
            "duration_ms": round(self.duration_ms, 2),
            "by_category": self.by_category,
            "results": [r.to_dict() for r in self.results],
        }

    def summary_text(self) -> str:
        lines = [
            f"Brahma Agent Capability Evaluation: {self.passed}/{self.total} passed ({self.score * 100.0:.1f}%) in {self.duration_ms:.1f}ms",
            "Category breakdown:",
        ]
        for cat, stats in sorted(self.by_category.items()):
            c_score = stats["score"] * 100.0 if stats["total"] else 0.0
            lines.append(f"  - {cat:<14}: {stats['passed']}/{stats['total']} ({c_score:.1f}%)")
        if self.failed > 0:
            lines.append("Failures:")
            for r in self.results:
                if not r.passed:
                    lines.append(f"  * [{r.scenario_id}] {'; '.join(r.failures)}")
        return "\n".join(lines)


# -- Evaluation Runner ------------------------------------------------------

def eval_policy() -> PermissionPolicy:
    """The fixed permission policy every evaluation scenario runs under.

    A scorecard that changes with the operator's local
    ``agent_permissions.json`` is not a scorecard: the same scenario would
    pass on one machine and fail on another for reasons the agent did not
    control, and a safety scenario could "pass" only because the local policy
    happened to deny the tool it was probing. Building from ``from_dict({})``
    uses the built-in defaults and never reads user config.

    Exec stays denied in headless mode, exactly as ``agent run`` withholds it:
    a scenario file is data, and ``eval`` must not grant a shell that ``run``
    does not. A caller that needs more (or less) can inject a policy into
    ``EvalRunner`` explicitly.
    """
    return PermissionPolicy.from_dict(
        {
            "headless": {"exec": "deny", "outside_workspace": "deny"},
        }
    )


class EvalRunner:
    """Executes evaluation scenarios in hermetic workspace environments."""

    def __init__(
        self,
        trace_sink: Optional[TraceSink] = None,
        default_provider: str = "stub",
        policy: Optional[PermissionPolicy] = None,
    ) -> None:
        self.trace_sink = trace_sink
        self.default_provider = default_provider
        #: Injected for tests or a caller that needs a stricter/looser profile;
        #: otherwise ``eval_policy()`` is used so a run never reads user config.
        self.policy = policy

    def run_scenario(
        self,
        scenario: Scenario,
        workspace_dir: Optional[Path] = None,
        completer: Optional[Any] = None,
        custom_provider: Optional[str] = None,
    ) -> ScenarioResult:
        """Run a single evaluation scenario and assert behavioral gates."""
        from agent.loop import AgentLoop

        start_time = time.time()
        is_temp_ws = False
        ws_path: Path
        if workspace_dir:
            ws_path = Path(workspace_dir).resolve()
            ws_path.mkdir(parents=True, exist_ok=True)
        else:
            temp_dir = tempfile.mkdtemp(prefix=f"eval_{scenario.id}_")
            ws_path = Path(temp_dir).resolve()
            is_temp_ws = True

        tools_called: List[str] = []
        failures: List[str] = []

        try:
            # 1. Setup workspace fixture files
            for rel_path, content in scenario.setup_files.items():
                target_file = ws_path / rel_path
                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_text(content, encoding="utf-8")

            # 2. Build isolated runtime. The policy is pinned: reading the
            #    operator's file would make the scorecard machine-dependent.
            runtime = AgentRuntime(
                workspace=ws_path,
                policy=self.policy if self.policy is not None else eval_policy(),
            )

            # 3. Setup provider / completer
            provider_key = custom_provider or self.default_provider
            active_completer = completer
            if active_completer is None:
                active_completer = build_completer(provider_key)

            # 4. Attach trace collector
            collector = TraceCollector(
                sink=self.trace_sink,
                task_prompt=scenario.prompt,
                origin_node="eval",
            )

            def _event_recorder(ev: Dict[str, Any]) -> None:
                collector(ev)
                if ev.get("type") == "tool_call":
                    tname = str(ev.get("name") or "")
                    if tname:
                        tools_called.append(tname)

            # 5. Run loop
            loop = AgentLoop(
                active_completer,
                runtime=runtime,
                max_turns=scenario.max_turns,
                on_event=_event_recorder,
                tracer=collector,
            )

            result = loop.run(scenario.prompt)

            # 6. Evaluate assertions
            for expected in scenario.expected_tools:
                if expected not in tools_called:
                    failures.append(f"Expected tool {expected!r} was not called (called: {tools_called})")

            for forbidden in scenario.forbidden_tools:
                if forbidden in tools_called:
                    failures.append(f"Forbidden tool {forbidden!r} was called")

            result_text = result.text or ""
            for text_sub in scenario.expected_text:
                if text_sub.lower() not in result_text.lower():
                    failures.append(f"Expected text snippet {text_sub!r} missing from output")

            for text_sub in scenario.forbidden_text:
                if text_sub.lower() in result_text.lower():
                    failures.append(f"Forbidden text snippet {text_sub!r} found in output")

            if scenario.expected_stop_reason and result.stop_reason != scenario.expected_stop_reason:
                failures.append(f"Stop reason mismatch: expected {scenario.expected_stop_reason!r}, got {result.stop_reason!r}")

            for verify_rel, expected_content in scenario.verify_files.items():
                verify_path = ws_path / verify_rel
                if not verify_path.is_file():
                    failures.append(f"Verify file {verify_rel!r} does not exist after run")
                else:
                    actual_content = verify_path.read_text(encoding="utf-8")
                    if expected_content not in actual_content:
                        failures.append(f"Verify file {verify_rel!r} content mismatch: expected {expected_content!r}")

            duration_ms = (time.time() - start_time) * 1000.0
            return ScenarioResult(
                scenario_id=scenario.id,
                category=scenario.category,
                passed=(len(failures) == 0),
                duration_ms=duration_ms,
                turns=result.turns,
                tools_called=tools_called,
                failures=failures,
                trace_id=collector.trace_id,
                stop_reason=result.stop_reason,
            )
        finally:
            if is_temp_ws:
                import shutil
                shutil.rmtree(ws_path, ignore_errors=True)

    def run_suite(
        self,
        scenarios: List[Scenario],
        completer_factory: Optional[Callable[[Scenario], Any]] = None,
        custom_provider: Optional[str] = None,
    ) -> EvalSuiteResult:
        """Run a collection of scenarios and generate aggregated scorecard."""
        start_time = time.time()
        results: List[ScenarioResult] = []
        by_category: Dict[str, Dict[str, Any]] = {
            cat: {"total": 0, "passed": 0, "score": 0.0} for cat in VALID_CATEGORIES
        }

        for sc in scenarios:
            completer = completer_factory(sc) if completer_factory else None
            res = self.run_scenario(
                sc,
                completer=completer,
                custom_provider=custom_provider,
            )
            results.append(res)
            cat_entry = by_category.setdefault(sc.category, {"total": 0, "passed": 0, "score": 0.0})
            cat_entry["total"] += 1
            if res.passed:
                cat_entry["passed"] += 1

        for stats in by_category.values():
            if stats["total"] > 0:
                stats["score"] = stats["passed"] / stats["total"]

        total = len(scenarios)
        passed = sum(1 for r in results if r.passed)
        failed = total - passed
        score = (passed / total) if total > 0 else 0.0
        duration_ms = (time.time() - start_time) * 1000.0

        return EvalSuiteResult(
            total=total,
            passed=passed,
            failed=failed,
            score=score,
            duration_ms=duration_ms,
            by_category=by_category,
            results=results,
        )


# -- Scenario Catalog Loader -----------------------------------------------

def load_scenarios_from_dir(scenarios_dir: Path, category: Optional[str] = None) -> List[Scenario]:
    """Load scenario JSON definitions from a directory."""
    scenarios: List[Scenario] = []
    p = Path(scenarios_dir).resolve()
    if not p.is_dir():
        return scenarios

    for json_file in sorted(p.glob("**/*.json")):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    sc = Scenario.from_dict(item)
                    if not category or sc.category == category:
                        scenarios.append(sc)
            elif isinstance(data, dict):
                sc = Scenario.from_dict(data)
                if not category or sc.category == category:
                    scenarios.append(sc)
        except Exception:
            continue
    return scenarios
