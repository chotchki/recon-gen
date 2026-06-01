"""BY.3 — Secret-isolation policy lint for ``.github/workflows/release.yml``.

release.yml's jobs handle the project's most sensitive credentials —
PyPI publish tokens (``publish-testpypi`` / ``publish-pypi``),
AWS OIDC role assumption for live deploys (``e2e-against-testpypi``),
and the ``GITHUB_TOKEN`` write scope that ``github-release`` uses to
cut releases. A compromise of any of these has a much worse blast
radius than a compromise of the test runner:

- PyPI tokens published from a self-hosted runner could push malicious
  wheels to every recon-gen user. The PyPI account is also the project
  identity to downstream integrators.
- AWS deploy creds get write-level QuickSight access to the project
  account, and ``release-e2e`` is the canonical pre-cut gate that
  exercises full deploys end-to-end.
- ``GITHUB_TOKEN`` with ``contents: write`` can manipulate release
  artifacts, tags, and (in this repo) the ``badges`` branch.

GitHub-managed ``ubuntu-latest`` runners are ephemeral, network-isolated,
and rotated per job. The Windows-host WSL2 self-hosted runner (BY.0+)
is excellent for fast CI and E2E tests against test-tier credentials,
but it sits on the operator's home network, is single-tenant, and
shares filesystem state across jobs. **release.yml's secret-bearing
jobs must stay on ubuntu-latest.**

This module enforces that contract structurally: a grep + YAML parse
of ``release.yml`` asserting every job's ``runs-on:`` is the literal
string ``ubuntu-latest``. Any future ``runs-on: [self-hosted, ...]``
line in release.yml fails this test and demands a deliberate review.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_YML = REPO_ROOT / ".github" / "workflows" / "release.yml"


def _job_definitions(yml_text: str) -> list[tuple[str, str]]:
    """Return ``[(job_name, runs_on_value), ...]`` parsed from release.yml.

    Tiny purpose-built scanner; doesn't try to be a full YAML parser.
    Looks for two-space-indented ``<job-name>:`` lines (top-level under
    ``jobs:``) followed by the first ``runs-on:`` inside that block
    (recognized as a four-or-more-space indent — i.e., a key under the
    job).
    """
    lines = yml_text.splitlines()
    out: list[tuple[str, str]] = []
    in_jobs_block = False
    current_job: str | None = None
    job_runs_on: str | None = None

    for raw in lines:
        stripped = raw.rstrip()
        # Section header: anchor on the top-level ``jobs:`` line.
        if re.match(r"^jobs:\s*$", stripped):
            in_jobs_block = True
            continue
        if not in_jobs_block:
            continue
        # Another top-level key (zero-indent, ends with colon) closes
        # the jobs block.
        if (
            stripped
            and not raw.startswith(" ")
            and stripped.endswith(":")
            and stripped != "jobs:"
        ):
            in_jobs_block = False
            if current_job is not None and job_runs_on is not None:
                out.append((current_job, job_runs_on))
            current_job, job_runs_on = None, None
            continue
        # Job header: ``  <job-name>:`` (two-space indent, no
        # trailing content beyond the colon).
        m_job = re.match(r"^  ([a-zA-Z0-9_-]+):\s*$", raw)
        if m_job:
            # Close out the previous job's entry before starting a new one.
            if current_job is not None and job_runs_on is not None:
                out.append((current_job, job_runs_on))
            current_job = m_job.group(1)
            job_runs_on = None
            continue
        # ``runs-on:`` inside a job (indent >= 4 spaces). We capture
        # only the FIRST runs-on per job (jobs typically only have one).
        if current_job is not None and job_runs_on is None:
            m_runs = re.match(r"^    runs-on:\s*(.+?)\s*$", raw)
            if m_runs:
                job_runs_on = m_runs.group(1).strip()

    # Tail flush — the last job in the file doesn't get closed by a
    # subsequent top-level key.
    if current_job is not None and job_runs_on is not None:
        out.append((current_job, job_runs_on))

    return out


def test_release_yml_exists() -> None:
    """Sanity: the file the lint targets must exist."""
    assert RELEASE_YML.is_file(), f"missing {RELEASE_YML}"


def test_release_yml_every_job_is_ubuntu_latest() -> None:
    """**BY.3 enforcement** — every job in release.yml must use
    ``runs-on: ubuntu-latest``. No self-hosted, no matrix, no fallback.

    See module docstring for the security rationale.

    To override an individual job's runs-on (e.g., a future
    ``windows-latest`` smoke test): add the job name to the
    ``ALLOWED_EXCEPTIONS`` allowlist below with a one-line ``# WHY``
    comment explaining why that job's secret-exposure profile permits
    a non-default runner.
    """
    # Allowlist: jobs explicitly approved for non-``ubuntu-latest`` runners.
    # KEEP EMPTY by default. Adding here requires a security review.
    ALLOWED_EXCEPTIONS: dict[str, str] = {
        # name: "WHY this job is safe to run elsewhere"
    }

    yml_text = RELEASE_YML.read_text(encoding="utf-8")
    jobs = _job_definitions(yml_text)
    assert jobs, (
        "no jobs parsed from release.yml — parser drift; tighten _job_definitions"
    )

    violations: list[str] = []
    for name, runs_on in jobs:
        if name in ALLOWED_EXCEPTIONS:
            continue
        if runs_on != "ubuntu-latest":
            violations.append(f"  {name}: runs-on: {runs_on!r}")

    if violations:
        pytest.fail(
            "BY.3 secret-isolation policy violated — release.yml jobs must "
            "use `runs-on: ubuntu-latest`. Self-hosted runners are forbidden "
            "for publish (PyPI), deploy (AWS OIDC), and GitHub Release jobs.\n"
            "Violations:\n" + "\n".join(violations)
        )


def test_release_yml_no_self_hosted_reference_in_yaml() -> None:
    """Belt-and-suspenders: outside of comments, the string ``self-hosted``
    should not appear in release.yml. Catches sneaky paths the per-job
    lint might miss (e.g., dynamic ``runs-on: ${{ matrix.runner }}``
    with self-hosted in a matrix value, or a future step-level runner
    override).

    Comment lines (``#`` prefix after optional indent) are allowed —
    the file's own header block legitimately explains *why* self-hosted
    is forbidden here, and grepping that text without context-awareness
    would false-positive on documentation.
    """
    yml_text = RELEASE_YML.read_text(encoding="utf-8")
    matches: list[tuple[int, str]] = []
    for lineno, line in enumerate(yml_text.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue  # comment — documentation references allowed
        if "self-hosted" in line.lower():
            matches.append((lineno, line.strip()))
    if matches:
        pytest.fail(
            "BY.3 — 'self-hosted' string appears in non-comment lines of "
            "release.yml. Audit each reference; release.yml jobs must run "
            "on GitHub-managed ubuntu-latest only.\n"
            + "\n".join(f"  release.yml:{n}: {ln}" for n, ln in matches)
        )
