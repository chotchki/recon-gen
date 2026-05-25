"""Markdown renderers for the audit report.

Mirrors each PDF section as Markdown so an integrator can review the
report's content via ``audit apply`` (no ``--execute``) before
committing to a PDF write. PDF and Markdown share the same
data-class shapes — the renderers walk the same dataclass instances
``cli/audit/__init__.py`` populates from the DB.
"""

from __future__ import annotations

from datetime import date, datetime

from recon_gen.common.as_of_frame import AsOfFrame
from recon_gen.common.intervals import DateInterval  # noqa: F401 — kept for tests that import via this module; BD.2 hides direct period reads
from recon_gen.common.provenance import (
    ProvenanceFingerprint,
    l2_fingerprint_placeholder,
)


# -- Renderers (cover page — U.2+ appends body sections) ----------------------


from recon_gen.cli.audit import (
    DailyStatementWalk,
    DriftViolation,
    ExecSummary,
    LimitBreachViolation,
    OverdraftViolation,
    StuckPendingViolation,
    StuckUnbundledViolation,
    SupersessionAuditData,
    _EXCEPTION_INVARIANTS,
    _format_age,
    _split_limit_breach_by_account_class,
    _split_overdraft_by_account_class,
    _split_stuck_pending_by_account_class,
    _split_stuck_unbundled_by_account_class,
)


def _render_audit_markdown(
    *,
    institution: str,
    frame: AsOfFrame,
    generated_at: datetime,
    exec_summary: ExecSummary | None,
    drift_rows: list[DriftViolation] | None,
    overdraft_rows: list[OverdraftViolation] | None,
    limit_breach_rows: list[LimitBreachViolation] | None,
    stuck_pending_rows: list[StuckPendingViolation] | None,
    stuck_unbundled_rows: list[StuckUnbundledViolation] | None,
    supersession_data: SupersessionAuditData | None,
    daily_statement_walks: list[DailyStatementWalk] | None,
    singleton_ids: set[str],
    version: str,
    l2_label: str,
    provenance: ProvenanceFingerprint | None,
    matview_evidence: list | None,  # list[MatviewEvidence] | None
    l2_instance_path: str | None,
) -> str:
    """Markdown rendering of the audit report.

    Mirrors the PDF page sequence — cover, executive summary,
    per-invariant violation tables (U.3.a Drift through U.3.f
    Supersession audit), then U.4 per-account Daily Statement walks
    — so an integrator can review the report's content before
    committing to a real PDF write.
    """
    start, end = frame.window.start, frame.window.end
    fingerprint = (
        provenance.composite_sha
        if provenance is not None
        else l2_fingerprint_placeholder()
    )
    cover = (
        "# Recon Generator Audit Report\n"
        "\n"
        f"## {institution}\n"
        "\n"
        f"**Reporting period:** {start.isoformat()} – {end.isoformat()} "
        "(inclusive)\n"
        "\n"
        f"**Generated:** {generated_at.isoformat(timespec='seconds')}\n"
        "\n"
        "This report covers the L1 reconciliation invariants — drift, "
        "overdraft, limit breach, stuck pending, stuck unbundled, "
        "supersession audit — for the period above. Sourced directly "
        "from the operator's database matviews; see the provenance "
        "fingerprint at the bottom of every page for reproducibility.\n"
        "\n"
        "---\n"
        "\n"
        f"_Provenance fingerprint:_ `{fingerprint}`\n"
    )
    body = (
        _render_executive_summary_markdown(exec_summary)
        + _render_drift_markdown(drift_rows)
        + _render_overdraft_markdown(overdraft_rows, singleton_ids)
        + _render_limit_breach_markdown(limit_breach_rows, singleton_ids)
        + _render_stuck_pending_markdown(stuck_pending_rows, singleton_ids)
        + _render_stuck_unbundled_markdown(
            stuck_unbundled_rows, singleton_ids,
        )
        + _render_supersession_markdown(supersession_data, frame)
        + _render_daily_statement_walks_markdown(daily_statement_walks)
        + _render_signoff_markdown(
            institution=institution,
            frame=frame,
            generated_at=generated_at,
            version=version,
            l2_label=l2_label,
            provenance=provenance,
        )
        + _render_appendix_markdown(
            version=version,
            l2_label=l2_label,
            provenance=provenance,
            matview_evidence=matview_evidence,
        )
    )
    return cover + body


def _render_executive_summary_markdown(
    summary: ExecSummary | None,
) -> str:
    """Executive summary section in Markdown form.

    Renders the same Volume + Exception-counts tables as the PDF.
    When ``summary`` is None (no DB), shows "—" cells with a notice
    so the layout stays previewable.
    """
    if summary is None:
        volume_rows = (
            "| Transactions (legs) | — |\n"
            "| Transfers (logical events) | — |\n"
            "| Dollar volume — gross | — |\n"
            "| Dollar volume — net | — |\n"
        )
        exc_labels = [
            f"{label}\\*" if date_col is None else label
            for label, _, date_col in _EXCEPTION_INVARIANTS
        ] + ["Supersession"]
        exc_rows = "".join(f"| {label} | — |\n" for label in exc_labels)
        notice = (
            "\n_Database not configured — totals shown as placeholders. "
            "Set `demo_database_url` in your config to populate._\n"
        )
    else:
        volume_rows = (
            f"| Transactions (legs) | {summary.transactions_count:,} |\n"
            f"| Transfers (logical events) | {summary.transfers_count:,} |\n"
            f"| Dollar volume — gross | "
            f"${summary.dollar_volume_gross:,.2f} |\n"
            f"| Dollar volume — net | "
            f"${summary.dollar_volume_net:,.2f} |\n"
        )
        exc_rows = "".join(
            f"| {label} | {count:,} |\n"
            for label, count in summary.exception_counts
        )
        notice = ""
    return (
        "\n"
        "---\n"
        "\n"
        "## Executive Summary\n"
        f"{notice}"
        "\n"
        "### Volume\n"
        "\n"
        "| Metric | Value |\n"
        "|---|---:|\n"
        f"{volume_rows}"
        "\n"
        "### Exception Counts\n"
        "\n"
        "| Invariant | Count |\n"
        "|---|---:|\n"
        f"{exc_rows}"
        "\n"
        "_\\* Current state — open as of report generation, "
        "regardless of when posted (matches the L1 dashboard "
        "convention for stuck-aging matviews)._\n"
    )


def _render_drift_markdown(
    rows: list[DriftViolation] | None,
) -> str:
    """Drift violations section in Markdown form.

    Mirrors the PDF page. None = DB not configured (placeholder
    notice only); empty list = DB healthy with zero violations
    in the period (good-news render); non-empty = full table.
    """
    header = (
        "\n"
        "---\n"
        "\n"
        "## Drift Violations\n"
        "\n"
        "_Per-account-day discrepancies between stored end-of-day "
        "balance and the balance computed from posted transactions._\n"
    )
    if rows is None:
        return header + (
            "\n_Database not configured — table not populated. "
            "Set `demo_database_url` in your config to query._\n"
        )
    if not rows:
        return header + (
            "\n_No drift detected for the period — books reconcile._\n"
        )
    body = (
        "\n"
        "| Account ID | Account name | Role | Day | Stored | Computed | Drift |\n"
        "|---|---|---|---|---:|---:|---:|\n"
    )
    for r in rows:
        body += (
            f"| `{r.account_id}` | {r.account_name} | {r.account_role} | "
            f"{r.business_day.isoformat()} | "
            f"${r.stored_balance:,.2f} | "
            f"${r.computed_balance:,.2f} | "
            f"${r.drift:,.2f} |\n"
        )
    return header + body


def _render_overdraft_markdown(
    rows: list[OverdraftViolation] | None,
    singleton_ids: set[str],
) -> str:
    """Overdraft violations section in Markdown form.

    Splits rows into parent accounts (L2 ``Account`` singletons —
    per-row detail because a parent itself going negative is a
    systemic event) and child accounts (template-materialized —
    rolled up by parent role with distinct-children-negative +
    total-peak-negative). Same None / [] / non-empty convention as
    the Drift section.
    """
    header = (
        "\n"
        "---\n"
        "\n"
        "## Overdraft Violations\n"
        "\n"
        "_Account-days where the stored end-of-day balance went "
        "negative. Parent accounts (L2 singletons — GL clearing, "
        "concentration, ZBA master) are shown per-row because a "
        "parent itself going negative is a systemic event. Child "
        "accounts (templated, e.g. customer DDAs, ZBA sub-accounts) "
        "roll up by parent role with distinct-children-negative + "
        "summed-peak-negative._\n"
    )
    if rows is None:
        return header + (
            "\n_Database not configured — table not populated. "
            "Set `demo_database_url` in your config to query._\n"
        )
    if not rows:
        return header + (
            "\n_No overdrafts detected for the period._\n"
        )

    parent_rows, child_groups = _split_overdraft_by_account_class(
        rows, singleton_ids,
    )
    out = header
    if parent_rows:
        out += (
            "\n"
            "### Parent Accounts (Per-Row Detail)\n"
            "\n"
            "| Account ID | Account name | Role | Day | Stored balance |\n"
            "|---|---|---|---|---:|\n"
        )
        for r in parent_rows:
            out += (
                f"| `{r.account_id}` | {r.account_name} | "
                f"{r.account_role} | {r.business_day.isoformat()} | "
                f"${r.stored_balance:,.2f} |\n"
            )
    else:
        out += "\n_No parent-account overdrafts in the period._\n"
    if child_groups:
        out += (
            "\n"
            "### Child Accounts Grouped by Parent Role\n"
            "\n"
            "| Parent role | Children negative | Total peak negative |\n"
            "|---|---:|---:|\n"
        )
        for s in child_groups:
            out += (
                f"| {s.parent_role} | {s.distinct_children_negative} "
                f"| ${s.total_peak_negative:,.2f} |\n"
            )
    return out


def _render_limit_breach_markdown(
    rows: list[LimitBreachViolation] | None,
    singleton_ids: set[str],
) -> str:
    """Limit breach violations section in Markdown form.

    Same parent-vs-child split as Overdraft. Children grouped by
    (parent_role, rail_name) since the LimitSchedule cap is
    keyed on that pair.
    """
    header = (
        "\n"
        "---\n"
        "\n"
        "## Limit Breach Violations\n"
        "\n"
        "_Account-day-rail_name cells where cumulative outbound "
        "exceeded the L2-configured cap. Parent accounts shown "
        "per-row; child accounts grouped by (parent role, transfer "
        "type) — the LimitSchedule key shape._\n"
    )
    if rows is None:
        return header + (
            "\n_Database not configured — table not populated. "
            "Set `demo_database_url` in your config to query._\n"
        )
    if not rows:
        return header + (
            "\n_No limit breaches detected for the period._\n"
        )

    parent_rows, child_groups = _split_limit_breach_by_account_class(
        rows, singleton_ids,
    )
    out = header
    if parent_rows:
        out += (
            "\n"
            "### Parent Accounts (Per-Row Detail)\n"
            "\n"
            "| Account ID | Account name | Role | Day | Transfer type "
            "| Outbound | Cap | Overshoot |\n"
            "|---|---|---|---|---|---:|---:|---:|\n"
        )
        for r in parent_rows:
            out += (
                f"| `{r.account_id}` | {r.account_name} | "
                f"{r.account_role} | {r.business_day.isoformat()} | "
                f"{r.rail_name} | ${r.outbound_total:,.2f} "
                f"| ${r.cap:,.2f} | ${r.overshoot:,.2f} |\n"
            )
    if child_groups:
        out += (
            "\n"
            "### Child Accounts Grouped by Parent Role + Transfer Type\n"
            "\n"
            "| Parent role | Transfer type | Children breaching "
            "| Total overshoot |\n"
            "|---|---|---:|---:|\n"
        )
        for s in child_groups:
            out += (
                f"| {s.parent_role} | {s.rail_name} "
                f"| {s.distinct_children_breaching} "
                f"| ${s.total_overshoot:,.2f} |\n"
            )
    return out


def _render_stuck_pending_markdown(
    rows: list[StuckPendingViolation] | None,
    singleton_ids: set[str],
) -> str:
    """Stuck pending violations section in Markdown form.

    Current-state matview: NO date filter, shows every transaction
    currently stuck in Pending past its aging cap regardless of when
    posted. Same parent/child split; child summary keys on
    (parent_role, rail_name) since the cap is per transfer type.
    """
    header = (
        "\n"
        "---\n"
        "\n"
        "## Stuck Pending Transactions\n"
        "\n"
        "_Transactions currently in Pending status whose age exceeds "
        "the L2-configured `max_pending_age_seconds` cap. "
        "**Current-state** — shown regardless of posting date "
        "(mirrors the L1 dashboard convention; the period band on "
        "the cover does not scope this section)._\n"
    )
    if rows is None:
        return header + (
            "\n_Database not configured — table not populated. "
            "Set `demo_database_url` in your config to query._\n"
        )
    if not rows:
        return header + (
            "\n_No stuck pending transactions — backlog clear._\n"
        )

    parent_rows, child_groups = _split_stuck_pending_by_account_class(
        rows, singleton_ids,
    )
    out = header
    if parent_rows:
        out += (
            "\n"
            "### Parent Accounts (Per-Row Detail)\n"
            "\n"
            "| Account ID | Account name | Transfer type | Posted "
            "| Amount | Age | Cap |\n"
            "|---|---|---|---|---:|---:|---:|\n"
        )
        for r in parent_rows:
            out += (
                f"| `{r.account_id}` | {r.account_name} | "
                f"{r.rail_name} | "
                f"{r.posting.strftime('%Y-%m-%d %H:%M')} | "
                f"${r.amount_money:,.2f} | "
                f"{_format_age(r.age_seconds)} | "
                f"{_format_age(r.max_pending_age_seconds)} |\n"
            )
    if child_groups:
        out += (
            "\n"
            "### Child Accounts Grouped by Parent Role + Transfer Type\n"
            "\n"
            "| Parent role | Transfer type | Children affected "
            "| Stuck transactions | Total amount |\n"
            "|---|---|---:|---:|---:|\n"
        )
        for s in child_groups:
            out += (
                f"| {s.parent_role} | {s.rail_name} "
                f"| {s.distinct_children_affected} "
                f"| {s.stuck_transaction_count} "
                f"| ${s.total_stuck_amount:,.2f} |\n"
            )
    return out


def _render_stuck_unbundled_markdown(
    rows: list[StuckUnbundledViolation] | None,
    singleton_ids: set[str],
) -> str:
    """Stuck unbundled violations section in Markdown form.

    Same shape as Stuck pending but the cap is
    ``max_unbundled_age_seconds`` (Posted-but-not-yet-bundled aging).
    Current-state, no date filter.
    """
    header = (
        "\n"
        "---\n"
        "\n"
        "## Stuck Unbundled Transactions\n"
        "\n"
        "_Posted transactions awaiting bundle assignment whose age "
        "exceeds the L2-configured `max_unbundled_age_seconds` cap. "
        "**Current-state** — shown regardless of posting date "
        "(mirrors the L1 dashboard convention)._\n"
    )
    if rows is None:
        return header + (
            "\n_Database not configured — table not populated. "
            "Set `demo_database_url` in your config to query._\n"
        )
    if not rows:
        return header + (
            "\n_No stuck unbundled transactions — bundling caught up._\n"
        )

    parent_rows, child_groups = _split_stuck_unbundled_by_account_class(
        rows, singleton_ids,
    )
    out = header
    if parent_rows:
        out += (
            "\n"
            "### Parent Accounts (Per-Row Detail)\n"
            "\n"
            "| Account ID | Account name | Transfer type | Posted "
            "| Amount | Age | Cap |\n"
            "|---|---|---|---|---:|---:|---:|\n"
        )
        for r in parent_rows:
            out += (
                f"| `{r.account_id}` | {r.account_name} | "
                f"{r.rail_name} | "
                f"{r.posting.strftime('%Y-%m-%d %H:%M')} | "
                f"${r.amount_money:,.2f} | "
                f"{_format_age(r.age_seconds)} | "
                f"{_format_age(r.max_unbundled_age_seconds)} |\n"
            )
    if child_groups:
        out += (
            "\n"
            "### Child Accounts Grouped by Parent Role + Transfer Type\n"
            "\n"
            "| Parent role | Transfer type | Children affected "
            "| Stuck transactions | Total amount |\n"
            "|---|---|---:|---:|---:|\n"
        )
        for s in child_groups:
            out += (
                f"| {s.parent_role} | {s.rail_name} "
                f"| {s.distinct_children_affected} "
                f"| {s.stuck_transaction_count} "
                f"| ${s.total_stuck_amount:,.2f} |\n"
            )
    return out


def _render_supersession_markdown(
    data: SupersessionAuditData | None,
    frame: AsOfFrame,
) -> str:
    """Supersession audit section in Markdown form.

    Two-table layout per the user's design:
      - Aggregate table (entire dataset, current-state): per (base
        table, supersedes category) total count + new-in-period count.
      - Detail tables (in-window only, one per base table): per-row
        correcting entries whose date falls in the report period.
    Detail tables stay bounded; aggregate carries the historical
    accumulation.
    """
    start, end = frame.window.start, frame.window.end
    header = (
        "\n"
        "---\n"
        "\n"
        "## Supersession Audit\n"
        "\n"
        "_Correcting entries (rows with `supersedes IS NOT NULL`) "
        "across both base tables. The aggregate table counts the "
        "**entire dataset**, current-state; the detail tables are "
        f"limited to {start.isoformat()} – {end.isoformat()} (inclusive) "
        "so the audit page stays bounded as supersession history "
        "accumulates._\n"
    )
    if data is None:
        return header + (
            "\n_Database not configured — table not populated. "
            "Set `demo_database_url` in your config to query._\n"
        )
    if not data.aggregates:
        return header + (
            "\n_No supersessions recorded — entries have not been "
            "corrected._\n"
        )

    out = header + (
        "\n"
        "### Aggregate (Entire Dataset)\n"
        "\n"
        "| Base table | Reason category | Total | New in period |\n"
        "|---|---|---:|---:|\n"
    )
    for r in data.aggregates:
        out += (
            f"| {r.base_table} | {r.supersedes_category} "
            f"| {r.total_count:,} | {r.new_in_period_count:,} |\n"
        )

    if data.transaction_details:
        out += (
            "\n"
            "### Transactions — Correcting Entries in Period\n"
            "\n"
            "| Transaction ID | Reason | Account ID | Account name "
            "| Posted | Amount |\n"
            "|---|---|---|---|---|---:|\n"
        )
        for d in data.transaction_details:
            out += (
                f"| `{d.transaction_id}` | {d.supersedes_category} "
                f"| `{d.account_id}` | {d.account_name} "
                f"| {d.posting.strftime('%Y-%m-%d %H:%M')} "
                f"| ${d.amount_money:,.2f} |\n"
            )
    if data.daily_balance_details:
        out += (
            "\n"
            "### Daily Balances — Correcting Entries in Period\n"
            "\n"
            "| Account ID | Account name | Day | Reason | Balance |\n"
            "|---|---|---|---|---:|\n"
        )
        for d in data.daily_balance_details:
            out += (
                f"| `{d.account_id}` | {d.account_name} "
                f"| {d.business_day.isoformat()} "
                f"| {d.supersedes_category} "
                f"| ${d.money:,.2f} |\n"
            )
    if not data.transaction_details and not data.daily_balance_details:
        out += (
            "\n_No new correcting entries posted in the report "
            "window — aggregate counts above are all from prior "
            "periods._\n"
        )
    return out


def _render_daily_statement_walks_markdown(
    walks: list[DailyStatementWalk] | None,
) -> str:
    """Per-account Daily Statement walks in Markdown form.

    One sub-section per (account, business_day) pair from U.3.a's
    drift table. KPIs sourced from ``<prefix>_daily_statement_summary``
    matview (same matview the L1 dashboard's Daily Statement sheet
    reads, so the numbers agree row-for-row).
    """
    header = (
        "\n"
        "---\n"
        "\n"
        "## Per-Account Daily Statement Walk\n"
        "\n"
        "_Per-(account, day) statement for every account that drifted "
        "in the report window, plus every internal parent-account day "
        "in the window — internal parents (L2 singletons: GL "
        "clearing, concentration, ZBA master) render even when drift "
        "is zero because their day-by-day walk is itself "
        "auditor-relevant. External counterparty singletons are out "
        "of scope for reconciliation and do not get walks._\n"
        "\n"
        "_Note: the **drift** KPI here is the per-day drift "
        "(`closing_stored − closing_recomputed`) and may differ from "
        "U.3.a's cumulative drift (which is "
        "`stored − sum(all transactions ever)`). The two diverge when "
        "the daily_balances feed is sparse._\n"
    )
    if walks is None:
        return header + (
            "\n_Database not configured — table not populated. "
            "Set `demo_database_url` in your config to query._\n"
        )
    if not walks:
        return header + (
            "\n_No drift in the report window — no walks needed._\n"
        )

    out = header
    for w in walks:
        out += (
            "\n"
            f"### {w.account_id} — {w.business_day_end.isoformat()}\n"
            "\n"
            f"**{w.account_name}** ({w.account_role})\n"
            "\n"
            "| Opening | Debits | Credits | Closing stored | Drift |\n"
            "|---:|---:|---:|---:|---:|\n"
            f"| ${w.opening_balance:,.2f} | "
            f"${w.total_debits:,.2f} | "
            f"${w.total_credits:,.2f} | "
            f"${w.closing_balance_stored:,.2f} | "
            f"${w.drift:,.2f} |\n"
        )
        if w.transactions:
            out += (
                "\n"
                "| Posted | Transaction ID | Transfer type "
                "| Direction | Amount | Status |\n"
                "|---|---|---|---|---:|---|\n"
            )
            for t in w.transactions:
                out += (
                    f"| {t.posting.strftime('%H:%M')} "
                    f"| `{t.transaction_id}` "
                    f"| {t.rail_name} "
                    f"| {t.amount_direction} "
                    f"| ${t.amount_money:,.2f} "
                    f"| {t.status} |\n"
                )
        else:
            out += "\n_No Posted Money records on this day._\n"
    return out


def _render_signoff_markdown(
    *,
    institution: str,
    frame: AsOfFrame,
    generated_at: datetime,
    version: str,
    l2_label: str,
    provenance: ProvenanceFingerprint | None,
) -> str:
    """Sign-off page in Markdown form (U.5).

    Two blocks: machine-attestable system block (auto-filled at
    generation time) + human-attestable auditor block (printable form
    fields). Splitting the two means an automated pipeline can ship
    a signed system block without forging an auditor signature, and
    an auditor can sign the human block without invalidating the
    machine attestation. The cryptographic seal over the system block
    lands in U.7.
    """
    start, end = frame.window.start, frame.window.end
    fingerprint = (
        provenance.composite_sha
        if provenance is not None
        else l2_fingerprint_placeholder()
    )
    return (
        "\n"
        "---\n"
        "\n"
        "## Sign-Off\n"
        "\n"
        "### System Attestation\n"
        "\n"
        "| Field | Value |\n"
        "| --- | --- |\n"
        f"| Institution | {institution} |\n"
        f"| Reporting period | {start.isoformat()} – {end.isoformat()} "
        "(inclusive) |\n"
        f"| Generated by | recon-gen v{version} |\n"
        f"| Generated at | {generated_at.isoformat(timespec='seconds')} |\n"
        f"| L2 instance | {l2_label} |\n"
        f"| Provenance fingerprint | `{fingerprint}` |\n"
        "\n"
        "_System signature: when ``signing:`` is set in config.yaml, "
        "the rendered PDF is auto-sealed by recon-gen via "
        "pyHanko (PEM RSA cert). The fields above bind the report "
        "to the code version, L2 spec, and source data; the "
        "reviewer block below is for human counter-signature._\n"
        "\n"
        "### Reviewer Attestation\n"
        "\n"
        "I have reviewed the contents of this report and attest to "
        "the findings above as of the report period. Sign by adding "
        "a digital signature in your PDF reader of choice (Adobe "
        "Acrobat, pyHanko, etc.). Subsequent reviewers may stack "
        "additional signatures without invalidating the system "
        "seal.\n"
        "\n"
        "**Notes / Exceptions:**\n"
        "\n"
        "```\n"
        + ("_" * 70 + "\n") * 6 +
        "```\n"
    )





def _render_appendix_markdown(
    *,
    version: str,
    l2_label: str,
    provenance,
    matview_evidence,
) -> str:
    """Provenance Appendix in Markdown (U.7.c).

    Mirrors the PDF appendix: matview SHA256 sidecar table +
    verify-command instructions + per-source recompute formulas
    + a copyable Python recipe.
    """
    placeholder = "<pending>"
    if provenance is not None:
        tx_hwm = str(provenance.transactions_hwm)
        tx_sha = provenance.transactions_sha
        bal_hwm = str(provenance.balances_hwm)
        bal_sha = provenance.balances_sha
        l2_sha = provenance.l2_yaml_sha
        code_id = provenance.code_identity
        composite = provenance.composite_sha
    else:
        tx_hwm = bal_hwm = tx_sha = bal_sha = l2_sha = placeholder
        code_id = f"v{version}"
        composite = placeholder

    # Matview evidence table
    if matview_evidence:
        mv_rows = "".join(
            f"| `{ev.matview}` | {ev.row_count:,} | `{ev.sha256}` |\n"
            for ev in matview_evidence
        )
    else:
        mv_rows = (
            "| _Database not configured at audit time_ | — | — |\n"
        )

    return (
        "\n"
        "---\n"
        "\n"
        "## Provenance Appendix\n"
        "\n"
        "_Everything an independent verifier needs to reproduce this "
        "report's bindings without recon-gen installed._\n"
        "\n"
        "### Matview Evidence\n"
        "\n"
        "_Per-matview SHA256 + row count. NOT part of the authoritative "
        "composite — matviews are derived data; a divergence between "
        "these and a recompute is a technical signal (matview needs "
        "refresh, schema drift), not a data-binding problem._\n"
        "\n"
        "| Matview | Rows | SHA256 |\n"
        "| --- | ---: | --- |\n"
        f"{mv_rows}"
        "\n"
        "### Reproduce With recon-gen\n"
        "\n"
        "```\n"
        "recon-gen audit verify report.pdf -c config.yaml "
        "--l2 <path-to-L2.yaml>\n"
        "```\n"
        "\n"
        "Extracts the embedded provenance JSON from the PDF's "
        "`/Subject` metadata, recomputes each input at the "
        "embedded high-water-marks, and compares. Exit 0 on match, "
        "1 on per-source diff.\n"
        "\n"
        "### Reproduce Manually\n"
        "\n"
        "Per-source values embedded in this report:\n"
        "\n"
        "| Source | Identifier | SHA256 |\n"
        "| --- | --- | --- |\n"
        f"| Transactions table | `entry <= {tx_hwm}` | `{tx_sha}` |\n"
        f"| Daily balances table | `entry <= {bal_hwm}` | `{bal_sha}` |\n"
        f"| L2 instance YAML | `{l2_label}` | `{l2_sha}` |\n"
        f"| recon-gen code | `{code_id}` | _(identity, no SHA)_ |\n"
        f"| **Composite fingerprint** | SHA256 of labeled lines | "
        f"**`{composite}`** |\n"
        "\n"
        "Recipe (no recon-gen install needed):\n"
        "\n"
        "```python\n"
        "import hashlib\n"
        "\n"
        "def canonical(v):\n"
        "    if v is None: return b''\n"
        "    if isinstance(v, bool): return b'1' if v else b'0'\n"
        "    if hasattr(v, 'isoformat'): "
        "return v.isoformat().encode()\n"
        "    return str(v).encode()\n"
        "\n"
        "def hash_table(cur, table, hwm):\n"
        "    cur.execute(f'SELECT * FROM {table} '\n"
        "                f'WHERE entry <= {hwm} ORDER BY entry')\n"
        "    cols = sorted(\n"
        "        enumerate(cur.description),\n"
        "        key=lambda i_d: i_d[1][0].lower())\n"
        "    h = hashlib.sha256()\n"
        "    for row in cur:\n"
        "        h.update(b'\\x1f'.join(\n"
        "            canonical(row[i]) for i, _ in cols))\n"
        "        h.update(b'\\x1e')\n"
        "    return h.hexdigest()\n"
        "\n"
        f"tx_sha  = hash_table(cur, '<prefix>_transactions', {tx_hwm})\n"
        f"bal_sha = hash_table(cur, '<prefix>_daily_balances', {bal_hwm})\n"
        "l2_sha  = hashlib.sha256(\n"
        "    open(L2_YAML_PATH, 'rb').read()).hexdigest()\n"
        "\n"
        "h = hashlib.sha256()\n"
        f"h.update(b'tx_hwm={tx_hwm}\\n')\n"
        "h.update(f'tx_sha={tx_sha}\\n'.encode())\n"
        f"h.update(b'bal_hwm={bal_hwm}\\n')\n"
        "h.update(f'bal_sha={bal_sha}\\n'.encode())\n"
        "h.update(f'l2_sha={l2_sha}\\n'.encode())\n"
        f"h.update(b'code={code_id}\\n')\n"
        "print(h.hexdigest())  # composite_sha\n"
        "```\n"
    )
