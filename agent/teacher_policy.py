"""Teacher Escalation Policy and Capability Synthesis Safety Layer (§11.1, §11.2, Phase 7).

Governs model escalation when the local agent cannot fulfill a turn:
1. Triggers: 2+ failed attempts on a command, missing capability, or explicit request.
2. Budget: Daily call and token caps, per-task attempt cap with local persistent ledger.
3. Egress: Strict privacy scrubbing (redacts local paths, user home, emails, API keys).
4. Safety & Crucible: Code must undergo AST validation and sandboxed testing.
5. Invariant: Generated skills are NEVER automatically installed. Staged for human review.
6. Provenance: Every synthesis records SHA-256 prompt hash, teacher model, and timestamps.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

DEFAULT_MAX_DAILY_CALLS = 10
DEFAULT_MAX_DAILY_TOKENS = 50_000
DEFAULT_MAX_TASK_ATTEMPTS = 2

GapType = Literal["repeated_failure", "missing_capability", "explicit_request"]


@dataclass
class TeacherBudget:
    """Daily and per-task budget tracker for teacher escalation (§11.1)."""

    max_daily_calls: int = DEFAULT_MAX_DAILY_CALLS
    max_daily_tokens: int = DEFAULT_MAX_DAILY_TOKENS
    max_task_attempts: int = DEFAULT_MAX_TASK_ATTEMPTS
    ledger_path: Optional[Path] = None

    def _get_ledger(self) -> Dict[str, Any]:
        path = self.ledger_path or (Path.cwd() / ".brahma" / "teacher_budget.json")
        today = datetime.date.today().isoformat()
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("date") == today:
                    return data
            except Exception:
                pass
        return {"date": today, "calls": 0, "tokens": 0, "tasks": {}}

    def _save_ledger(self, data: Dict[str, Any]) -> None:
        path = self.ledger_path or (Path.cwd() / ".brahma" / "teacher_budget.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def check_budget(self, task_id: str, estimated_tokens: int = 1_000) -> Tuple[bool, str]:
        """Check whether teacher invocation is permitted under current caps."""
        ledger = self._get_ledger()
        calls = ledger.get("calls", 0)
        tokens = ledger.get("tokens", 0)
        tasks = ledger.get("tasks", {})

        task_calls = tasks.get(task_id, 0)
        if task_calls >= self.max_task_attempts:
            return False, f"Per-task escalation limit ({self.max_task_attempts}) reached for task '{task_id}'."

        if calls >= self.max_daily_calls:
            return False, f"Daily teacher call cap ({self.max_daily_calls}) reached for today."

        if tokens + estimated_tokens > self.max_daily_tokens:
            return False, f"Daily teacher token budget ({self.max_daily_tokens}) exceeded."

        return True, "Budget check passed."

    def record_usage(self, task_id: str, tokens: int = 1_000, calls: int = 1) -> None:
        """Record consumed teacher calls and tokens into ledger."""
        ledger = self._get_ledger()
        ledger["calls"] = ledger.get("calls", 0) + calls
        ledger["tokens"] = ledger.get("tokens", 0) + tokens
        tasks = ledger.setdefault("tasks", {})
        tasks[task_id] = tasks.get(task_id, 0) + calls
        self._save_ledger(ledger)


class EgressScrubber:
    """Sanitizes context briefs before transmission to external teacher models (§11.1)."""

    _EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    _TOKEN_PATTERNS = [
        re.compile(r"\bAIza[0-9A-Za-z-_]{35}\b"),              # Google API Key
        re.compile(r"\bsk-[a-zA-Z0-9_\-]{20,}\b"),             # OpenAI / Anthropic Key
        re.compile(r"\bghp_[a-zA-Z0-9]{36}\b"),                # GitHub Personal Token
        re.compile(r"\bBearer\s+[a-zA-Z0-9_\-\.]{20,}\b", re.I), # Bearer token
    ]

    @classmethod
    def scrub(
        cls,
        text: str,
        workspace: Optional[Path] = None,
        home: Optional[Path] = None,
    ) -> str:
        """Redact absolute paths, home directories, emails, and credential tokens."""
        if not text:
            return ""

        scrubbed = str(text)

        # 1. Redact workspace path
        ws = str(workspace.resolve()) if workspace else str(Path.cwd().resolve())
        scrubbed = scrubbed.replace(ws, "<WORKSPACE_ROOT>")
        # Also replace Windows backslash version
        scrubbed = scrubbed.replace(ws.replace("/", "\\"), "<WORKSPACE_ROOT>")
        scrubbed = scrubbed.replace(ws.replace("\\", "/"), "<WORKSPACE_ROOT>")

        # 2. Redact user home directory
        hm = str(home.resolve()) if home else str(Path.home().resolve())
        scrubbed = scrubbed.replace(hm, "<USER_HOME>")
        scrubbed = scrubbed.replace(hm.replace("/", "\\"), "<USER_HOME>")
        scrubbed = scrubbed.replace(hm.replace("\\", "/"), "<USER_HOME>")

        # 3. Redact emails
        scrubbed = cls._EMAIL_RE.sub("<EMAIL_REDACTED>", scrubbed)

        # 4. Redact tokens and API keys
        for pat in cls._TOKEN_PATTERNS:
            scrubbed = pat.sub("<TOKEN_REDACTED>", scrubbed)

        return scrubbed


@dataclass
class ExecutionGap:
    """An identified capability gap or execution failure eligible for escalation (§11.1)."""

    task_id: str
    gap_type: GapType
    description: str
    command: Optional[str] = None
    error_message: Optional[str] = None
    consecutive_failures: int = 1
    created_at: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())

    def is_eligible_for_escalation(self) -> bool:
        """Triggers: 2+ failed attempts on same command, or missing capability (§11.1)."""
        if self.gap_type == "repeated_failure":
            return self.consecutive_failures >= 2
        if self.gap_type in ("missing_capability", "explicit_request"):
            return True
        return False


@dataclass
class SynthesisProvenance:
    """Provenance audit record for synthesized capabilities (§11.1)."""

    skill_name: str
    teacher_model: str
    prompt_hash: str
    created_at: str
    tested_in_sandbox: bool = False
    sandbox_passed: bool = False
    approved: bool = False
    approved_by: Optional[str] = None
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TeacherEscalationPolicy:
    """Enforces safety, budget, and staging invariants for teacher escalation (§11.1, §11.2)."""

    def __init__(
        self,
        budget: Optional[TeacherBudget] = None,
        workspace: Optional[Path] = None,
    ) -> None:
        self.budget = budget or TeacherBudget()
        self.workspace = workspace or Path.cwd()
        self._gaps: Dict[str, ExecutionGap] = {}

    def track_failure(self, task_id: str, command: str, error: str) -> Optional[ExecutionGap]:
        """Track command failures and detect when the 2-failure threshold is met."""
        key = f"{task_id}::{command.strip()}"
        existing = self._gaps.get(key)
        if existing and existing.command == command:
            existing.consecutive_failures += 1
            existing.error_message = error
            return existing

        gap = ExecutionGap(
            task_id=task_id,
            gap_type="repeated_failure",
            description=f"Command repeatedly failing: {command}",
            command=command,
            error_message=error,
            consecutive_failures=1,
        )
        self._gaps[key] = gap
        return gap

    def build_teacher_brief(
        self,
        goal: str,
        task_id: str,
        context_notes: Optional[str] = None,
    ) -> str:
        """Formulate a sanitized, policy-compliant capability brief for the teacher model (§11.2)."""
        scrubbed_goal = EgressScrubber.scrub(goal, workspace=self.workspace)
        scrubbed_notes = EgressScrubber.scrub(context_notes or "", workspace=self.workspace)

        brief = f"""CAPABILITY SYNTHESIS BRIEF (§11.2)
Task ID: {task_id}
Goal: {scrubbed_goal}

Instructions:
1. Produce a deterministic, self-contained capability package in Python.
2. Provide a valid manifest JSON declaring parameter schema.
3. Provide unit test cases covering normal execution and edge cases.
4. Do not solve the user's task in prose. Do not require network access unless explicitly requested.
5. Declare every external dependency.

Context Notes:
{scrubbed_notes or '(None)'}
"""
        return brief

    def stage_synthesized_skill(
        self,
        skill_name: str,
        manifest: Dict[str, Any],
        code: str,
        test_cases: List[Dict[str, Any]],
        teacher_model: str,
        prompt_brief: str,
        staged_dir: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Stage a synthesized capability package with provenance record.

        INVARIANT: Skills are NEVER auto-installed. They reside in staged_skills
        awaiting explicit human verification and review (§11.1).
        """
        # Determine staging directory
        if staged_dir is None:
            try:
                from core.skill_forge import staged_skills_dir
                target_base = staged_skills_dir()
            except Exception:
                target_base = Path.cwd() / ".brahma" / "staged_skills"
        else:
            target_base = staged_dir

        target_dir = target_base / skill_name
        target_dir.mkdir(parents=True, exist_ok=True)

        # 1. Write skill.py
        (target_dir / "skill.py").write_text(code, encoding="utf-8")

        # 2. Write manifest.json
        (target_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )

        # 3. Write test_cases.json
        (target_dir / "test_cases.json").write_text(
            json.dumps(test_cases, indent=2), encoding="utf-8"
        )

        # 4. Compute prompt hash
        prompt_hash = hashlib.sha256(prompt_brief.encode("utf-8")).hexdigest()

        # 5. Write provenance record (§11.1)
        prov = SynthesisProvenance(
            skill_name=skill_name,
            teacher_model=teacher_model,
            prompt_hash=prompt_hash,
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            tested_in_sandbox=False,
            sandbox_passed=False,
            approved=False,
            notes="Staged capability synthesis awaiting human review.",
        )
        (target_dir / "provenance.json").write_text(
            json.dumps(prov.to_dict(), indent=2), encoding="utf-8"
        )

        # 6. Write staged status marker
        staged_info = {
            "name": skill_name,
            "staged": True,
            "activated": False,
            "teacher_model": teacher_model,
            "staged_at": prov.created_at,
        }
        (target_dir / "staged.json").write_text(
            json.dumps(staged_info, indent=2), encoding="utf-8"
        )

        return {
            "ok": True,
            "staged": True,
            "activated": False,
            "path": str(target_dir),
            "provenance": prov.to_dict(),
        }
