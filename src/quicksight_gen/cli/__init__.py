"""``quicksight-gen`` CLI — top-level command surface.

The CLI is organized around the artifacts the tool produces:

  schema      apply | clean | test
  data        apply | refresh | clean | hash | etl-example | test
  json        apply | clean | test | probe
  docs        apply | serve | clean | test | export | screenshot
  audit       apply | clean | test                              # Phase U
  dashboards                                                    # Phase X.2 (rename of `serve app2 apply`)
  studio                                                        # Phase X.4

Every artifact's ``apply``/``clean`` defaults to *emit* (print SQL to
stdout, write JSON to ``out/``, build site to ``site/``, render
Markdown source for the audit report). Pass ``--execute`` to actually
run the destructive thing (connect to the DB, deploy to AWS, write
the PDF). The ``docs``, ``dashboards``, and ``studio`` commands have
no ``--execute`` because building a static site / running a server IS
the operation.

Per-artifact files: ``schema.py``, ``data.py``, ``json.py``,
``docs.py``, ``audit.py``, ``dashboards.py``, ``studio.py``. Shared
helpers: ``_helpers.py`` (Click options) + ``_html_serve.py`` (the
shared dashboards/studio uvicorn loop). Per-app JSON-emit helpers:
``_app_builders.py``.
"""

from __future__ import annotations

import click

from quicksight_gen import __version__
from quicksight_gen.cli.audit import audit as _audit_group
from quicksight_gen.cli.dashboards import dashboards as _dashboards_command
from quicksight_gen.cli.data import data as _data_group
from quicksight_gen.cli.docs import docs as _docs_group
from quicksight_gen.cli.json import json_ as _json_group
from quicksight_gen.cli.schema import schema as _schema_group
from quicksight_gen.cli.studio import studio as _studio_command


@click.group()
@click.version_option(version=__version__, prog_name="quicksight-gen")
def main() -> None:
    """Generate + deploy AWS QuickSight dashboards from one L2 YAML."""


main.add_command(_schema_group, name="schema")
main.add_command(_data_group, name="data")
main.add_command(_json_group, name="json")
main.add_command(_docs_group, name="docs")
main.add_command(_audit_group, name="audit")
main.add_command(_dashboards_command, name="dashboards")
main.add_command(_studio_command, name="studio")


__all__ = ["main"]
