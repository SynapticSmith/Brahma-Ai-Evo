"""The ``exec.sandbox`` policy: validation, env scrubbing, prefix and quoting."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.permissions import PermissionPolicy, PolicyError
from agent.sandbox import (
    SandboxError,
    SandboxConfig,
    build_env,
    is_secret_name,
    parse_sandbox,
    prepare_command,
    sandbox_from_policy,
)


def test_the_default_sandbox_scrubs_and_has_no_limits():
    config = parse_sandbox(None)

    assert config.prefix == ()
    assert config.env_scrub is True
    assert config.has_limits is False
    assert config.active is True


def test_a_partial_sandbox_keeps_the_secret_default():
    config = parse_sandbox({"max_memory_mb": 256})

    assert config.max_memory_mb == 256
    assert config.env_scrub is True


def test_secret_names_use_component_boundaries():
    for name in (
        "OPENAI_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "GITHUB_TOKEN",
        "npm_config_auth_token",
        "DATABASE_PASSWORD",
        "DATABASE_URL",
        "SSH_PRIVATE_KEY_PATH",
        "PGPASSWORD",
    ):
        assert is_secret_name(name), name


def test_ordinary_names_are_not_secret_looking():
    for name in (
        "PATH",
        "TOKENIZERS_PARALLELISM",
        "KEYBOARD_LAYOUT",
        "PWD",
        "PYTHONPATH",
        "USERPROFILE",
        "PASSENGER_ROOT",
    ):
        assert not is_secret_name(name), name


def test_build_env_withholds_secret_shaped_values():
    env, withheld = build_env(
        SandboxConfig(),
        {
            "PATH": "/bin",
            "OPENAI_API_KEY": "sk-secret",
            "GH_TOKEN": "gh-secret",
            "TOKENIZERS_PARALLELISM": "false",
            "SAFE": "keep",
        },
    )

    assert withheld == 2
    assert env is not None
    assert env["PATH"] == "/bin"
    assert env["TOKENIZERS_PARALLELISM"] == "false"
    assert env["SAFE"] == "keep"
    assert "OPENAI_API_KEY" not in env
    assert "GH_TOKEN" not in env


def test_env_keep_is_case_insensitive():
    env, withheld = build_env(
        SandboxConfig(env_keep=("gh_token",)),
        {"GH_TOKEN": "gh-secret", "GITHUB_TOKEN": "other"},
    )

    assert withheld == 1
    assert env is not None
    assert env["GH_TOKEN"] == "gh-secret"
    assert "GITHUB_TOKEN" not in env


def test_scrubbing_off_means_inherit_everything():
    assert build_env(SandboxConfig(env_scrub=False), {"TOKEN": "x"}) == (None, 0)


def test_no_prefix_keeps_the_shell():
    assert prepare_command("echo hi", (), Path.cwd()) == ("echo hi", True)


def test_a_prefix_runs_without_a_shell_and_appends_the_command():
    argv, use_shell = prepare_command("echo hi", ("docker", "run", "img"), Path("/work"))

    assert use_shell is False
    assert argv == ["docker", "run", "img", "echo hi"]


def test_a_command_placeholder_sets_the_position():
    argv, use_shell = prepare_command(
        "echo hi", ("python", "wrapper.py", "{command}", "--flag"), Path.cwd()
    )

    assert use_shell is False
    assert argv == ["python", "wrapper.py", "echo hi", "--flag"]


def test_cwd_is_substituted_inside_an_item(tmp_path: Path):
    argv, _ = prepare_command(
        "pytest", ("docker", "run", "-v", "{cwd}:/work", "img"), tmp_path
    )

    assert f"{tmp_path}:/work" in argv


def test_command_must_be_a_whole_prefix_item():
    with pytest.raises(SandboxError):
        prepare_command("x", ("python", "--eval={command}"), Path.cwd())
    with pytest.raises(SandboxError):
        prepare_command("x", ("{command}", "{command}"), Path.cwd())


@pytest.mark.parametrize(
    "raw",
    [
        {"prefix": "docker"},
        {"prefix": [""]},
        {"prefix": [1]},
        {"env_scrub": "yes"},
        {"env_keep": "PATH"},
        {"max_memory_mb": -1},
        {"max_memory_mb": True},
        {"max_memory_mb": 1.5},
        {"max_processes": 10_001},
        {"max_cpu_seconds": 86_401},
    ],
)
def test_malformed_sandbox_values_are_rejected(raw):
    with pytest.raises(SandboxError):
        parse_sandbox(raw)


def test_policy_merges_the_default_sandbox_under_a_partial_edit():
    policy = PermissionPolicy.from_dict({"exec": {"sandbox": {"prefix": ["docker"]}}})

    sandbox = policy.data["exec"]["sandbox"]
    assert sandbox["prefix"] == ["docker"]
    assert sandbox["env_scrub"] is True
    assert sandbox["max_memory_mb"] == 0


def test_a_malformed_sandbox_fails_at_policy_load(tmp_path: Path):
    path = tmp_path / "policy.json"
    path.write_text('{"exec": {"sandbox": {"max_processes": -4}}}', encoding="utf-8")

    with pytest.raises(PolicyError):
        PermissionPolicy.load(path)


def test_sandbox_from_policy_reads_the_section():
    policy = PermissionPolicy.from_dict(
        {"exec": {"sandbox": {"env_keep": ["CI"], "max_cpu_seconds": 30}}}
    )

    config = sandbox_from_policy(policy)
    assert config is not None
    assert config.env_keep == ("CI",)
    assert config.max_cpu_seconds == 30
    assert sandbox_from_policy(None) is None


def test_describe_shows_the_exec_sandbox():
    described = PermissionPolicy.from_dict({}).describe()

    assert described["exec"]["sandbox"]["env_scrub"] is True
    assert described["exec"]["sandbox"]["prefix"] == []
