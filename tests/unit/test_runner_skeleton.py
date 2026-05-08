"""Y.2.gate.c.1 / b.14.* skeleton primitives.

Locks the run-id format, the argv normalization (``up_to=<x>`` ↔ ``up_to <x>``),
and the destructive-op refusal pattern. These shapes are stable contracts the
rest of the c-stage implementation (capture, diff, dispatch) builds on.
"""

from __future__ import annotations

import os
import re

import pytest

from quicksight_gen._dev import runner
from quicksight_gen.common.env_keys import (
    QS_E2E_USER_ARN,
    QS_GEN_CONFIG,
    QS_GEN_DEMO_DATABASE_URL,
    QS_GEN_FUZZ_SEED,
    QS_GEN_RUNNER_YES,
    QS_GEN_TEST_L2_INSTANCE,
)


def test_create_run_id_format() -> None:
    """Run-id = `<utc-ts>-<short-sha>[-dirty]`. UTC, sortable, dirty-aware."""
    run_id = runner.create_run_id()
    assert re.match(r"^\d{8}T\d{6}Z-[\w]+(?:-dirty)?$", run_id), run_id


def test_create_run_id_is_unique_per_call() -> None:
    """Two calls in different seconds produce different ids; same-second is OK
    because the runner only creates one run-id per invocation."""
    a = runner.create_run_id()
    b = runner.create_run_id()
    # Same second collision is fine; what matters is the format.
    assert re.match(r"^\d{8}T\d{6}Z-", a)
    assert re.match(r"^\d{8}T\d{6}Z-", b)


def test_normalize_argv_splits_equals_form() -> None:
    """`up_to=<layer>` → `[up_to, <layer>]` so argparse subcommands work."""
    assert runner._normalize_argv(["up_to=unit"]) == ["up_to", "unit"]
    assert runner._normalize_argv(["up_to=unit", "--variants=pg"]) == [
        "up_to",
        "unit",
        "--variants=pg",
    ]


def test_normalize_argv_passthrough_for_space_form() -> None:
    """Space form `up_to <layer>` already-correct; passthrough."""
    assert runner._normalize_argv(["up_to", "unit"]) == ["up_to", "unit"]
    assert runner._normalize_argv(["status"]) == ["status"]
    assert runner._normalize_argv([]) == []


def test_normalize_argv_only_splits_first_token() -> None:
    """Don't accidentally split `--variants=pg` (which lives later in argv)."""
    assert runner._normalize_argv(["up_to", "unit", "--variants=pg"]) == [
        "up_to",
        "unit",
        "--variants=pg",
    ]


def test_destructive_down_refuses_without_yes() -> None:
    """b.14.3 — `down` is destructive; refuse with NEEDS_OPERATOR exit code."""
    code = runner.main(["down"])
    assert code == runner.EXIT_NEEDS_OPERATOR


# Y.2.gate.c.9 — ``sweep`` flipped from "refuse without --yes" to
# "dry-run by default, --yes to delete" so the verb is operator-safe
# at the entry point (matches the standalone script convention).
# The dry-run path needs a working AWS / config setup so it's not
# unit-testable as a no-op refusal — see the dedicated sweep tests
# at the end of the file (test_cmd_sweep_*).


def test_up_to_creates_run_dir() -> None:
    """`up_to=<layer>` returns success when all layers pass — proves the wiring.

    Mocks `dispatch_layer` so the test doesn't recursively run pytest /
    pyright; that's smoke-tested separately."""

    def fake_pass(layer: str, run_dir: Path, options: Any = None, **kwargs: Any) -> runner.LayerResult:
        return runner.LayerResult(layer=layer, exit_code=0, duration_seconds=0.01)

    with patch.object(runner, "dispatch_layer", side_effect=fake_pass):
        code = runner.main(["up_to=unit"])
    assert code == runner.EXIT_SUCCESS


def test_layers_list_matches_audit_table() -> None:
    """Y.2.gate.b.11 lock — runner's LAYERS is the runtime authority; the audit
    doc layer table is the documented mirror. Pyright collapsed into unit
    (2026-05-07): conftest sessionstart handles type-check before pytest
    runs, no separate runner layer.

    b.3.impl.layer (2026-05-07): `app2` inserted between `db` and
    `deploy` per audit §7.10 (App2 = layer 3.7 fast-feedback gate
    against local Docker, before AWS deploy)."""
    assert runner.LAYERS == ("unit", "db", "app2", "deploy", "api", "browser")


# Y.2.gate.c.8 — dependency probe tests.

import io
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch


def _fake_completed(returncode: int, stderr: str = "", stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["fake"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_layer_deps_match_audit_table() -> None:
    """c.14-shape cross-check: layer-to-deps mapping reflects audit §3.

    unit is dependency-free (in-process; pyright runs via conftest sessionstart);
    db needs docker (containers per b.2); app2 needs docker (b.3.impl.layer —
    NO aws because App2 is local-only by audit §7.10);
    deploy/api need aws + docker; browser adds qs_arn for embed signing.
    Edits to either side without the other should fail loudly."""
    assert runner._LAYER_DEPS["unit"] == frozenset()
    assert runner._LAYER_DEPS["db"] == frozenset({"docker"})
    assert runner._LAYER_DEPS["app2"] == frozenset({"docker"})
    assert runner._LAYER_DEPS["deploy"] == frozenset({"aws", "docker"})
    assert runner._LAYER_DEPS["api"] == frozenset({"aws", "docker"})
    assert runner._LAYER_DEPS["browser"] == frozenset({"aws", "docker", "qs_arn"})
    assert "pyright" not in runner._LAYER_DEPS


def test_probe_aws_success_returns_none() -> None:
    with patch.object(runner, "_run_probe_subprocess", return_value=_fake_completed(0, stdout="...arn:...")):
        assert runner._probe_aws() is None


def test_probe_aws_expired_token() -> None:
    with patch.object(
        runner,
        "_run_probe_subprocess",
        return_value=_fake_completed(255, stderr="An error occurred (ExpiredToken) when calling..."),
    ):
        result = runner._probe_aws()
    assert result is not None
    assert result.kind == "aws_creds_expired"
    assert "aws sso login" in result.message


def test_probe_aws_no_creds() -> None:
    with patch.object(
        runner,
        "_run_probe_subprocess",
        return_value=_fake_completed(255, stderr="Unable to locate credentials"),
    ):
        result = runner._probe_aws()
    assert result is not None
    assert result.kind == "aws_no_creds"


def test_probe_aws_cli_missing() -> None:
    with patch.object(runner, "_run_probe_subprocess", return_value=_fake_completed(127, stderr="aws: not found")):
        result = runner._probe_aws()
    assert result is not None
    assert result.kind == "aws_cli_missing"


def test_probe_docker_success_returns_none() -> None:
    with patch.object(runner, "_run_probe_subprocess", return_value=_fake_completed(0)):
        assert runner._probe_docker() is None


def test_probe_docker_daemon_down() -> None:
    with patch.object(
        runner,
        "_run_probe_subprocess",
        return_value=_fake_completed(1, stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock"),
    ):
        result = runner._probe_docker()
    assert result is not None
    assert result.kind == "docker_daemon_down"


def test_probe_docker_cli_missing() -> None:
    with patch.object(runner, "_run_probe_subprocess", return_value=_fake_completed(127, stderr="docker: not found")):
        result = runner._probe_docker()
    assert result is not None
    assert result.kind == "docker_cli_missing"


def test_probe_qs_arn_set(monkeypatch: Any) -> None:
    # 12-digit account ID required by the registry's IAM-ARN validator
    # (Y.2.gate.b.15) — the previous 3-digit test value would now
    # raise EnvVarInvalid at the boundary, defeating the probe.
    monkeypatch.setenv(QS_E2E_USER_ARN.name, "arn:aws:quicksight:us-east-1:111122223333:user/default/test")
    assert runner._probe_qs_e2e_user_arn() is None


def test_probe_qs_arn_unset(monkeypatch: Any) -> None:
    """Probe fails when env var unset AND no cfg auth fallback (h+i.0).

    The cfg-discovery fallback (added in combined h+i.0 spike) lets the
    probe pass when ``run/config.<dialect>.yaml`` carries an ``auth:``
    block — the runner will derive the ARN later. This test monkeypatches
    the cfg-discovery to return None so we exercise the original
    "no env, no cfg auth" failure path.
    """
    monkeypatch.delenv(QS_E2E_USER_ARN.name, raising=False)
    monkeypatch.setattr(runner, "_resolve_seed_config", lambda _candidates: None)
    result = runner._probe_qs_e2e_user_arn()
    assert result is not None
    assert result.kind == "qs_arn_unset"


def test_probe_qs_arn_passes_with_cfg_auth_profile(monkeypatch: Any) -> None:
    """Y.2.gate.h+i.0 — cfg.auth.aws_profile presence satisfies the probe.

    When operator has wired AWS_PROFILE via cfg, the runner will derive
    QS_E2E_USER_ARN via STS+ListUsers in `_run_one_variant`. Probe should
    pre-pass instead of demanding the env var be exported.
    """
    monkeypatch.delenv(QS_E2E_USER_ARN.name, raising=False)
    fake_cfg = SimpleNamespace(
        auth=SimpleNamespace(aws_profile="quicksight-gen-local", quicksight_user_arn=None),
    )
    monkeypatch.setattr(runner, "_resolve_seed_config", lambda _candidates: Path("/tmp/fake-cfg.yaml"))
    monkeypatch.setattr(
        "quicksight_gen.common.config.load_config", lambda _path: fake_cfg,
    )
    assert runner._probe_qs_e2e_user_arn() is None


def test_probe_qs_arn_passes_with_cfg_auth_override(monkeypatch: Any) -> None:
    """Y.2.gate.h+i.0 — explicit cfg.auth.quicksight_user_arn satisfies probe."""
    monkeypatch.delenv(QS_E2E_USER_ARN.name, raising=False)
    fake_cfg = SimpleNamespace(
        auth=SimpleNamespace(
            aws_profile=None,
            quicksight_user_arn="arn:aws:quicksight:us-east-1:111122223333:user/default/test",
        ),
    )
    monkeypatch.setattr(runner, "_resolve_seed_config", lambda _candidates: Path("/tmp/fake-cfg.yaml"))
    monkeypatch.setattr(
        "quicksight_gen.common.config.load_config", lambda _path: fake_cfg,
    )
    assert runner._probe_qs_e2e_user_arn() is None


def test_probe_dependencies_unit_no_deps() -> None:
    """unit layer (which now also runs pyright via conftest) has no external
    deps — pure in-process. Probe always empty."""
    assert runner.probe_dependencies("unit") == []


def test_probe_dependencies_browser_aggregates_failures(monkeypatch: Any) -> None:
    """All three deps fail → all three failures returned in one pass.

    Operator sees everything missing in one go; doesn't have to fix one,
    re-run, hit the next, etc.

    h+i.0 — also nulls the cfg-auth fallback so the qs_arn probe stays in
    its env-only failure path (otherwise an operator's local cfg with an
    `auth:` block would make the qs_arn dep silently pass).
    """
    monkeypatch.delenv(QS_E2E_USER_ARN.name, raising=False)
    monkeypatch.setattr(runner, "_resolve_seed_config", lambda _candidates: None)
    fake_probes = {
        "aws": lambda: runner.ProbeFailure(kind="aws_creds_expired", message="..."),
        "docker": lambda: runner.ProbeFailure(kind="docker_daemon_down", message="..."),
        "qs_arn": runner._probe_qs_e2e_user_arn,
    }
    monkeypatch.setattr(runner, "_PROBE_FUNCTIONS", fake_probes)
    failures = runner.probe_dependencies("browser")
    kinds = {f.kind for f in failures}
    assert kinds == {"aws_creds_expired", "docker_daemon_down", "qs_arn_unset"}


def test_run_probe_subprocess_handles_timeout() -> None:
    """A hanging probe shouldn't lock the runner — synthesize rc=124."""
    with patch.object(
        subprocess,
        "run",
        side_effect=subprocess.TimeoutExpired(cmd=["fake"], timeout=10.0),
    ):
        result = runner._run_probe_subprocess(["fake"])
    assert result.returncode == 124


def test_run_probe_subprocess_handles_missing_binary() -> None:
    """Missing binary → rc=127, doesn't raise."""
    with patch.object(subprocess, "run", side_effect=FileNotFoundError("not found")):
        result = runner._run_probe_subprocess(["bogus-cmd"])
    assert result.returncode == 127


# Y.2.gate.c.4 — auto-prune tests.

import os as _os
import time
from pathlib import Path


def _mk_run_dir(parent: Path, run_id: str, *, mtime: float | None = None) -> Path:
    d = parent / run_id
    d.mkdir(parents=True, exist_ok=True)
    if mtime is not None:
        _os.utime(d, (mtime, mtime))
    return d


def test_prune_old_runs_no_op_on_missing_dir(tmp_path: Path) -> None:
    """No runs/ dir → empty result, no error."""
    nonexistent = tmp_path / "does-not-exist"
    assert runner.prune_old_runs(retain=20, runs_dir=nonexistent) == []


def test_prune_old_runs_no_op_under_threshold(tmp_path: Path) -> None:
    """Fewer than `retain` runs → nothing deleted."""
    for i in range(5):
        _mk_run_dir(tmp_path, f"2026010{i}T120000Z-abc{i}")
    deleted = runner.prune_old_runs(retain=20, runs_dir=tmp_path)
    assert deleted == []
    assert len(list(tmp_path.iterdir())) == 5


def test_prune_old_runs_deletes_oldest(tmp_path: Path) -> None:
    """>retain runs → oldest by mtime go away; newest `retain` survive."""
    base = time.time()
    # Create 5 runs with explicit mtimes (oldest = 0, newest = 4)
    for i in range(5):
        _mk_run_dir(tmp_path, f"2026010{i}T120000Z-abc{i}", mtime=base + i)
    deleted = runner.prune_old_runs(retain=3, runs_dir=tmp_path)
    assert len(deleted) == 2
    surviving = sorted(p.name for p in tmp_path.iterdir())
    # Newest three survive (i=2, i=3, i=4)
    assert surviving == [
        "20260102T120000Z-abc2",
        "20260103T120000Z-abc3",
        "20260104T120000Z-abc4",
    ]


def test_prune_old_runs_only_touches_run_id_pattern(tmp_path: Path) -> None:
    """Defensive — files / non-matching dirs the operator parked under runs/
    are NEVER deleted, even when over the retention threshold."""
    base = time.time()
    # Make 25 real run dirs (well over default N=20).
    for i in range(25):
        _mk_run_dir(tmp_path, f"2026010{i % 9}T1200{i:02d}Z-abc{i:02d}", mtime=base + i)
    # Park unrelated stuff alongside.
    (tmp_path / "operator-notes.txt").write_text("don't delete me")
    (tmp_path / "scratch-dir").mkdir()
    (tmp_path / "scratch-dir" / "file.txt").write_text("...")

    runner.prune_old_runs(retain=20, runs_dir=tmp_path)

    # Operator's stuff still there.
    assert (tmp_path / "operator-notes.txt").exists()
    assert (tmp_path / "scratch-dir").exists()
    assert (tmp_path / "scratch-dir" / "file.txt").exists()
    # Real run dirs pruned to exactly 20.
    run_dirs = [p for p in tmp_path.iterdir() if p.is_dir() and runner._RUN_ID_PATTERN.match(p.name)]
    assert len(run_dirs) == 20


def test_prune_old_runs_uses_mtime_not_name(tmp_path: Path) -> None:
    """If an operator touches an old run, it should survive over a never-touched
    newer one. mtime is the correct sort key."""
    base = time.time()
    # 'old' by name but recently touched
    old_by_name = _mk_run_dir(tmp_path, "20200101T120000Z-old", mtime=base + 100)
    # 'new' by name but not touched recently
    _mk_run_dir(tmp_path, "20991231T120000Z-new", mtime=base - 100)
    runner.prune_old_runs(retain=1, runs_dir=tmp_path)
    survivors = list(tmp_path.iterdir())
    assert len(survivors) == 1
    assert survivors[0] == old_by_name


# Y.2.gate.c.5 — chain + dispatch tests.


def test_chain_through_unit_only() -> None:
    """unit is the start of the chain (pyright collapsed into it via conftest)."""
    assert runner.chain_through("unit") == ["unit"]


def test_chain_through_db() -> None:
    assert runner.chain_through("db") == ["unit", "db"]


def test_chain_through_browser_full() -> None:
    """b.3.impl.layer (2026-05-07): app2 inserted between db and deploy
    per audit §7.10."""
    assert runner.chain_through("browser") == ["unit", "db", "app2", "deploy", "api", "browser"]


def test_chain_through_app2() -> None:
    """b.3.impl.layer — app2 is layer 3.7 (after db, before deploy).
    Operator can stop at app2 to skip the AWS layers entirely."""
    assert runner.chain_through("app2") == ["unit", "db", "app2"]


def test_layer_command_unit_runs_pytest() -> None:
    """Unit layer runs pytest. Conftest sessionstart hook handles pyright
    before any test collects — no QS_GEN_SKIP_PYRIGHT env needed."""
    cmd_env = runner._layer_command("unit", Path("/tmp/run"))
    assert cmd_env is not None
    cmd, env_addl = cmd_env
    assert cmd[0].endswith("pytest")
    assert "QS_GEN_SKIP_PYRIGHT" not in env_addl
    assert env_addl["QS_GEN_RUN_DIR"] == "/tmp/run"
    assert env_addl["QS_GEN_LAYER"] == "unit"


def test_layer_command_db_sets_e2e_gate() -> None:
    """Layer 3 (DB SQL smoke) needs QS_GEN_E2E=1 to bypass the e2e gate."""
    cmd_env = runner._layer_command("db", Path("/tmp/run"))
    assert cmd_env is not None
    cmd, env_addl = cmd_env
    assert "test_dataset_sql_smoke.py" in cmd[1]
    assert env_addl["QS_GEN_E2E"] == "1"


def test_layer_command_app2_dispatches_html2_tests() -> None:
    """b.3.impl.layer — Layer 3.7 (App2 against local Docker) dispatches
    the three test_html2_*.py files. Both stub fetcher tests and the
    live-DB fetcher test land in this layer because all three exercise
    the App2 Starlette server + Playwright path."""
    cmd_env = runner._layer_command("app2", Path("/tmp/run"))
    assert cmd_env is not None
    cmd, env_addl = cmd_env
    cmd_str = " ".join(cmd)
    assert "test_html2_executives.py" in cmd_str
    assert "test_html2_executives_live.py" in cmd_str
    assert "test_html2_money_trail.py" in cmd_str
    # Behind QS_GEN_E2E=1 like every other tests/e2e/ file.
    assert env_addl["QS_GEN_E2E"] == "1"
    assert env_addl["QS_GEN_LAYER"] == "app2"


def test_layer_command_app2_threads_run_dir_env() -> None:
    """app2 runs Playwright; failure traces land under
    `$QS_GEN_RUN_DIR/browser/<test-id>/...` per c.11."""
    cmd_env = runner._layer_command("app2", Path("/tmp/myrun"))
    assert cmd_env is not None
    _, env_addl = cmd_env
    assert env_addl["QS_GEN_RUN_DIR"] == "/tmp/myrun"


def test_layer_command_stub_layers_return_none() -> None:
    """deploy/api/browser are not yet wired (need cfg loading + variants).
    None signals the dispatch path to record skipped=True. (`app2` IS
    wired — see test_layer_command_app2_dispatches_html2_tests.)"""
    for layer in ("deploy", "api", "browser"):
        assert runner._layer_command(layer, Path("/tmp/run")) is None


def test_app2_in_db_touching_layers() -> None:
    """b.3.impl.layer — App2 reads from the variant DB via
    `make_tree_db_fetcher`, so it MUST be in DB_TOUCHING_LAYERS to
    receive QS_GEN_DEMO_DATABASE_URL from setup_variant. Otherwise
    `--variants=local-pg` would seed the container but App2 would
    still try to talk to whatever the cfg file points at."""
    assert "app2" in runner.DB_TOUCHING_LAYERS


def test_dispatch_layer_threads_variant_env_to_app2(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """b.3.impl.layer — variant_env merges into the app2 subprocess
    env so QS_GEN_DEMO_DATABASE_URL is visible to make_tree_db_fetcher
    inside the pytest subprocess. Mirrors the same wiring as the db
    layer test but for app2."""
    captured: dict[str, Any] = {}
    with patch.object(subprocess, "Popen", side_effect=_fake_popen_factory(captured)):
        runner.dispatch_layer(
            "app2", tmp_path, runner.RunOptions(),
            variant_env={"QS_GEN_DEMO_DATABASE_URL": "postgresql://localhost:5432/test"},
        )
    assert captured["env"]["QS_GEN_DEMO_DATABASE_URL"] == "postgresql://localhost:5432/test"


def test_dispatch_layer_stub_returns_skipped() -> None:
    """Stub layers don't fail the chain — they return skipped=True with rc=0."""
    result = runner.dispatch_layer("deploy", Path("/tmp/run"))
    assert result.skipped is True
    assert result.passed is True
    assert result.exit_code == 0


def test_dispatch_layer_runs_real_subprocess(tmp_path: Path) -> None:
    """Real dispatch invokes subprocess.Popen; the result reflects the exit code."""
    captured: dict[str, Any] = {}
    with patch.object(subprocess, "Popen", side_effect=_fake_popen_factory(captured)) as mock_popen:
        result = runner.dispatch_layer("unit", tmp_path)
    assert mock_popen.called
    assert result.passed is True
    assert result.skipped is False
    assert result.exit_code == 0


def test_dispatch_layer_failure_propagates(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}
    with patch.object(
        subprocess, "Popen",
        side_effect=_fake_popen_factory(captured, returncode=1),
    ):
        result = runner.dispatch_layer("unit", tmp_path)
    assert result.passed is False
    assert result.exit_code == 1


def test_dispatch_layer_passes_run_dir_via_env(tmp_path: Path) -> None:
    """QS_GEN_RUN_DIR threads through to the pytest subprocess so conftest
    fixtures (c.10/c.11/c.12) can route artifacts under runs/<run-id>/.
    QS_GEN_FUZZ_SEED also threads (c.6.xdist-safety)."""
    captured: dict[str, Any] = {}
    with patch.object(subprocess, "Popen", side_effect=_fake_popen_factory(captured)):
        runner.dispatch_layer("unit", tmp_path, runner.RunOptions(fuzz_seed_value=42))
    env = captured["env"]
    assert env["QS_GEN_RUN_DIR"] == str(tmp_path)
    assert env["QS_GEN_LAYER"] == "unit"
    assert env["QS_GEN_FUZZ_SEED"] == "42"


def test_cmd_up_to_stops_on_first_failure() -> None:
    """When layer N fails, layers >N are NOT dispatched; chain returns FAILURE."""
    dispatched: list[str] = []

    def fake_dispatch(layer: str, run_dir: Path, options: Any = None, **kwargs: Any) -> runner.LayerResult:
        dispatched.append(layer)
        if layer == "unit":
            return runner.LayerResult(layer=layer, exit_code=1, duration_seconds=0.01)
        return runner.LayerResult(layer=layer, exit_code=0, duration_seconds=0.01)

    with (
        patch.object(runner, "probe_dependencies", return_value=[]),
        patch.object(runner, "dispatch_layer", side_effect=fake_dispatch),
    ):
        code = runner.main(["up_to=db"])
    assert code == runner.EXIT_FAILURE
    # unit dispatched; db should NOT have run because unit failed.
    assert dispatched == ["unit"]


def test_cmd_up_to_runs_full_chain_when_all_pass() -> None:
    dispatched: list[str] = []

    def fake_dispatch(layer: str, run_dir: Path, options: Any = None, **kwargs: Any) -> runner.LayerResult:
        dispatched.append(layer)
        return runner.LayerResult(layer=layer, exit_code=0, duration_seconds=0.01)

    with (
        patch.object(runner, "probe_dependencies", return_value=[]),
        patch.object(runner, "_is_dirty", return_value=False),
        patch.object(runner, "dispatch_layer", side_effect=fake_dispatch),
    ):
        code = runner.main(["up_to=browser"])
    assert code == runner.EXIT_SUCCESS
    assert dispatched == ["unit", "db", "app2", "deploy", "api", "browser"]


def test_prune_runs_pattern_accepts_dirty_suffix() -> None:
    """The pattern must accept the optional `-dirty` suffix from create_run_id."""
    assert runner._RUN_ID_PATTERN.match("20260507T120000Z-abc1234")
    assert runner._RUN_ID_PATTERN.match("20260507T120000Z-abc1234-dirty")
    assert runner._RUN_ID_PATTERN.match("20260507T120000Z-nogit")
    # Negatives — operator-parked stuff should NOT match.
    assert not runner._RUN_ID_PATTERN.match("operator-notes.txt")
    assert not runner._RUN_ID_PATTERN.match("scratch-dir")
    assert not runner._RUN_ID_PATTERN.match("20260507")


# Y.2.gate.c.2 — timings + hashes capture tests.

import json


def test_layer_command_threads_qs_gen_layer_env() -> None:
    """QS_GEN_LAYER must reach pytest subprocesses so conftest hooks know
    which layer's JSONL file to append to."""
    cmd_env = runner._layer_command("unit", Path("/tmp/run"))
    assert cmd_env is not None
    _, env_addl = cmd_env
    assert env_addl["QS_GEN_LAYER"] == "unit"


def test_aggregate_test_jsonl_empty_when_no_files(tmp_path: Path) -> None:
    """Missing timings dir → empty mapping (no error)."""
    assert runner._aggregate_test_jsonl(tmp_path) == {}


def test_aggregate_test_jsonl_reads_one_layer(tmp_path: Path) -> None:
    timings = tmp_path / "timings"
    timings.mkdir()
    (timings / "unit.jsonl").write_text(
        '{"layer": "unit", "test_id": "tests/unit/foo.py::test_a", "duration_seconds": 0.012, "outcome": "passed"}\n'
        '{"layer": "unit", "test_id": "tests/unit/foo.py::test_b", "duration_seconds": 0.005, "outcome": "passed"}\n'
    )
    result = runner._aggregate_test_jsonl(tmp_path)
    assert "unit" in result
    assert result["unit"]["tests/unit/foo.py::test_a"]["duration_seconds"] == 0.012
    assert result["unit"]["tests/unit/foo.py::test_b"]["outcome"] == "passed"


def test_aggregate_test_jsonl_merges_xdist_workers(tmp_path: Path) -> None:
    """c.6 — when xdist is active, each worker writes <layer>-<worker_id>.jsonl;
    aggregator merges them into one layer entry."""
    timings = tmp_path / "timings"
    timings.mkdir()
    (timings / "unit-gw0.jsonl").write_text(
        '{"layer": "unit", "test_id": "tests/unit/a.py::t1", "duration_seconds": 0.01, "outcome": "passed"}\n'
    )
    (timings / "unit-gw1.jsonl").write_text(
        '{"layer": "unit", "test_id": "tests/unit/b.py::t2", "duration_seconds": 0.02, "outcome": "passed"}\n'
    )
    result = runner._aggregate_test_jsonl(tmp_path)
    assert set(result["unit"].keys()) == {"tests/unit/a.py::t1", "tests/unit/b.py::t2"}


def test_collect_run_outputs_writes_timings_json(tmp_path: Path) -> None:
    layer_results = [
        runner.LayerResult(layer="pyright", exit_code=0, duration_seconds=1.91),
        runner.LayerResult(layer="unit", exit_code=0, duration_seconds=10.35),
        runner.LayerResult(layer="deploy", exit_code=0, duration_seconds=0.0, skipped=True),
    ]
    runner.collect_run_outputs(tmp_path, layer_results)
    timings = json.loads((tmp_path / "timings.json").read_text())
    assert timings["layer_durations"] == {"pyright": 1.91, "unit": 10.35}
    assert timings["skipped_layers"] == ["deploy"]
    assert timings["layer_exit_codes"] == {"pyright": 0, "unit": 0, "deploy": 0}


def test_collect_run_outputs_writes_empty_hashes_stub(tmp_path: Path) -> None:
    """c.13 will populate hashes.json; for now it's a stub written iff missing."""
    runner.collect_run_outputs(tmp_path, [])
    assert (tmp_path / "hashes.json").read_text() == "{}\n"


def test_collect_run_outputs_preserves_existing_hashes(tmp_path: Path) -> None:
    """If something else already wrote hashes.json (e.g. a future test fixture
    using c.13's API), don't clobber it."""
    (tmp_path / "hashes.json").write_text('{"seed": "abc123"}\n')
    runner.collect_run_outputs(tmp_path, [])
    assert (tmp_path / "hashes.json").read_text() == '{"seed": "abc123"}\n'


def test_collect_run_outputs_aggregates_test_durations(tmp_path: Path) -> None:
    timings = tmp_path / "timings"
    timings.mkdir()
    (timings / "unit.jsonl").write_text(
        '{"layer": "unit", "test_id": "tests/unit/a.py::t1", "duration_seconds": 0.01, "outcome": "passed"}\n'
    )
    runner.collect_run_outputs(
        tmp_path, [runner.LayerResult(layer="unit", exit_code=0, duration_seconds=2.0)]
    )
    timings_json = json.loads((tmp_path / "timings.json").read_text())
    assert "tests/unit/a.py::t1" in timings_json["test_durations"]["unit"]


# Y.2.gate.c.3 — drift-diff tests.


def _write_timings(run_dir: Path, layer_durations: dict[str, float]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "timings.json").write_text(json.dumps({"layer_durations": layer_durations}) + "\n")


def test_extract_sha_clean() -> None:
    assert runner._extract_sha("20260507T213138Z-9336911") == "9336911"


def test_extract_sha_dirty() -> None:
    assert runner._extract_sha("20260507T213138Z-9336911-dirty") == "9336911"


def test_drift_entry_delta_pct() -> None:
    entry = runner.DriftEntry(layer="unit", current_seconds=15.0, prior_seconds=10.0)
    assert entry.delta_pct == 0.5  # +50%
    assert entry.is_drift is True  # exactly at threshold = trigger


def test_drift_entry_under_threshold() -> None:
    entry = runner.DriftEntry(layer="unit", current_seconds=11.0, prior_seconds=10.0)
    assert entry.delta_pct == pytest.approx(0.1)
    assert entry.is_drift is False


def test_drift_entry_no_prior() -> None:
    entry = runner.DriftEntry(layer="unit", current_seconds=10.0, prior_seconds=None)
    assert entry.delta_pct is None
    assert entry.is_drift is False


def test_drift_entry_zero_prior_avoids_div_by_zero() -> None:
    entry = runner.DriftEntry(layer="unit", current_seconds=10.0, prior_seconds=0.0)
    assert entry.delta_pct is None  # we skip rather than divide by zero
    assert entry.is_drift is False


def test_find_prior_run_no_runs_dir(tmp_path: Path) -> None:
    """Missing runs/ → None, not a crash."""
    assert runner.find_prior_run("20260507T120000Z-abc", runs_dir=tmp_path / "missing") is None


def test_find_prior_run_no_other_runs(tmp_path: Path) -> None:
    """Only the current run exists → None."""
    _write_timings(tmp_path / "20260507T120000Z-abc", {"unit": 10.0})
    assert runner.find_prior_run("20260507T120000Z-abc", runs_dir=tmp_path) is None


def test_find_prior_run_prefers_same_sha(tmp_path: Path) -> None:
    """Two priors: one with same SHA, one with different SHA + more recent.
    Same-SHA wins (same-code comparison is the closest signal)."""
    _write_timings(tmp_path / "20260101T120000Z-abc", {"unit": 10.0})
    same_sha = tmp_path / "20260102T120000Z-abc"
    _write_timings(same_sha, {"unit": 11.0})
    base = time.time()
    os.utime(same_sha, (base, base))
    diff_sha = tmp_path / "20260507T120000Z-xyz"
    _write_timings(diff_sha, {"unit": 30.0})
    os.utime(diff_sha, (base + 100, base + 100))  # newer mtime
    found = runner.find_prior_run("20260508T120000Z-abc", runs_dir=tmp_path)
    assert found == same_sha


def test_find_prior_run_falls_back_to_most_recent(tmp_path: Path) -> None:
    """No same-SHA prior → most-recent overall."""
    base = time.time()
    older = tmp_path / "20260101T120000Z-old"
    _write_timings(older, {"unit": 10.0})
    os.utime(older, (base - 100, base - 100))
    newer = tmp_path / "20260102T120000Z-new"
    _write_timings(newer, {"unit": 11.0})
    os.utime(newer, (base, base))
    found = runner.find_prior_run("20260507T120000Z-current", runs_dir=tmp_path)
    assert found == newer


def test_find_prior_run_skips_runs_without_timings(tmp_path: Path) -> None:
    """A run dir without timings.json (incomplete / failed early) is not a candidate."""
    incomplete = tmp_path / "20260101T120000Z-old"
    incomplete.mkdir()
    # No timings.json
    valid = tmp_path / "20260102T120000Z-new"
    _write_timings(valid, {"unit": 11.0})
    found = runner.find_prior_run("20260507T120000Z-current", runs_dir=tmp_path)
    assert found == valid


def test_compute_drift_layer_in_both() -> None:
    current = {"layer_durations": {"unit": 15.0}}
    prior = {"layer_durations": {"unit": 10.0}}
    entries = runner.compute_drift(current, prior)
    assert len(entries) == 1
    assert entries[0].layer == "unit"
    assert entries[0].delta_pct == 0.5


def test_compute_drift_layer_only_in_current() -> None:
    """New layer (not in prior) → entry with prior=None, no drift."""
    current = {"layer_durations": {"unit": 10.0, "db": 25.0}}
    prior = {"layer_durations": {"unit": 9.5}}
    entries = runner.compute_drift(current, prior)
    by_layer = {e.layer: e for e in entries}
    assert by_layer["unit"].prior_seconds == 9.5
    assert by_layer["db"].prior_seconds is None


def test_compute_drift_ignores_layer_only_in_prior() -> None:
    """Chain narrowing (`up_to=unit` after a prior `up_to=browser`) → don't
    spam drift entries for layers we didn't run this time."""
    current = {"layer_durations": {"unit": 10.0}}
    prior = {"layer_durations": {"unit": 9.5, "db": 24.0, "deploy": 90.0}}
    entries = runner.compute_drift(current, prior)
    assert {e.layer for e in entries} == {"unit"}


def test_report_drift_no_prior_prints_and_returns(tmp_path: Path, capsys: Any) -> None:
    """First run ever → print "no prior" + return cleanly (no crash)."""
    current = tmp_path / "runs" / "20260507T120000Z-abc"
    _write_timings(current, {"unit": 10.0})
    runner.report_drift(current, runs_dir=tmp_path / "runs")
    out = capsys.readouterr().out
    assert "no prior run" in out


def test_report_drift_marks_over_threshold(tmp_path: Path, capsys: Any) -> None:
    """+50% → ⚠ marker; +49% → no marker."""
    runs = tmp_path / "runs"
    prior = runs / "20260101T120000Z-abc"
    _write_timings(prior, {"unit": 10.0, "db": 20.0})
    current = runs / "20260102T120000Z-abc"
    _write_timings(current, {"unit": 14.9, "db": 31.0})  # unit +49%, db +55%
    runner.report_drift(current, runs_dir=runs)
    out = capsys.readouterr().out
    # unit is under threshold — no warning
    unit_line = [line for line in out.splitlines() if "drift: unit" in line][0]
    assert "⚠" not in unit_line
    # db over threshold — warning
    db_line = [line for line in out.splitlines() if "drift: db" in line][0]
    assert "⚠" in db_line


# Y.2.gate.c.7 — flag plumbing tests.


def test_run_options_defaults() -> None:
    """Default options match audit locks: variants=default, fuzz_seeds=1
    (b.1 lock), all booleans False, only=None."""
    opts = runner.RunOptions()
    assert opts.only is None
    assert opts.variants == "default"
    assert opts.fuzz_seeds == 1
    assert opts.skip_cheap is False
    assert opts.keep_on_failure is False
    assert opts.trace_all is False
    assert opts.allow_dirty_deploy is False


def test_layer_command_only_adds_pytest_k_flag() -> None:
    """--only threads through to `pytest -k <expr>` for layers running pytest."""
    opts = runner.RunOptions(only="test_drift")
    cmd_env = runner._layer_command("unit", Path("/tmp/run"), opts)
    assert cmd_env is not None
    cmd, _ = cmd_env
    assert "-k" in cmd
    assert cmd[cmd.index("-k") + 1] == "test_drift"


def test_layer_command_unknown_layer_returns_none() -> None:
    """Layers not in LAYERS — including the dropped 'pyright' — return None
    (handled as 'not yet wired' stub by dispatch)."""
    assert runner._layer_command("pyright", Path("/tmp/run")) is None
    assert runner._layer_command("nonsense", Path("/tmp/run")) is None


def test_layer_command_trace_all_sets_env() -> None:
    """--trace-all sets QS_GEN_TRACE_ALL=1 in subprocess env (consumed by c.11)."""
    opts = runner.RunOptions(trace_all=True)
    cmd_env = runner._layer_command("unit", Path("/tmp/run"), opts)
    assert cmd_env is not None
    _, env_addl = cmd_env
    assert env_addl.get("QS_GEN_TRACE_ALL") == "1"


def test_layer_command_no_trace_env_when_default() -> None:
    """Default options don't set QS_GEN_TRACE_ALL — only opt-in adds it."""
    cmd_env = runner._layer_command("unit", Path("/tmp/run"))
    assert cmd_env is not None
    _, env_addl = cmd_env
    assert "QS_GEN_TRACE_ALL" not in env_addl


def test_is_deploy_or_later() -> None:
    """Layers 4+ touch external state; dirty-state refusal applies only there."""
    assert runner._is_deploy_or_later("unit") is False
    assert runner._is_deploy_or_later("db") is False
    assert runner._is_deploy_or_later("deploy") is True
    assert runner._is_deploy_or_later("api") is True
    assert runner._is_deploy_or_later("browser") is True


def test_cmd_up_to_dirty_refuses_at_deploy_layer(monkeypatch: Any) -> None:
    """b.10 — `up_to=deploy` (or higher) refuses on tracked-changes dirty
    state. NEEDS_OPERATOR exit, message tells operator what to do."""
    monkeypatch.delenv(QS_GEN_RUNNER_YES.name, raising=False)
    with patch.object(runner, "_is_dirty", return_value=True):
        code = runner.main(["up_to=deploy"])
    assert code == runner.EXIT_NEEDS_OPERATOR


def test_cmd_up_to_dirty_ok_below_deploy(monkeypatch: Any) -> None:
    """Layers 1-3 (pyright/unit/db) are local + idempotent; dirty state OK."""
    monkeypatch.delenv(QS_GEN_RUNNER_YES.name, raising=False)

    def fake_dispatch(layer: str, run_dir: Path, options: Any = None, **kwargs: Any) -> runner.LayerResult:
        return runner.LayerResult(layer=layer, exit_code=0, duration_seconds=0.01)

    with (
        patch.object(runner, "_is_dirty", return_value=True),
        patch.object(runner, "probe_dependencies", return_value=[]),
        patch.object(runner, "dispatch_layer", side_effect=fake_dispatch),
    ):
        code = runner.main(["up_to=db"])
    assert code == runner.EXIT_SUCCESS


def test_cmd_up_to_allow_dirty_deploy_bypasses(monkeypatch: Any) -> None:
    """`--allow-dirty-deploy` bypasses the b.10 refusal."""
    monkeypatch.delenv(QS_GEN_RUNNER_YES.name, raising=False)

    def fake_dispatch(layer: str, run_dir: Path, options: Any = None, **kwargs: Any) -> runner.LayerResult:
        return runner.LayerResult(layer=layer, exit_code=0, duration_seconds=0.01)

    with (
        patch.object(runner, "_is_dirty", return_value=True),
        patch.object(runner, "probe_dependencies", return_value=[]),
        patch.object(runner, "dispatch_layer", side_effect=fake_dispatch),
    ):
        code = runner.main(["up_to=deploy", "--allow-dirty-deploy"])
    assert code == runner.EXIT_SUCCESS


def test_cmd_up_to_qs_gen_runner_yes_env_bypasses_dirty(monkeypatch: Any) -> None:
    """QS_GEN_RUNNER_YES=1 also bypasses b.10 (mirrors b.14.3 destructive-op
    convention so the env var works for both flag families)."""
    monkeypatch.setenv(QS_GEN_RUNNER_YES.name, "1")

    def fake_dispatch(layer: str, run_dir: Path, options: Any = None, **kwargs: Any) -> runner.LayerResult:
        return runner.LayerResult(layer=layer, exit_code=0, duration_seconds=0.01)

    with (
        patch.object(runner, "_is_dirty", return_value=True),
        patch.object(runner, "probe_dependencies", return_value=[]),
        patch.object(runner, "dispatch_layer", side_effect=fake_dispatch),
    ):
        code = runner.main(["up_to=deploy"])
    assert code == runner.EXIT_SUCCESS


def test_argparse_accepts_all_c7_flags() -> None:
    """Smoke: every c.7 flag parses without error. Catches typos / dest collisions."""
    parser = runner._build_parser()
    parsed = parser.parse_args(
        [
            "up_to",
            "unit",
            "--only=test_foo",
            "--variants=full",
            "--fuzz-seeds=10",
            "--skip-cheap",
            "--keep-on-failure",
            "--trace-all",
            "--allow-dirty-deploy",
        ]
    )
    assert parsed.layer == "unit"
    assert parsed.only == "test_foo"
    assert parsed.variants == "full"
    assert parsed.fuzz_seeds == 10
    assert parsed.skip_cheap is True
    assert parsed.keep_on_failure is True
    assert parsed.trace_all is True
    assert parsed.allow_dirty_deploy is True


def test_options_from_args_threads_correctly() -> None:
    """_options_from_args produces a RunOptions matching the parsed args."""
    parser = runner._build_parser()
    parsed = parser.parse_args(["up_to", "unit", "--only=test_foo", "--trace-all"])
    opts = runner._options_from_args(parsed)
    assert opts.only == "test_foo"
    assert opts.trace_all is True
    assert opts.allow_dirty_deploy is False  # not passed


# Y.2.gate.c.14 — dispatch-table cross-check (per b.11 lock):
# runner.py is the runtime authority; audit doc §3 is the documented mirror.
# Spot-check high-signal cells so a one-sided edit fails loudly without us
# building a full markdown parser. Keep the sample small + intentional —
# exhaustive coverage is not the goal.

_AUDIT_DOC_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "audits" / "y_2_gate_test_layer_chain_audit.md"
)


def _read_audit_text() -> str:
    return _AUDIT_DOC_PATH.read_text(encoding="utf-8")


def test_audit_doc_exists() -> None:
    """Foundational: the audit doc is at the expected path. If this fails the
    other c.14 tests are unhelpful — flag the path drift first."""
    assert _AUDIT_DOC_PATH.exists(), f"audit doc not found at {_AUDIT_DOC_PATH}"


def test_audit_layers_table_mentions_every_runner_layer() -> None:
    """Every runner LAYERS entry needs documentation in the audit. Maps
    runner names to audit-table descriptions; if the audit renames a row
    or runner adds a layer, this catches the drift.

    Pyright is NOT a runner layer (collapsed into unit via conftest sessionstart
    per 2026-05-07 lock); audit still documents it as conceptual layer 1."""
    audit = _read_audit_text()
    runner_to_audit_name = {
        "unit": "Unit + JSON tests",
        "db": "DB SQL smoke",
        # b.3.impl.layer (2026-05-07) — App2 promoted from layer 7
        # to layer 3.7 per audit §7.10 ("App2 against local Docker
        # as the early e2e gate"). Audit table row 7 still reads
        # "App2 (HTMX) live e2e"; that phrase covers both placements.
        "app2": "App2 (HTMX) live e2e",
        "deploy": "Deploy",
        "api": "API e2e",
        "browser": "Browser e2e",
    }
    assert set(runner_to_audit_name.keys()) == set(runner.LAYERS), (
        "test mapping out of sync with runner.LAYERS — update the dict above"
    )
    for runner_name, audit_phrase in runner_to_audit_name.items():
        assert audit_phrase in audit, (
            f"audit doc missing layer description for runner '{runner_name}' "
            f"(expected phrase: '{audit_phrase}')"
        )


def test_audit_calls_pyright_pure_static_check() -> None:
    """High-signal cell: audit row 1 (pyright) still describes it as 'pure
    static check'. Pyright isn't a runner LAYER anymore (collapsed into unit
    via conftest sessionstart) but it's still a documented conceptual gate;
    the audit row should still characterize it as type-only / no externals."""
    audit = _read_audit_text()
    assert "pure static check" in audit
    assert "pyright" not in runner._LAYER_DEPS  # not a runner LAYER


def test_audit_calls_out_qs_e2e_user_arn_for_browser() -> None:
    """High-signal cell: audit row 6 (Browser e2e) lists 'QS_E2E_USER_ARN ...
    required'. Runner reflects: 'qs_arn' in browser deps. If audit drops the
    requirement OR runner removes 'qs_arn' from browser, drift gets flagged."""
    assert "qs_arn" in runner._LAYER_DEPS["browser"]
    audit = _read_audit_text()
    assert "QS_E2E_USER_ARN" in audit and "required" in audit


def test_audit_calls_out_aws_creds_for_deploy() -> None:
    """High-signal cell: audit row 4 (Deploy) preconditions = 'AWS creds valid'.
    Runner reflects: 'aws' in deploy deps."""
    assert "aws" in runner._LAYER_DEPS["deploy"]
    audit = _read_audit_text()
    assert "AWS creds valid" in audit


def test_audit_distinguishes_pg_oracle_dialect_axis() -> None:
    """High-signal cell: audit §3 has separate rows for Dialect: PG and
    Dialect: Oracle (audit-side asymmetry: layer 3c is psycopg-only, layer 6
    Oracle is cron-only). The runner's docker dep covers BOTH dialect cells
    today; the future c.6 dialect-fan-out will split. This test pins the
    audit's PG/Oracle row distinction so b.6 doesn't accidentally erase it."""
    audit = _read_audit_text()
    assert "**Dialect: PG**" in audit
    assert "**Dialect: Oracle**" in audit
    # And the explicit asymmetry note (§3 'Notes:' bullet about layer 3c).
    assert "psycopg" in audit  # the reason layer 3c is PG-only


def test_audit_first_layer_pyright_has_no_external_preconditions() -> None:
    """Audit table row 1 'Preconditions' column should still say 'None'.
    Pyright runs via repo-root conftest sessionstart (not a runner layer),
    but the audit still documents the no-externals contract."""
    audit = _read_audit_text()
    assert "None — pure static check" in audit
    assert "pyright" not in runner._LAYER_DEPS


# Y.2.gate.c.6 — within-variant parallelism (--parallel=N → pytest -n N).


def test_run_options_parallel_default_is_one() -> None:
    """Default = serial (1 worker). Operator opts into parallelism."""
    assert runner.RunOptions().parallel == 1


def test_layer_command_no_n_flag_when_parallel_one() -> None:
    """parallel=1 → no -n flag (let pytest run inline; cleaner output for
    iteration-loop runs)."""
    cmd_env = runner._layer_command("unit", Path("/tmp/run"), runner.RunOptions(parallel=1))
    assert cmd_env is not None
    cmd, _ = cmd_env
    assert "-n" not in cmd


def test_layer_command_adds_n_flag_when_parallel_gt_one() -> None:
    """parallel=4 → pytest -n 4 (xdist)."""
    cmd_env = runner._layer_command("unit", Path("/tmp/run"), runner.RunOptions(parallel=4))
    assert cmd_env is not None
    cmd, _ = cmd_env
    assert "-n" in cmd
    assert cmd[cmd.index("-n") + 1] == "4"


def test_layer_command_n_flag_threads_to_db_layer() -> None:
    """db layer (3a smoke) also takes -n; valuable for the 37 datasets."""
    cmd_env = runner._layer_command("db", Path("/tmp/run"), runner.RunOptions(parallel=4))
    assert cmd_env is not None
    cmd, _ = cmd_env
    assert "-n" in cmd


def test_argparse_accepts_parallel_int() -> None:
    parser = runner._build_parser()
    parsed = parser.parse_args(["up_to", "unit", "--parallel=8"])
    assert parsed.parallel == 8


def test_argparse_parallel_default_is_one() -> None:
    parser = runner._build_parser()
    parsed = parser.parse_args(["up_to", "unit"])
    assert parsed.parallel == 1


def test_options_from_args_threads_parallel() -> None:
    parser = runner._build_parser()
    parsed = parser.parse_args(["up_to", "unit", "--parallel=4"])
    opts = runner._options_from_args(parsed)
    assert opts.parallel == 4


# Y.2.gate.b.14 — pyright verb (fast type-check; no pytest).


def test_argparse_accepts_pyright_verb_no_args() -> None:
    """`./run_tests.sh pyright` parses with no paths (defaults to strict-include set)."""
    parser = runner._build_parser()
    parsed = parser.parse_args(["pyright"])
    assert parsed.verb == "pyright"
    assert parsed.paths == []


def test_argparse_accepts_pyright_verb_with_paths() -> None:
    """`./run_tests.sh pyright src/foo.py src/bar.py` collects multiple paths."""
    parser = runner._build_parser()
    parsed = parser.parse_args(["pyright", "src/foo.py", "src/bar.py"])
    assert parsed.paths == ["src/foo.py", "src/bar.py"]


def test_cmd_pyright_no_paths_runs_default() -> None:
    """No paths → pyright invoked with no args (strict-include set from pyproject.toml)."""
    fake = subprocess.CompletedProcess(args=["fake"], returncode=0, stdout="", stderr="")
    with patch.object(subprocess, "run", return_value=fake) as mock_run:
        code = runner.main(["pyright"])
    assert code == runner.EXIT_SUCCESS
    cmd = mock_run.call_args.args[0]
    assert cmd[0].endswith("pyright")
    assert len(cmd) == 1  # no paths appended


def test_cmd_pyright_with_paths_passes_through() -> None:
    """Paths thread to pyright argv."""
    fake = subprocess.CompletedProcess(args=["fake"], returncode=0, stdout="", stderr="")
    with patch.object(subprocess, "run", return_value=fake) as mock_run:
        code = runner.main(["pyright", "src/foo.py"])
    assert code == runner.EXIT_SUCCESS
    cmd = mock_run.call_args.args[0]
    assert cmd[-1] == "src/foo.py"


def test_cmd_pyright_failure_returns_failure_exit() -> None:
    """Type errors → EXIT_FAILURE (rc=1) so the chain `&&`-and-continue pattern halts."""
    fake = subprocess.CompletedProcess(args=["fake"], returncode=1, stdout="", stderr="")
    with patch.object(subprocess, "run", return_value=fake):
        code = runner.main(["pyright"])
    assert code == runner.EXIT_FAILURE


# Y.2.gate.c.6.xdist-safety — fuzz seed value resolution + env passthrough.


def test_resolve_fuzz_seed_value_random_when_unset(monkeypatch: Any) -> None:
    """No env override → fresh random seed each call. Two calls likely
    different (32-bit space; collision odds vanishingly small)."""
    monkeypatch.delenv(QS_GEN_FUZZ_SEED.name, raising=False)
    a = runner.resolve_fuzz_seed_value()
    b = runner.resolve_fuzz_seed_value()
    assert isinstance(a, int) and 0 <= a < 2**32
    assert a != b  # not pinned; would be flaky with 1-in-4-billion odds


def test_resolve_fuzz_seed_value_honors_env(monkeypatch: Any) -> None:
    """`QS_GEN_FUZZ_SEED=N` env → operator pin for failure repro. All workers
    in this run see the same value."""
    monkeypatch.setenv(QS_GEN_FUZZ_SEED.name, "12345")
    assert runner.resolve_fuzz_seed_value() == 12345


def test_resolve_fuzz_seed_value_random_on_blank_env(monkeypatch: Any) -> None:
    """Blank env (e.g. accidentally exported empty) → fall back to random,
    not crash on int('')."""
    monkeypatch.setenv(QS_GEN_FUZZ_SEED.name, "")
    seed = runner.resolve_fuzz_seed_value()
    assert isinstance(seed, int)


def test_run_options_fuzz_seed_value_default_none() -> None:
    """RunOptions() default = None; resolution happens in _options_from_args.
    A None value means 'don't set the env at all' (preserves existing env if
    operator set it some other way)."""
    assert runner.RunOptions().fuzz_seed_value is None


def test_options_from_args_resolves_fuzz_seed(monkeypatch: Any) -> None:
    """_options_from_args populates fuzz_seed_value (random unless env pinned)."""
    monkeypatch.setenv(QS_GEN_FUZZ_SEED.name, "98765")
    parser = runner._build_parser()
    parsed = parser.parse_args(["up_to", "unit"])
    opts = runner._options_from_args(parsed)
    assert opts.fuzz_seed_value == 98765


def test_layer_command_threads_qs_gen_fuzz_seed_env() -> None:
    """fuzz_seed_value → QS_GEN_FUZZ_SEED env passed to subprocess. All xdist
    workers inherit; parametrize collection is deterministic."""
    opts = runner.RunOptions(fuzz_seed_value=42)
    cmd_env = runner._layer_command("unit", Path("/tmp/run"), opts)
    assert cmd_env is not None
    _, env_addl = cmd_env
    assert env_addl.get("QS_GEN_FUZZ_SEED") == "42"


def test_layer_command_no_fuzz_env_when_value_none() -> None:
    """fuzz_seed_value=None → no QS_GEN_FUZZ_SEED in env_addl. Preserves
    operator's externally-set env (if any) without us shadowing it."""
    cmd_env = runner._layer_command("unit", Path("/tmp/run"), runner.RunOptions())
    assert cmd_env is not None
    _, env_addl = cmd_env
    assert "QS_GEN_FUZZ_SEED" not in env_addl


# Y.2.gate.c.9 — sweep subcommand.


def test_argparse_accepts_sweep_verb_no_yes() -> None:
    """`./run_tests.sh sweep` parses (default = dry-run)."""
    parser = runner._build_parser()
    parsed = parser.parse_args(["sweep"])
    assert parsed.verb == "sweep"
    assert parsed.yes is False


def test_argparse_accepts_sweep_verb_with_yes() -> None:
    """`./run_tests.sh sweep --yes` parses (destructive opt-in)."""
    parser = runner._build_parser()
    parsed = parser.parse_args(["sweep", "--yes"])
    assert parsed.yes is True


def _install_fake_harness_cleanup(
    monkeypatch: Any, *, matched: dict[str, list[tuple[str, str]]],
    deleted: dict[str, int] | None = None,
) -> tuple[list[Any], list[Any]]:
    """Inject a fake `_harness_cleanup` module so cmd_sweep's runtime
    import doesn't need the real tests/e2e/_harness_cleanup. Returns
    (collect_calls, sweep_calls) lists that capture invocations."""
    import sys
    import types

    collect_calls: list[Any] = []
    sweep_calls: list[Any] = []

    def fake_collect(client: Any, account_id: str, *, tag_key: str, tag_value: str) -> dict[str, list[tuple[str, str]]]:
        collect_calls.append((client, account_id, tag_key, tag_value))
        return matched

    def fake_sweep(client: Any, account_id: str, *, tag_key: str, tag_value: str) -> dict[str, int]:
        sweep_calls.append((client, account_id, tag_key, tag_value))
        return deleted or {k: len(v) for k, v in matched.items()}

    fake_module = types.ModuleType("_harness_cleanup")
    fake_module._collect_resources_matching_tag = fake_collect  # type: ignore[attr-defined]: monkey-patching test attrs onto a fake ModuleType
    fake_module.sweep_qs_resources_by_tag = fake_sweep  # type: ignore[attr-defined]: monkey-patching test attrs onto a fake ModuleType
    monkeypatch.setitem(sys.modules, "_harness_cleanup", fake_module)
    return collect_calls, sweep_calls


def _install_fake_aws(monkeypatch: Any) -> None:
    """Stub load_config + boto3.client so cmd_sweep doesn't need
    real config files or AWS creds for the unit-test path."""
    import boto3
    from quicksight_gen.common import config as config_mod

    fake_cfg = type(
        "CfgStub", (),
        {"aws_region": "us-east-1", "aws_account_id": "111122223333"},
    )()
    monkeypatch.setattr(config_mod, "load_config", lambda _: fake_cfg)
    monkeypatch.setattr(boto3, "client", lambda *a, **kw: object())
    # Force the config-path probe to succeed by pointing it at any
    # existing file (PLAN.md is fine — we only check existence, the
    # file content is read by the stubbed load_config).
    monkeypatch.setenv(QS_GEN_CONFIG.name, str(runner.REPO_ROOT / "PLAN.md"))


def test_cmd_sweep_dry_run_collects_without_deleting(
    monkeypatch: Any, capsys: Any,
) -> None:
    """No --yes → calls _collect, never calls sweep_qs_resources."""
    _install_fake_aws(monkeypatch)
    monkeypatch.delenv(QS_GEN_RUNNER_YES.name, raising=False)
    collect_calls, sweep_calls = _install_fake_harness_cleanup(
        monkeypatch,
        matched={
            "dashboard": [],
            "analysis": [],
            "dataset": [("orphan-1", "arn:orphan-1")],
            "datasource": [],
            "theme": [],
        },
    )

    code = runner.main(["sweep"])
    assert code == runner.EXIT_SUCCESS
    assert len(collect_calls) == 1
    assert len(sweep_calls) == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "orphan-1" in out
    assert "re-run with --yes" in out


def test_cmd_sweep_with_yes_invokes_sweep(monkeypatch: Any, capsys: Any) -> None:
    """--yes → sweep_qs_resources_by_tag is called; not _collect."""
    _install_fake_aws(monkeypatch)
    monkeypatch.delenv(QS_GEN_RUNNER_YES.name, raising=False)
    collect_calls, sweep_calls = _install_fake_harness_cleanup(
        monkeypatch,
        matched={
            "dashboard": [], "analysis": [],
            "dataset": [("orphan-1", "arn:orphan-1")],
            "datasource": [], "theme": [],
        },
    )

    code = runner.main(["sweep", "--yes"])
    assert code == runner.EXIT_SUCCESS
    assert len(sweep_calls) == 1
    out = capsys.readouterr().out
    assert "deleting" in out.lower()
    assert "deleted" in out.lower()


def test_cmd_sweep_qs_gen_runner_yes_env_bypasses_yes_flag(
    monkeypatch: Any,
) -> None:
    """`QS_GEN_RUNNER_YES=1` env matches `--yes` per the b.14.3
    destructive-op convention."""
    _install_fake_aws(monkeypatch)
    monkeypatch.setenv(QS_GEN_RUNNER_YES.name, "1")
    _, sweep_calls = _install_fake_harness_cleanup(
        monkeypatch,
        matched={
            "dashboard": [], "analysis": [], "dataset": [],
            "datasource": [], "theme": [],
        },
    )

    code = runner.main(["sweep"])
    assert code == runner.EXIT_SUCCESS
    assert len(sweep_calls) == 1


def test_cmd_sweep_no_config_file_returns_needs_operator(
    monkeypatch: Any, tmp_path: Any, capsys: Any,
) -> None:
    """When no config.yaml is discoverable, exit needs-operator
    instead of crashing on a missing file."""
    monkeypatch.delenv(QS_GEN_CONFIG.name, raising=False)
    # Point REPO_ROOT at an empty tmp dir so the candidate paths
    # (run/config.yaml, config.yaml, run/config.{postgres,oracle}.yaml)
    # all resolve to non-existent files.
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)

    code = runner.main(["sweep"])
    assert code == runner.EXIT_NEEDS_OPERATOR
    err = capsys.readouterr().err
    assert "no config.yaml" in err.lower()


# Y.2.gate.b.8.impl — skip-if-already-green cache.


def _stub_short_sha(monkeypatch: Any, value: str = "deadbeef") -> None:
    monkeypatch.setattr(runner, "_short_sha", lambda: value)
    monkeypatch.setattr(runner, "_is_dirty", lambda: False)


def test_write_cache_marker_creates_per_sha_per_layer_file(
    monkeypatch: Any, tmp_path: Any,
) -> None:
    """write_cache_marker → file at <cache>/<sha>.<layer>.json with
    matching shape."""
    _stub_short_sha(monkeypatch, "abc1234")
    monkeypatch.setattr(runner, "RUN_TESTS_CACHE_DIR", tmp_path / ".cache")

    runner.write_cache_marker("unit", duration_seconds=1.5)

    # Default variant gets included in the filename for variant-aware
    # cache (Y.2.gate.b.2.impl).
    marker = tmp_path / ".cache" / "abc1234.unit.default.json"
    assert marker.exists()
    import json as _json
    data = _json.loads(marker.read_text())
    assert data["sha"] == "abc1234"
    assert data["layer"] == "unit"
    assert data["variant"] == "default"
    assert data["duration_seconds"] == 1.5
    assert data["passed_at"]  # iso timestamp


def test_write_cache_marker_no_op_on_dirty_sha(
    monkeypatch: Any, tmp_path: Any,
) -> None:
    """Dirty SHA = don't pollute the cache. The marker would be
    unsound (next clean commit could re-use the same parent SHA)."""
    monkeypatch.setattr(runner, "_short_sha", lambda: "abc1234")
    monkeypatch.setattr(runner, "_is_dirty", lambda: True)
    monkeypatch.setattr(runner, "RUN_TESTS_CACHE_DIR", tmp_path / ".cache")

    runner.write_cache_marker("unit", duration_seconds=1.5)

    assert not (tmp_path / ".cache").exists()


def test_write_cache_marker_no_op_when_no_git(
    monkeypatch: Any, tmp_path: Any,
) -> None:
    """No git repo (sha == 'nogit') = don't cache. Direct ``pytest``
    invocations outside a repo shouldn't pollute anywhere."""
    monkeypatch.setattr(runner, "_short_sha", lambda: "nogit")
    monkeypatch.setattr(runner, "_is_dirty", lambda: False)
    monkeypatch.setattr(runner, "RUN_TESTS_CACHE_DIR", tmp_path / ".cache")

    runner.write_cache_marker("unit", duration_seconds=1.5)

    assert not (tmp_path / ".cache").exists()


def test_is_layer_cached_green_true_when_marker_exists(
    monkeypatch: Any, tmp_path: Any,
) -> None:
    """Marker present + matches current SHA → green."""
    _stub_short_sha(monkeypatch, "abc1234")
    monkeypatch.setattr(runner, "RUN_TESTS_CACHE_DIR", tmp_path / ".cache")

    runner.write_cache_marker("unit", duration_seconds=1.5)
    assert runner.is_layer_cached_green("unit") is True


def test_is_layer_cached_green_false_when_no_marker(
    monkeypatch: Any, tmp_path: Any,
) -> None:
    """No marker = not cached."""
    _stub_short_sha(monkeypatch, "abc1234")
    monkeypatch.setattr(runner, "RUN_TESTS_CACHE_DIR", tmp_path / ".cache")

    assert runner.is_layer_cached_green("unit") is False


def test_is_layer_cached_green_false_when_sha_doesnt_match(
    monkeypatch: Any, tmp_path: Any,
) -> None:
    """Marker for one SHA doesn't help when current SHA differs.
    Defensive against handing-edited markers / shared cache dirs."""
    monkeypatch.setattr(runner, "_short_sha", lambda: "abc1234")
    monkeypatch.setattr(runner, "_is_dirty", lambda: False)
    monkeypatch.setattr(runner, "RUN_TESTS_CACHE_DIR", tmp_path / ".cache")
    runner.write_cache_marker("unit", duration_seconds=1.5)

    monkeypatch.setattr(runner, "_short_sha", lambda: "different")
    assert runner.is_layer_cached_green("unit") is False


def test_is_layer_cached_green_false_for_non_skippable_layers(
    monkeypatch: Any, tmp_path: Any,
) -> None:
    """Heavy layers (deploy, api, browser) NEVER report cached-green
    even if a marker file exists. Their pass-state is per-run by
    nature (live AWS / per-test resource names)."""
    _stub_short_sha(monkeypatch, "abc1234")
    monkeypatch.setattr(runner, "RUN_TESTS_CACHE_DIR", tmp_path / ".cache")
    # Force a marker file to exist for "deploy" — even with the file
    # present, is_layer_cached_green should still return False.
    (tmp_path / ".cache").mkdir(parents=True)
    (tmp_path / ".cache" / "abc1234.deploy.json").write_text(
        '{"sha": "abc1234", "layer": "deploy", "passed_at": "2026-05-07T00:00:00+00:00"}',
    )
    assert runner.is_layer_cached_green("deploy") is False


def test_is_layer_cached_green_false_when_dirty(
    monkeypatch: Any, tmp_path: Any,
) -> None:
    """Dirty SHA = always re-run, even with a green marker (the
    cached state reflects the parent commit, not the current dirty
    state)."""
    monkeypatch.setattr(runner, "_short_sha", lambda: "abc1234")
    monkeypatch.setattr(runner, "_is_dirty", lambda: False)
    monkeypatch.setattr(runner, "RUN_TESTS_CACHE_DIR", tmp_path / ".cache")
    runner.write_cache_marker("unit", duration_seconds=1.5)

    monkeypatch.setattr(runner, "_is_dirty", lambda: True)
    assert runner.is_layer_cached_green("unit") is False


def test_skippable_layers_are_unit_and_db_only() -> None:
    """Lock the contract: only the cheap layers participate in the
    cache. Catches a future drift where someone adds 'deploy' here."""
    assert runner.SKIPPABLE_LAYERS == ("unit", "db")


# Y.2.gate.b.2.impl — variant axis (testcontainers per-dialect).


def test_known_variants_includes_default_and_local_pg() -> None:
    """Lock the variant set. New variant names land here first; their
    setup_variant impl follows."""
    assert "default" in runner.KNOWN_VARIANTS
    assert "local-pg" in runner.KNOWN_VARIANTS
    assert "local-oracle" in runner.KNOWN_VARIANTS


def test_resolve_variants_default_returns_singleton() -> None:
    assert runner.resolve_variants("default") == ["default"]


def test_resolve_variants_empty_string_returns_default() -> None:
    """Empty string (operator passed `--variants=`) → behave as
    default. No silent failure into "no variants run"."""
    assert runner.resolve_variants("") == ["default"]


def test_resolve_variants_csv_returns_list() -> None:
    assert runner.resolve_variants("local-pg") == ["local-pg"]
    assert runner.resolve_variants("default,local-pg") == ["default", "local-pg"]


def test_resolve_variants_unknown_raises() -> None:
    """Typo'd variant names fail-loud with the known set surfaced."""
    with pytest.raises(ValueError, match="unknown variant"):
        runner.resolve_variants("local-postgress")


def test_setup_variant_default_is_no_op() -> None:
    env, handle = runner.setup_variant("default")
    assert env == {}
    assert handle is None


def test_teardown_variant_no_op_for_none() -> None:
    """Teardown is no-op when handle is None (default variant)."""
    runner.teardown_variant(None)  # must not raise


def test_teardown_variant_swallows_exceptions() -> None:
    """Sidecar contract — never break the chain on container teardown
    failure (network glitch, container already stopped, etc.)."""
    class _Throws:
        def stop(self) -> None:
            raise RuntimeError("docker exploded")

    runner.teardown_variant(_Throws())  # must not raise


def _fake_popen_factory(captured: dict[str, Any], *, returncode: int = 0):
    """Build a Popen-shaped fake the dispatch_layer code can drive
    through the new tee-to-file machinery without spinning up a real
    subprocess. The fake captures the env it was called with for
    assertions, returns immediately, and exposes empty stdout/stderr
    iterators so the tee threads exit cleanly. ``returncode`` is the
    fake exit code (default 0; pass 1 to simulate failure)."""
    class _FakeProc:
        def __init__(self) -> None:
            self.stdout = io.StringIO("")
            self.stderr = io.StringIO("")
            self.returncode = returncode

        def wait(self) -> int:
            return self.returncode

    def _fake_popen(
        cmd: Any, cwd: Any = None, env: Any = None,
        stdout: Any = None, stderr: Any = None,
        bufsize: int = -1, text: bool = False,
    ) -> _FakeProc:
        captured["cmd"] = list(cmd)
        captured["env"] = dict(env or {})
        return _FakeProc()

    return _fake_popen


def _fake_spawn_with_tee(
    captured: list[dict[str, Any]], *, returncode: int = 0,
):
    """Y.2.gate.c.6.async — fake ``_spawn_with_tee`` for tests that
    drive ``seed_variant`` (which calls ``_spawn_with_tee`` per step).
    Captures every call's cmd/env/paths/prefix into ``captured`` and
    returns ``(returncode, 0.01)``. Append-shape (one dict per call)
    so a 3-step seed_variant produces 3 entries — the assertions then
    check ordering + per-step shape."""
    def _fake(
        cmd: Any, *, cwd: Any, env: Any,
        stdout_path: Any, stderr_path: Any,
        terminal_prefix: str = "",
    ) -> tuple[int, float]:
        captured.append({
            "cmd": list(cmd),
            "cwd": cwd,
            "env": dict(env or {}),
            "stdout_path": stdout_path,
            "stderr_path": stderr_path,
            "terminal_prefix": terminal_prefix,
        })
        return returncode, 0.01

    return _fake


def test_dispatch_layer_threads_variant_env_to_db_layer(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """Y.2.gate.b.2.impl — variant_env merges into the subprocess env
    for DB-touching layers so QS_GEN_DEMO_DATABASE_URL etc. is visible
    to fixtures inside the pytest subprocess."""
    captured: dict[str, Any] = {}
    with patch.object(subprocess, "Popen", side_effect=_fake_popen_factory(captured)):
        runner.dispatch_layer(
            "db", tmp_path, runner.RunOptions(),
            variant_env={"QS_GEN_DEMO_DATABASE_URL": "postgresql://localhost:5432/test"},
        )
    assert captured["env"]["QS_GEN_DEMO_DATABASE_URL"] == "postgresql://localhost:5432/test"


def test_dispatch_layer_does_not_thread_variant_env_to_unit(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """Unit layer doesn't need variant_env — passes through clean.
    Tests that assert no QS_GEN_DEMO_DATABASE_URL would otherwise
    break when the operator runs --variants=local-pg."""
    captured: dict[str, Any] = {}
    monkeypatch.delenv(QS_GEN_DEMO_DATABASE_URL.name, raising=False)
    with patch.object(subprocess, "Popen", side_effect=_fake_popen_factory(captured)):
        runner.dispatch_layer(
            "unit", tmp_path, runner.RunOptions(),
            variant_env={"QS_GEN_DEMO_DATABASE_URL": "postgresql://localhost:5432/test"},
        )
    # Variant env did NOT leak into the unit subprocess.
    assert "QS_GEN_DEMO_DATABASE_URL" not in captured["env"]


# Y.2.gate.b.2.impl.oracle followup — per-layer subprocess capture.
# Every dispatch persists cmd.json + stdout.log + stderr.log under
# <run_dir>/<layer>/ so failures leave a complete trail in the run dir
# (CI artifact upload, hands-off run review, post-mortem for the
# next-step failure that the prior bare-streaming run made invisible).

def test_dispatch_layer_writes_cmd_json_with_input_and_result(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """cmd.json captures the input we sent (cmd argv, cwd,
    env-overrides) plus the result (exit code, duration). Re-written
    after the subprocess finishes; the pre-run write is the
    crash-trail safety net."""
    monkeypatch.delenv(QS_GEN_DEMO_DATABASE_URL.name, raising=False)
    captured: dict[str, Any] = {}
    with patch.object(subprocess, "Popen", side_effect=_fake_popen_factory(captured)):
        result = runner.dispatch_layer(
            "db", tmp_path, runner.RunOptions(),
            variant_env={"QS_GEN_DEMO_DATABASE_URL": "url-x"},
        )

    cmd_json = json.loads((tmp_path / "db" / "cmd.json").read_text())
    assert cmd_json["layer"] == "db"
    assert cmd_json["cmd"] == captured["cmd"]
    # env_overrides is the deltas only (variant env + per-layer env),
    # NOT the inherited os.environ noise.
    assert cmd_json["env_overrides"]["QS_GEN_DEMO_DATABASE_URL"] == "url-x"
    assert "PATH" not in cmd_json["env_overrides"]
    assert cmd_json["exit_code"] == result.exit_code
    assert cmd_json["duration_seconds"] >= 0


def test_dispatch_layer_captures_stdout_and_stderr_to_files(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """Real-subprocess test: stdout + stderr each land in their own
    log file under <run_dir>/<layer>/. Uses a tiny python -c so the
    threading + tee path runs end-to-end without mocking Popen."""
    monkeypatch.delenv(QS_GEN_DEMO_DATABASE_URL.name, raising=False)

    # Stub the layer-command resolver to return a deterministic
    # tiny subprocess instead of the real pytest invocation.
    cmd = [
        sys.executable, "-c",
        "import sys; sys.stdout.write('hello-out\\n'); "
        "sys.stderr.write('hello-err\\n'); sys.exit(0)",
    ]
    monkeypatch.setattr(
        runner, "_layer_command", lambda layer, run_dir, options: (cmd, {}),
    )

    runner.dispatch_layer("db", tmp_path, runner.RunOptions())

    stdout = (tmp_path / "db" / "stdout.log").read_text()
    stderr = (tmp_path / "db" / "stderr.log").read_text()
    assert "hello-out" in stdout
    assert "hello-err" in stderr
    # Streams are kept separate — stderr content does NOT leak into stdout.log.
    assert "hello-err" not in stdout
    assert "hello-out" not in stderr


def test_dispatch_layer_records_nonzero_exit_code(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """Subprocess exits non-zero → cmd.json records the exact code
    and the LayerResult propagates it to the caller (cmd_up_to uses
    this to set EXIT_NEEDS_OPERATOR / stop the chain)."""
    cmd = [sys.executable, "-c", "import sys; sys.exit(7)"]
    monkeypatch.setattr(
        runner, "_layer_command", lambda layer, run_dir, options: (cmd, {}),
    )

    result = runner.dispatch_layer("db", tmp_path, runner.RunOptions())
    assert result.exit_code == 7

    cmd_json = json.loads((tmp_path / "db" / "cmd.json").read_text())
    assert cmd_json["exit_code"] == 7


def test_dispatch_layer_recursion_guard_fails_loud(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """Surfaced 2026-05-08: the runner used to spawn pytest recursively
    when a test forgot to mock subprocess.Popen. The guard now refuses
    to spawn pytest from inside pytest with the real Popen class, with
    a message naming the fix. Deliberate-regression: assert the guard
    fires rather than letting the recursive spawn explode.

    Uses a fake ``pytest`` argv at cmd[0] so the guard's basename
    check catches it. The real ``_layer_command`` returns
    ``[VENV_BIN/pytest, tests/..., -q]`` for layers, so cmd[0] is
    always pytest in production — same shape this test exercises.
    """
    cmd = ["pytest", "tests/unit", "-q"]
    monkeypatch.setattr(
        runner, "_layer_command", lambda layer, run_dir, options: (cmd, {}),
    )
    # PYTEST_CURRENT_TEST is set automatically by pytest; sanity-assert
    # that we're inside it so the guard's first condition is met.
    assert os.environ.get("PYTEST_CURRENT_TEST")

    with pytest.raises(RuntimeError, match="recursive spawn explodes"):
        runner.dispatch_layer("unit", tmp_path, runner.RunOptions())


def test_cache_marker_variant_aware(monkeypatch: Any, tmp_path: Any) -> None:
    """Same SHA + same layer with different variants → different marker
    files; cache lookup for one variant doesn't hit the other."""
    _stub_short_sha(monkeypatch, "abc1234")
    monkeypatch.setattr(runner, "RUN_TESTS_CACHE_DIR", tmp_path / ".cache")

    runner.write_cache_marker("unit", duration_seconds=1.5, variant="default")
    runner.write_cache_marker("db", duration_seconds=10.0, variant="local-pg")

    # Each variant has its own marker.
    assert (tmp_path / ".cache" / "abc1234.unit.default.json").exists()
    assert (tmp_path / ".cache" / "abc1234.db.local-pg.json").exists()
    # Cross-variant lookups don't hit.
    assert runner.is_layer_cached_green("unit", variant="default") is True
    assert runner.is_layer_cached_green("unit", variant="local-pg") is False
    assert runner.is_layer_cached_green("db", variant="local-pg") is True
    assert runner.is_layer_cached_green("db", variant="default") is False


# Y.2.gate.b.2.impl.schema — seed_variant tests. The variant container
# starts empty; seed_variant runs schema apply + data apply + data
# refresh against the container URL before the db layer dispatches.

def test_seed_variant_default_is_no_op() -> None:
    """`default` variant means external Aurora / Oracle — already
    seeded by the operator. seed_variant must do nothing."""
    with patch.object(subprocess, "run") as mock_run:
        runner.seed_variant("default", {})
    mock_run.assert_not_called()


def test_seed_variant_unknown_raises() -> None:
    """Typo'd variant name fails loudly. Symmetric with
    setup_variant's ValueError."""
    with pytest.raises(ValueError, match="unknown variant"):
        runner.seed_variant("local-postgress", {})


def test_seed_variant_local_pg_runs_three_subprocesses(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """local-pg seed runs schema apply, data apply, data refresh —
    in that order, all three with --execute. Order matters: schema
    creates tables + matviews, data populates source tables, refresh
    populates matviews from source tables."""
    cfg = tmp_path / "fake_pg_cfg.yaml"
    cfg.write_text("dialect: postgres\n")
    monkeypatch.setattr(
        runner, "_resolve_seed_config_for_local_pg", lambda: cfg,
    )
    monkeypatch.delenv(QS_GEN_TEST_L2_INSTANCE.name, raising=False)

    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        runner, "_spawn_with_tee", _fake_spawn_with_tee(captured),
    )

    runner.seed_variant("local-pg", {"QS_GEN_DEMO_DATABASE_URL": "x"})

    # Three subprocesses, in order.
    assert len(captured) == 3
    captured_cmds = [c["cmd"] for c in captured]
    # Each is `quicksight-gen <verb> apply/refresh --execute -c <cfg>`.
    verbs = [(c[1], c[2]) for c in captured_cmds]
    assert verbs == [("schema", "apply"), ("data", "apply"), ("data", "refresh")]
    for cmd in captured_cmds:
        assert "--execute" in cmd
        assert "-c" in cmd
        assert str(cfg) in cmd


def test_seed_variant_threads_env_overrides_to_subprocesses(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """The container URL flows to each subprocess via env. load_config
    inside the subprocess picks it up via QS_GEN_DEMO_DATABASE_URL
    env override (config.py:364) and writes against the container."""
    cfg = tmp_path / "fake_pg_cfg.yaml"
    cfg.write_text("")
    monkeypatch.setattr(
        runner, "_resolve_seed_config_for_local_pg", lambda: cfg,
    )
    monkeypatch.delenv(QS_GEN_TEST_L2_INSTANCE.name, raising=False)

    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        runner, "_spawn_with_tee", _fake_spawn_with_tee(captured),
    )

    container_url = "postgresql+psycopg2://test:test@localhost:60455/test"
    runner.seed_variant("local-pg", {"QS_GEN_DEMO_DATABASE_URL": container_url})

    # Every step sees the override.
    assert len(captured) == 3
    for entry in captured:
        assert entry["env"].get("QS_GEN_DEMO_DATABASE_URL") == container_url


def test_seed_variant_honors_qs_gen_test_l2_instance(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """`QS_GEN_TEST_L2_INSTANCE` env (already used by tests/e2e/conftest
    fixtures) flows through as `--l2 <yaml>` so seed + db tests target
    the same L2 instance. Without the env, the CLI defaults to
    bundled spec_example."""
    cfg = tmp_path / "fake_pg_cfg.yaml"
    cfg.write_text("")
    l2_yaml = tmp_path / "my_instance.yaml"
    l2_yaml.write_text("")
    monkeypatch.setattr(
        runner, "_resolve_seed_config_for_local_pg", lambda: cfg,
    )
    monkeypatch.setenv(QS_GEN_TEST_L2_INSTANCE.name, str(l2_yaml))

    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        runner, "_spawn_with_tee", _fake_spawn_with_tee(captured),
    )

    runner.seed_variant("local-pg", {})

    for entry in captured:
        cmd = entry["cmd"]
        assert "--l2" in cmd
        assert str(l2_yaml) in cmd


def test_seed_variant_local_pg_raises_when_no_cfg_found(
    monkeypatch: Any,
) -> None:
    """No postgres-dialect cfg → operator-actionable RuntimeError, not
    a confusing subprocess failure 3 layers deep. Surfaces the cfg
    discovery rules so the operator can fix it in one read."""
    monkeypatch.setattr(
        runner, "_resolve_seed_config_for_local_pg", lambda: None,
    )
    with pytest.raises(RuntimeError, match="postgres-dialect cfg"):
        runner.seed_variant("local-pg", {})


# Y.2.gate.b.2.impl.oracle — Oracle arm of seed_variant. Mirrors the
# local-pg shape: same 3 CLI subprocesses, same env-threading + L2
# override, distinct cfg-discovery list (run/config.oracle.yaml).
# Live container test happens via `./run_tests.sh up_to=db
# --variants=local-oracle`; these unit tests cover the wiring shape
# without requiring Docker.

def test_seed_variant_local_oracle_runs_three_subprocesses(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """Same shape as local-pg: schema apply → data apply → data refresh,
    each with --execute pointed at the oracle-dialect cfg."""
    cfg = tmp_path / "fake_oracle_cfg.yaml"
    cfg.write_text("dialect: oracle\n")
    monkeypatch.setattr(
        runner, "_resolve_seed_config_for_local_oracle", lambda: cfg,
    )
    monkeypatch.delenv(QS_GEN_TEST_L2_INSTANCE.name, raising=False)

    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        runner, "_spawn_with_tee", _fake_spawn_with_tee(captured),
    )

    runner.seed_variant("local-oracle", {"QS_GEN_DEMO_DATABASE_URL": "x"})

    assert len(captured) == 3
    captured_cmds = [c["cmd"] for c in captured]
    verbs = [(c[1], c[2]) for c in captured_cmds]
    assert verbs == [("schema", "apply"), ("data", "apply"), ("data", "refresh")]
    for cmd in captured_cmds:
        assert "--execute" in cmd
        assert "-c" in cmd
        assert str(cfg) in cmd


def test_seed_variant_local_oracle_raises_when_no_cfg_found(
    monkeypatch: Any,
) -> None:
    """No oracle-dialect cfg → operator-actionable RuntimeError naming
    `run/config.oracle.yaml` so the operator can fix it in one read."""
    monkeypatch.setattr(
        runner, "_resolve_seed_config_for_local_oracle", lambda: None,
    )
    with pytest.raises(RuntimeError, match="oracle-dialect cfg"):
        runner.seed_variant("local-oracle", {})


def test_resolve_seed_config_oracle_falls_back_to_run_oracle(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """No env override + run/config.oracle.yaml exists at the repo root
    → return that path. Symmetric with the local-pg cfg discovery —
    each variant has its own dialect-flavored fallback list."""
    monkeypatch.delenv(QS_GEN_CONFIG.name, raising=False)
    fake_repo = tmp_path / "repo"
    (fake_repo / "run").mkdir(parents=True)
    cfg = fake_repo / "run" / "config.oracle.yaml"
    cfg.write_text("dialect: oracle\n")
    monkeypatch.setattr(runner, "REPO_ROOT", fake_repo)
    assert runner._resolve_seed_config_for_local_oracle() == cfg


def test_seed_variant_raises_on_subprocess_failure(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """Any of schema/data/refresh failing → RuntimeError with the
    failing step named. Caller (cmd_up_to) catches + maps to
    EXIT_NEEDS_OPERATOR; teardown still runs via the surrounding
    try/finally."""
    cfg = tmp_path / "fake_pg_cfg.yaml"
    cfg.write_text("")
    monkeypatch.setattr(
        runner, "_resolve_seed_config_for_local_pg", lambda: cfg,
    )
    monkeypatch.delenv(QS_GEN_TEST_L2_INSTANCE.name, raising=False)

    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        runner, "_spawn_with_tee", _fake_spawn_with_tee(captured, returncode=1),
    )
    with pytest.raises(RuntimeError, match="schema"):
        runner.seed_variant("local-pg", {})


def test_resolve_seed_config_explicit_env_override(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """`QS_GEN_CONFIG` operator override wins over the cfg-candidate
    list. Mirrors the same env override the e2e suite + cmd_sweep
    already honor."""
    cfg = tmp_path / "my_pg.yaml"
    cfg.write_text("dialect: postgres\n")
    monkeypatch.setenv(QS_GEN_CONFIG.name, str(cfg))
    assert runner._resolve_seed_config_for_local_pg() == cfg


def test_resolve_seed_config_explicit_env_missing_returns_none(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """Operator pointed QS_GEN_CONFIG at a path that doesn't exist —
    don't silently fall back to a candidate. The override stated
    intent; respect it (and surface the absence)."""
    monkeypatch.setenv(QS_GEN_CONFIG.name, str(tmp_path / "does-not-exist.yaml"))
    assert runner._resolve_seed_config_for_local_pg() is None


def test_resolve_seed_config_falls_back_to_run_postgres(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """No env override + run/config.postgres.yaml exists at the repo
    root → return that path. We only check run/config.postgres.yaml
    here (not run/config.yaml) because run/config.yaml may be Oracle-
    flavored and won't match a Postgres container."""
    monkeypatch.delenv(QS_GEN_CONFIG.name, raising=False)
    fake_repo = tmp_path / "repo"
    (fake_repo / "run").mkdir(parents=True)
    cfg = fake_repo / "run" / "config.postgres.yaml"
    cfg.write_text("dialect: postgres\n")
    monkeypatch.setattr(runner, "REPO_ROOT", fake_repo)
    assert runner._resolve_seed_config_for_local_pg() == cfg


def test_cmd_up_to_seeds_variant_when_db_layer_in_chain(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """cmd_up_to fires seed_variant for non-default variants when the
    chain includes a DB-touching layer. Parallel to the cache-marker
    + dispatch-layer wiring — the seed step is gated on chain shape,
    not just variant choice."""
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(
        runner, "setup_variant",
        lambda name: ({"QS_GEN_DEMO_DATABASE_URL": "x"}, object()),
    )

    seed_calls: list[tuple[str, dict[str, str]]] = []

    def fake_seed(name: str, env_overrides: dict[str, str], **_: Any) -> None:
        seed_calls.append((name, env_overrides))

    monkeypatch.setattr(runner, "seed_variant", fake_seed)
    monkeypatch.setattr(runner, "teardown_variant", lambda h: None)

    def fake_dispatch(layer: str, run_dir: Path, options: Any = None, **kwargs: Any) -> runner.LayerResult:
        return runner.LayerResult(layer=layer, exit_code=0, duration_seconds=0.1)

    monkeypatch.setattr(runner, "dispatch_layer", fake_dispatch)
    monkeypatch.setattr(runner, "probe_dependencies", lambda layer: [])

    parser = runner._build_parser()
    args = parser.parse_args(["up_to", "db", "--variants", "local-pg"])
    rc = runner.cmd_up_to(args)
    assert rc == runner.EXIT_SUCCESS
    assert len(seed_calls) == 1
    assert seed_calls[0][0] == "local-pg"
    assert seed_calls[0][1] == {"QS_GEN_DEMO_DATABASE_URL": "x"}


def test_cmd_up_to_skips_seed_when_chain_is_unit_only(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """unit-only chain doesn't need a seeded DB — saves ~30s on
    type-check iteration. The variant container still spins up (so
    the cache marker semantics stay variant-aware), but no schema /
    data work is done."""
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(
        runner, "setup_variant",
        lambda name: ({"QS_GEN_DEMO_DATABASE_URL": "x"}, object()),
    )

    seed_calls: list[str] = []
    monkeypatch.setattr(
        runner, "seed_variant",
        lambda name, env, **_: seed_calls.append(name),
    )
    monkeypatch.setattr(runner, "teardown_variant", lambda h: None)

    def fake_dispatch(layer: str, run_dir: Path, options: Any = None, **kwargs: Any) -> runner.LayerResult:
        return runner.LayerResult(layer=layer, exit_code=0, duration_seconds=0.1)

    monkeypatch.setattr(runner, "dispatch_layer", fake_dispatch)
    monkeypatch.setattr(runner, "probe_dependencies", lambda layer: [])

    parser = runner._build_parser()
    args = parser.parse_args(["up_to", "unit", "--variants", "local-pg"])
    rc = runner.cmd_up_to(args)
    assert rc == runner.EXIT_SUCCESS
    assert seed_calls == []  # never invoked


def test_cmd_up_to_skips_seed_for_default_variant(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """Default variant = external Aurora; operator already seeded.
    Even when the chain includes db, cmd_up_to skips seed_variant —
    we'd be double-seeding the operator's external DB otherwise."""
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(
        runner, "setup_variant", lambda name: ({}, None),
    )

    seed_calls: list[str] = []
    monkeypatch.setattr(
        runner, "seed_variant",
        lambda name, env, **_: seed_calls.append(name),
    )
    monkeypatch.setattr(runner, "teardown_variant", lambda h: None)

    def fake_dispatch(layer: str, run_dir: Path, options: Any = None, **kwargs: Any) -> runner.LayerResult:
        return runner.LayerResult(layer=layer, exit_code=0, duration_seconds=0.1)

    monkeypatch.setattr(runner, "dispatch_layer", fake_dispatch)
    monkeypatch.setattr(runner, "probe_dependencies", lambda layer: [])

    parser = runner._build_parser()
    args = parser.parse_args(["up_to", "db"])  # default variant
    rc = runner.cmd_up_to(args)
    assert rc == runner.EXIT_SUCCESS
    assert seed_calls == []


def test_normalize_pg_url_strips_psycopg2_driver() -> None:
    """testcontainers-python returns SQLAlchemy-style URLs
    (`postgresql+psycopg2://...`) but psycopg3 rejects the driver
    suffix. Strip it so the URL is plain libpq form."""
    raw = "postgresql+psycopg2://test:test@localhost:60455/test"
    assert runner._normalize_pg_url(raw) == "postgresql://test:test@localhost:60455/test"


def test_normalize_pg_url_passthrough_when_already_clean() -> None:
    """A URL that's already in plain libpq form passes through
    unchanged — idempotent transformation."""
    clean = "postgresql://user:pw@host:5432/db"
    assert runner._normalize_pg_url(clean) == clean


def test_cmd_up_to_seed_failure_still_runs_teardown(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """If seed_variant raises, teardown_variant must still fire.
    Otherwise a failed seed leaves a hot Docker container behind
    (cost / port conflict / orphan). Symmetric with the existing
    layer-failure teardown contract."""
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path / "runs")
    handle = object()
    monkeypatch.setattr(
        runner, "setup_variant",
        lambda name: ({"QS_GEN_DEMO_DATABASE_URL": "x"}, handle),
    )

    def fail_seed(name: str, env: dict[str, str], **_: Any) -> None:
        raise RuntimeError("boom: schema apply died")

    monkeypatch.setattr(runner, "seed_variant", fail_seed)

    teardown_calls: list[object] = []
    monkeypatch.setattr(
        runner, "teardown_variant",
        lambda h: teardown_calls.append(h),
    )
    monkeypatch.setattr(runner, "dispatch_layer", lambda *a, **k: None)
    monkeypatch.setattr(runner, "probe_dependencies", lambda layer: [])

    parser = runner._build_parser()
    args = parser.parse_args(["up_to", "db", "--variants", "local-pg"])
    rc = runner.cmd_up_to(args)
    assert rc == runner.EXIT_NEEDS_OPERATOR
    assert teardown_calls == [handle]


# Y.2.gate.c.6.async — multi-variant asyncio.gather fan-out tests.
# Cover the four design locks: nested per-variant run dirs, per-line
# terminal prefix (asserted via captured kwargs to dispatch_layer),
# soft fast-fail per variant (one variant failing doesn't kill the
# sibling's run), and exit-code aggregation (any failure → final
# EXIT_FAILURE).

def test_cmd_up_to_multi_variant_runs_each_variant(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """Multi-variant: every variant in --variants gets its own
    _run_one_variant invocation. Both variants run; neither short-
    circuits the other (asyncio.gather collects all results)."""
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(runner, "probe_dependencies", lambda layer: [])

    invocations: list[tuple[str, Path, str]] = []

    def fake_run_one(
        variant: str, run_dir: Path, options: Any, chain: list[str],
        *, terminal_prefix: str = "",
    ) -> tuple[str, list[runner.LayerResult], int]:
        invocations.append((variant, run_dir, terminal_prefix))
        return variant, [
            runner.LayerResult(layer="unit", exit_code=0, duration_seconds=0.1),
        ], runner.EXIT_SUCCESS

    monkeypatch.setattr(runner, "_run_one_variant", fake_run_one)

    parser = runner._build_parser()
    args = parser.parse_args([
        "up_to", "unit", "--variants", "local-pg,local-oracle",
    ])
    rc = runner.cmd_up_to(args)
    assert rc == runner.EXIT_SUCCESS
    variants_seen = {variant for variant, _, _ in invocations}
    assert variants_seen == {"local-pg", "local-oracle"}


def test_cmd_up_to_multi_variant_nests_run_dir_per_variant(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """Per design lock #1 — nested ``runs/<id>/<variant>/``. Easier
    to ripgrep + per-variant artifacts (cmd.json, stdout.log, seed/)
    don't collide between sibling variants."""
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(runner, "probe_dependencies", lambda layer: [])

    invocations: list[tuple[str, Path]] = []

    def fake_run_one(
        variant: str, run_dir: Path, options: Any, chain: list[str],
        *, terminal_prefix: str = "",
    ) -> tuple[str, list[runner.LayerResult], int]:
        invocations.append((variant, run_dir))
        return variant, [
            runner.LayerResult(layer="unit", exit_code=0, duration_seconds=0.1),
        ], runner.EXIT_SUCCESS

    monkeypatch.setattr(runner, "_run_one_variant", fake_run_one)

    parser = runner._build_parser()
    args = parser.parse_args([
        "up_to", "unit", "--variants", "local-pg,local-oracle",
    ])
    runner.cmd_up_to(args)

    # Per-variant run_dir is the top-level run_dir suffixed with the
    # variant name. The top-level run_dir lives under RUNS_DIR.
    by_variant = dict(invocations)
    assert by_variant["local-pg"].name == "local-pg"
    assert by_variant["local-oracle"].name == "local-oracle"
    assert by_variant["local-pg"].parent == by_variant["local-oracle"].parent
    assert by_variant["local-pg"].parent.parent == tmp_path / "runs"


def test_cmd_up_to_multi_variant_threads_terminal_prefix(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """Per design lock #2 — per-line terminal prefix
    ``[<variant>] `` flows from cmd_up_to → _run_one_variant →
    (downstream into dispatch_layer + _tee_stream)."""
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(runner, "probe_dependencies", lambda layer: [])

    prefixes: dict[str, str] = {}

    def fake_run_one(
        variant: str, run_dir: Path, options: Any, chain: list[str],
        *, terminal_prefix: str = "",
    ) -> tuple[str, list[runner.LayerResult], int]:
        prefixes[variant] = terminal_prefix
        return variant, [
            runner.LayerResult(layer="unit", exit_code=0, duration_seconds=0.1),
        ], runner.EXIT_SUCCESS

    monkeypatch.setattr(runner, "_run_one_variant", fake_run_one)

    parser = runner._build_parser()
    args = parser.parse_args([
        "up_to", "unit", "--variants", "local-pg,local-oracle",
    ])
    runner.cmd_up_to(args)

    assert prefixes == {
        "local-pg": "[local-pg] ",
        "local-oracle": "[local-oracle] ",
    }


def test_cmd_up_to_multi_variant_soft_fast_fail_per_variant(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """Per design lock #3 — soft fast-fail. One variant failing does
    NOT abort sibling variants. Both variants always run to completion
    (or to their own first failure inside _run_one_variant). Final
    exit code reports any-failure as EXIT_FAILURE."""
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(runner, "probe_dependencies", lambda layer: [])

    invoked: list[str] = []

    def fake_run_one(
        variant: str, run_dir: Path, options: Any, chain: list[str],
        *, terminal_prefix: str = "",
    ) -> tuple[str, list[runner.LayerResult], int]:
        invoked.append(variant)
        if variant == "local-pg":
            return variant, [
                runner.LayerResult(layer="unit", exit_code=1, duration_seconds=0.1),
            ], runner.EXIT_FAILURE
        return variant, [
            runner.LayerResult(layer="unit", exit_code=0, duration_seconds=0.1),
        ], runner.EXIT_SUCCESS

    monkeypatch.setattr(runner, "_run_one_variant", fake_run_one)

    parser = runner._build_parser()
    args = parser.parse_args([
        "up_to", "unit", "--variants", "local-pg,local-oracle",
    ])
    rc = runner.cmd_up_to(args)
    # Both variants ran — soft fast-fail did not skip the sibling.
    assert set(invoked) == {"local-pg", "local-oracle"}
    # Failure wins the final exit code.
    assert rc == runner.EXIT_FAILURE


def test_cmd_up_to_multi_variant_aggregates_timings(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    """Multi-variant top-level timings.json keys layers with
    ``<variant>.<layer>`` so report_drift's per-layer compare works
    unchanged + per-variant attribution stays clear in CI artifacts."""
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(runner, "probe_dependencies", lambda layer: [])

    def fake_run_one(
        variant: str, run_dir: Path, options: Any, chain: list[str],
        *, terminal_prefix: str = "",
    ) -> tuple[str, list[runner.LayerResult], int]:
        return variant, [
            runner.LayerResult(layer="unit", exit_code=0, duration_seconds=1.5),
        ], runner.EXIT_SUCCESS

    monkeypatch.setattr(runner, "_run_one_variant", fake_run_one)

    parser = runner._build_parser()
    args = parser.parse_args([
        "up_to", "unit", "--variants", "local-pg,local-oracle",
    ])
    runner.cmd_up_to(args)

    # Find the run dir we just created.
    runs_dir = tmp_path / "runs"
    run_dirs = [p for p in runs_dir.iterdir() if p.is_dir()]
    assert len(run_dirs) == 1
    top = run_dirs[0]
    aggregated = json.loads((top / "timings.json").read_text())
    assert "local-pg.unit" in aggregated["layer_durations"]
    assert "local-oracle.unit" in aggregated["layer_durations"]
    # Per-variant timings.json also exists under the nested dir.
    assert (top / "local-pg" / "timings.json").exists()
    assert (top / "local-oracle" / "timings.json").exists()
