"""Sphinx configuration for the recon_gen API reference (ReadTheDocs).

The end-user / integrator docs live separately at GitHub Pages
(mkdocs, ``src/recon_gen/mkdocs.yml``). This config drives the Python
API surface only — autosummary walks the package tree, napoleon
parses Google-style docstrings, and sphinx-autodoc-typehints surfaces
the inline type annotations.
"""

from __future__ import annotations

import importlib.metadata


project = "recon-gen"
author = "Christopher Hotchkiss"
copyright = f"2026, {author}"  # noqa: A001 — sphinx convention

try:
    release = importlib.metadata.version("recon-gen")
except importlib.metadata.PackageNotFoundError:
    release = "dev"
version = release

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_autodoc_typehints",
    "myst_parser",
]

autosummary_generate = True
autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
    "inherited-members": False,
}
autodoc_typehints = "description"
autodoc_typehints_format = "short"
napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_use_param = True
napoleon_use_rtype = True

exclude_patterns = [
    "_build",
    "Thumbs.db",
    ".DS_Store",
]

html_theme = "sphinx_rtd_theme"
html_title = f"{project} API"
html_short_title = "API"

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}
