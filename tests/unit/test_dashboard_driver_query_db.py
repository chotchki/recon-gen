"""BG.1 — unit coverage for the ``query_db_via_cfg`` ground-truth helper.

``DashboardDriver.query_db`` on both renderers delegates to this single
shared helper so identity assertions (rendered_kpi == query_db(sql, …))
compare against ONE ground truth — different SQL paths to "the same"
answer would silently hide bugs.

Covers the three shape contracts that the BG.X tightening (BG.2-BG.6)
will lean on:

1. **Bare ``:name`` binds work** — App2's URL-supplied params land
   directly in the bind dict.
2. **QS ``<<$pName>>`` placeholders translate + bind** — same SQL
   string the deployed dataset author wrote, sourced from production
   ``apps/<app>/datasets.py``, runs against the local DB.
3. **Empty + multi-value binds round-trip** — sentinel-default IN-lists
   degrade to "match all" exactly the way ``apply_dataset_param_defaults``
   handles them in production.

Test scope is intentionally narrow: the wire shape between
``query_db_via_cfg`` and ``execute_visual_sql`` is what BG.2-BG.6 will
trust. The full ``_sql_executor`` pipeline (placeholder dispatch,
multi-valued IN-list expansion, default substitution) is owned by
``test_html_sql_executor.py``.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest

from recon_gen.common.models import (
    DatasetParameter,
    StringDatasetParameter,
    StringDatasetParameterDefaultValues,
)
from recon_gen.common.sql.dialect import Dialect
from tests._test_helpers import make_test_config
from tests.e2e._drivers.base import query_db_via_cfg

if TYPE_CHECKING:
    from recon_gen.common.config import Config


@pytest.fixture
def sqlite_cfg() -> Iterator["Config"]:
    """Spin up a tiny SQLite-backed cfg with one table. ``query_db_via_cfg``
    opens / closes the connection per call, so the test fixture only needs
    to plant the data + return the cfg."""
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE accounts ("
        "  account_id TEXT,"
        "  account_role TEXT,"
        "  balance INTEGER"
        ")"
    )
    conn.executemany(
        "INSERT INTO accounts VALUES (?, ?, ?)",
        [
            ("acc-1", "internal", 100),
            ("acc-2", "internal", 200),
            ("acc-3", "external", 50),
        ],
    )
    conn.commit()
    conn.close()
    cfg = make_test_config(dialect=Dialect.SQLITE, demo_database_url=path)
    try:
        yield cfg
    finally:
        os.unlink(path)


def test_query_db_returns_rows_as_column_keyed_dicts(sqlite_cfg: "Config") -> None:
    rows = query_db_via_cfg(
        sqlite_cfg,
        "SELECT account_id, balance FROM accounts ORDER BY account_id",
    )
    assert rows == [
        {"account_id": "acc-1", "balance": 100},
        {"account_id": "acc-2", "balance": 200},
        {"account_id": "acc-3", "balance": 50},
    ]


def test_query_db_with_named_binds_narrows_result(sqlite_cfg: "Config") -> None:
    """The App2 URL contract: ``:name`` placeholders bind from the
    ``binds`` dict directly. Picker-derived test code drives this shape."""
    rows = query_db_via_cfg(
        sqlite_cfg,
        "SELECT account_id, balance FROM accounts "
        "WHERE account_role = :param_pRole ORDER BY account_id",
        binds={"param_pRole": "internal"},
    )
    assert rows == [
        {"account_id": "acc-1", "balance": 100},
        {"account_id": "acc-2", "balance": 200},
    ]


def test_query_db_translates_qs_dataset_params_to_binds(sqlite_cfg: "Config") -> None:
    """Y.1.e — the production datasets carry ``<<$pName>>`` literals
    (QS's substitution shape). The shared helper translates → binds via
    the same pipeline App2 uses, so the SAME SQL string the deployed
    dataset author wrote runs end-to-end here. This is the load-bearing
    contract for BG.2-BG.6: lift the SQL straight out of
    ``apps/<app>/datasets.py``, pass it through ``query_db``, compare to
    the rendered visual."""
    rows = query_db_via_cfg(
        sqlite_cfg,
        "SELECT account_id FROM accounts "
        "WHERE account_role = <<$pRole>> ORDER BY account_id",
        binds={"param_pRole": "external"},
    )
    assert rows == [{"account_id": "acc-3"}]


def test_query_db_unsupplied_param_falls_back_to_dataset_default(sqlite_cfg: "Config") -> None:
    """Y.2.app2.cde — when ``binds`` doesn't supply a ``param_<name>``
    referenced in the SQL, the matching ``dataset_parameters`` default
    substitutes inline (same as App2's initial-page-load behavior +
    QS's ``DefaultValues`` resolution). The honest-gate tests pass the
    same ``dataset_parameters`` the production dataset declares; an
    unset picker behaves exactly as it does on the rendered visual."""
    rows = query_db_via_cfg(
        sqlite_cfg,
        "SELECT account_id FROM accounts "
        "WHERE account_role = <<$pRole>> ORDER BY account_id",
        binds={},
        dataset_parameters=[
            DatasetParameter(
                StringDatasetParameter=StringDatasetParameter(
                    Id="id-pRole", Name="pRole", ValueType="SINGLE_VALUED",
                    DefaultValues=StringDatasetParameterDefaultValues(
                        StaticValues=["internal"],
                    ),
                )
            ),
        ],
    )
    assert rows == [
        {"account_id": "acc-1"},
        {"account_id": "acc-2"},
    ]
