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


def test_destructive_sweep_refuses_without_yes() -> None:
    """Same for `sweep`."""
    code = runner.main(["sweep"])
    assert code == runner.EXIT_NEEDS_OPERATOR


def test_up_to_creates_run_dir() -> None:
    """`up_to=<layer>` returns success when all layers pass — proves the wiring.

    Mocks `dispatch_layer` so the test doesn't recursively run pytest /
    pyright; that's smoke-tested separately."""

    def fake_pass(layer: str, run_dir: Path, options: Any = None) -> runner.LayerResult:
        return runner.LayerResult(layer=layer, exit_code=0, duration_seconds=0.01)

    with patch.object(runner, "dispatch_layer", side_effect=fake_pass):
        code = runner.main(["up_to=unit"])
    assert code == runner.EXIT_SUCCESS


def test_layers_list_matches_audit_table() -> None:
    """Y.2.gate.b.11 lock — runner's LAYERS is the runtime authority; the audit
    doc layer table is the documented mirror. Pyright collapsed into unit
    (2026-05-07): conftest sessionstart handles type-check before pytest
    runs, no separate runner layer."""
    assert runner.LAYERS == ("unit", "db", "deploy", "api", "browser")


# Y.2.gate.c.8 — dependency probe tests.

import subprocess
from typing import Any
from unittest.mock import patch


def _fake_completed(returncode: int, stderr: str = "", stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["fake"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_layer_deps_match_audit_table() -> None:
    """c.14-shape cross-check: layer-to-deps mapping reflects audit §3.

    unit is dependency-free (in-process; pyright runs via conftest sessionstart);
    db needs docker (containers per b.2); deploy/api need aws + docker; browser
    adds qs_arn for embed signing. Edits to either side without the other
    should fail loudly."""
    assert runner._LAYER_DEPS["unit"] == frozenset()
    assert runner._LAYER_DEPS["db"] == frozenset({"docker"})
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
    monkeypatch.setenv("QS_E2E_USER_ARN", "arn:aws:quicksight:us-east-1:123:user/default/test")
    assert runner._probe_qs_e2e_user_arn() is None


def test_probe_qs_arn_unset(monkeypatch: Any) -> None:
    monkeypatch.delenv("QS_E2E_USER_ARN", raising=False)
    result = runner._probe_qs_e2e_user_arn()
    assert result is not None
    assert result.kind == "qs_arn_unset"


def test_probe_dependencies_unit_no_deps() -> None:
    """unit layer (which now also runs pyright via conftest) has no external
    deps — pure in-process. Probe always empty."""
    assert runner.probe_dependencies("unit") == []


def test_probe_dependencies_browser_aggregates_failures(monkeypatch: Any) -> None:
    """All three deps fail → all three failures returned in one pass.

    Operator sees everything missing in one go; doesn't have to fix one,
    re-run, hit the next, etc."""
    monkeypatch.delenv("QS_E2E_USER_ARN", raising=False)
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
    assert runner.chain_through("browser") == ["unit", "db", "deploy", "api", "browser"]


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


def test_layer_command_stub_layers_return_none() -> None:
    """deploy/api/browser are not yet wired (need cfg loading + variants).
    None signals the dispatch path to record skipped=True."""
    for layer in ("deploy", "api", "browser"):
        assert runner._layer_command(layer, Path("/tmp/run")) is None


def test_dispatch_layer_stub_returns_skipped() -> None:
    """Stub layers don't fail the chain — they return skipped=True with rc=0."""
    result = runner.dispatch_layer("deploy", Path("/tmp/run"))
    assert result.skipped is True
    assert result.passed is True
    assert result.exit_code == 0


def test_dispatch_layer_runs_real_subprocess(tmp_path: Path) -> None:
    """Real dispatch invokes subprocess.run; the result reflects the exit code."""
    fake = subprocess.CompletedProcess(args=["fake"], returncode=0, stdout="", stderr="")
    with patch.object(subprocess, "run", return_value=fake) as mock_run:
        result = runner.dispatch_layer("unit", tmp_path)
    assert mock_run.called
    assert result.passed is True
    assert result.skipped is False
    assert result.exit_code == 0


def test_dispatch_layer_failure_propagates(tmp_path: Path) -> None:
    fake = subprocess.CompletedProcess(args=["fake"], returncode=1, stdout="", stderr="")
    with patch.object(subprocess, "run", return_value=fake):
        result = runner.dispatch_layer("unit", tmp_path)
    assert result.passed is False
    assert result.exit_code == 1


def test_dispatch_layer_passes_run_dir_via_env(tmp_path: Path) -> None:
    """QS_GEN_RUN_DIR threads through to the pytest subprocess so conftest
    fixtures (c.10/c.11/c.12) can route artifacts under runs/<run-id>/.
    QS_GEN_FUZZ_SEED also threads (c.6.xdist-safety)."""
    fake = subprocess.CompletedProcess(args=["fake"], returncode=0, stdout="", stderr="")
    with patch.object(subprocess, "run", return_value=fake) as mock_run:
        runner.dispatch_layer("unit", tmp_path, runner.RunOptions(fuzz_seed_value=42))
    call_kwargs = mock_run.call_args
    env = call_kwargs.kwargs["env"]
    assert env["QS_GEN_RUN_DIR"] == str(tmp_path)
    assert env["QS_GEN_LAYER"] == "unit"
    assert env["QS_GEN_FUZZ_SEED"] == "42"


def test_cmd_up_to_stops_on_first_failure() -> None:
    """When layer N fails, layers >N are NOT dispatched; chain returns FAILURE."""
    dispatched: list[str] = []

    def fake_dispatch(layer: str, run_dir: Path, options: Any = None) -> runner.LayerResult:
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

    def fake_dispatch(layer: str, run_dir: Path, options: Any = None) -> runner.LayerResult:
        dispatched.append(layer)
        return runner.LayerResult(layer=layer, exit_code=0, duration_seconds=0.01)

    with (
        patch.object(runner, "probe_dependencies", return_value=[]),
        patch.object(runner, "_is_dirty", return_value=False),
        patch.object(runner, "dispatch_layer", side_effect=fake_dispatch),
    ):
        code = runner.main(["up_to=browser"])
    assert code == runner.EXIT_SUCCESS
    assert dispatched == ["unit", "db", "deploy", "api", "browser"]


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
    monkeypatch.delenv("QS_GEN_RUNNER_YES", raising=False)
    with patch.object(runner, "_is_dirty", return_value=True):
        code = runner.main(["up_to=deploy"])
    assert code == runner.EXIT_NEEDS_OPERATOR


def test_cmd_up_to_dirty_ok_below_deploy(monkeypatch: Any) -> None:
    """Layers 1-3 (pyright/unit/db) are local + idempotent; dirty state OK."""
    monkeypatch.delenv("QS_GEN_RUNNER_YES", raising=False)

    def fake_dispatch(layer: str, run_dir: Path, options: Any = None) -> runner.LayerResult:
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
    monkeypatch.delenv("QS_GEN_RUNNER_YES", raising=False)

    def fake_dispatch(layer: str, run_dir: Path, options: Any = None) -> runner.LayerResult:
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
    monkeypatch.setenv("QS_GEN_RUNNER_YES", "1")

    def fake_dispatch(layer: str, run_dir: Path, options: Any = None) -> runner.LayerResult:
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


# Y.2.gate.c.6.xdist-safety — fuzz seed value resolution + env passthrough.


def test_resolve_fuzz_seed_value_random_when_unset(monkeypatch: Any) -> None:
    """No env override → fresh random seed each call. Two calls likely
    different (32-bit space; collision odds vanishingly small)."""
    monkeypatch.delenv("QS_GEN_FUZZ_SEED", raising=False)
    a = runner.resolve_fuzz_seed_value()
    b = runner.resolve_fuzz_seed_value()
    assert isinstance(a, int) and 0 <= a < 2**32
    assert a != b  # not pinned; would be flaky with 1-in-4-billion odds


def test_resolve_fuzz_seed_value_honors_env(monkeypatch: Any) -> None:
    """`QS_GEN_FUZZ_SEED=N` env → operator pin for failure repro. All workers
    in this run see the same value."""
    monkeypatch.setenv("QS_GEN_FUZZ_SEED", "12345")
    assert runner.resolve_fuzz_seed_value() == 12345


def test_resolve_fuzz_seed_value_random_on_blank_env(monkeypatch: Any) -> None:
    """Blank env (e.g. accidentally exported empty) → fall back to random,
    not crash on int('')."""
    monkeypatch.setenv("QS_GEN_FUZZ_SEED", "")
    seed = runner.resolve_fuzz_seed_value()
    assert isinstance(seed, int)


def test_run_options_fuzz_seed_value_default_none() -> None:
    """RunOptions() default = None; resolution happens in _options_from_args.
    A None value means 'don't set the env at all' (preserves existing env if
    operator set it some other way)."""
    assert runner.RunOptions().fuzz_seed_value is None


def test_options_from_args_resolves_fuzz_seed(monkeypatch: Any) -> None:
    """_options_from_args populates fuzz_seed_value (random unless env pinned)."""
    monkeypatch.setenv("QS_GEN_FUZZ_SEED", "98765")
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
