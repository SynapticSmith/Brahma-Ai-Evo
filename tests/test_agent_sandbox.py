"""The ``exec.sandbox`` policy: validation, env scrubbing, prefix, quoting, and confinement probes."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agent.__main__ import main as agent_main
from agent.permissions import ALLOW, DENY, PermissionPolicy, PolicyError
from agent.runtime import AgentRuntime
from agent.sandbox import (
    SandboxError,
    SandboxConfig,
    build_env,
    clear_confinement_cache,
    is_known_unconfined_prefix,
    is_secret_name,
    parse_sandbox,
    prepare_command,
    probe_confinement,
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
    assert described["exec"]["sandbox"]["require_confined"] is False


def test_require_confined_defaults_false_and_validates_boolean():
    assert parse_sandbox(None).require_confined is False
    assert parse_sandbox({"require_confined": True}).require_confined is True
    with pytest.raises(SandboxError):
        parse_sandbox({"require_confined": "yes"})


def test_wsl_and_host_shells_are_rejected_as_unconfined_prefixes():
    for prefix in (
        ("wsl", "sh", "-c"),
        ("C:\\Windows\\System32\\wsl.exe", "--", "sh", "-c"),
        ("cmd.exe", "/c"),
        ("powershell", "-Command"),
        ("bash", "-c"),
    ):
        reason = is_known_unconfined_prefix(prefix)
        assert reason is not None, prefix

    assert is_known_unconfined_prefix(("docker", "run", "--rm", "alpine", "sh", "-c")) is None


def _write_fake_sandbox_runner(script_path: Path, mode: str) -> None:
    """Write a cross-platform Python launcher acting as a sandbox prefix for tests.

    ``mode`` controls which probes pass:
    - ``"unconfined"``: runs the shell command on the host unchanged (fails all 3 probes).
    - ``"net_leaky"``: blocks write_outside and uid_isolation, but allows network_egress.
    - ``"confined"``: blocks write_outside, blocks network_egress, and passes uid_isolation.
    """
    script_path.write_text(
        "import subprocess, sys\n"
        f"mode = {mode!r}\n"
        "cmd = sys.argv[-1]\n"
        "if 'BRAHMA_SANDBOX_PROBE_OK' in cmd:\n"
        "    print('BRAHMA_SANDBOX_PROBE_OK')\n"
        "    sys.exit(0)\n"
        "if 'BRAHMA_OUTSIDE_WRITTEN' in cmd:\n"
        "    if mode == 'unconfined':\n"
        "        subprocess.run(cmd, shell=True)\n"
        "        sys.exit(0)\n"
        "    sys.exit(1)\n"
        "if 'BRAHMA_NET_CONNECTED' in cmd:\n"
        "    if mode in ('unconfined', 'net_leaky'):\n"
        "        subprocess.run(cmd, shell=True)\n"
        "        sys.exit(0)\n"
        "    sys.exit(1)\n"
        "if 'BRAHMA_UID_OK' in cmd:\n"
        "    if mode == 'unconfined':\n"
        "        subprocess.run(cmd, shell=True)\n"
        "        sys.exit(0)\n"
        "    print('BRAHMA_UID_OK')\n"
        "    sys.exit(0)\n"
        "r = subprocess.run(cmd, shell=True)\n"
        "sys.exit(r.returncode)\n",
        encoding="utf-8",
    )


def test_probe_confinement_rejects_empty_and_wsl_prefixes(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()

    empty_report = probe_confinement(SandboxConfig(), ws)
    assert empty_report.confined is False
    assert "no exec.sandbox.prefix" in empty_report.error

    wsl_report = probe_confinement(SandboxConfig(prefix=("wsl", "sh", "-c")), ws)
    assert wsl_report.confined is False
    assert "WSL2" in wsl_report.error
    assert wsl_report.checks[0].name == "prefix_type"
    assert wsl_report.checks[0].passed is False


def test_probe_confinement_detects_unconfined_partial_and_confined_wrappers(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()

    unconfined_runner = tmp_path / "unconfined_runner.py"
    _write_fake_sandbox_runner(unconfined_runner, "unconfined")
    unconfined_cfg = SandboxConfig(prefix=(sys.executable, str(unconfined_runner)))
    rep_unconfined = probe_confinement(unconfined_cfg, ws)
    assert rep_unconfined.confined is False
    by_name = {c.name: c.passed for c in rep_unconfined.checks}
    assert by_name == {
        "write_outside": False,
        "network_egress": False,
        "uid_isolation": False,
    }

    leaky_runner = tmp_path / "leaky_runner.py"
    _write_fake_sandbox_runner(leaky_runner, "net_leaky")
    leaky_cfg = SandboxConfig(prefix=(sys.executable, str(leaky_runner)))
    rep_leaky = probe_confinement(leaky_cfg, ws)
    assert rep_leaky.confined is False
    by_name_leaky = {c.name: c.passed for c in rep_leaky.checks}
    assert by_name_leaky == {
        "write_outside": True,
        "network_egress": False,
        "uid_isolation": True,
    }

    confined_runner = tmp_path / "confined_runner.py"
    _write_fake_sandbox_runner(confined_runner, "confined")
    confined_cfg = SandboxConfig(prefix=(sys.executable, str(confined_runner)))
    rep_confined = probe_confinement(confined_cfg, ws)
    assert rep_confined.confined is True
    by_name_confined = {c.name: c.passed for c in rep_confined.checks}
    assert by_name_confined == {
        "write_outside": True,
        "network_egress": True,
        "uid_isolation": True,
    }


def test_require_confined_blocks_exec_unless_confinement_verified(tmp_path: Path):
    clear_confinement_cache()
    ws = tmp_path / "workspace"
    ws.mkdir()

    # 1. require_confined=True with no prefix -> hard DENY
    policy_no_prefix = PermissionPolicy.from_dict(
        {
            "classes": {"exec": "allow"},
            "exec": {"sandbox": {"require_confined": True, "prefix": []}},
        }
    )
    res_no_prefix = policy_no_prefix.evaluate(
        "run_command",
        {"command": "echo hi"},
        tool_class="exec",
        context_workspace=ws,
    )
    assert res_no_prefix.decision == DENY
    assert res_no_prefix.rule == "exec.sandbox.require_confined"

    # 2. require_confined=True with a verified confined prefix -> ALLOW
    confined_runner = tmp_path / "confined_runner.py"
    _write_fake_sandbox_runner(confined_runner, "confined")
    policy_confined = PermissionPolicy.from_dict(
        {
            "classes": {"exec": "allow"},
            "exec": {
                "sandbox": {
                    "require_confined": True,
                    "prefix": [sys.executable, str(confined_runner)],
                }
            },
        }
    )
    res_confined = policy_confined.evaluate(
        "run_command",
        {"command": "echo hi"},
        tool_class="exec",
        context_workspace=ws,
    )
    assert res_confined.decision == ALLOW

    runtime = AgentRuntime(workspace=ws, policy=policy_no_prefix, headless=True)
    out = runtime.execute(
        "run_command",
        {"command": "echo should_not_run"},
    )
    assert "exec.sandbox.require_confined" in out


def test_cli_sandbox_doctor_reports_without_mutating_policy(tmp_path: Path, capsys):
    clear_confinement_cache()
    ws = tmp_path / "workspace"
    ws.mkdir()
    confined_runner = tmp_path / "confined_runner.py"
    _write_fake_sandbox_runner(confined_runner, "confined")

    policy_file = tmp_path / "agent_permissions.json"
    policy_payload = {
        "exec": {
            "sandbox": {
                "prefix": [sys.executable, str(confined_runner)],
                "require_confined": False,
            }
        }
    }
    original_text = json.dumps(policy_payload, indent=2)
    policy_file.write_text(original_text, encoding="utf-8")

    rc = agent_main(
        [
            "sandbox",
            "doctor",
            "--cwd",
            str(ws),
            "--permissions",
            str(policy_file),
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["confined"] is True
    assert [c["name"] for c in payload["checks"]] == [
        "write_outside",
        "network_egress",
        "uid_isolation",
    ]
    # Doctor reports only — never writes or flips policy on disk.
    assert policy_file.read_text(encoding="utf-8") == original_text

