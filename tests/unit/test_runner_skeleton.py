"""Y.2.gate.c.1 / b.14.* skeleton primitives.

Locks the run-id format, the argv normalization (``up_to=<x>`` ↔ ``up_to <x>``),
and the destructive-op refusal pattern. These shapes are stable contracts the
rest of the c-stage implementation (capture, diff, dispatch) builds on.
"""

from __future__ import annotations

import re

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
    fake_pass = lambda layer, run_dir: runner.LayerResult(  # noqa: E731
        layer=layer, exit_code=0, duration_seconds=0.01
    )
    with patch.object(runner, "dispatch_layer", side_effect=fake_pass):
        code = runner.main(["up_to=unit"])
    assert code == runner.EXIT_SUCCESS


def test_layers_list_matches_audit_table() -> None:
    """Y.2.gate.b.11 lock — runner's LAYERS is the runtime authority; the audit
    doc layer table is the documented mirror. This is the small-canonical-sample
    cross-check that catches one-side-only edits (full version lands in c.14)."""
    assert runner.LAYERS == ("pyright", "unit", "db", "deploy", "api", "browser")


# Y.2.gate.c.8 — dependency probe tests.

import subprocess
from typing import Any
from unittest.mock import patch


def _fake_completed(returncode: int, stderr: str = "", stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["fake"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_layer_deps_match_audit_table() -> None:
    """c.14-shape cross-check: layer-to-deps mapping reflects audit §3.

    pyright/unit are dependency-free (in-process); db needs docker (containers
    per b.2); deploy/api need aws + docker; browser adds qs_arn for embed
    signing. Edits to either side without the other should fail loudly."""
    assert runner._LAYER_DEPS["pyright"] == frozenset()
    assert runner._LAYER_DEPS["unit"] == frozenset()
    assert runner._LAYER_DEPS["db"] == frozenset({"docker"})
    assert runner._LAYER_DEPS["deploy"] == frozenset({"aws", "docker"})
    assert runner._LAYER_DEPS["api"] == frozenset({"aws", "docker"})
    assert runner._LAYER_DEPS["browser"] == frozenset({"aws", "docker", "qs_arn"})


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


def test_probe_dependencies_pyright_no_deps() -> None:
    """pyright layer requires nothing external; probe always empty."""
    assert runner.probe_dependencies("pyright") == []


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


def test_chain_through_pyright() -> None:
    assert runner.chain_through("pyright") == ["pyright"]


def test_chain_through_db() -> None:
    assert runner.chain_through("db") == ["pyright", "unit", "db"]


def test_chain_through_browser_full() -> None:
    assert runner.chain_through("browser") == ["pyright", "unit", "db", "deploy", "api", "browser"]


def test_layer_command_pyright() -> None:
    """Layer 1 runs pyright directly — no pytest, no QS_GEN_SKIP_PYRIGHT needed.

    Both QS_GEN_RUN_DIR + QS_GEN_LAYER thread through (c.2 timings hook needs
    them; pyright doesn't use them, but threading uniformly keeps the
    layer-command shape symmetric)."""
    cmd_env = runner._layer_command("pyright", Path("/tmp/run"))
    assert cmd_env is not None
    cmd, env_addl = cmd_env
    assert cmd[-1].endswith("pyright")
    assert env_addl == {"QS_GEN_RUN_DIR": "/tmp/run", "QS_GEN_LAYER": "pyright"}


def test_layer_command_unit_skips_pyright() -> None:
    """Layer 2 sets QS_GEN_SKIP_PYRIGHT=1 so we don't pyright twice in one chain."""
    cmd_env = runner._layer_command("unit", Path("/tmp/run"))
    assert cmd_env is not None
    cmd, env_addl = cmd_env
    assert cmd[0].endswith("pytest")
    assert env_addl["QS_GEN_SKIP_PYRIGHT"] == "1"
    assert env_addl["QS_GEN_RUN_DIR"] == "/tmp/run"


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
        result = runner.dispatch_layer("pyright", tmp_path)
    assert mock_run.called
    assert result.passed is True
    assert result.skipped is False
    assert result.exit_code == 0


def test_dispatch_layer_failure_propagates(tmp_path: Path) -> None:
    fake = subprocess.CompletedProcess(args=["fake"], returncode=1, stdout="", stderr="")
    with patch.object(subprocess, "run", return_value=fake):
        result = runner.dispatch_layer("pyright", tmp_path)
    assert result.passed is False
    assert result.exit_code == 1


def test_dispatch_layer_passes_run_dir_via_env(tmp_path: Path) -> None:
    """QS_GEN_RUN_DIR threads through to the pytest subprocess so conftest
    fixtures (c.10/c.11/c.12) can route artifacts under runs/<run-id>/."""
    fake = subprocess.CompletedProcess(args=["fake"], returncode=0, stdout="", stderr="")
    with patch.object(subprocess, "run", return_value=fake) as mock_run:
        runner.dispatch_layer("pyright", tmp_path)
    call_kwargs = mock_run.call_args
    env = call_kwargs.kwargs["env"]
    assert env["QS_GEN_RUN_DIR"] == str(tmp_path)


def test_cmd_up_to_stops_on_first_failure() -> None:
    """When layer N fails, layers >N are NOT dispatched; chain returns FAILURE."""
    dispatched: list[str] = []

    def fake_dispatch(layer: str, run_dir: Path) -> runner.LayerResult:
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
    # pyright + unit dispatched; db should NOT have run because unit failed.
    assert dispatched == ["pyright", "unit"]


def test_cmd_up_to_runs_full_chain_when_all_pass() -> None:
    dispatched: list[str] = []

    def fake_dispatch(layer: str, run_dir: Path) -> runner.LayerResult:
        dispatched.append(layer)
        return runner.LayerResult(layer=layer, exit_code=0, duration_seconds=0.01)

    with (
        patch.object(runner, "probe_dependencies", return_value=[]),
        patch.object(runner, "dispatch_layer", side_effect=fake_dispatch),
    ):
        code = runner.main(["up_to=browser"])
    assert code == runner.EXIT_SUCCESS
    assert dispatched == ["pyright", "unit", "db", "deploy", "api", "browser"]


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
