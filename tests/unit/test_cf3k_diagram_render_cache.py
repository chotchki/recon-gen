"""CF.3.k — diagram render cache contract.

In-memory LRU on `_build_digraph_cached` keyed by (instance id, prefix,
focus, layer, hide_singleleg). Studio is single-process so a module-
level dict is enough; the cache invalidates naturally when L2 yaml
save → fresh L2Instance → different id() → cache miss → fresh build.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from recon_gen.common.l2.loader import load_instance


FIXTURES_DIR = Path(__file__).parent.parent / "l2"


def _reset_cache() -> None:
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _DIAGRAM_DIGRAPH_CACHE,
    )
    _DIAGRAM_DIGRAPH_CACHE.clear()


def test_cf3k_same_args_returns_cached_digraph() -> None:
    """Two calls with identical (instance, prefix, focus, layer,
    hide_singleleg) return the SAME object — cache hit."""
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _build_digraph_cached,
    )
    _reset_cache()
    inst = load_instance(FIXTURES_DIR / "spec_example.yaml")
    g1 = _build_digraph_cached(
        inst,
        db_table_prefix="cf3k_test",
        focus_node_id=None,
        layer=3,
        hide_singleleg=False,
    )
    g2 = _build_digraph_cached(
        inst,
        db_table_prefix="cf3k_test",
        focus_node_id=None,
        layer=3,
        hide_singleleg=False,
    )
    assert g1 is g2, "expected cache hit on identical args"


def test_cf3k_different_layer_misses() -> None:
    """Different layer → different cache entry → distinct objects."""
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _build_digraph_cached,
    )
    _reset_cache()
    inst = load_instance(FIXTURES_DIR / "spec_example.yaml")
    g_l1 = _build_digraph_cached(
        inst, db_table_prefix="cf3k_test",
        focus_node_id=None, layer=1, hide_singleleg=False,
    )
    g_l3 = _build_digraph_cached(
        inst, db_table_prefix="cf3k_test",
        focus_node_id=None, layer=3, hide_singleleg=False,
    )
    assert g_l1 is not g_l3
    # L3 should have far more edges than L1 — sanity that the cache
    # didn't accidentally fold these.
    assert g_l1.source != g_l3.source


def test_cf3k_different_hide_singleleg_misses() -> None:
    """Different hide_singleleg → different cache entry."""
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _build_digraph_cached,
    )
    _reset_cache()
    inst = load_instance(FIXTURES_DIR / "spec_example.yaml")
    g_show = _build_digraph_cached(
        inst, db_table_prefix="cf3k_test",
        focus_node_id=None, layer=3, hide_singleleg=False,
    )
    g_hide = _build_digraph_cached(
        inst, db_table_prefix="cf3k_test",
        focus_node_id=None, layer=3, hide_singleleg=True,
    )
    assert g_show is not g_hide


def test_cf3k_fresh_instance_misses_natural_invalidation() -> None:
    """Different L2Instance object id → cache miss → fresh build.

    Simulates the operator saving the yaml: the L2InstanceCache
    returns a new L2Instance object (different identity) and the
    digraph cache naturally falls through.
    """
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _build_digraph_cached,
    )
    _reset_cache()
    inst_a = load_instance(FIXTURES_DIR / "spec_example.yaml")
    inst_b = load_instance(FIXTURES_DIR / "spec_example.yaml")  # fresh load
    assert id(inst_a) != id(inst_b), (
        "test setup expects two fresh load_instance calls to yield "
        "distinct objects"
    )
    g_a = _build_digraph_cached(
        inst_a, db_table_prefix="cf3k_test",
        focus_node_id=None, layer=3, hide_singleleg=False,
    )
    g_b = _build_digraph_cached(
        inst_b, db_table_prefix="cf3k_test",
        focus_node_id=None, layer=3, hide_singleleg=False,
    )
    assert g_a is not g_b, (
        "expected cache MISS when the instance object identity changes"
    )


def test_cf3k_cache_bounded() -> None:
    """LRU bound prevents unbounded growth."""
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _DIAGRAM_DIGRAPH_CACHE,
        _DIAGRAM_DIGRAPH_CACHE_MAX,
        _build_digraph_cached,
    )
    pytest.importorskip("graphviz")
    _reset_cache()
    inst = load_instance(FIXTURES_DIR / "spec_example.yaml")
    # Fill beyond the cap by varying the focus_node_id (cheap key spin).
    for i in range(_DIAGRAM_DIGRAPH_CACHE_MAX + 8):
        _build_digraph_cached(
            inst, db_table_prefix="cf3k_test",
            focus_node_id=f"sentinel_{i}", layer=3, hide_singleleg=False,
        )
    assert len(_DIAGRAM_DIGRAPH_CACHE) <= _DIAGRAM_DIGRAPH_CACHE_MAX, (
        f"cache size {len(_DIAGRAM_DIGRAPH_CACHE)} exceeds bound "
        f"{_DIAGRAM_DIGRAPH_CACHE_MAX}"
    )
