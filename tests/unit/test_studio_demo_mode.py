"""AE.2.b — Studio `--demo-mode` route lockdown tests.

The `--demo-mode` flag on `recon-gen studio` strips the mutation
endpoints from the route table so a public-demo host (Phase AE, Mac
mini under sandbox-exec) can't be poked into mutating L2 yaml on disk,
running the operator's etl_hook shell command, or triggering an AWS
deploy.

Lockdown contract — when `demo_mode=True`, these routes do NOT mount:

* Every L2 editor route (`POST/PUT/DELETE /l2_shape/...`)
* `PUT /data/knobs/etl_hook`
* `POST /deploy`

Read-only routes (landing, diagram, data view, dashboards, trainer
GETs, GET /l2_shape/... for the L2 view) and the rest of the trainer
knob mutations (plants, end_date, window, seed, scope, only_template,
derive_balances) STAY mounted — `--demo-mode` is a mutation-perimeter
cut, not a feature blackout. The trainer knobs persist to a tmpdir
sidefile (wired by `cli/studio.py`) so writes don't try to land on
the read-only L2 yaml's parent directory.

Defense in depth: sandbox-exec profile under `deploy/sandbox/` also
denies file-write on L2 yaml + cfg.yaml regardless of this flag. The
sandbox is the load-bearing safety; `--demo-mode` is the UX cut so
the UI doesn't expose buttons that would 500 against the sandbox.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

starlette = pytest.importorskip("starlette")

from recon_gen.common.config import Config
from recon_gen.common.html._studio_routes import make_studio_routes
from recon_gen.common.l2.cache import L2InstanceCache
from recon_gen.common.l2.tg_cache import TestGeneratorCache
from recon_gen.common.sql import Dialect


_FIXTURES = Path(__file__).resolve().parent.parent / "l2"


def _sqlite_cfg(tmp_path: Path, **overrides: object) -> Config:
    db_path = tmp_path / "demo.sqlite"
    base = Config(
        aws_account_id="111122223333",
        aws_region="us-east-1",
        deployment_name="recon-test",
        db_table_prefix="test",
        datasource_arn=(
            "arn:aws:quicksight:us-east-1:111122223333:datasource/x"
        ),
        demo_database_url=f"sqlite:///{db_path}",
        dialect=Dialect.SQLITE,
    )
    if overrides:
        base = replace(base, **overrides)  # type: ignore[arg-type]: replace's overload erases the per-field types
    return base


@pytest.fixture
def yaml_path() -> Iterator[Path]:
    yield _FIXTURES / "spec_example.yaml"


def _route_paths_with_methods(routes: list[object]) -> set[tuple[str, str]]:
    """Flatten a Starlette route list into ``(path, method)`` tuples.

    Mount entries are skipped — they nest sub-routes (e.g.
    `/studio/static`) that the lockdown contract doesn't address.
    """
    from starlette.routing import Route  # noqa: PLC0415
    out: set[tuple[str, str]] = set()
    for r in routes:
        if isinstance(r, Route):
            for m in r.methods or set():
                out.add((r.path, m))
    return out


def _build_routes_with_demo_mode(
    yaml_path: Path, tmp_path: Path, *, demo_mode: bool,
) -> list[object]:
    cfg = _sqlite_cfg(tmp_path)
    cache = L2InstanceCache.from_path(yaml_path)
    tg_cache = TestGeneratorCache(cfg.test_generator)
    return make_studio_routes(  # pyright: ignore[reportReturnType]: Starlette routes is list[Route | Mount]; widening is intentional
        cache,
        cfg=cfg,
        tg_cache=tg_cache,
        demo_mode=demo_mode,
    )


# ---------- mutation routes are present in baseline ----------

def test_baseline_mounts_editor_etl_deploy_routes(
    yaml_path: Path, tmp_path: Path,
) -> None:
    """Sanity check the baseline (demo_mode=False) mounts the routes
    that demo-mode is supposed to strip — otherwise the negative tests
    below would be vacuously passing.
    """
    routes = _build_routes_with_demo_mode(
        yaml_path, tmp_path, demo_mode=False,
    )
    pairs = _route_paths_with_methods(routes)
    # L2 yaml mutation routes (editor creates POST under /l2_shape/*).
    editor_pairs = {(p, m) for (p, m) in pairs if p.startswith("/l2_shape/")}
    assert any(m == "POST" for (_, m) in editor_pairs), (
        "Baseline must mount at least one POST under /l2_shape/* "
        "(editor 'create' route)."
    )
    assert ("/data/knobs/etl_hook", "PUT") in pairs
    assert ("/deploy", "POST") in pairs


# ---------- demo_mode strips the mutation routes ----------

def test_demo_mode_strips_l2_shape_mutating_routes(
    yaml_path: Path, tmp_path: Path,
) -> None:
    """In demo_mode, POST/PUT/DELETE handlers on /l2_shape/* must not
    be in the route table. The read-only list + read-card GETs are
    preserved (visitors can browse the L2 yaml's accounts / rails /
    templates / chains).
    """
    routes = _build_routes_with_demo_mode(
        yaml_path, tmp_path, demo_mode=True,
    )
    pairs = _route_paths_with_methods(routes)
    mutating_l2_shape = {
        (p, m) for (p, m) in pairs
        if p.startswith("/l2_shape/") and m in ("POST", "PUT", "DELETE")
    }
    assert mutating_l2_shape == set(), (
        f"demo_mode must strip POST/PUT/DELETE on /l2_shape/*; "
        f"found: {sorted(mutating_l2_shape)!r}"
    )
    # Form-GET routes whose submit buttons would 404 are also stripped
    # — /l2_shape/{kind}/new (POST submission goes nowhere) and
    # /l2_shape/{kind}/{entity_id}/edit (PUT submission ditto).
    form_gets = {
        (p, m) for (p, m) in pairs
        if p.startswith("/l2_shape/") and m == "GET"
        and (p.endswith("/new") or p.endswith("/edit"))
    }
    assert form_gets == set(), (
        f"demo_mode must strip /new + /edit form GETs; "
        f"found: {sorted(form_gets)!r}"
    )


def test_demo_mode_preserves_l2_shape_read_gets(
    yaml_path: Path, tmp_path: Path,
) -> None:
    """Demo-mode preserves the read-only browse view: list GET +
    entity read-card GET. This is the bug that surfaced when sasquatch
    went live — `/l2_shape/account/` 404'd because the editor routes
    were stripped wholesale.
    """
    routes = _build_routes_with_demo_mode(
        yaml_path, tmp_path, demo_mode=True,
    )
    pairs = _route_paths_with_methods(routes)
    expected_reads = {
        ("/l2_shape/{kind}/", "GET"),
        ("/l2_shape/{kind}/{entity_id}", "GET"),
    }
    missing = expected_reads - pairs
    assert missing == set(), (
        f"demo_mode dropped expected read-only /l2_shape/* routes: "
        f"{sorted(missing)!r}"
    )


def test_demo_mode_strips_etl_hook_put(
    yaml_path: Path, tmp_path: Path,
) -> None:
    """`PUT /data/knobs/etl_hook` triggers the operator's configured
    shell command (cfg.etl_hook). Public-demo hosting MUST NOT expose
    arbitrary shell-exec — strip in demo_mode.
    """
    routes = _build_routes_with_demo_mode(
        yaml_path, tmp_path, demo_mode=True,
    )
    pairs = _route_paths_with_methods(routes)
    assert ("/data/knobs/etl_hook", "PUT") not in pairs


def test_demo_mode_strips_deploy_post(
    yaml_path: Path, tmp_path: Path,
) -> None:
    """`POST /deploy` orchestrates the AWS QuickSight deploy pipeline
    against the operator's AWS account. No public-demo should ever
    execute that — strip in demo_mode.
    """
    routes = _build_routes_with_demo_mode(
        yaml_path, tmp_path, demo_mode=True,
    )
    pairs = _route_paths_with_methods(routes)
    assert ("/deploy", "POST") not in pairs


# ---------- demo_mode preserves the safe surface ----------

def test_demo_mode_preserves_read_routes(
    yaml_path: Path, tmp_path: Path,
) -> None:
    """Demo-mode is a mutation cut, not a feature blackout. Read-only
    routes (landing, data view, diagram, trainer JSON, visible-entity
    map) must still mount.
    """
    routes = _build_routes_with_demo_mode(
        yaml_path, tmp_path, demo_mode=True,
    )
    pairs = _route_paths_with_methods(routes)
    expected_reads = {
        ("/", "GET"),
        ("/data", "GET"),
        ("/data/timeline", "GET"),
        ("/diagram", "GET"),
        ("/diagram/trainer", "GET"),
        ("/diagram/visible", "GET"),
    }
    missing = expected_reads - pairs
    assert missing == set(), (
        f"demo_mode dropped expected read-only routes: {sorted(missing)!r}"
    )


def test_demo_mode_preserves_trainer_knob_routes(
    yaml_path: Path, tmp_path: Path,
) -> None:
    """Trainer knobs (plants, end_date, window, seed, scope,
    only_template, derive_balances) STAY mounted in demo_mode. They
    write to `.studio-state.yaml` which `cli/studio.py` redirects to a
    per-process tmpdir under demo_mode — so mutations are allowed at
    the route level but don't persist past the launchd restart cycle.

    `etl_hook` is the one trainer knob that DOES strip (separate test
    above) because it executes the operator's configured shell
    command rather than just flipping state.
    """
    routes = _build_routes_with_demo_mode(
        yaml_path, tmp_path, demo_mode=True,
    )
    pairs = _route_paths_with_methods(routes)
    expected_knobs = {
        ("/data/knobs/plants", "PUT"),
        ("/data/knobs/end_date", "PUT"),
        ("/data/knobs/window", "PUT"),
        ("/data/knobs/seed", "PUT"),
        ("/data/knobs/scope", "PUT"),
        ("/data/knobs/only_template", "PUT"),
        ("/data/knobs/derive_balances", "PUT"),
    }
    missing = expected_knobs - pairs
    assert missing == set(), (
        f"demo_mode dropped trainer knob routes that should remain: "
        f"{sorted(missing)!r}"
    )


# ---------- AH.4: demo-mode chrome cosmetics (rendered HTML) ----------
# The route lockdown above is the load-bearing safety; these assert the
# UX layer — a read-only banner + suppressed Deploy / create / edit
# affordances so the demo doesn't tease buttons that 404.
class TestDemoModeChrome:
    def _home(self, yaml_path: Path, tmp_path: Path, *, demo_mode: bool) -> str:
        from recon_gen.common.html._studio_routes import _render_home_page

        cfg = _sqlite_cfg(tmp_path)
        cache = L2InstanceCache.from_path(yaml_path)
        return _render_home_page(
            cache, dev_log=False, cfg=cfg, demo_mode=demo_mode,
        )

    def test_home_banner_present_only_in_demo_mode(
        self, yaml_path: Path, tmp_path: Path,
    ) -> None:
        assert "Read-only demo" not in self._home(
            yaml_path, tmp_path, demo_mode=False,
        )
        assert "Read-only demo" in self._home(
            yaml_path, tmp_path, demo_mode=True,
        )

    def test_home_deploy_button_hidden_in_demo_mode(
        self, yaml_path: Path, tmp_path: Path,
    ) -> None:
        assert 'id="deploy-btn"' in self._home(
            yaml_path, tmp_path, demo_mode=False,
        )
        assert 'id="deploy-btn"' not in self._home(
            yaml_path, tmp_path, demo_mode=True,
        )

    def test_home_create_affordance_hidden_in_demo_mode(
        self, yaml_path: Path, tmp_path: Path,
    ) -> None:
        # The per-kind "+ Add" links + the singleton "Edit" link.
        assert "+ Add" in self._home(yaml_path, tmp_path, demo_mode=False)
        assert "+ Add" not in self._home(yaml_path, tmp_path, demo_mode=True)

    def test_data_page_banner_and_deploy(
        self, yaml_path: Path, tmp_path: Path,
    ) -> None:
        from recon_gen.common.html._studio_routes import _render_data_page

        cfg = _sqlite_cfg(tmp_path)
        cache = L2InstanceCache.from_path(yaml_path)
        demo = _render_data_page(cache, dev_log=False, cfg=cfg, demo_mode=True)
        live = _render_data_page(cache, dev_log=False, cfg=cfg, demo_mode=False)
        assert "Read-only demo" in demo and 'id="deploy-btn"' not in demo
        assert "Read-only demo" not in live and 'id="deploy-btn"' in live

    def test_read_card_edit_delete_hidden_in_demo_mode(
        self, yaml_path: Path,
    ) -> None:
        from recon_gen.common.html._studio_editor_routes import _render_read_card

        inst = L2InstanceCache.from_path(yaml_path).get()
        account = inst.accounts[0]
        live = _render_read_card("account", account, inst, demo_mode=False)
        demo = _render_read_card("account", account, inst, demo_mode=True)
        # AM.1 step 6 (2026-05-25): `.edit-link` / `.delete-link`
        # semantic classes retired. Check the stable hooks: the live
        # card carries the edit + DELETE href targets, the demo card
        # carries neither (the route is 404'd in demo mode anyway).
        assert "/edit" in live and "hx-delete=" in live
        assert "/edit" not in demo and "hx-delete=" not in demo
