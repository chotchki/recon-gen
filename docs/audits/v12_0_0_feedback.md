# recon-gen v12 Cold-Read — Upstream Findings (scrubbed)

Source: a screenshots-only, context-isolated cold read (6 judges: 3 dashboard personas + 3 studio personas — Integrator / Trainer / ETL-engineer) against a private L2 instance on the self-hosted sqlite stack, `recon-gen` ≈ v12.0.2. All institution/program/account specifics and dollar figures are stripped; only generic product behavior remains. Sheet names and plant-kind names are recon-gen's own.

**Headline:** the dashboards are in good shape — no render errors, empty states are real components, per-account statements reconcile to the penny, drift glyphs match the computed difference. The findings below are real product issues a fresh user hit, ordered by severity. The studio surface (Editor / ETL / Training) is well-designed but the Trainer has concrete plant/Apply bugs.

## HIGH

**U1 — App Info "data clock" is internally impossible.** The App Info sheet shows a matview "Latest Date" timestamped *after* the page's "generated-at" stamp (observed ~3h20m later). For a reconciliation tool, an as-of clock that runs backwards undermines trust in every "as of" on the deck. Likely a UTC-vs-local or matview-vs-render timezone mismatch. Fix: stamp both from the same clock/zone and assert generated-at ≥ latest-data.

**U2 — The Executives app surfaces no exception / breach / drift signal.** Coverage, volume, and money-moved read well, but every escalation metric (open exceptions, overdrafts, parent-balance drift, anomalies) lives only in the L1 and Investigation apps — two apps away. An oversight user who opens only "Executives" concludes the program is clean when it is not. Fix: add a rolled-up health/breach tile to the exec surface with threshold banding (green/amber/red) and a deep-link to the underlying exception sheets.

**U3 — Trainer `limit_breach_outbound` plant does not plant.** After Session Start + Apply with this plant enabled, the Clean and Violation tour dashboards are byte-for-byte identical ("Breaches in Window = 0"). The plant silently no-ops, so a trainer cannot demonstrate a limit breach. (The card's own help text also notes "0" is ambiguous between healthy / stale / not-planted — worth disambiguating regardless.)

**U4 — Apply status contradicts itself.** After Apply, a green "Apply done" banner renders stacked over a red "N plant(s) failed: …" banner — and at least one plant listed as *failed* (drift) visibly DID apply (its violation dashboard populated). The post-Apply enabled-counter and per-card checkbox state also go inconsistent. Contradictory success/failure status is a trust-killer for an operator deciding whether a training session is ready. Fix: reconcile the per-plant result accounting; show one authoritative status.

## MEDIUM

**U5 — Plant magnitude mismatch.** A small configured plant magnitude renders as a very large aggregate figure in the violation view (off by orders of magnitude). Either the configured value isn't threaded through, or a per-row knob is being summed/displayed as an aggregate without a label. Fix: make the violation view echo the configured magnitude, or label the figure as the aggregate it is.

**U6 — The full (L2) topology diagram is unreadable at realistic scale.** At ~90 nodes / ~108 edges the graph is an overlapping hairball, with two unexplained grey "supernode" blobs and mid-word title wrapping. End-to-end "what flows where" can't be answered from it. (The single-layer/L1 diagram is clean and legible — good.) Fix: clustering/collapse, zoom-to-subgraph, or progressive disclosure for large models; explain the grey aggregate nodes.

**U7 — Editor entity lists have no search / sort / pagination.** For a large model the rail list renders tens of thousands of pixels tall (one long scroll); the template list similarly. Maintenance navigation is impractical. Fix: search + sort + pagination (or virtualized list) on the entity-list views.

**U8 — Transfer-Templates Sankey is illegible.** At realistic template counts the Sankey is a mass of overlapping bands with no readable routing. Fix: filtering/highlight-on-hover, or a different layout above N flows.

**U9 — Two drift sheets show differently-scoped "largest parent drift" KPIs with near-identical labels** (two seven-figure values that differ). A user can't tell which is authoritative. Fix: disambiguate the labels (scope/window) or reconcile to one figure.

**U10 — Picker-driven sheets open fully blank with no empty-state prompt.** The per-account statement, money-trail, and account-network sheets render an empty page until a required picker is set — indistinguishable from a broken/error sheet to a first-time user. (They work once a value is chosen.) Fix: an explicit empty-state ("Select an account to begin") instead of a blank canvas. (Recurring across rounds; appears to be partially in-flight.)

**U11 — No drill from a flagged drift/exception to the offending leg.** When a per-account statement shows nonzero drift, the detail ledger lists only posted rows — no reconciling-items / failed-leg breakout, no running balance, no subtotal — so an accountant can't trace *why* it's out from within the tool. Fix: a reconciling-items / excluded-leg breakout (and ideally a running balance) on the statement detail.

**U12 — Studio Session Start hangs (and corrupts base matviews) on an oversized sqlite seed** rather than failing fast. With a large seed under sqlite, Session Start clones the config tables, then stalls indefinitely on the data clone (single-writer contention with the serving process) and leaves a base matview dropped/unrebuilt. Fix: detect the sqlite + large-seed case and fail fast with guidance (smaller seed / use a concurrent-writer DB), and make the overlay clone transactional so an abort can't leave the base half-rebuilt.

## LOW / polish

**U13 — Copy & defaults:** an Investigation intro says "Three sheets" then lists four; an empty-table message reads "No row match the current filter" (grammar); the ETL Probe "From" date shows a `01/01/1900` sentinel instead of a sensible default or placeholder.

**U14 — "Net should be ≈ zero" metric shows a large nonzero value with no tolerance band** on the exec money-moved sheet — reads alarming without context. Fix: a tolerance band / expected-range annotation.

---

### Not upstream (recorded so they aren't re-filed)
- Several Trainer pairs read as "weak demos" partly due to **our** seed (a noisy baseline that already carries structural exceptions, and a too-subtle single-plant delta) — a seed/scenario tuning matter on our side, not a product bug.
- The ETL Refresh-tally / expanded-triage-card / Probe-diff surfaces were captured in their pre-interaction state by **our** capture script (it screenshots the landings without driving the loop), so their payoff is unproven here — a capture-harness gap on our side, not evidence of a product defect.
