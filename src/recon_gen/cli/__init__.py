"""``recon-gen`` CLI — top-level command surface.

The CLI is organized around the artifacts the tool produces:

  schema      apply | clean | migrate-mark | test
  data        apply | refresh | clean | semantic-lock | etl-example | test
  docs        apply | serve | clean | test | export | screenshot
  audit       apply | clean | test | verify                    # Phase U
  dashboards                                                    # Phase X.2 (rename of `serve app2 apply`)
  studio                                                        # Phase X.4

Every artifact's ``apply``/``clean`` defaults to EMIT (print SQL to
stdout, build site to ``site/``, render Markdown source for the audit
report). Pass ``--execute`` to actually run the destructive thing
(connect to the DB, write the PDF). The ``docs``, ``dashboards`` and
``studio`` commands have no ``--execute`` because building a static
site / running a server IS the operation.

Per-artifact files: ``schema.py``, ``data.py``, ``docs.py``,
``audit.py``, ``dashboards.py``, ``studio.py``. Shared helpers:
``_helpers.py`` (Click options) + ``_html_serve.py`` (the shared
dashboards/studio uvicorn loop).

DW.7 (2026-06-27) — the ``json`` group (AWS QuickSight deploy) is gone;
the self-hosted ``dashboards`` / ``studio`` servers + the audit PDF are
the supported fronts.
"""

from __future__ import annotations

import click

from recon_gen import __version__
from recon_gen.cli.audit import audit as _audit_group
from recon_gen.cli.dashboards import dashboards as _dashboards_command
from recon_gen.cli.data import data as _data_group
from recon_gen.cli.docs import docs as _docs_group
from recon_gen.cli.schema import schema as _schema_group
from recon_gen.cli.studio import studio as _studio_command


@click.group()
@click.version_option(version=__version__, prog_name="recon-gen")
def main() -> None:
    """Independent reconciliation validation from one L2 YAML — self-hosted
    dashboards + regulator-ready audit PDF."""


main.add_command(_schema_group, name="schema")
main.add_command(_data_group, name="data")
main.add_command(_docs_group, name="docs")
main.add_command(_audit_group, name="audit")
main.add_command(_dashboards_command, name="dashboards")
main.add_command(_studio_command, name="studio")


__all__ = ["main"]
