"""``quicksight-gen`` CLI — six artifact groups.

The CLI is organized around the artifacts the tool produces:

  schema  apply | clean | test
  data    apply | refresh | clean | hash | etl-example | test
  json    apply | clean | test | probe
  docs    apply | serve | clean | test | export | screenshot
  audit   apply | clean | test                              # Phase U
  serve   app2 apply                                        # Phase X.2

Every artifact's ``apply``/``clean`` defaults to *emit* (print SQL to
stdout, write JSON to ``out/``, build site to ``site/``, render
Markdown source for the audit report). Pass ``--execute`` to actually
run the destructive thing (connect to the DB, deploy to AWS, write
the PDF). The ``docs`` and ``serve`` groups have no ``--execute``
because building a static site / running a server IS the operation.

Per-artifact files: ``schema.py``, ``data.py``, ``json.py``,
``docs.py``, ``audit.py``, ``serve.py``. Shared helpers:
``_helpers.py``. Per-app JSON-emit helpers: ``_app_builders.py``.
"""

from __future__ import annotations

import click

from quicksight_gen import __version__
from quicksight_gen.cli.audit import audit as _audit_group
from quicksight_gen.cli.data import data as _data_group
from quicksight_gen.cli.docs import docs as _docs_group
from quicksight_gen.cli.json import json_ as _json_group
from quicksight_gen.cli.schema import schema as _schema_group
from quicksight_gen.cli.serve import serve as _serve_group


@click.group()
@click.version_option(version=__version__, prog_name="quicksight-gen")
def main() -> None:
    """Generate + deploy AWS QuickSight dashboards from one L2 YAML."""


main.add_command(_schema_group, name="schema")
main.add_command(_data_group, name="data")
main.add_command(_json_group, name="json")
main.add_command(_docs_group, name="docs")
main.add_command(_audit_group, name="audit")
main.add_command(_serve_group, name="serve")


__all__ = ["main"]
