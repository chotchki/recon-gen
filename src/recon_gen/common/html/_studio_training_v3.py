"""BV.4.0+4.4 — /training/ landing surface.

The new Trainer landing per BV.5 spike (15 design locks). Single
page, all 25 plant kinds in per-family accordions, checkbox + inline
form fields + Clean / Violation Tour links per card. Session Start
populates a `<base>_v_*` overlay; Apply mutates it to match the
checkbox state. Two Tour links per card route between the base
prefix (Clean) and the v overlay (Violation) via the `?prefix=` URL
param the BV.4.2 work threads through dashboard routes.

BV.4.0 — vertical slice (1 card, phantom_rail).
BV.4.4 — scale to all 25 cards, per-family accordions, bulk-toggle
chips, selection-density badges, top-level filter, collision-safe
form field naming (`form_<kind>_<primitive>`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from html import escape

from recon_gen.common.html._studio_training_v2 import (
    resolve_section,
)
from recon_gen.common.html.render import _render_inline_markdown
from recon_gen.common.l2.plant_registry import (
    PLANT_REGISTRY, PlantCategory, PlantKindEntry,
    PrimitiveIntField, PrimitiveStringField,
)


# Per-family display order on the landing — matches the §0.5 matrix.
_FAMILY_ORDER: tuple[str, ...] = (
    "L1 Conservation",
    "L1 Cap",
    "L1 Aging",
    "L1 Chain coherence",
    "L1 Audit",
    "L2 Triage gaps",
    "L2 Coverage gaps",
    "L2FT Hygiene",
)


def render_training_v3_landing(
    *,
    top_nav_html: str = "",
    devlog_meta: str = "",
    devlog_script: str = "",
    theme_head: str = "",
    asset_url: str = "/static/output.css",
    base_prefix: str,
    v_overlay_exists: bool,
    session_status: str | None = None,
    enabled_kinds: tuple[str, ...] = (),
    pending_kinds: tuple[str, ...] = (),
    form_values: Mapping[str, Mapping[str, str]] | None = None,
    failed_kinds: Mapping[str, str] | None = None,
    last_apply: Mapping[str, object] | None = None,
    l2_stale: bool = False,
    session_start_time: str = "",
    session_start_running: bool = False,
    apply_running: bool = False,
    apply_pending_count: int = 0,
    standalone_mode: bool = False,
    demo_banner_html: str = "",
    standalone_banner_html: str = "",
) -> str:
    """The /training/ landing.

    Args:
      base_prefix: ``cfg.db.table_prefix`` — the production prefix.
        Clean dashboard link points here.
      v_overlay_exists: ``True`` when ``<base>_v_*`` tables are
        present (Session Start has run). Drives enable/disable of
        Apply + Tour buttons.
      session_status: optional banner text to display. CF.1 narrows
        this to the Session-Start ribbon only — post-Apply state
        comes from ``last_apply``.
      enabled_kinds: kinds whose checkboxes were last applied.
      pending_kinds: BV.4.10.e — kinds the operator had checked when
        they clicked Session Start, NOT yet applied. Rendered as
        DOM-side check-on-load via inline JS so ``cb.defaultChecked``
        still reflects only ``enabled_kinds`` and the diff preview
        ("+N new — Apply to commit") works correctly.
      form_values: per-kind form-value snapshot.
      last_apply: CF.1 — kv-sourced last-Apply outcome.
        ``None`` = unknown / kv unreachable (render no banner).
        ``{}`` = no Apply since last Session Start (render no banner).
        Populated dict (must include ``finished_at``) → render
        green / amber / red based on ``succeeded`` + ``failed`` sets.
    """
    enabled_set = set(enabled_kinds)
    pending_set = set(pending_kinds) - enabled_set
    fv = form_values or {}
    failed = failed_kinds or {}

    # Group registry entries by family in display order.
    by_family: dict[str, list[PlantKindEntry]] = {}
    for entry in PLANT_REGISTRY:
        by_family.setdefault(entry.family, []).append(entry)

    families_html: list[str] = []
    for idx, family in enumerate(_FAMILY_ORDER):
        entries = by_family.get(family, [])
        if not entries:
            continue
        families_html.append(_render_family_section(
            family, entries,
            enabled_set=enabled_set,
            form_values=fv,
            failed=failed,
            base_prefix=base_prefix,
            v_overlay_exists=v_overlay_exists,
            open_by_default=(idx == 0),
        ))
    # Any registry families not in _FAMILY_ORDER (defensive — a new
    # family lands without _FAMILY_ORDER update) render at the end.
    for family, entries in by_family.items():
        if family in _FAMILY_ORDER:
            continue
        families_html.append(_render_family_section(
            family, entries,
            enabled_set=enabled_set,
            form_values=fv,
            failed=failed,
            base_prefix=base_prefix,
            v_overlay_exists=v_overlay_exists,
            open_by_default=False,
        ))

    banner_html = ""
    # CZ.5.fix2 (2026-06-09) — standalone-mode banner now flows in
    # at the chrome level (above the top-nav-following position) via
    # `standalone_banner_html` kwarg, matching every other Studio page's
    # convention (see lines 620/914/1031 in _studio_routes.py — same
    # `{demo_banner}{standalone_banner}` pattern). The prior v3-inline
    # rounded-box variant looked alien against the rest of Studio per
    # operator cold-read; replaced with the shared `_standalone_mode_banner`
    # output the route handler now plumbs through. Transient ribbons
    # (session_status / l2_stale / last_apply) still stack inline below.
    # CF.1 — Session-Start success ribbon (transient, ?status= driven).
    # The Apply path no longer feeds session_status; only the
    # Session-Start 303-redirect does.
    if session_status:
        banner_html += (
            '<div class="bg-success/10 border border-success rounded-md '
            'px-3 py-2 mb-3 text-sm" data-test-training-banner>'
            f'<strong class="text-success">✓</strong> {escape(session_status)}'
            "</div>"
        )
    if l2_stale:
        banner_html += (
            '<div class="bg-warning/10 border border-warning rounded-md '
            'px-3 py-2 mb-3 text-sm" data-test-l2-stale-banner>'
            '<strong class="text-warning">⚠</strong> '
            'Your L2 yaml has changed since this Session Start'
            f'{f" ({escape(session_start_time)})" if session_start_time else ""}. '
            'Click <strong>Session Start (re-fetch)</strong> to pick up the new schema '
            '+ reseed the base + re-clone the v overlay.'
            "</div>"
        )
    # CF.1 — kv-sourced 3-state last-Apply banner. last_apply is read
    # from `<base>_v_config_kv` on every /training/ landing GET, so
    # the banner survives navigation + Studio restart and renders
    # honest amber on partial-failure (was: unconditional green via
    # ?status=Apply+done. URL redirect). Three branches:
    #
    #   * all succeeded → GREEN (data-test-training-banner +
    #     data-test-last-apply-banner)
    #   * partial failure → AMBER (data-test-partial-banner +
    #     data-test-failed-banner + data-test-last-apply-banner)
    #   * all failed → RED (data-test-failed-banner +
    #     data-test-last-apply-banner)
    #
    # `data-test-failed-banner` is retained for the failure cases so
    # existing test selectors keep working; the amber branch carries
    # both attrs because it IS a failure surface (some plants failed)
    # AND a partial surface (some succeeded). When last_apply is
    # populated, the legacy `if failed:` fallback below is skipped.
    if last_apply is not None and "finished_at" in last_apply:
        from typing import cast as _cast  # noqa: PLC0415
        succeeded_raw: object = last_apply.get("succeeded")
        failed_dict_raw: object = last_apply.get("failed")
        attempted_raw: object = last_apply.get("attempted")
        finished_at = str(last_apply.get("finished_at", ""))
        succeeded_list: list[str] = (
            [str(k) for k in _cast(list[object], succeeded_raw)]
            if isinstance(succeeded_raw, list) else []
        )
        attempted_list: list[str] = (
            [str(k) for k in _cast(list[object], attempted_raw)]
            if isinstance(attempted_raw, list) else []
        )
        failed_dict: dict[str, str] = (
            {str(k): str(v) for k, v in _cast(dict[object, object], failed_dict_raw).items()}
            if isinstance(failed_dict_raw, dict) else {}
        )
        # Empty Apply (operator submitted with nothing checked) renders
        # no banner — `attempted=[]` is the only state where succeeded
        # AND failed are both empty AND the apply path actually ran;
        # suppress so the operator isn't confronted with "0 plant(s)
        # succeeded" copy.
        if attempted_list or succeeded_list or failed_dict:
            n_succeeded = len(succeeded_list)
            n_failed = len(failed_dict)
            per_kind_details_html = ""
            if failed_dict:
                per_kind_details_html = "".join(
                    (
                        '<li class="ml-4 list-disc">'
                        f'<code class="font-mono">{escape(kind)}</code>: '
                        f'<span class="text-secondary-fg">'
                        f'{escape(_first_line_of_error(failed_dict[kind]))}'
                        '</span>'
                        '</li>'
                    )
                    for kind in sorted(failed_dict.keys())
                )
            details_block = (
                ' <details class="mt-1 inline-block">'
                '<summary class="cursor-pointer">'
                'show why each plant failed'
                '</summary>'
                f'<ul class="mt-1 text-xs">{per_kind_details_html}</ul>'
                '</details>'
            ) if failed_dict else ""
            if n_failed == 0 and n_succeeded > 0:
                # CF.1 followup — the GREEN last_apply branch uses
                # ONLY `data-test-last-apply-banner`. The
                # `data-test-training-banner` attr is reserved for the
                # Session-Start success ribbon (driven by ?status=),
                # so e2e drivers that wait on it as a
                # "Session Start finished" signal don't get fooled by
                # a stale GREEN last_apply painted by a prior Apply.
                banner_html += (
                    '<div class="bg-success/10 border border-success rounded-md '
                    'px-3 py-2 mb-3 text-sm" data-test-last-apply-banner>'
                    f'<strong class="text-success">✓</strong> '
                    f'Last apply: {n_succeeded} plant(s) succeeded at '
                    f'{escape(finished_at)}.'
                    "</div>"
                )
            elif n_failed > 0 and n_succeeded > 0:
                banner_html += (
                    '<div class="bg-warning/10 border border-warning rounded-md '
                    'px-3 py-2 mb-3 text-sm" data-test-partial-banner '
                    'data-test-failed-banner data-test-last-apply-banner>'
                    '<strong class="text-warning">⚠</strong> '
                    f'Last apply: {n_succeeded} succeeded, {n_failed} '
                    f'failed at {escape(finished_at)}.'
                    f'{details_block}'
                    "</div>"
                )
            elif n_failed > 0 and n_succeeded == 0:
                banner_html += (
                    '<div class="bg-danger/10 border border-danger rounded-md '
                    'px-3 py-2 mb-3 text-sm" data-test-failed-banner '
                    'data-test-last-apply-banner>'
                    '<strong class="text-danger">✗</strong> '
                    f'Last apply: all {n_failed} plant(s) failed at '
                    f'{escape(finished_at)}.'
                    f'{details_block}'
                    "</div>"
                )
    # BV.4.10.d — Session-Start-in-flight banner with collapsible
    # live tail. The wrapper polls `/training/session-start/stream`
    # every 1s; on completion the response carries
    # `HX-Trigger: training-session-start-finished` which the inline
    # script catches + reloads /training/ so the post-run state
    # (success banner + applied-plants ledger reads) renders.
    if session_start_running:
        banner_html += _render_progress_banner(
            test_attr="data-test-training-session-start-banner",
            tail_id="training-session-start-live-tail",
            stream_url="/training/session-start/stream",
            title="Session Start in progress",
            hint=(
                "re-fetches the base prefix + rebuilds the v overlay "
                "(~sub-second DuckDB / ~30s Postgres / ~10 min Oracle "
                "for the /etl/run leg)"
            ),
            finished_event="training-session-start-finished",
            redirect_url="/training/?status=Session+started+%E2%80%94+v+overlay+ready.",
        )
    if apply_running:
        n_str = (
            f"{apply_pending_count} plant(s)"
            if apply_pending_count
            else "your selection"
        )
        banner_html += _render_progress_banner(
            test_attr="data-test-training-apply-banner",
            tail_id="training-apply-live-tail",
            stream_url="/training/apply/stream",
            title="Apply in progress",
            hint=(
                f"applying {escape(n_str)} against the v overlay. "
                "Matview refresh is the dominant cost — "
                "your existing planted state isn't re-cloned when "
                "you're only adding new plants."
            ),
            finished_event="training-apply-finished",
            # CF.1 — drop the unconditional ?status=Apply+done. green
            # ribbon. The post-Apply landing now reads `last_apply`
            # from kv and renders honest green/amber/red based on the
            # actual outcome; appending a green ?status= here would
            # stack a lying claim on top of an amber kv truth.
            redirect_url="/training/",
        )

    total_enabled = sum(
        1 for entry in PLANT_REGISTRY if entry.kind in enabled_set
    )
    total_kinds = len(PLANT_REGISTRY)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Recon-Gen · Studio · Training</title>
  {devlog_meta}{theme_head}
  <link rel="stylesheet" href="{escape(asset_url)}">
  <!-- BV.4.10.d — htmx loaded for the Session-Start live tail's
       hx-get polling. Pre-BV.4.10.d the page had zero hx-* attrs
       (form posts went through native browser submit), so htmx
       wasn't loaded — and the freshly-added hx-get poll silently
       no-op'd. -->
  <script src="/static/vendor/js/htmx.min.js" defer></script>
  {devlog_script}
</head>
<body class="block min-h-screen font-sans bg-surface-bg text-primary-fg">
  {top_nav_html}
  {demo_banner_html}{standalone_banner_html}
  <header class="px-8 py-4 border-b border-surface-border bg-white">
    <h1 class="text-xl font-semibold m-0">Training</h1>
    <p class="text-sm text-secondary-fg max-w-3xl m-0 mt-1">
      Pick the violation plants you want to study in this session,
      click <strong>Apply</strong>, then use each card's
      <strong>Clean dashboard</strong> / <strong>Violation dashboard</strong>
      links to see the before/after.
      Session Start populates a <code>{escape(base_prefix)}_v</code>
      overlay that all the Violation views read from; your production
      <code>{escape(base_prefix)}</code> prefix is untouched.
    </p>
    <details class="mt-3 text-sm" data-test-training-workflow-help>
      <summary class="cursor-pointer text-secondary-fg font-semibold">
        How this page works
      </summary>
      <ol class="mt-2 ml-5 list-decimal text-secondary-fg max-w-3xl flex flex-col gap-1">
        <li><strong>Session Start</strong> — populates a fresh
          <code>{escape(base_prefix)}_v</code> overlay cloned from
          your production data. Do this first when you arrive on this
          page; your production prefix is untouched.</li>
        <li><strong>Check the boxes</strong> next to violation kinds
          you want to study (e.g., <em>drift</em>,
          <em>overdraft</em>). Per-card primitives like "Days ago"
          tune what gets planted.</li>
        <li><strong>Apply selection</strong> — plants the chosen
          kinds into the v overlay. Checked but not-yet-applied boxes
          survive Session Start (your pending picks are restored).</li>
        <li><strong>Clean dashboard / Violation dashboard</strong>
          links on each card open the same dashboard side-by-side,
          one reading from base (no planted rows) and one from
          <code>{escape(base_prefix)}_v</code> (with the planted
          violation visible).</li>
        <li><strong>Cleanup</strong> when you're done — drops the v
          overlay entirely. Reversible: click Session Start again to
          re-clone.</li>
      </ol>
    </details>
    {_render_session_controls(v_overlay_exists, any_op_running=(session_start_running or apply_running), standalone_mode=standalone_mode)}
  </header>
  <main class="px-8 py-6 flex flex-col gap-4">
    {banner_html}
    <form method="post" action="/training/apply" id="training-apply-form" class="flex flex-col gap-4">
      <section class="bg-white border border-surface-border rounded-md p-4 flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-3">
          <span class="text-sm font-semibold" data-test-top-density>
            {total_enabled}/{total_kinds} plants enabled
          </span>
          <button type="button" data-test-top-all
                  class="text-xs px-2 py-1 border border-surface-border rounded-sm hover:bg-accent/10 cursor-pointer"
                  onclick="window._bvToggleAll(true)">[Select all]</button>
          <button type="button" data-test-top-none
                  class="text-xs px-2 py-1 border border-surface-border rounded-sm hover:bg-accent/10 cursor-pointer"
                  onclick="window._bvToggleAll(false)">[None]</button>
        </div>
        <div class="flex items-center gap-2">
          <label class="text-xs text-secondary-fg">Show:</label>
          <select id="bv-show-filter"
                  aria-label="Filter plant kinds by enabled state"
                  class="text-xs px-2 py-1 border border-surface-border rounded-sm bg-white cursor-pointer"
                  onchange="window._bvApplyFilter(this.value)">
            <option value="all">All</option>
            <option value="enabled">Only enabled</option>
            <option value="errors">Only with errors</option>
          </select>
        </div>
      </section>
      <!-- CF audit followup (Studio Med #2) — the bottom apply bar
           is `sticky bottom-0` and was overlapping the final plant
           card's header on scroll. `pb-24` (6rem) reserves enough
           viewport headroom for the bar's height (≈ p-4 + content
           ≈ 3.5rem) so the last card stays legible. -->
      <div id="bv-families" class="flex flex-col gap-2 pb-24">
        {chr(10).join(families_html)}
      </div>
      <div id="bv-empty-state" data-test-empty-state
           class="hidden bg-surface border border-surface-border rounded-md p-6 text-center text-sm text-secondary-fg">
        <p class="font-semibold mb-1">No plants match this filter.</p>
        <p>Switch the <strong>Show:</strong> selector back to <em>All</em>, or click
        <strong>[Select all]</strong> on a family below to start a teaching session.</p>
      </div>
      <div class="bg-white border border-surface-border rounded-md p-4 sticky bottom-0 flex items-center gap-3 z-10">
        <button type="submit" id="training-apply-btn"
                class="px-4 py-2 bg-accent text-accent-fg rounded-sm border border-accent text-sm font-semibold hover:opacity-85{(' opacity-50 cursor-not-allowed' if (not v_overlay_exists or session_start_running or apply_running) else '')}"
                {("disabled" if (not v_overlay_exists or session_start_running or apply_running) else "")}>
          ⚡ Apply selection
        </button>
        <span id="bv-apply-diff" data-test-bv-apply-diff
              class="text-xs text-secondary-fg">no changes pending</span>
        {("" if v_overlay_exists else '<span class="text-xs text-secondary-fg">Click Session Start first to populate the v overlay.</span>')}
      </div>
    </form>
  </main>
  <script>
    // BV.4.10.e — pending_kinds carried across Session Start. The
    // landing JS reads this at init time and sets `cb.checked = true`
    // for matching kinds WITHOUT touching `defaultChecked`, so the
    // diff preview ("+N new") shows the operator's selection survived
    // but is still uncommitted to v_config_kv.
    window._bvPendingKinds = {_pending_kinds_js_array(pending_set)};
  </script>
  <script>
{_BV_LANDING_JS}
  </script>
</body>
</html>
"""


# BV.4.10.d — small Tailwind animated spinner. Replaces the ⏳
# emoji which was static — the operator can't tell from a glance
# whether the page is alive. `animate-spin` (Tailwind core) rotates
# the borders 1 turn/sec via CSS keyframes.
_SPINNER_HTML = (
    '<span class="inline-block h-3 w-3 align-middle '
    'border-2 border-accent border-t-transparent rounded-full '
    'animate-spin mr-2" aria-hidden="true"></span>'
)


def _render_progress_banner(
    *, test_attr: str, tail_id: str, stream_url: str,
    title: str, hint: str,
    finished_event: str, redirect_url: str,
) -> str:
    """BV.4.10.d — common shape for the Session Start + Apply
    in-progress banners. Spinner + title + hint + collapsible
    live-tail + inline script that catches the finished HX-Trigger
    and reloads /training/.

    The live-tail `<details>` stays collapsed by default so the page
    isn't dominated by a wall of event log lines (per operator:
    "loader and a way to expand the log so you can see problems").
    """
    return f"""
    <div class="bg-accent/10 border border-accent rounded-md px-3 py-2 mb-3 text-sm"
         {test_attr}>
      {_SPINNER_HTML}<strong class="text-accent">{escape(title)}</strong> —
      {hint}
      <details class="mt-2">
        <summary class="cursor-pointer text-xs text-secondary-fg">Show event log</summary>
        <div id="{tail_id}"
             class="bg-white border border-surface-border rounded-md p-3 max-h-72 overflow-y-auto font-mono text-xs mt-1"
             hx-get="{escape(stream_url)}"
             hx-trigger="load, every 1s"
             hx-swap="outerHTML">
          <p class="text-secondary-fg italic">Waiting for events…</p>
        </div>
      </details>
    </div>
    <script>
      document.body.addEventListener('{finished_event}', function() {{
        setTimeout(() => {{ window.location.href = '{redirect_url}'; }}, 250);
      }});
    </script>
"""


def render_training_apply_live_tail(
    *,
    events: list[Mapping[str, object]],
    running: bool,
) -> str:
    """BV.4.10.d — apply live-tail. Shares fragment shape with
    `render_training_session_start_live_tail` but polls
    `/training/apply/stream` instead. Distinct id so the same page
    can host both mounts if (hypothetically) two tasks ran
    concurrently — they wouldn't in practice (the POST handlers
    double-click guard against in-flight tasks)."""
    return _render_live_tail_fragment(
        events=events, running=running,
        tail_id="training-apply-live-tail",
        stream_url="/training/apply/stream",
    )


def _render_live_tail_fragment(
    *,
    events: list[Mapping[str, object]],
    running: bool,
    tail_id: str,
    stream_url: str,
) -> str:
    """Common fragment renderer for both session-start + apply
    live tails."""
    if not events:
        body = (
            '<p class="text-secondary-fg italic" '
            'data-test-training-tail-empty>Waiting for events…</p>'
        )
    else:
        lines: list[str] = []
        for event in events:
            event_name = str(event.get("event") or event.get("kind") or "")
            level_class = (
                "text-danger" if (
                    event_name.endswith(":halt")
                    or "cancelled" in event_name
                    or event_name.endswith(":failed")
                ) else "text-secondary-fg"
            )
            fields = " ".join(
                f"{escape(str(k))}={escape(str(v))}"
                for k, v in event.items()
                if k not in ("event", "kind", "ts_unix")
            )
            lines.append(
                f'<div class="leading-relaxed">'
                f'<span class="{level_class} mr-2 text-[10px] uppercase">[evt]</span>'
                f'<span>{escape(event_name)}</span>'
                f'{(" " + fields) if fields else ""}'
                f'</div>'
            )
        body = "".join(lines)
    poll_attrs = ""
    state_attr = "finished"
    if running:
        poll_attrs = (
            f' hx-get="{escape(stream_url)}"'
            ' hx-trigger="every 1s"'
            ' hx-swap="outerHTML"'
        )
        state_attr = "running"
    return (
        f'<div id="{tail_id}"'
        ' class="bg-white border border-surface-border rounded-md p-3 max-h-72 overflow-y-auto font-mono text-xs mt-1"'
        f' data-test-training-tail-state="{state_attr}"'
        f' data-test-training-tail-count="{len(events)}"'
        f'{poll_attrs}>'
        f'{body}'
        '</div>'
    )


def render_training_session_start_live_tail(
    *,
    events: list[Mapping[str, object]],
    running: bool,
) -> str:
    """BV.4.10.d — session-start live-tail (shared shape with
    `render_training_apply_live_tail`)."""
    return _render_live_tail_fragment(
        events=events, running=running,
        tail_id="training-session-start-live-tail",
        stream_url="/training/session-start/stream",
    )


def _render_session_controls(
    v_overlay_exists: bool, *,
    any_op_running: bool = False,
    standalone_mode: bool = False,
) -> str:
    """Top-of-page Session Start / Force rebuild / Cleanup buttons (DL.10).

    Pre-overlay: Session Start only.
    Post-overlay: Session Start (full lifecycle — re-runs /etl/run)
      + Force rebuild (drop v overlay + reclone from base, wipes
      Apply state) + Cleanup.

    BV.4.10.d.3 — when an op is in flight (`any_op_running=True`)
    every button gets `disabled` + visual-affordance classes so the
    operator can't double-click and confuse the queue. The server
    already no-ops re-POSTs while a task runs, but the operator's
    experience without the disabled state is "I clicked, nothing
    happened, let me click again" — the UI should prevent the
    second click, not just absorb it silently.
    """
    session_start_title = (
        "Full lifecycle: runs the /etl/run flow (so base prefix is "
        "current) + drops + creates the v overlay schema + clones "
        "base data + refreshes v matviews. DuckDB finishes in seconds; "
        "PG takes ~30s; Oracle takes ~10 min for the /etl/run leg."
    )
    disabled_attr = " disabled" if any_op_running else ""
    # Tailwind `disabled:` variant doesn't always cover hover overrides,
    # so explicitly add opacity + cursor-not-allowed when disabled.
    disabled_cls = (
        " opacity-50 cursor-not-allowed" if any_op_running else ""
    )
    # CZ.5 — in standalone-mode the rebuild label switches to the
    # REPLAN-locked "Clear synthetic rows and re-seed" copy. The
    # rebuild path is the closest v3-era analogue to BU.1.6's v2
    # Reset-to-clean-baseline button (both drop + reseed the active
    # surface from base); v3's Apply/Session-Start only touch the v
    # overlay and stay generic.
    if standalone_mode:
        from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
            STANDALONE_RESET_BUTTON_LABEL,
        )
        rebuild_label = f"↻ {STANDALONE_RESET_BUTTON_LABEL}"
        rebuild_title = (
            "Standalone mode (cfg.app2.etl_hook is None) — only rows tagged "
            "metadata.source='training' will be removed. Any unmarked "
            "rows are presumed real customer data and survive."
        )
        rebuild_test_attr = " data-test-training-rebuild-standalone"
    else:
        rebuild_label = "↻ Force rebuild from base"
        rebuild_title = (
            "Drops + reclones the v overlay from current base + "
            "wipes Apply state. Skips /etl/run (base stays as-is). For "
            "when you want to throw out whatever is in v overlay and "
            "start fresh from base — DL.9 Apply is incremental and "
            "won't do that on its own."
        )
        rebuild_test_attr = ""
    rebuild_btn = (
        '<form method="post" action="/training/reclone" class="inline-block">'
        f'<button type="submit" id="training-reclone-btn"{rebuild_test_attr}{disabled_attr} '
        f'class="px-3 py-1.5 bg-white text-accent rounded-sm border border-accent text-xs font-semibold hover:bg-accent/10{disabled_cls}" '
        f'title="{escape(rebuild_title)}">'
        f"{escape(rebuild_label)}"
        "</button>"
        "</form>"
        if v_overlay_exists else ""
    )
    cleanup_btn = (
        '<form method="post" action="/training/cleanup" class="inline-block">'
        f'<button type="submit" id="training-cleanup-btn"{disabled_attr} '
        f'class="px-3 py-1.5 bg-warning text-white rounded-sm border border-warning text-xs font-semibold hover:opacity-85{disabled_cls}" '
        'title="Drops the &lt;base&gt;_v_* schema. Base prefix untouched.">'
        "🗑 Cleanup"
        "</button>"
        "</form>"
        if v_overlay_exists else ""
    )
    session_start_label = (
        "▶ Session Start (re-fetch)" if v_overlay_exists
        else "▶ Session Start"
    )
    return f"""
    <div class="mt-3 inline-flex items-center gap-2">
      <form method="post" action="/training/session-start" id="training-session-start-form"
            onsubmit="return window._bvCarryPendingToSessionStart(this)"
            class="inline-block">
        <button type="submit" id="training-session-start-btn"{disabled_attr}
                class="px-3 py-1.5 bg-accent text-accent-fg rounded-sm border border-accent text-xs font-semibold hover:opacity-85{disabled_cls}"
                title="{escape(session_start_title)}">
          {session_start_label}
        </button>
      </form>
      {rebuild_btn}
      {cleanup_btn}
    </div>
    """


def _render_family_section(
    family: str, entries: list[PlantKindEntry],
    *,
    enabled_set: set[str],
    form_values: Mapping[str, Mapping[str, str]],
    failed: Mapping[str, str],
    base_prefix: str,
    v_overlay_exists: bool,
    open_by_default: bool,
) -> str:
    """One `<details>` accordion per family, rendering all member
    cards. Summary line carries the family pretty-label + selection-
    density badge."""
    enabled_in_family = sum(
        1 for entry in entries if entry.kind in enabled_set
    )
    total_in_family = len(entries)
    cards_html = "\n".join(
        _render_card(
            entry,
            enabled=(entry.kind in enabled_set),
            form_values=form_values.get(entry.kind, {}),
            failed_message=failed.get(entry.kind),
            base_prefix=base_prefix,
            v_overlay_exists=v_overlay_exists,
        )
        for entry in entries
    )
    open_attr = " open" if open_by_default else ""
    # JS-safe family-id (drop spaces) for the bulk-toggle target.
    family_id = family.replace(" ", "_")
    return (
        '<details class="bg-white border border-surface-border rounded-md overflow-hidden" '
        f'data-test-training-family="{escape(family)}"{open_attr}>'
        '<summary class="cursor-pointer px-4 py-3 font-semibold hover:bg-surface-bg flex items-center gap-3 flex-wrap">'
        f'<span>{escape(family)}</span>'
        f'<span class="text-xs font-normal text-secondary-fg" data-test-family-badge data-family="{escape(family_id)}">'
        f'({enabled_in_family}/{total_in_family} enabled)</span>'
        '<button type="button" '
        f'data-test-family-all="{escape(family_id)}" '
        'class="text-xs px-2 py-1 border border-surface-border rounded-sm hover:bg-accent/10 cursor-pointer font-normal" '
        f'onclick="event.preventDefault(); event.stopPropagation(); window._bvToggleFamily(\'{escape(family_id)}\', true)">[all]</button>'
        '<button type="button" '
        f'data-test-family-none="{escape(family_id)}" '
        'class="text-xs px-2 py-1 border border-surface-border rounded-sm hover:bg-accent/10 cursor-pointer font-normal" '
        f'onclick="event.preventDefault(); event.stopPropagation(); window._bvToggleFamily(\'{escape(family_id)}\', false)">[none]</button>'
        '</summary>'
        f'<div class="px-4 pb-4 flex flex-col gap-3" data-family-body="{escape(family_id)}">'
        f'{cards_html}'
        '</div>'
        '</details>'
    )


def _render_card(
    entry: PlantKindEntry,
    *,
    enabled: bool,
    form_values: Mapping[str, str],
    failed_message: str | None,
    base_prefix: str,
    v_overlay_exists: bool,
) -> str:
    """One kind's card. Carries title + description + checkbox + per-
    kind inline form fields + Clean/Violation Tour links + What-to-do
    copy. Per the BV.5 spike's Card-as-Anchor lock (DL.8)."""
    section = resolve_section(entry)
    primitives_html = "\n".join(
        _render_primitive_field(entry.kind, p, form_values.get(p.name))
        for p in entry.primitives
    )
    if not entry.primitives:
        primitives_html = (
            '<p class="text-xs text-secondary-fg m-0">'
            "(No operator-tunable parameters — the L2 declaration "
            "determines the planted scenario.)</p>"
        )
    v_prefix = f"{base_prefix}_v"
    tour_url = entry.tour_destination.primary_url
    clean_link = (
        f'<a class="text-accent hover:underline text-sm font-semibold" '
        f'href="{escape(tour_url)}?prefix={escape(base_prefix)}" '
        f'data-test-tour-clean-{escape(entry.kind)}>Clean dashboard →</a>'
    )
    violation_link = (
        f'<a class="text-accent hover:underline text-sm font-semibold" '
        f'href="{escape(tour_url)}?prefix={escape(v_prefix)}" '
        f'data-test-tour-violation-{escape(entry.kind)}>Violation dashboard →</a>'
    ) if v_overlay_exists else (
        '<span class="text-xs text-secondary-fg">'
        '(Violation dashboard available after Session Start + Apply)'
        '</span>'
    )
    checked_attr = " checked" if enabled else ""
    qualifier_html = (
        f'<span class="text-xs text-secondary-fg">— {escape(entry.kind_qualifier)}</span>'
        if entry.kind_qualifier else ""
    )
    error_badge_html = ""
    error_attr = ""
    card_bg = ""
    if failed_message:
        error_attr = ' data-error="1"'
        card_bg = ' bg-danger/5'
        error_badge_html = (
            '<span class="text-xs px-2 py-0.5 bg-danger text-white rounded-sm" '
            f'title="{escape(failed_message)}" '
            f'data-test-error-badge-{escape(entry.kind)}>error planting</span>'
        )
    # BV.4.10.a — currently-planted pill makes the v-overlay state
    # obvious at a glance. The checkbox shows the same info implicitly
    # via its `checked` state, but a green pill next to the title
    # screams "this row is in v_<matview>" without requiring the
    # operator to inspect each checkbox carefully.
    planted_badge_html = (
        '<span class="text-xs px-2 py-0.5 bg-success text-white rounded-sm" '
        f'data-test-planted-badge-{escape(entry.kind)}>currently planted</span>'
    ) if enabled else ""
    return f"""
    <article class="border border-surface-border rounded-md p-4 flex flex-col gap-2{card_bg}"
             data-test-training-kind="{escape(entry.kind)}"{error_attr}>
      <header class="flex items-baseline gap-3 flex-wrap">
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" name="enabled_kinds" value="{escape(entry.kind)}"{checked_attr}
                 data-test-training-enable-{escape(entry.kind)}>
          <span class="text-sm font-semibold">{escape(section.title)}</span>
        </label>
        {qualifier_html}
        <span class="text-xs text-secondary-fg font-mono">{escape(entry.kind)}</span>
        {planted_badge_html}
        {error_badge_html}
      </header>
      <p class="text-xs text-secondary-fg max-w-3xl m-0">
        {_render_inline_markdown(_first_sentence(section.short_statement))}
      </p>
      <div class="mt-2 flex flex-wrap gap-3 items-end">
        {primitives_html}
      </div>
      <details class="mt-2 text-xs">
        <summary class="cursor-pointer text-secondary-fg">What to do about it</summary>
        <div class="mt-1 prose prose-xs max-w-none">{_render_inline_markdown(section.what_to_do)}</div>
      </details>
      <div class="mt-2 flex items-center gap-4">
        {clean_link}
        {violation_link}
      </div>
    </article>
    """


def _render_primitive_field(
    kind: str,
    primitive: PrimitiveIntField | PrimitiveStringField,
    form_value: str | None,
) -> str:
    """Inline-on-card rendering of a primitive.

    Form-field naming is `form_<kind>_<primitive_name>` to avoid
    collision across kinds when many cards co-exist on the same page
    (BV.4.4 scale-to-25 requirement)."""
    field_name = f"form_{kind}_{primitive.name}"

    if isinstance(primitive, PrimitiveIntField):
        value = form_value if form_value is not None else str(primitive.default)
        attrs: list[str] = []
        if primitive.min_value is not None:
            attrs.append(f'min="{primitive.min_value}"')
        if primitive.max_value is not None:
            attrs.append(f'max="{primitive.max_value}"')
        return (
            '<label class="flex flex-col gap-1">'
            f'<span class="text-xs text-secondary-fg">{escape(primitive.label)}</span>'
            f'<input type="number" name="{escape(field_name)}" '
            f'value="{escape(value)}" {" ".join(attrs)} '
            'class="px-2 py-1 border border-surface-border rounded-sm text-sm bg-white w-24">'
            '</label>'
        )
    assert isinstance(primitive, PrimitiveStringField)
    value = form_value if form_value is not None else primitive.default
    return (
        '<label class="flex flex-col gap-1">'
        f'<span class="text-xs text-secondary-fg">{escape(primitive.label)}</span>'
        f'<input type="text" name="{escape(field_name)}" '
        f'value="{escape(value)}" '
        'class="px-2 py-1 border border-surface-border rounded-sm text-sm bg-white w-48">'
        '</label>'
    )


def _first_sentence(text: str) -> str:
    """Trim a short-statement paragraph to its first sentence for
    card-density."""
    if not text:
        return ""
    first = text.split(". ", 1)[0]
    if first and not first.endswith("."):
        first += "."
    return first


def _first_line_of_error(text: str) -> str:
    """CF.0 Fix B — trim a `trainer_failed_plants` kv value to its
    first line for banner-inline rendering. The kv carries the full
    `f"{type(exc).__name__}: {exc}"` shape per `v_overlay.py:464`;
    we want just the actionable summary (the picker's ValueError
    message, e.g. "drift plant: no 2-leg Rail with destination
    matching the template role declared in this L2."), not any
    multi-line traceback content."""
    if not text:
        return ""
    return text.split("\n", 1)[0].strip()


# Unused import suppressor — PlantCategory is reserved for BV.4.5
# error-card grouping (per-category visual band).
_ = PlantCategory
_ = Iterable


def _pending_kinds_js_array(pending_set: set[str]) -> str:
    """BV.4.10.e — render the pending-kinds set as a JS array literal
    suitable for ``window._bvPendingKinds = ...``. Kind names are
    ASCII identifiers (validated at registry-build time), so quoting
    with JSON-style double quotes is sufficient — no XSS surface."""
    if not pending_set:
        return "[]"
    quoted = ", ".join(f'"{kind}"' for kind in sorted(pending_set))
    return f"[{quoted}]"


# -- Landing-page JS (bulk-toggle chips + Show filter) ----------------------
#
# Small enough to inline. Adds three window-scoped helpers the buttons
# call via inline onclick: _bvToggleAll, _bvToggleFamily, _bvApplyFilter.
# Also recomputes the per-family + top-level density badges on any
# checkbox change.

_BV_LANDING_JS = """
(function () {
  const root = document;
  // BV.4.10.e (P1.2) — re-apply the operator's pending checkbox state
  // that was carried across the Session Start redirect. We set
  // `cb.checked = true` AFTER the DOM parses, leaving
  // `cb.defaultChecked` reflecting only the server-side applied state.
  // So the diff preview correctly reads "+N new — Apply to commit"
  // for the pending picks. Defer the density/diff recompute to the
  // tail of the IIFE (see `updateDensity()` + `updateApplyDiff()`
  // calls just before close) so the counters reflect the restored
  // pending state, not the stale committed state.
  const _bvPending = (window._bvPendingKinds || []);
  if (_bvPending.length > 0) {
    const _bvPendingSet = new Set(_bvPending);
    root.querySelectorAll('input[type="checkbox"][name="enabled_kinds"]').forEach(cb => {
      if (_bvPendingSet.has(cb.value)) cb.checked = true;
    });
  }
  function checkboxes(scope) {
    return scope.querySelectorAll('input[type="checkbox"][name="enabled_kinds"]');
  }
  function updateDensity() {
    // Top-level badge.
    const all = checkboxes(root);
    const totalEnabled = Array.from(all).filter(c => c.checked).length;
    const topBadge = root.querySelector('[data-test-top-density]');
    if (topBadge) topBadge.textContent = totalEnabled + '/' + all.length + ' plants enabled';
    // Per-family badges.
    root.querySelectorAll('[data-family-body]').forEach(body => {
      const fid = body.getAttribute('data-family-body');
      const inFamily = checkboxes(body);
      const en = Array.from(inFamily).filter(c => c.checked).length;
      const badge = root.querySelector(`[data-test-family-badge][data-family="${fid}"]`);
      if (badge) badge.textContent = '(' + en + '/' + inFamily.length + ' enabled)';
    });
  }
  window._bvToggleAll = function (enable) {
    checkboxes(root).forEach(c => { c.checked = !!enable; });
    updateDensity();
  };
  window._bvToggleFamily = function (familyId, enable) {
    const body = root.querySelector(`[data-family-body="${familyId}"]`);
    if (!body) return;
    checkboxes(body).forEach(c => { c.checked = !!enable; });
    updateDensity();
  };
  window._bvApplyFilter = function (mode) {
    let anyFamilyShown = false;
    root.querySelectorAll('[data-test-training-family]').forEach(fam => {
      const cards = fam.querySelectorAll('[data-test-training-kind]');
      let anyShown = false;
      cards.forEach(card => {
        const cb = card.querySelector('input[type="checkbox"][name="enabled_kinds"]');
        const enabled = cb && cb.checked;
        // BV.4.5 — per-card error state via the data-error attr.
        const hasError = card.dataset.error === '1';
        let show = true;
        if (mode === 'enabled') show = !!enabled;
        else if (mode === 'errors') show = !!hasError;
        card.style.display = show ? '' : 'none';
        if (show) anyShown = true;
      });
      fam.style.display = anyShown ? '' : 'none';
      if (anyShown) anyFamilyShown = true;
    });
    // BV.4.8.P1.3 — surface the empty-state hint when the filter
    // hides every family (first-time-operator hits this on "Only
    // enabled" before enabling anything; without copy the page
    // reads as broken).
    const empty = root.querySelector('#bv-empty-state');
    if (empty) {
      if (anyFamilyShown) empty.classList.add('hidden');
      else empty.classList.remove('hidden');
    }
  };
  // BV.4.10.b — Apply diff preview. Walks all enabled_kinds
  // checkboxes; for each, compares current `checked` vs
  // `defaultChecked` (the initial state the server rendered with —
  // which mirrors v_config_kv's `trainer_applied_plants` set).
  // The diff drives the sticky-Apply-bar's "Will plant N, remove M"
  // label so the operator can predict what Apply will do BEFORE
  // clicking. DL.9 fast-path means "no changes pending" is a no-op
  // Apply at the matview-refresh level.
  function updateApplyDiff() {
    const diff = root.querySelector('#bv-apply-diff');
    if (!diff) return;
    let added = 0, removed = 0;
    root.querySelectorAll('input[type="checkbox"][name="enabled_kinds"]').forEach(cb => {
      if (cb.checked && !cb.defaultChecked) added++;
      else if (!cb.checked && cb.defaultChecked) removed++;
    });
    if (added === 0 && removed === 0) {
      diff.textContent = 'no changes pending';
      diff.className = 'text-xs text-secondary-fg';
    } else {
      const parts = [];
      if (added > 0) parts.push(`+${added} new`);
      if (removed > 0) parts.push(`−${removed} removed`);
      diff.textContent = parts.join(', ') + ' — Apply to commit';
      diff.className = 'text-xs text-accent font-semibold';
    }
  }
  // Live density + diff updates on any checkbox toggle.
  root.addEventListener('change', e => {
    if (e.target && e.target.matches && e.target.matches('input[type="checkbox"][name="enabled_kinds"]')) {
      updateDensity();
      updateApplyDiff();
    }
  });
  // Initial sync — the page may render with prior pending changes
  // if the operator's browser preserved form state across a reload,
  // OR if BV.4.10.e carried pending_kinds across a Session Start.
  // Run density first so the top + per-family counters reflect the
  // restored DOM state, then diff so the sticky-Apply bar matches.
  updateDensity();
  updateApplyDiff();
})();

// BV.4.10.e (P1.2) — preserve the operator's pending checkbox state
// across a Session Start. Without this, clicking Session Start while
// you have boxes checked silently discards them because the
// post→redirect→render cycle re-renders from server state (which
// reflects `trainer_applied_plants` only, not in-flight DOM mutation).
// We collect the current checked-box set from the apply form and
// append them as hidden inputs on the session-start form just before
// submission; the server stashes them in module state, the next
// landing render restores them as the checkbox's HTML `checked`
// attribute (so the operator's selection survives end-to-end).
window._bvCarryPendingToSessionStart = function(sessionStartForm) {
  var applyForm = document.getElementById("training-apply-form");
  if (!applyForm) return true;
  // Remove any prior carried inputs (defensive — if the operator
  // double-clicked, we don't want duplicates).
  sessionStartForm.querySelectorAll('input[data-bv-carried]').forEach(function(el) {
    el.parentNode.removeChild(el);
  });
  applyForm.querySelectorAll('input[type="checkbox"][name="enabled_kinds"]').forEach(function(cb) {
    if (cb.checked) {
      var hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = "pending_kinds";
      hidden.value = cb.value;
      hidden.setAttribute("data-bv-carried", "1");
      sessionStartForm.appendChild(hidden);
    }
  });
  return true;
};
"""
