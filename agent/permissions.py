"""Capability policy for the agentic harness.

``core/confirm.py`` answers "is a human standing there to press CONFIRM?".
That is the right question for a shutdown, and the wrong one for an agent
that wants to make forty file edits. An agent needs a *rule engine*: what may
this tool class do, in which directories, with which commands, when nobody is
watching?

The model is never consulted. It cannot pass ``confirmed=True`` (the failure
mode documented at the top of ``core/confirm.py``) and it cannot widen the
workspace. Decisions come from this file only.

Resolution order — first match wins:

1. an explicit per-tool rule (``tools.<name>``)
2. a hard deny: an exec command matching ``exec.deny_patterns``
3. a protected path (``paths.protected``) or a path outside the workspace
   (``paths.outside_workspace``)
4. ``headless.<class>`` when no approver is available
5. ``classes.<class>``
6. ``default``

The distinction between ``ask`` and ``deny`` matters: ``ask`` goes to the
approver (HUD banner in the GUI, prompt on a TTY, refuse in CI) and the model
gets a sentence explaining that a human must approve. ``deny`` is final and
the model is told not to retry.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

ALLOW = "allow"
ASK = "ask"
DENY = "deny"
DECISIONS: Tuple[str, ...] = (ALLOW, ASK, DENY)


class PolicyError(ValueError):
    """The policy document is malformed."""


#: Commands refused outright, before any class rule. Deliberately narrow:
#: a denylist is not a sandbox, it is a tripwire for the catastrophic. The
#: real protection is that ``exec`` defaults to ``ask``.
DEFAULT_EXEC_DENY_PATTERNS: Tuple[str, ...] = (
    r"rm\s+-[a-z]*r[a-z]*f[a-z]*\s+/(?:\s|$)",
    r"mkfs\.",
    r"dd\s+if=.*of=/dev/",
    r":\(\)\s*\{\s*:\|:&\s*\};:",          # fork bomb
    r"format\s+[a-z]:",
    r"del\s+/[fsq].*[a-z]:\\\s*$",
    r"rd\s+/s\s+/q\s+[a-z]:\\\s*$",
    r"remove-item\s+.*-recurse.*-force\s+[a-z]:\\\s*$",
    r"shutdown\s+(?:/s|/r|-h|-r)",
    r"cipher\s+/w\s*[a-z]:",
    r"reg\s+delete\s+hklm",
)

#: Relative to the workspace. ``fnmatch`` semantics on the POSIX-style
#: relative path, so ``**/`` is not required: ``.env*`` matches at any depth
#: because matching is done against the basename as well.
DEFAULT_PROTECTED_GLOBS: Tuple[str, ...] = (
    ".git/config",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "*.pfx",
)

DEFAULT_POLICY: Dict[str, Any] = {
    "version": 1,
    "default": ALLOW,
    "classes": {
        "read": ALLOW,
        "write": ALLOW,
        "exec": ASK,
        "network": ALLOW,
        "device": ALLOW,
        "native": ALLOW,
    },
    "tools": {},
    "paths": {
        "outside_workspace": ASK,
        "protected": ASK,
    },
    "protected_globs": list(DEFAULT_PROTECTED_GLOBS),
    "exec": {
        "deny_patterns": list(DEFAULT_EXEC_DENY_PATTERNS),
        "allow_patterns": [],
    },
    "headless": {
        "exec": DENY,
        "outside_workspace": DENY,
    },
}


def default_policy_dict() -> Dict[str, Any]:
    """A deep-enough copy that callers cannot mutate the module constant."""
    return json.loads(json.dumps(DEFAULT_POLICY))


@dataclass(frozen=True)
class PolicyResult:
    decision: str
    reason: str = ""
    rule: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision == ALLOW

    @property
    def needs_approval(self) -> bool:
        return self.decision == ASK

    def for_model(self, tool_name: str) -> str:
        """The sentence the model reads when a call did not run."""
        if self.decision == DENY:
            return (
                f"Permission denied for '{tool_name}' ({self.rule}): {self.reason} "
                "This is a hard policy denial. Do not retry the same call; "
                "tell the user what you need and why."
            )
        return (
            f"Permission required for '{tool_name}' ({self.rule}): {self.reason} "
            "A human must approve this before it runs. Do not claim it happened."
        )


def _decision(value: Any, where: str) -> str:
    if value is None:
        raise PolicyError(f"{where}: missing decision")
    text = str(value).strip().lower()
    if text not in DECISIONS:
        raise PolicyError(f"{where}: {text!r} is not one of {DECISIONS}")
    return text


def _coerce_tool_rule(raw: Any, name: str) -> Dict[str, Any]:
    """Accept either ``"tool": "allow"`` or the object form."""
    if isinstance(raw, str):
        return {"decision": _decision(raw, f"tools.{name}")}
    if not isinstance(raw, dict):
        raise PolicyError(f"tools.{name}: expected a string or an object")
    rule = dict(raw)
    if "decision" in rule:
        rule["decision"] = _decision(rule["decision"], f"tools.{name}.decision")
    return rule


@dataclass
class PermissionPolicy:
    """An immutable-in-practice view of ``agent_permissions.json``."""

    data: Dict[str, Any] = field(default_factory=default_policy_dict)
    source: Optional[Path] = None

    # -- construction ------------------------------------------------------

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]], source: Optional[Path] = None) -> "PermissionPolicy":
        merged = default_policy_dict()
        if data:
            for key, value in data.items():
                if key in ("classes", "paths", "exec", "headless") and isinstance(value, dict):
                    merged[key].update(value)
                elif key == "tools" and isinstance(value, dict):
                    merged["tools"].update({
                        name: _coerce_tool_rule(rule, name) for name, rule in value.items()
                    })
                else:
                    merged[key] = value
        # Validate eagerly: a typo in a decision must fail at load, not at the
        # moment an agent tries to delete something.
        for key in ("default",):
            merged[key] = _decision(merged.get(key), key)
        for section in ("classes", "paths", "headless"):
            for name, value in dict(merged.get(section) or {}).items():
                merged[section][name] = _decision(value, f"{section}.{name}")
        return cls(data=merged, source=source)

    @classmethod
    def load(cls, path: Optional[str | Path] = None) -> "PermissionPolicy":
        """Load from an explicit path, the environment, user data, or defaults."""
        candidates: List[Path] = []
        if path:
            candidates.append(Path(path))
        env = os.getenv("BRAHMA_AGENT_PERMISSIONS")
        if env:
            candidates.append(Path(env))
        try:
            from core.user_paths import get_user_data_dir

            # Matches the layout of the other user config (``config/``).
            candidates.append(get_user_data_dir() / "config" / "agent_permissions.json")
        except Exception:
            pass
        candidates.append(Path(__file__).resolve().parent.parent / "config" / "agent_permissions.json")

        for candidate in candidates:
            if candidate.is_file():
                try:
                    raw = json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise PolicyError(f"cannot read permission policy {candidate}: {exc}") from exc
                if not isinstance(raw, dict):
                    raise PolicyError(f"permission policy {candidate} must be a JSON object")
                return cls.from_dict(raw, source=candidate)
        return cls.from_dict({})

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")
        return target

    # -- queries -----------------------------------------------------------

    @property
    def protected_globs(self) -> List[str]:
        raw = self.data.get("protected_globs") or []
        return [str(g) for g in raw]

    def _path_is_protected(self, rel: str) -> bool:
        name = Path(rel).name
        rel_posix = rel.replace("\\", "/")
        for pattern in self.protected_globs:
            if fnmatch.fnmatch(rel_posix, pattern) or fnmatch.fnmatch(name, pattern):
                return True
        return False

    def _exec_denied(self, args: Mapping[str, Any]) -> Optional[str]:
        command = str(args.get("command") or args.get("cmd") or "")
        if not command:
            return None
        exec_cfg = self.data.get("exec") or {}
        for pattern in exec_cfg.get("allow_patterns") or []:
            if re.search(str(pattern), command, re.IGNORECASE):
                return None
        for pattern in exec_cfg.get("deny_patterns") or []:
            if re.search(str(pattern), command, re.IGNORECASE):
                return str(pattern)
        return None

    def evaluate(
        self,
        tool_name: str,
        args: Optional[Mapping[str, Any]] = None,
        *,
        tool_class: str = "native",
        context_workspace: Optional[Path] = None,
        headless: bool = False,
        requires_approval: bool = False,
    ) -> PolicyResult:
        """Decide one call. ``headless`` means "no human can be asked"."""
        args = dict(args or {})
        rules = self.data.get("tools") or {}
        rule = rules.get(tool_name)

        # 1. explicit per-tool rule
        if isinstance(rule, dict) and rule.get("decision"):
            decision = rule["decision"]
            detail = str(rule.get("reason") or "explicit rule for this tool.")
            if decision == ASK and headless:
                # An explicit ask still cannot be honoured without an approver.
                return PolicyResult(DENY, f"{detail} No approver is available.", f"tools.{tool_name}")
            return PolicyResult(decision, detail, f"tools.{tool_name}")

        # 2. hard deny on catastrophic commands
        if tool_class == "exec":
            denied_by = self._exec_denied(args)
            if denied_by:
                return PolicyResult(
                    DENY,
                    "the command matches a catastrophic-command rule.",
                    f"exec.deny_patterns[{denied_by}]",
                )

        # 3. path scoping
        if context_workspace is not None:
            from .spec import PATH_ARG_KEYS  # local import: spec imports nothing here

            for key in PATH_ARG_KEYS:
                raw = args.get(key)
                if not isinstance(raw, str) or not raw.strip():
                    continue
                candidate = Path(raw).expanduser()
                if not candidate.is_absolute():
                    candidate = context_workspace / candidate
                try:
                    candidate = candidate.resolve()
                except OSError:
                    continue
                try:
                    rel = str(candidate.relative_to(context_workspace))
                except ValueError:
                    paths_cfg = self.data.get("paths") or {}
                    section = "headless.outside_workspace" if headless else "paths.outside_workspace"
                    value = (
                        (self.data.get("headless") or {}).get("outside_workspace")
                        if headless else paths_cfg.get("outside_workspace")
                    )
                    decision = _decision(value or DENY, section)
                    return PolicyResult(
                        decision,
                        f"{candidate} is outside the workspace {context_workspace}.",
                        section,
                    )
                if self._path_is_protected(rel):
                    value = (self.data.get("paths") or {}).get("protected", ASK)
                    return PolicyResult(
                        _decision(value, "paths.protected"),
                        f"{rel} matches a protected-file pattern.",
                        "paths.protected",
                    )

        # 4/5/6. class default, headless override, global default
        classes = self.data.get("classes") or {}
        headless_map = self.data.get("headless") or {}
        decision = classes.get(tool_class)
        rule_name = f"classes.{tool_class}"
        if headless and tool_class in headless_map:
            decision = headless_map[tool_class]
            rule_name = f"headless.{tool_class}"
        if decision is None:
            decision = self.data.get("default", ALLOW)
            rule_name = "default"
        decision = _decision(decision, rule_name)

        if requires_approval and decision == ALLOW:
            if headless:
                return PolicyResult(
                    DENY,
                    "this tool always needs approval and no approver is available.",
                    "tool.requires_approval",
                )
            return PolicyResult(ASK, "this tool always needs approval.", "tool.requires_approval")

        return PolicyResult(decision, f"class '{tool_class}' default.", rule_name)

    def describe(self) -> Dict[str, Any]:
        return {
            "source": str(self.source) if self.source else "(built-in defaults)",
            "default": self.data.get("default"),
            "classes": dict(self.data.get("classes") or {}),
            "headless": dict(self.data.get("headless") or {}),
            "paths": dict(self.data.get("paths") or {}),
            "tools": dict(self.data.get("tools") or {}),
        }


def write_template(
    path: str | Path, *, workspace_note: str = "", overwrite: bool = False
) -> Path:
    """Write a commented-in-spirit template the user can edit.

    JSON has no comments, so the explanations live in
    ``docs/AGENTIC-HARNESS.md``; this function only materializes the defaults.

    Refuses to clobber an existing policy unless ``overwrite=True``: a user who
    tuned their decisions and re-ran ``permissions init`` would otherwise lose
    the file silently.
    """
    target = Path(path)
    if target.exists() and not overwrite:
        raise FileExistsError(str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = default_policy_dict()
    if workspace_note:
        payload["_note"] = workspace_note
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target
