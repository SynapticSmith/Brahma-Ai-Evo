"""Tests for Git Worktree Management Harness (§11.3, Phase 7)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.worktree import (
    create_worktree,
    get_task_path,
    get_worktree_root,
    list_worktrees,
    merge_worktree,
    remove_worktree,
    worktree_status,
)


def test_worktree_paths(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    wt_root = get_worktree_root(root)
    assert wt_root == root / ".brahma" / "worktrees"

    task_dir = get_task_path("my-test-task", root)
    assert task_dir == wt_root / "my-test-task"


def test_create_worktree_invokes_git(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()

    mock_res = MagicMock(returncode=0, stdout="Worktree added\n", stderr="")
    with patch("tools.worktree.run_captured", return_value=mock_res) as mock_run:
        res = create_worktree("experiment-1", base="main", repo_root=root)
        assert res["ok"] is True
        assert res["task"] == "experiment-1"
        assert res["branch"] == "task/experiment-1"

        # Verify git worktree add was invoked
        calls = [c[0][0] for c in mock_run.call_args_list]
        assert any("git worktree add" in c for c in calls)


def test_worktree_status_clean_and_dirty(tmp_path: Path):
    root = tmp_path / "repo"
    task_dir = root / ".brahma" / "worktrees" / "exp-2"
    task_dir.mkdir(parents=True)

    # 1. Clean status
    def mock_run_clean(cmd, **kwargs):
        if "rev-parse HEAD" in cmd:
            return MagicMock(returncode=0, stdout="abcdef123456\n", stderr="")
        if "rev-parse --abbrev-ref" in cmd:
            return MagicMock(returncode=0, stdout="task/exp-2\n", stderr="")
        if "status --porcelain" in cmd:
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("tools.worktree.run_captured", side_effect=mock_run_clean):
        st = worktree_status("exp-2", repo_root=root)
        assert st.exists is True
        assert st.branch == "task/exp-2"
        assert st.head_sha == "abcdef123456"
        assert st.is_dirty is False

    # 2. Dirty status
    def mock_run_dirty(cmd, **kwargs):
        if "status --porcelain" in cmd:
            return MagicMock(returncode=0, stdout=" M modified_file.py\n?? untracked.py\n", stderr="")
        return mock_run_clean(cmd, **kwargs)

    with patch("tools.worktree.run_captured", side_effect=mock_run_dirty):
        st_dirty = worktree_status("exp-2", repo_root=root)
        assert st_dirty.is_dirty is True
        assert st_dirty.untracked_count == 1
        assert st_dirty.modified_count == 1


def test_remove_worktree_refuses_dirty_without_force(tmp_path: Path):
    root = tmp_path / "repo"
    task_dir = root / ".brahma" / "worktrees" / "dirty-task"
    task_dir.mkdir(parents=True)

    def mock_run(cmd, **kwargs):
        if "status --porcelain" in cmd:
            return MagicMock(returncode=0, stdout=" M dirty_file.py\n", stderr="")
        return MagicMock(returncode=0, stdout="ok\n", stderr="")

    with patch("tools.worktree.run_captured", side_effect=mock_run):
        # Attempt without force -> must fail
        res = remove_worktree("dirty-task", repo_root=root, force=False)
        assert res["ok"] is False
        assert "uncommitted changes" in res["error"]

        # Attempt with force -> succeeds
        res_forced = remove_worktree("dirty-task", repo_root=root, force=True)
        assert res_forced["ok"] is True


def test_merge_worktree_fast_forward_only(tmp_path: Path):
    root = tmp_path / "repo"
    task_dir = root / ".brahma" / "worktrees" / "merge-task"
    task_dir.mkdir(parents=True)

    def mock_run(cmd, **kwargs):
        if "status --porcelain" in cmd:
            return MagicMock(returncode=0, stdout="", stderr="")
        if "git merge --ff-only" in cmd:
            return MagicMock(returncode=0, stdout="Updating abc..def\nFast-forward\n", stderr="")
        return MagicMock(returncode=0, stdout="ok\n", stderr="")

    with patch("tools.worktree.run_captured", side_effect=mock_run):
        res = merge_worktree("merge-task", base="main", repo_root=root, force=True)
        assert res["ok"] is True
        assert res["merged_into"] == "main"


def test_list_worktrees(tmp_path: Path):
    root = tmp_path / "repo"
    (root / ".brahma" / "worktrees" / "task-alpha").mkdir(parents=True)
    (root / ".brahma" / "worktrees" / "task-beta").mkdir(parents=True)

    with patch("tools.worktree.run_captured", return_value=MagicMock(returncode=0, stdout="", stderr="")):
        items = list_worktrees(repo_root=root)
        assert len(items) == 2
        tasks = [it["task"] for it in items]
        assert "task-alpha" in tasks
        assert "task-beta" in tasks
