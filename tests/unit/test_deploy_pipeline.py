"""X.4.g — deploy pipeline coverage.

The pipeline module is HTTP-free: each step takes the cfg + an
optional ``DevLogWriter`` (a ``Callable[[Mapping], Awaitable[None]]``)
and returns a primitive (exit code, row count, etc.). Tests assert
against the writer-collected event list, which is the same shape the
studio's POST /deploy endpoint will surface.

Async functions are wrapped in ``asyncio.run`` (project convention —
see tests/unit/test_common_db.py) rather than relying on
``pytest.mark.asyncio`` (the plugin isn't installed).
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace

import pytest

from quicksight_gen.common.config import Config
from quicksight_gen.common.l2.deploy_pipeline import step_1_etl_hook


def _base_cfg() -> Config:
    return Config(
        aws_account_id="111122223333",
        aws_region="us-east-1",
        datasource_arn=(
            "arn:aws:quicksight:us-east-1:111122223333:datasource/x"
        ),
    )


class _EventCollector:
    """List-collecting DevLogWriter for assertions."""

    def __init__(self) -> None:
        self.events: list[Mapping[str, object]] = []

    async def __call__(self, payload: Mapping[str, object]) -> None:
        self.events.append(dict(payload))

    def kinds(self) -> list[str]:
        return [str(e.get("event", "")) for e in self.events]

    def by_kind(self, kind: str) -> list[Mapping[str, object]]:
        return [e for e in self.events if e.get("event") == kind]


def _run_step_1(cfg: Config, sink: _EventCollector | None) -> int:
    return asyncio.run(step_1_etl_hook(cfg, dev_log=sink))


# ---------- skip paths ----------

def test_etl_hook_unset_returns_zero_and_emits_skip() -> None:
    cfg = _base_cfg()
    assert cfg.etl_hook is None
    sink = _EventCollector()
    assert _run_step_1(cfg, sink) == 0
    assert sink.kinds() == ["deploy:step1:skip"]
    assert sink.events[0]["reason"] == "etl_hook not configured"


def test_etl_hook_whitespace_only_skips() -> None:
    """Empty / whitespace shlex result is treated as no-op (not error)."""
    cfg = replace(_base_cfg(), etl_hook="   ")
    sink = _EventCollector()
    assert _run_step_1(cfg, sink) == 0
    assert sink.kinds() == ["deploy:step1:skip"]
    assert "empty after shlex" in str(sink.events[0]["reason"])


# ---------- exit code propagation ----------

def test_etl_hook_zero_exit_returns_zero() -> None:
    cfg = replace(_base_cfg(), etl_hook="sh -c 'exit 0'")
    sink = _EventCollector()
    assert _run_step_1(cfg, sink) == 0
    assert "deploy:step1:done" in sink.kinds()
    assert sink.by_kind("deploy:step1:done")[0]["exit_code"] == 0


def test_etl_hook_nonzero_exit_propagates() -> None:
    """Halt contract: caller checks rc != 0 and skips step 2."""
    cfg = replace(_base_cfg(), etl_hook="sh -c 'exit 7'")
    sink = _EventCollector()
    assert _run_step_1(cfg, sink) == 7
    assert sink.by_kind("deploy:step1:done")[0]["exit_code"] == 7


# ---------- streaming ----------

def test_etl_hook_stdout_streams_line_by_line() -> None:
    cfg = replace(_base_cfg(), etl_hook="sh -c 'echo first; echo second'")
    sink = _EventCollector()
    _run_step_1(cfg, sink)
    stdout_lines = [
        e["line"] for e in sink.by_kind("deploy:step1:stdout")
    ]
    assert stdout_lines == ["first", "second"]


def test_etl_hook_stderr_streams_separately() -> None:
    cfg = replace(_base_cfg(), etl_hook=(
        "sh -c 'echo to-stdout; echo to-stderr 1>&2'"
    ))
    sink = _EventCollector()
    _run_step_1(cfg, sink)
    assert [e["line"] for e in sink.by_kind("deploy:step1:stdout")] == [
        "to-stdout",
    ]
    assert [e["line"] for e in sink.by_kind("deploy:step1:stderr")] == [
        "to-stderr",
    ]


def test_etl_hook_event_order_start_then_streams_then_done() -> None:
    """The full lifecycle in order; pipeline orchestration relies on
    this so it can render progress incrementally."""
    cfg = replace(_base_cfg(), etl_hook="sh -c 'echo go; exit 3'")
    sink = _EventCollector()
    assert _run_step_1(cfg, sink) == 3
    kinds = sink.kinds()
    assert kinds[0] == "deploy:step1:start"
    assert kinds[-1] == "deploy:step1:done"
    assert "deploy:step1:stdout" in kinds


# ---------- dev_log opt-out ----------

def test_etl_hook_dev_log_none_does_not_crash() -> None:
    """Pipeline callers may opt out of streaming (e.g. CLI's --quiet)."""
    cfg = replace(_base_cfg(), etl_hook="sh -c 'exit 0'")
    assert _run_step_1(cfg, None) == 0


# ---------- failure modes ----------

def test_etl_hook_missing_binary_propagates() -> None:
    """A missing binary is operator-actionable, NOT a silent skip.
    Whole point of declaring etl_hook is that it MUST run."""
    cfg = replace(
        _base_cfg(),
        etl_hook="/nonexistent/binary/that/does-not-exist arg1",
    )
    sink = _EventCollector()
    with pytest.raises(FileNotFoundError):
        _run_step_1(cfg, sink)
    # `start` event fired before the failure surfaced.
    assert sink.kinds()[0] == "deploy:step1:start"
