"""Git Worktree Management Harness (§11.3).

Provides isolated workspace branching for agent experiments and capability testing:
- Worktrees live under `.brahma/worktrees/<task-id>/` (gitignored).
- Execution runs through `agent.process.run_captured` with lifetime containment.
- Merge is fast-forward only and requires a clean verification pass (ruff + pytest).
- Safe removal refuses to delete uncommitted work unless `--force` is provided.

CLI Usage:
    python tools/worktree.py create --task <task-id> [--base main]
    python tools/worktree.py status --task <task-id>
    python tools/worktree.py verify --task <task-id>
    python tools/worktree.py merge  --task <task-id> [--base main] [--force]
    python tools/worktree.py remove --task <task-id> [--force]
    python tools/worktree.py list
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure repository root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent.process import run_captured


def _sanitize_task_id(task: str) -> str:
    """Ensure task identifier contains only safe alphanumeric, dash, and underscore characters."""
    sanitized = re.sub(r"[^a-zA-Z0-9_\-]", "-", str(task or "").strip())
    sanitized = re.sub(r"-+", "-", sanitized).strip("-")
    if not sanitized:
        raise ValueError("Task identifier cannot be empty or contain only invalid characters.")
    return sanitized


def get_worktree_root(repo_root: Optional[Path] = None) -> Path:
    """Return the parent directory for all task worktrees."""
    base = repo_root or _REPO_ROOT
    return base / ".brahma" / "worktrees"


def get_task_path(task: str, repo_root: Optional[Path] = None) -> Path:
    """Return the absolute path for a specific task worktree."""
    clean_task = _sanitize_task_id(task)
    return get_worktree_root(repo_root) / clean_task


@dataclass
class WorktreeStatus:
    task: str
    path: str
    exists: bool
    branch: Optional[str] = None
    head_sha: Optional[str] = None
    is_dirty: bool = False
    untracked_count: int = 0
    modified_count: int = 0
    verification_passed: Optional[bool] = None
    verification_details: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def create_worktree(
    task: str,
    base: str = "main",
    repo_root: Optional[Path] = None,
    timeout: float = 60.0,
) -> Dict[str, Any]:
    """Create an isolated git worktree for a task branch (§11.3)."""
    root = repo_root or _REPO_ROOT
    clean_task = _sanitize_task_id(task)
    target_dir = get_task_path(clean_task, root)
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    if target_dir.exists():
        return {
            "ok": False,
            "error": f"Worktree directory already exists at {target_dir}",
            "path": str(target_dir),
        }

    branch_name = f"task/{clean_task}"

    # Check if branch exists
    chk = run_captured(
        f"git rev-parse --verify --quiet refs/heads/{branch_name}",
        cwd=root,
        timeout=timeout,
    )
    branch_exists = chk.returncode == 0

    if branch_exists:
        cmd = f"git worktree add {target_dir} {branch_name}"
    else:
        cmd = f"git worktree add -b {branch_name} {target_dir} {base}"

    res = run_captured(cmd, cwd=root, timeout=timeout)
    if res.returncode != 0:
        return {
            "ok": False,
            "error": res.stderr.strip() or res.stdout.strip(),
            "returncode": res.returncode,
        }

    return {
        "ok": True,
        "task": clean_task,
        "branch": branch_name,
        "path": str(target_dir),
        "base": base,
    }


def worktree_status(
    task: str,
    repo_root: Optional[Path] = None,
    timeout: float = 30.0,
) -> WorktreeStatus:
    """Inspect the status and dirtiness of a task worktree."""
    root = repo_root or _REPO_ROOT
    clean_task = _sanitize_task_id(task)
    target_dir = get_task_path(clean_task, root)

    if not target_dir.is_dir():
        return WorktreeStatus(
            task=clean_task,
            path=str(target_dir),
            exists=False,
        )

    # Current branch and SHA
    sha_res = run_captured("git rev-parse HEAD", cwd=target_dir, timeout=timeout)
    head_sha = sha_res.stdout.strip() if sha_res.returncode == 0 else None

    br_res = run_captured("git rev-parse --abbrev-ref HEAD", cwd=target_dir, timeout=timeout)
    branch = br_res.stdout.strip() if br_res.returncode == 0 else None

    # Working tree status
    st_res = run_captured("git status --porcelain", cwd=target_dir, timeout=timeout)
    lines = [line.strip() for line in st_res.stdout.splitlines() if line.strip()]
    is_dirty = len(lines) > 0

    untracked = sum(1 for line in lines if line.startswith("??"))
    modified = len(lines) - untracked

    # Verification status marker
    verify_marker = target_dir / ".verification.json"
    ver_passed = None
    ver_details = None
    if verify_marker.is_file():
        try:
            ver_details = json.loads(verify_marker.read_text(encoding="utf-8"))
            ver_passed = bool(ver_details.get("passed"))
        except Exception:
            pass

    return WorktreeStatus(
        task=clean_task,
        path=str(target_dir),
        exists=True,
        branch=branch,
        head_sha=head_sha,
        is_dirty=is_dirty,
        untracked_count=untracked,
        modified_count=modified,
        verification_passed=ver_passed,
        verification_details=ver_details,
    )


def verify_worktree(
    task: str,
    repo_root: Optional[Path] = None,
    timeout: float = 120.0,
) -> Dict[str, Any]:
    """Run linting and tests in the isolated worktree directory (§11.3)."""
    status = worktree_status(task, repo_root=repo_root)
    if not status.exists:
        return {"ok": False, "error": f"Worktree for task '{task}' does not exist."}

    target_dir = Path(status.path)

    # 1. Ruff lint check
    ruff_res = run_captured(
        "python -m ruff check .",
        cwd=target_dir,
        timeout=timeout,
    )
    ruff_ok = ruff_res.returncode == 0

    # 2. Pytest execution
    pytest_res = run_captured(
        "python -m pytest tests/test_agent_*.py tests/test_turn_context.py -q",
        cwd=target_dir,
        timeout=timeout,
    )
    pytest_ok = pytest_res.returncode == 0

    passed = ruff_ok and pytest_ok
    details = {
        "task": task,
        "passed": passed,
        "ruff_ok": ruff_ok,
        "ruff_stdout": ruff_res.stdout[:2000],
        "ruff_stderr": ruff_res.stderr[:2000],
        "pytest_ok": pytest_ok,
        "pytest_stdout": pytest_res.stdout[:2000],
        "pytest_stderr": pytest_res.stderr[:2000],
    }

    # Record verification cache marker
    try:
        (target_dir / ".verification.json").write_text(
            json.dumps(details, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    return {"ok": passed, **details}


def merge_worktree(
    task: str,
    base: str = "main",
    repo_root: Optional[Path] = None,
    force: bool = False,
    timeout: float = 60.0,
) -> Dict[str, Any]:
    """Merge worktree branch into base using fast-forward only (§11.3)."""
    root = repo_root or _REPO_ROOT
    status = worktree_status(task, repo_root=root)
    if not status.exists:
        return {"ok": False, "error": f"Worktree for task '{task}' does not exist."}

    if status.is_dirty:
        return {
            "ok": False,
            "error": "Cannot merge: worktree has uncommitted or untracked changes.",
        }

    if not force:
        if status.verification_passed is not True:
            # Run verification automatically
            ver = verify_worktree(task, repo_root=root, timeout=timeout)
            if not ver.get("ok"):
                return {
                    "ok": False,
                    "error": "Merge aborted: verification checks failed.",
                    "verification": ver,
                }

    branch = status.branch or f"task/{_sanitize_task_id(task)}"

    # Fast-forward merge into base
    cmd = f"git merge --ff-only {branch}"
    res = run_captured(cmd, cwd=root, timeout=timeout)
    if res.returncode != 0:
        return {
            "ok": False,
            "error": f"Fast-forward merge failed: {res.stderr.strip() or res.stdout.strip()}",
            "returncode": res.returncode,
        }

    return {
        "ok": True,
        "task": task,
        "branch": branch,
        "merged_into": base,
        "message": res.stdout.strip(),
    }


def remove_worktree(
    task: str,
    repo_root: Optional[Path] = None,
    force: bool = False,
    delete_branch: bool = False,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """Remove an isolated worktree (§11.3). Refuses if dirty unless force=True."""
    root = repo_root or _REPO_ROOT
    status = worktree_status(task, repo_root=root)
    if not status.exists:
        return {"ok": False, "error": f"Worktree for task '{task}' does not exist."}

    if status.is_dirty and not force:
        return {
            "ok": False,
            "error": (
                "Worktree has uncommitted changes. Use --force to discard them and remove."
            ),
            "untracked_count": status.untracked_count,
            "modified_count": status.modified_count,
        }

    cmd = f"git worktree remove {status.path}" + (" --force" if force else "")
    res = run_captured(cmd, cwd=root, timeout=timeout)
    if res.returncode != 0:
        return {
            "ok": False,
            "error": res.stderr.strip() or res.stdout.strip(),
            "returncode": res.returncode,
        }

    branch_deleted = False
    if delete_branch and status.branch:
        del_res = run_captured(
            f"git branch -D {status.branch}", cwd=root, timeout=timeout
        )
        branch_deleted = del_res.returncode == 0

    return {
        "ok": True,
        "task": task,
        "removed_path": status.path,
        "branch_deleted": branch_deleted,
    }


def list_worktrees(repo_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """List all active task worktrees under `.brahma/worktrees/`."""
    root = repo_root or _REPO_ROOT
    wt_root = get_worktree_root(root)
    if not wt_root.is_dir():
        return []

    results: List[Dict[str, Any]] = []
    for item in sorted(wt_root.iterdir()):
        if item.is_dir():
            status = worktree_status(item.name, repo_root=root)
            results.append(status.to_dict())
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Brahma AI Git Worktree Harness (§11.3)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # create
    p_create = sub.add_parser("create", help="Create a new isolated task worktree")
    p_create.add_argument("--task", required=True, help="Task identifier")
    p_create.add_argument("--base", default="main", help="Base git ref to branch from")
    p_create.add_argument("--json", action="store_true", help="Output JSON")

    # status
    p_status = sub.add_parser("status", help="Inspect status of a task worktree")
    p_status.add_argument("--task", required=True, help="Task identifier")
    p_status.add_argument("--json", action="store_true", help="Output JSON")

    # verify
    p_verify = sub.add_parser("verify", help="Run lint and test checks in worktree")
    p_verify.add_argument("--task", required=True, help="Task identifier")
    p_verify.add_argument("--json", action="store_true", help="Output JSON")

    # merge
    p_merge = sub.add_parser("merge", help="Fast-forward merge worktree into base")
    p_merge.add_argument("--task", required=True, help="Task identifier")
    p_merge.add_argument("--base", default="main", help="Target branch for ff-merge")
    p_merge.add_argument("--force", action="store_true", help="Bypass verify check")
    p_merge.add_argument("--json", action="store_true", help="Output JSON")

    # remove
    p_remove = sub.add_parser("remove", help="Remove task worktree")
    p_remove.add_argument("--task", required=True, help="Task identifier")
    p_remove.add_argument("--force", action="store_true", help="Force remove dirty worktree")
    p_remove.add_argument("--delete-branch", action="store_true", help="Delete task branch")
    p_remove.add_argument("--json", action="store_true", help="Output JSON")

    # list
    p_list = sub.add_parser("list", help="List all active worktrees")
    p_list.add_argument("--json", action="store_true", help="Output JSON")

    args = parser.parse_args()

    if args.command == "create":
        res = create_worktree(args.task, base=args.base)
    elif args.command == "status":
        res = worktree_status(args.task).to_dict()
    elif args.command == "verify":
        res = verify_worktree(args.task)
    elif args.command == "merge":
        res = merge_worktree(args.task, base=args.base, force=args.force)
    elif args.command == "remove":
        res = remove_worktree(args.task, force=args.force, delete_branch=args.delete_branch)
    elif args.command == "list":
        res = list_worktrees()  # type: ignore[assignment]
    else:
        parser.print_help()
        return 1

    if getattr(args, "json", False):
        print(json.dumps(res, indent=2))
    else:
        if isinstance(res, dict):
            ok = res.get("ok", True)
            status_word = "OK" if ok else "FAILED"
            print(f"[{status_word}] {res}")
        elif isinstance(res, list):
            print(f"Active worktrees ({len(res)}):")
            for item in res:
                print(f"  - {item.get('task')}: {item.get('path')} (branch: {item.get('branch')})")

    return 0 if (isinstance(res, dict) and res.get("ok", True)) or isinstance(res, list) else 1


if __name__ == "__main__":
    sys.exit(main())
