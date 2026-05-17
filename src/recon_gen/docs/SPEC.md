# Domain Model — Recon Generator

## Overall Goal

Help integrators generate AWS QuickSight dashboards that help non-technical financial users find and triage problems in their unique institution. This consists of a shared common library that wraps the QuickSight JSON and a series of example applications built on top that are easily customizable to the situation.

## Audiences

Four audiences with different needs. Design decisions trace to one or more of them; features that serve none are out of scope.

- **Business Analyst / Product Owner**: customizes the apps onto a real institution.
  - Describes the institution's structure and external relationships in L2 so the demo data and dashboards reflect their world.
  - Trains the other audiences against a stable demo system that mirrors real-data deployments.
- **Integration Engineer**: wires the apps into a host system.
  - Understands the two source tables (`Transaction`, `StoredBalance`) that drive every app.
  - Writes ETL that populates them on a recurring schedule.
  - Builds custom apps on the L1 primitives, or extends the shipped apps.
  - Edits each behavior in one place (DRY); trusts the test suite to catch regressions; iterates fast (regenerate + redeploy in one command); reskins via theme presets.
- **Non-technical Accountant**: uses the dashboards day-to-day.
  - Job is to find problems and route them to the team that fixes them.
  - Strong accounting background, not a programmer; the dashboards are unfamiliar — plain-English labels, hint text, and Getting Started prose are load-bearing.
  - Needs to recognize *when* something needs investigation, not *how* to fix the broken upstream system.
- **Third-party Stakeholder**: consumes the dashboards for compliance, metrics, or audit.
  - Not the primary user. The system stays extensible to meet evolving requirements without disrupting the core experience.

## Architecture Layers

The model is organized in three layers:

- **LAYER 1 — Universal model**: Money, accounts, transfers, transactions, balances, and the invariants they obey. Same for every institution. Shipped as library code. Integrators do not modify.
- **LAYER 2 — Institutional model**: Per-integrator description of this institution's account roles, transfer rails, business processes, and reconciliation expectations. Defined by the integrator as data (a YAML instance). The library reads it to scope LAYER 1 constraints to the institution's specifics, generate seed data, and render handbook prose.
- **LAYER 3 — Applications**: A fixed set of dashboard apps, each answering one **question shape** the L1 primitives and L2 instance can produce. The library ships multiple orthogonal apps; an institution gets every shipped app deployed against its single L2 instance, no code changes required. Integrators build custom apps on the L1 primitives when no shipped app covers their question.

LAYER 1 SHAPES are rigid (Conservation is Conservation); LAYER 1 SCOPES (which TransferTypes have `ExpectedNet=0`, which accounts have `ExpectedEODBalance` set, etc.) are filled in by LAYER 2. LAYER 2 itself is fully defined by the integrator — the library has no opinion beyond providing the LAYER 1 building blocks to express it. LAYER 3 is fixed by the library; institutions get the same app shapes regardless of L2 content.

## Notation Conventions

- **Type definition**: `TypeName: (Field: Type, OptionalField?: Type)` — both field names and types are PascalCase. A bare type name in a tuple is shorthand for a same-named field: `(ID, Name?)` ≡ `(ID: ID, Name?: Name)`.
- **Type as set of values**: `TypeName ⊇ {member, …}` for open sets (the system uses at least these; more may exist); `TypeName = {member, …}` for closed sets (the universe is fixed).
- **Set filter**: `TypeName(Field = value, …)` denotes the subset of `TypeName` instances where each named field equals the given value. The set name is the type name (no plural).
- **Field access**: `instance.Field`. When a parameter would shadow a type name, prefix with `in` (e.g. `inAccount: Account`).
- **Operators** (all binary operators take surrounding spaces):
  - **Comparison**: `=`, `≠`, `≤`, `≥`, `<`, `>` — standard numeric / value comparison.
  - **Set notation**: `x ∈ S` ("x is in S"); `A ⊆ B` ("A is a subset of B"); `A ⊇ B` ("A contains B" — used for open enums: "at least these members").
  - **Logic**: `¬P` ("not P"); `∃ x ∈ S where P` ("some x in S satisfies P").
  - **Aggregation**: `Σ S.Field` (sum of `.Field` across every element of S); `max S.Field` (largest such value); `|x|` (absolute value of x); `x between A and B` (shorthand for `A ≤ x ≤ B`).
  - **Definition**: `Foo := expression` defines `Foo` as the named expression (used by theorems).
- **Constraint strength**: MUST and SHOULD per RFC 2119. MUST = a hard invariant the system relies on; SHOULD = an expected condition whose violation surfaces as a dashboard exception.
- **YAML key convention**: SPEC type and field names are PascalCase; the YAML representation transliterates them to snake_case (`SourceRole` → `source_role`). Role / Rail / Template *names* themselves stay PascalCase as identifier values.

---

# Layer 1 — Universal Model

## Primitives (Axioms)

Identity & labels:
- `Entry`: ordered sequence
- `ID`: opaque identifier
- `Name`: human-readable label
- `Value`: human-readable string
- `Scope` = {Internal, External}

Money:
- `Currency`: ISO 4217 code; the system is pinned to a single `Currency`
- `Money`: signed Decimal to 2dp in `Currency`
- `Direction` = {Debit, Credit}
- `Amount`: (Money, Direction)
  - INVARIANT: `Money ≥ 0` if `Direction = Credit`; `Money ≤ 0` if `Direction = Debit`

Time:
- `Timestamp`: instant in UTC (integrators convert at the boundary)
- `BusinessDay`: (StartTime: Timestamp, EndTime: Timestamp)
- `Duration`: a span of time (used for aging windows)

Transfer machinery:
- `Status` ⊇ {Pending, Posted}
- `TransferType` ⊇ {Sale}
- `Origin` ⊇ {InternalInitiated, ExternalForcePosted}
- `SupersedeReason` ⊇ {Inflight, BundleAssignment, TechnicalCorrection}
- `Metadata`: `Map[Name, Value]`

Entities:
- `Account`: (ID, Name?, Parent?: Account, Scope, ExpectedEODBalance?: Money)
- `Transfer`: (ID, Completion: Timestamp, TransferType, Parent?: Transfer, ExpectedNet?: Money)
- `Transaction`: (Entry, ID, Account, Amount, Status, Posting: Timestamp, Transfer, Origin, BundleId?: ID, Supersedes?: SupersedeReason, Metadata)
- `StoredBalance`: (Entry, Account, BusinessDay, Money, Limits?: Map[TransferType, Money], Supersedes?: SupersedeReason)

Expected Implementation Entities:
- `DailyBalance`: `StoredBalance` + `Account`
- `StoredTransaction`: `Transaction` + `Transfer`

### Status lifecycle

Transactions typically transition `Pending → Posted` via successive Entry rows of the same ID. A Pending Transaction is recorded but not yet considered settled fact — the integrator has captured the event but its required fields aren't all present yet. L1 invariants scope to `Status = Posted` because Pending values represent uncertainty about whether the event happened; counting them would produce false reconciliation results.

What makes a Transaction validly Posted is declared per-Rail in L2 (see `PostedRequirements`). Other state machines (e.g., `Pending → Cancelled`) are integrator-defined; the library does not interpret status values outside `{Pending, Posted}`.

### Higher-Entry rows: inflight vs correction vs bundling

Every higher-Entry row that supersedes a previous Entry (for the same `Transaction.ID` or the same `(StoredBalance.Account, StoredBalance.BusinessDay)` pair) MUST set the typed `Supersedes` field naming the category. The categorization is determined by the **prior row's** state and the entity kind:

**Categories applicable to `Transaction`:**

- **`Inflight`** — the prior row had `Status = Pending`. The new row completes the data (possibly transitioning Status to Posted, or just filling in more fields while still Pending). This is **NOT a correction** — nothing was wrong; the row was always going to fill in over time as the integrator's ETL caught up. Normal lifecycle progression.
- **`BundleAssignment`** — the prior row had `Status = Posted` and `BundleId` NULL; the new row carries `BundleId = <bundle Transfer's id>`, otherwise identical. The bundler consumed this Transaction and recorded which aggregating Transfer it folded into. Also not a correction — the prior row was correct, just unbundled.
- **`TechnicalCorrection`** — the prior row had wrong data and the new row changes one or more load-bearing values. **This IS a correction** — upstream wrote the wrong data, and the new row is what should have been written. The superseded row stays visible for audit.

**Categories applicable to `StoredBalance`:**

- **`TechnicalCorrection`** — the prior snapshot value was wrong (either we recorded it wrong, or the source authority later restated). The new row carries the corrected `Money` value. The superseded row stays visible for audit.

`Inflight` and `BundleAssignment` do not apply to StoredBalance — snapshots don't have a Pending lifecycle and aren't bundled. Any higher-Entry StoredBalance is by construction a `TechnicalCorrection`.

The distinction matters because the dashboard / handbook surfaces these very differently:
- Inflight progressions are noise during normal operation; only an Inflight row that's been Pending for too long (per `MaxPendingAge`) is worth surfacing.
- Bundle assignments are operational — accountants want to see which bundle a row landed in, but it's not an exception.
- Technical corrections are exceptions worth investigation — somebody had bad data; the audit trail (prior + corrected entries) is what they need.

Recording the reason is load-bearing for the recon experience: an accountant looking at a row's history needs to immediately see whether the supersedence means "your ETL is fine, this row was just inflight" or "your upstream got something wrong here."

## Derivatives (Theorems)
- `CurrentTransaction` := `{ tx ∈ Transaction : tx.Entry = max(Transaction(ID = tx.ID).Entry) }`
- `CurrentStoredBalance` := `{ sb ∈ StoredBalance : sb.Entry = max(StoredBalance(Account = sb.Account, BusinessDay = sb.BusinessDay).Entry) }`
- `ComputedBalance(inAccount: Account, inBusinessDay: BusinessDay)` := `Σ CurrentTransaction(Account = inAccount, Status = Posted, Posting ≤ inBusinessDay.EndTime).Amount.Money`. **Cumulative through end-of-day** — every Posted transaction with `Posting ≤ EndTime` contributes regardless of how far in the past it was, NOT just events on `inBusinessDay`. See the "Stored balance contract" note under System Constraints for the implementation contract this implies for integrators planting StoredBalance entries.
- `Drift(inAccount: Account, inBusinessDay: BusinessDay)` := `CurrentStoredBalance(Account = inAccount, BusinessDay = inBusinessDay).Money − ComputedBalance(inAccount, inBusinessDay)`
- `LedgerDrift(inAccount: Account, inBusinessDay: BusinessDay)` := `CurrentStoredBalance(Account = inAccount, BusinessDay = inBusinessDay).Money − ( Σ CurrentTransaction(Account = inAccount, Status = Posted, Posting ≤ inBusinessDay.EndTime).Amount.Money + Σ CurrentStoredBalance(Account.Parent = inAccount, BusinessDay = inBusinessDay).Money )`
- `NetOfTransfer(inTransfer: Transfer)` := `Σ CurrentTransaction(Transfer = inTransfer, Status = Posted).Amount.Money`
- `IsParent(inAccount: Account)` := `∃ child ∈ Account where child.Parent = inAccount`
- `OutboundFlow(inAccount: Account, inTransferType: TransferType, inBusinessDay: BusinessDay)` := `Σ |CurrentTransaction(Account = inAccount, Transfer.TransferType = inTransferType, Amount.Direction = Debit, Status = Posted, Posting between inBusinessDay.StartTime and inBusinessDay.EndTime).Amount.Money|`
- `Age(inTransaction: Transaction)` := `now() − inTransaction.Posting`

## System Constraints

- **Conservation**: For every `t: Transfer` where `t.ExpectedNet` is set, `Σ CurrentTransaction(Transfer = t, Status = Posted).Amount.Money` SHOULD equal `t.ExpectedNet`. (Single-leg transfers leave `ExpectedNet` unset and are exempt; standard double-entry transfers set `ExpectedNet = 0`.)
- **Timeliness**: For every `tx: CurrentTransaction`, `tx.Posting ≤ tx.Transfer.Completion` SHOULD hold. Remediation is append-only — a violation (or any other Conservation-breaking condition) is corrected by posting a new Transaction against the same Transfer, not by amending the offending one.
- **BusinessDay enclosure**: For every `tx: CurrentTransaction` where `tx.Account.Scope = Internal`, there MUST exist `sb: CurrentStoredBalance(Account = tx.Account)` such that `sb.BusinessDay.StartTime ≤ tx.Posting ≤ sb.BusinessDay.EndTime`.
- **Non-negative stored balance**: For every `sb: CurrentStoredBalance`, `sb.Money` SHOULD be `≥ 0`.
- **Sub-ledger drift**: For every `sb: CurrentStoredBalance` where `sb.Account.Scope = Internal` and `¬IsParent(sb.Account)`, `Drift(sb.Account, sb.BusinessDay)` SHOULD equal `0`.
- **Ledger drift**: For every `sb: CurrentStoredBalance` where `sb.Account.Scope = Internal` and `IsParent(sb.Account)`, `LedgerDrift(sb.Account, sb.BusinessDay)` SHOULD equal `0`.
- **Parent balance existence**: For every `sb: CurrentStoredBalance` where `sb.Account.Parent` is set, there MUST exist `CurrentStoredBalance(Account = sb.Account.Parent, BusinessDay = sb.BusinessDay)`.
- **Expected EOD balance**: For every `sb: CurrentStoredBalance` where `sb.Account.ExpectedEODBalance` is set, `sb.Money` SHOULD equal `sb.Account.ExpectedEODBalance`.
- **Limit breach**: For every `sb: CurrentStoredBalance` where `sb.Limits` is set, for every `(t, limit) ∈ sb.Limits`, for every child `c ∈ Account(Parent = sb.Account)`, `OutboundFlow(c, t, sb.BusinessDay)` SHOULD be `≤ limit`. (Limits live on the parent's `StoredBalance` and apply to each child individually — not aggregated across children.)
- **Immutability**: Every `Transaction` and `StoredBalance` entity is immutable. Violations of constraints should be repaired by posting additional transactions. System errors may be corrected (but not hidden) by entering a higher entry; every higher Entry row MUST set `Supersedes` to record why.

**Stored balance contract** (implementation note for integrators planting StoredBalance rows): every StoredBalance row is an assertion that this account's cumulative net through that BusinessDay's end is exactly `Money`. The Sub-ledger drift / Ledger drift constraints check that assertion against `ComputedBalance` (see Theorems). Practically:
- A drift-free StoredBalance for `(account, day)` SHOULD have `Money = ComputedBalance(account, day)` — i.e., the cumulative net of every Posted transaction on this account with `Posting ≤ day.EndTime`.
- A StoredBalance posted at `0` for an account with prior posted activity will surface as drift equal to the negative of the cumulative — this is correct semantic, not a bug.
- To plant intentional drift for testing exception surfaces, set `Money = ComputedBalance + delta` and the Drift theorem returns `delta`.
- An account with no StoredBalance for a given BusinessDay is invisible to the Drift / Overdraft / Expected EOD constraints (they iterate over CurrentStoredBalance, not over Account). This is the right semantic — if the integrator never asserted a stored balance, there's nothing to compare against.

## Design Principles

- **Metadata promotion**: `Metadata` is opaque to System Constraints and Theorems — it carries values for display and integrator-defined filtering only. If a rule (a constraint, theorem, invariant, or scenario predicate) needs to read a value to evaluate, that value MUST be promoted out of `Metadata` into a typed field on the bearing entity. The set of typed fields is the set of load-bearing values; everything in `Metadata` is observational.
- **Three kinds of higher-Entry row** (also see "Higher-Entry rows" above):
  - **Inflight progression** (Transaction only; prior row was Pending) — the new row carries more complete data; possibly transitions Status to Posted. Not a correction; this is how integrator ETL completes a row over time. `Supersedes = Inflight`.
  - **Bundle assignment** (Transaction only; prior row was Posted with BundleId NULL) — the bundler consumed this Transaction; the new row records its `BundleId`. Not a correction. `Supersedes = BundleAssignment`.
  - **Technical correction** (Transaction or StoredBalance; prior row was wrong) — upstream wrote a wrong amount, wrong Account reference, wrong Parent, wrong daily balance, etc. The new row is what should have been written. The superseded row stays visible for audit. `Supersedes = TechnicalCorrection`.
- **Business-process failures vs technical errors**:
  - **Business-process failures** (a real-world event went wrong — a wrong transfer was actually executed, a leg never posted, a balance ended overdrawn) are corrected by posting **additional Transactions against the same Transfer**, NOT by superseding existing rows. The original Transaction(s) stay as-is — they record what actually happened in the business.
  - **Technical errors** (the system wrote the wrong row for a real-world event) are corrected by superseding (above).
  - The distinction is "did the real-world event happen the way the row says?" If yes but our row is wrong → technical correction. If no, the row is correct as a record of what happened → fix by posting an additional Transaction.
- **Account dimension is read-only**: This system reads accounts from upstream and uses their typed structural attributes (`Scope`, `Parent`, `ExpectedEODBalance`) to evaluate constraints. It does not provide tools to create, modify, or audit accounts. `Account.Name` is a human-convenience display label and is not load-bearing for any constraint or theorem.
- **Implementation**: Entities are stored in an append-only format with an automatically-incrementing `Entry` id. Technical-error remediation MUST insert a new entity with a higher `Entry` id than the error's.

---

# Layer 2 — Institutional Model

## Purpose

LAYER 2 captures the integrator's institution: which accounts exist, what kinds of money movement the institution operates, how those movements relate, and what constraints apply. The library reads it to:
- Scope LAYER 1 invariants to the institution's specifics.
- Drive deterministic seed-data generation that exercises every declared rail.
- Render handbook prose against the institution's vocabulary.

LAYER 2 is fully defined by the integrator. The library has no opinion on its content beyond providing the LAYER 1 building blocks the integrator's L2 expresses against.

## How L2 plugs into L1

| L1 element | L2 contribution |
|---|---|
| `Account` | Declared per-instance and per-template by L2. |
| `TransferType` (open enum) | L2 contributes members. |
| `Transfer.ExpectedNet` | Set by L2 — per-Rail (standalone Transfers) or per-TransferTemplate (shared multi-leg Transfers). |
| `Transfer.Completion` | Set by L2 — per-Rail or per-TransferTemplate. |
| `Transaction.Account` | Resolved per leg from the firing Rail's `SourceRole` / `DestinationRole` / `LegRole`. When the role comes from an AccountTemplate, the concrete account instance is selected at posting time from the leg's Metadata. |
| `Transaction.Origin` | Declared per-leg per-Rail. The rail-level `Origin` field applies to all legs by default; `SourceOrigin` / `DestinationOrigin` override per-leg when the legs differ (e.g., the leg touching an external counterparty is `ExternalForcePosted` while the internal counterpart is `InternalInitiated`). |
| `Transaction.Posting` | Runtime / ETL-supplied; L2 does NOT contribute. |
| `Transaction.Amount` | Runtime / ETL-supplied; L2 does NOT contribute. |
| `Transaction.Status` | Lifecycle managed via L2's `PostedRequirements`. The library refuses to mark `Status = Posted` for a Transaction missing any of the firing Rail's PostedRequirements. |
| `Transaction.BundleId` | Populated by AggregatingRail bundlers; integrator's ETL leaves it NULL. |
| `Transaction.Metadata` | L2 declares the key set per Rail; values remain opaque runtime data. |
| `StoredBalance.Limits` | Populated from L2's Limit Schedules. |

L2 contributes no invariants of its own. All checks reduce to L1 invariants firing on data L2 has shaped.

## Primitives

### Description fields *(optional, on every primitive)*

Every L2 primitive (and the top-level `L2Instance` itself) carries an optional `Description?: Value` field. Free-form prose authored by the integrator — typically markdown — explaining what this entity is and why it exists. The library does no pre-processing; the value reaches handbook + training render templates as-is.

The field is **optional** at the type level (defaults to absent) for backward compatibility, but **SHOULD** be filled per RFC 2119 — handbook and training-scenario quality depends on it. An integrator skipping descriptions still gets functioning dashboards; what they lose is the auto-rendered prose explaining each entity's purpose.

```
Description: Value     # markdown-friendly prose, single field, no schema beyond "string"
```

Why on every primitive (including `ChainEntry` and `LimitSchedule` which look like pure plumbing): training-scenario authoring needs the *why* context — "this XOR group exists because exactly one payout vehicle fires per cycle", "this cap exists because regulators require X" — not just the names. The handbook reads them to render entity-purpose paragraphs without authors having to reproduce the institutional knowledge inline in handbook source.

Per primitive's type signature below, `Description?` is shown as an optional last field; it is intentionally omitted from the worked-example YAML blocks to keep them shape-focused, but production L2 instances should fill it.

---

### Deployment prefix *(declared in cfg.yaml, not in the L2 instance)*

The L2 instance does NOT carry a prefix (Z.C, 2026-05-15 — the legacy `InstancePrefix: Identifier` field at the top of the L2 YAML was dropped). Prefixing is a deployment concern, not a model concern, and lives in `cfg.yaml`:

```yaml
# cfg.yaml — both required, no defaults
deployment_name: "recon-prod"   # prefixes every QS resource ID
db_table_prefix: "recon_prod"   # prefixes every DB table / matview / dataset name
```

**`db_table_prefix` format**: MUST match `^[a-z][a-z0-9_]*$` (lowercase start, alphanumeric or underscore thereafter), max 30 characters. The lowercase-only constraint avoids Postgres' quoted-vs-unquoted-identifier hazard; the 30-character cap leaves room for the longest table-name suffix within Postgres' 63-character identifier limit.

Two deployments of the same L2 instance coexist in one database by using distinct `db_table_prefix` values (and distinct `deployment_name` values to avoid colliding QS resource IDs); cross-deployment JOINs are not supported.

Prefix-based isolation (over Postgres schemas) is the default because not all deployment environments grant `CREATE SCHEMA` rights to the library's runtime; bare table/view name prefixing works everywhere.

---

### Roles *(open vocabulary)*

```
Role: Identifier
```

An integrator-defined label for an Account or class of Accounts. Roles serve two purposes:

1. **Stable handle for Rails to reference accounts.** A Rail that says `SourceRole: ConcentrationMaster` is more portable than `SourceAccount: gl-1850`, particularly when the referenced account comes from an AccountTemplate (many runtime instances of the same role).
2. **Class label for templates.** `Role: CustomerSubledger` lets thousands of customer-instance accounts share one declared shape.

Roles are open: the integrator declares whichever labels are useful. The library has no built-in roles.

---

### Accounts *(required: list of L1 `Account`)*

1-of-1 accounts that exist exactly once in the institution. Each entry MUST populate the L1 required fields and SHOULD populate optional fields where they apply.

```
Account: (
  ID,
  Name?,
  Role?: Role,
  Scope,
  ParentRole?: Role,
  ExpectedEODBalance?: Money,
  Description?: Value,
)
```

Notes:
- `ParentRole` references the parent by Role rather than by ID, so parent accounts that come from AccountTemplates are expressible. The library resolves `ParentRole` to a concrete L1 `Account.Parent` reference at materialization time.
- An Account whose `Role` is unique resolves any Rail reference to that role unambiguously.

---

### Account Templates *(optional: list)*

A class of accounts that exists in many instances at runtime — one per customer, one per location, one per merchant. Declares the shape; concrete instances are materialized by the integrator's seed/ETL process.

```
AccountTemplate: (
  Role,
  Scope,
  ParentRole?: Role,
  ExpectedEODBalance?: Money,
  Description?: Value,
)
```

When a Rail references a Role provided by an AccountTemplate, the Rail describes the SHAPE; the specific account instance for a given posting is selected at posting time, typically from the Transaction's Metadata (e.g., `customer_id`).

#### Constraints

- **Singleton parent only.** `ParentRole` MUST resolve to a singleton `Account`, never to another `AccountTemplate`. Template-under-template nesting is forbidden because the per-instance parent assignment becomes ambiguous (which of N parent-template instances does a given child-template instance roll up to?). If per-customer subledger nesting is needed, model it by carrying `customer_id` as Metadata on a singleton-parented subledger rather than nesting accounts.
- **Name handling.** Concrete-instance `Name` is integrator-supplied at materialization time (typically by the ETL/seed process). If not provided, the materialized `ID` is used as the display Name. AccountTemplate itself doesn't declare a name pattern — the library doesn't synthesize names from metadata.

---

### Rails *(required: list)*

A canonical leg-pattern the institution operates. Each Rail produces one or two `Transaction` legs per firing.

```
Rail: (
  Name,
  TransferType,                          # extends L1 TransferType
  MetadataKeys: [Identifier, …],         # which Metadata keys legs may populate (informative)

  # Origin — per-leg (extends L1 Origin). At least one resolution path MUST be available
  # for every leg. See "Per-leg Origin" below.
  Origin?: Origin,                       # default for all legs (shorthand when all legs share)
  SourceOrigin?: Origin,                 # 2-leg only: override for source/debit leg
  DestinationOrigin?: Origin,            # 2-leg only: override for destination/credit leg

  # Shape — exactly one of the two groups below:

  # (a) Two-leg
  SourceRole?: RoleExpression,           # debit leg's account
  DestinationRole?: RoleExpression,      # credit leg's account
  ExpectedNet?: Money,                   # required when this rail fires standalone Transfers

  # (b) Single-leg
  LegRole?: RoleExpression,
  LegDirection?: {Debit, Credit, Variable},

  # Optional flags
  Aggregating?: Boolean,                 # see Aggregating Rails below
  BundlesActivity?: [BundleSelector, …],
  Cadence?: CadenceExpression,

  # Inflight-handling (see "Inflight transaction handling" below)
  PostedRequirements?: [Identifier, …],  # additional integrator-declared posting requirements
  MaxPendingAge?: Duration,              # aging watch for Pending → Posted lag
  MaxUnbundledAge?: Duration,            # aging watch for Posted-but-not-bundled (only for bundled rails)

  Description?: Value,                   # see "Description fields" above
)

RoleExpression: Role | (Role | Role | …)   # union role; see below

BundleSelector: TransferType | RailName | TransferTemplateName | TransferTemplateName.LegRailName
```

#### Per-leg Origin

`Transaction.Origin` is a per-Transaction field at L1. Two legs of the same Rail commonly share an Origin (e.g., a fully-internal sweep where both legs are `InternalInitiated`), but real flows often need different Origins per leg — most commonly when one leg touches an external counterparty (`ExternalForcePosted` — the external party drove this) while the other touches an internal account (`InternalInitiated` — we recorded the response on our books).

Resolution rules:
- **1-leg rails**: only `Origin` applies. `SourceOrigin` / `DestinationOrigin` are ignored if set (load-time warning).
- **2-leg rails**:
  - If `Origin` is set and neither override is set: both legs resolve to `Origin`.
  - If `SourceOrigin` and `DestinationOrigin` are both set: each leg resolves to its respective override; rail-level `Origin` is ignored if also set (load-time warning).
  - If only one of `SourceOrigin` / `DestinationOrigin` is set: the other leg resolves to rail-level `Origin` (which MUST then be set). If `Origin` is unset in this case, load-time error — the unspecified leg has no resolved Origin.
  - At least one of `Origin`, `SourceOrigin`, `DestinationOrigin` MUST be sufficient to resolve both legs.

#### Two-leg rails
Declare both `SourceRole` (debit leg) and `DestinationRole` (credit leg). When fired as a standalone Transfer, `ExpectedNet` MUST be set (typically `0`); L1 Conservation enforces `Σ legs = ExpectedNet`. When the rail is a leg-pattern of a TransferTemplate, `ExpectedNet` lives on the template, not the rail.

#### Single-leg rails
Declare `LegRole` and `LegDirection`. Per L1, the resulting Transfer leaves `ExpectedNet` unset and is exempt from Conservation in isolation. Single-leg rails (with `Aggregating: false` or unset) MUST be reconciled by EITHER:
- A `TransferTemplate` whose `LegRails` includes this rail (the shared Transfer's `ExpectedNet` provides closure via Conservation + Timeliness), OR
- An `AggregatingRail` whose `BundlesActivity` matches this rail (periodic reconciliation closes the drift).

A non-aggregating single-leg rail that meets neither condition is a configuration error — the drift it introduces would persist forever.

A rail MAY be reconciled by both (a leg of a TransferTemplate AND bundled by an AggregatingRail) — they reconcile different kinds of drift (transfer-net closure vs pool ledger drift). This combination is explicitly permitted.

**Single-leg aggregating rails** are exempt from the reconciliation rule above — they ARE the reconciliation mechanism (sweeping their drift into an External counterparty by design). They do not themselves appear in another rail's `BundlesActivity`.

#### `LegDirection = Variable`
Both the leg's amount AND direction are determined at posting time by surrounding context — specifically, by the requirement that a containing TransferTemplate's `ExpectedNet` hold given the other legs already posted. A "settlement" leg that posts whatever amount/direction closes the bundle is the canonical case.

A TransferTemplate MUST contain at most one Variable-direction leg per shared Transfer. Two or more Variable legs leave the closure under-determined; the library detects this at load-time validation, not at posting.

A Variable-direction leg MUST be the LAST leg posted on its Transfer — all sibling legs MUST be `Status = Posted` (not Pending) before the Variable leg posts. Posting a Variable leg while sibling legs are still Pending is a posting-time error (the closure amount can't be computed against incomplete data).

#### Union roles
`(RoleA | RoleB)` — a Role field MAY express that the rail can target accounts of more than one role. Each firing still resolves to one concrete role per leg; the union is about which roles are admissible, not about firing multiple legs at once.

#### Rail uniqueness *(`Rail.name` is the type identifier)*

Z.B (2026-05-15): under the symmetric grammar collapse, **`Rail.name` IS the type identifier**. The legacy `transfer_type` field on Rails / TransferTemplates is gone, and `<prefix>_transactions.transfer_type` follows it; the table now keys on `rail_name` alone for the Rail-to-Transaction binding. Per-direction families (e.g., `CustomerInboundACH` + `CustomerOutboundACH`) are simply two distinct rail names — no separate discriminator to collide.

The L2 validator enforces:

- **U3** — `Rail.name` is unique across the L2 instance. (Implicitly subsumes the legacy U6 per-leg `(TransferType, Role)` uniqueness rule, which is unrepresentable in the new grammar.)
- **R10** — every `LimitSchedule.rail` resolves to a declared `Rail.name`.
- **R11** — every bare `bundles_activity` selector on an aggregating rail resolves to a declared `Rail.name` (or a `Template.LegRail` dotted form per R9).

When the integrator's chart of accounts genuinely has direction-specific rails, declare them as distinct named Rails — the type identifier IS the name, and inbound + outbound are unambiguously different rails because they have unambiguously different names.

---

### Aggregating Rails *(Rail variant)*

A Rail with `Aggregating: true` sweeps activity from many other Transfers over a period without being chain-related to any one of them. Pool-to-pool balancing, periodic clearing settlements, EOM interest sweeps.

```
# Same Rail shape as above, plus:
Aggregating: true
BundlesActivity: [BundleSelector, …]
Cadence: CadenceExpression
```

`BundlesActivity` is the aggregating-rail equivalent of `Chain` — it expresses which activity the rail rolls up over, in lieu of explicit parent-child chain entries.

#### `BundleSelector` semantics

A `BundleSelector` matches eligible activity by union (OR):
- `TransferType` — every Transaction whose Transfer has this type.
- `RailName` — every Transaction produced by that specific rail.
- `TransferTemplateName` — every Transaction belonging to a Transfer of that template (i.e., every leg of that template's Transfers).
- `TransferTemplateName.LegRailName` — every Transaction belonging to a Transfer of that template AND produced by that specific leg-pattern rail. Use this to scope to one leg of a multi-leg template.

A single Transaction matched by multiple selectors counts once toward the bundle.

A Transaction is **eligible** when:
- `Status = Posted`
- `BundleId IS NULL`
- It matches the AggregatingRail's `BundlesActivity`

#### Bundling semantics (append-only)

When an AggregatingRail fires:
1. Bundler queries eligible Transactions matching `BundlesActivity`.
2. Bundler computes the net Amount across them.
3. Bundler creates a new Transfer (the aggregating Transfer) with a fresh ID — call it `bundle_id` — and posts the rail's leg(s) against it.
4. For each consumed source Transaction, bundler appends a higher-Entry `Transaction` row with `BundleId = bundle_id`, `Supersedes = BundleAssignment`. Per L1 append-only, the original row is preserved; `CurrentTransaction(ID = tx.ID)` is now the higher-Entry one.

This pattern keeps consumed-tracking append-only — no row mutation — and preserves a full audit trail of when each Transaction was bundled and into which aggregating Transfer.

#### Late-arriving Pending rows *(M.3.13)*

A Transaction whose `Pending` row arrives after a previous bundler firing has already closed for the same Rail's eligibility window is bundled by the **next** bundler firing — not retroactively into the closed bundle. The bundler treats eligibility purely on current state at the moment it runs (`Status = Posted` AND `BundleId IS NULL`), so a late-arriving leg whose Pending → Posted transition completes after the previous cadence boundary lands in whatever bundle is open the next time the bundler fires.

Concretely:
- The previous bundle's `BundleId` reflects the bundler's firing day, not the consumed source rows' `posted_at` days.
- A row that was already `Posted` at the previous firing but somehow missed the eligibility query (network blip, RDBMS replication lag) gets picked up on the next firing — same mechanism, just shifted one cadence cycle.
- `MaxUnbundledAge` measures wall-clock age of the Posted-and-eligible state, not bundler latency. An aggressive `MaxUnbundledAge` shorter than the bundler's cadence period intentionally surfaces every late-arriving row as a SHOULD-violation; the integrator chooses how aggressive to set it relative to the cadence.

This is intentional: the alternative (re-opening a closed bundle to add late rows) would mutate `BundleAssignment` rows after the fact and break L1's append-only invariant. Late rows always wait for the next firing.

#### `CadenceExpression` vocabulary *(v1)*

| Literal | Meaning |
|---|---|
| `intraday-Nh` | Every N hours during the business day (e.g., `intraday-2h`). |
| `daily-eod` | Once at end of business day. |
| `daily-bod` | Once at start of business day. |
| `weekly-<weekday>` | Once per week on the named weekday (e.g., `weekly-fri`). |
| `monthly-eom` | Once at end of calendar month. |
| `monthly-bom` | Once at start of calendar month. |
| `monthly-<day>` | Once per month on the named day (e.g., `monthly-15`). |

Cadences outside this vocabulary are not recognized in v1; the library rejects unknown literals at load time. Extending the vocabulary is a SPEC change, not an integrator-supplied resolver.

#### Constraints

- An Aggregating rail MUST NOT appear as `Child` in any Chain entry. It runs on the declared cadence, sweeping up activity matching `BundlesActivity` that is eligible but not yet bundled.
- Aggregating rails are typically two-leg, but single-leg aggregating rails are permitted (e.g., a single-leg sweep that lands in an external counterparty).

The library uses `Aggregating: true` to render these rails distinctly from the per-transfer chain DAG and to skip them in chain-validity checks.

---

### Transfer Templates *(optional: list)*

Most Rails fire 1:1 with Transfers (one Rail firing produces one Transfer). Some flows are inherently multi-leg: many Rails firing accumulate as legs into ONE shared Transfer, whose `ExpectedNet` and `Completion` close the bundle.

```
TransferTemplate: (
  Name,
  TransferType,                          # the shared Transfer's TransferType
  ExpectedNet: Money,                    # MUST be set
  TransferKey: [MetadataKey, …],         # values whose equality groups legs onto one Transfer
  Completion: CompletionExpression,      # how Transfer.Completion is derived
  LegRails: [RailName, …],               # which Rails fire as legs into this Transfer
  Description?: Value,                   # see "Description fields" above
)
```

Semantics: every firing of a `LegRails` rail with the same `TransferKey` values posts to the same shared Transfer.
- L1 Conservation flags the Transfer if its legs don't sum to `ExpectedNet` (catches missing legs, including a missing closing leg).
- L1 Timeliness flags the Transfer if any leg posts after `Completion` (catches late closure).

This is the L2 mechanism that bridges single-leg Rails to L1 enforcement: a single-leg posting that's individually exempt from Conservation IS subject to it as a leg of a TransferTemplate that requires net-zero closure by deadline.

A Rail listed in `LegRails` of a TransferTemplate MUST NOT also fire standalone Transfers — its firings always join the shared Transfer for the matching `TransferKey`.

#### `TransferKey` semantics

`TransferKey` declares which Metadata KEYS participate in the grouping rule (schema-level). The runtime VALUES under those keys remain opaque integrator-supplied data — consistent with L1's Metadata Promotion principle, which governs values, not key declarations.

`TransferKey` values are **auto-derived as `PostedRequirements`** for every Rail in `LegRails`: a leg whose Metadata is missing one or more declared `TransferKey` keys (or whose value is NULL) cannot be Posted, because it can't be assigned to a shared Transfer. Integrators don't need to repeat TransferKey fields in each Rail's PostedRequirements; the library projects them automatically.

#### Transfer ID derivation (lookup-or-create)

For TransferTemplate-based Transfers, the Transfer's L1 `ID` is allocated **lookup-or-create**: the first leg posting for a given (template, TransferKey-values) tuple creates a Transfer with a fresh ID; subsequent legs query by (template, TransferKey-values) and post against that ID.

**Implementers MUST treat this as a known failure point.** Concurrent posters racing on the first leg for a key can produce duplicate Transfers — fix path is L1's append-only entry correction (post a higher-Entry version of the duplicate's legs pointing them at the surviving Transfer ID; supersede the duplicate Transfer record). The library SHOULD provide a uniqueness constraint on (template_name, transfer_key_values) at the storage layer to catch the race at write time rather than at reconciliation time.

For ordinary business processing — where an integrator's ETL is well-behaved — lookup-or-create works without intervention. It's the high-throughput / concurrent-poster scenarios that need the entry-correction fallback.

#### TransferKey scope and field-name validity *(M.3.13)*

`TransferKey` values are scoped by **`(template_name, transfer_key_values)`**, not by `transfer_key_values` alone. Two different templates that happen to declare the same `TransferKey` field names with the same values do NOT collide on a shared Transfer — the template name disambiguates them. So `TemplateA(transfer_key=[merchant_id, period])` and `TemplateB(transfer_key=[merchant_id, period])` firing simultaneously with `merchant_id="m1", period="2026-04"` produce two distinct Transfers (one per template).

Every `TransferKey` field name MUST also appear in every leg_rail's `MetadataKeys` (validator R12 — see Validation rules above). The auto-derivation chain is: TransferKey → PostedRequirements → ETL must populate. If the field isn't in the rail's `MetadataKeys`, ETL has no schema slot to populate it — the leg can't reach Posted, and the rail is dead.

A `TransferKey` value of `NULL` (or an empty string after trimming whitespace, where the integrator's storage layer surfaces "empty" as distinct from NULL) is treated as "missing" for grouping purposes — the leg can't be Posted because its grouping membership is undefined. This mirrors the SPEC's general "TransferKey value is NULL → leg can't be Posted" rule above.

#### `CompletionExpression` vocabulary *(v1)*

| Literal | Meaning |
|---|---|
| `business_day_end` | End of the BusinessDay the Transfer was opened. |
| `business_day_end+Nd` | End of the BusinessDay N business days after open (e.g., `business_day_end+3d`). N counts business days, skipping weekends and holidays per the integrator-supplied business calendar. |
| `month_end` | End of the calendar month the Transfer was opened. |
| `metadata.<key>` | Resolves to the Timestamp value at Metadata key `<key>` on any leg of the Transfer. ETL is responsible for pre-computing this value and posting it on at least one leg. |

Expressions outside this vocabulary are not recognized in v1; the library rejects unknown literals at load time.

---

### Chains *(optional: list)*

Parent → child relationships between Rails or Transfer Templates. Used to:
- Validate that a Transfer's L1 `Parent` reference matches an allowed pattern.
- Render multi-stage pipelines.
- Generate orphan checks (every required parent SHOULD have a corresponding child).

```
Chain: (
  Parent: RailName | TransferTemplateName,
  Children: [RailName | TransferTemplateName, ...],   # one or more
  Description?: Value,                                  # see "Description fields" above
)
```

The shape of `Children` encodes the firing semantics:

- **One child** = required. Every `Parent` firing MUST invoke that child;
  a missing child surfaces as a Chain Orphan exception.
- **Two or more children** = XOR alternation. Exactly one of the listed
  children MUST fire per `Parent` firing.

Resolution:
- When `Parent` is a Rail, child Transfers' L1 `Parent` reference points to the parent Rail's Transfer.
- When `Parent` is a TransferTemplate, child Transfers' L1 `Parent` reference points to the shared Transfer (not to any one of its component leg postings).

A missing child fires as an orphan exception (RFC 2119 SHOULD: violation surfaces as a dashboard exception, not a hard failure).

When a chain row has a singleton `Children` list, the child Rail's `parent_transfer_id` field is **auto-derived as a `PostedRequirement`** — the child can't be Posted without naming its parent. (Multi-children rows make `parent_transfer_id` optional on the child's Posted legs — only one of the XOR siblings fires per parent invocation.)

#### XOR alternation

A multi-children chain row encodes "exactly one of these MUST fire per parent Transfer instance" — the same XOR semantics older versions of this SPEC spread across separate `Required: false` + `XorGroup: <name>` entries. Each Chain row IS its own XOR group.

XOR alternation captures flows like:
- "Exactly one of {success path, reversal path} happens for an escrow transfer."
- "Exactly one of {ACH payout, wire payout, internal payout} fires per settlement cycle."

The library evaluates XOR membership: missing-firings AND multiple-firings both surface as exceptions.

#### Reversals

Reversals are not a separate L2 primitive. A reversal is a Rail (typically with the same shape as the original but opposite-direction leg) participating in an XOR group with the success Rail — the success-vs-reversal example above is the canonical pattern.

---

### Limit Schedules *(optional: list)*

Daily caps on outbound flow per `(parent role, transfer type)`. Time-invariant in v1.

```
LimitSchedule: (
  ParentRole: Role,
  TransferType,
  Cap: Money,
  Description?: Value,                   # see "Description fields" above
)
```

The library projects each LimitSchedule entry into the relevant `StoredBalance.Limits` map for every StoredBalance of every account whose Role matches `ParentRole`, for every BusinessDay. L1's Limit Breach invariant then evaluates per child individually (the cap is per-child, not aggregated across siblings of the parent).

The combination `(ParentRole, TransferType)` MUST be unique across LimitSchedule entries — duplicate combinations are a load-time configuration error.

---

## Inflight transaction handling

L2 needs to reason about Transactions in flight: those that are recorded but not yet eligible to count as settled fact, and those that are settled but not yet bundled. This section covers the declarative knobs and the lifecycle.

### Lifecycle (per Transaction)

```
[ETL writes row]   →   Pending   →   Posted, BundleId NULL   →   Posted, BundleId set
                   ↑              ↑                          ↑
        Status = Pending     PostedRequirements          AggregatingRail bundler
        (some required      all populated                consumes and assigns
         fields may          (higher-Entry row,           (higher-Entry row,
         still be NULL)      Supersedes = Inflight)       Supersedes = BundleAssignment)
                            ──────────────              ──────────────────
                             MaxPendingAge              MaxUnbundledAge
                             watches this               watches this
```

Each transition is a **higher-Entry row of the same Transaction ID**, with `Supersedes` recording the category. Per L1's "Three kinds of higher-Entry row":
- Pending → (more complete Pending OR Posted) is `Inflight` — normal lifecycle progression, NOT a correction.
- Posted → Posted-with-BundleId is `BundleAssignment`.
- Posted → Posted-with-different-data is `TechnicalCorrection` (upstream got it wrong).

The first two are normal operation. Only the third is an exception worth surfacing.

Not every Transaction goes through every state. Specifically:
- A Transaction whose Rail has no PostedRequirements (or whose ETL writes the row already complete) may be Posted from creation — no Pending state, no Inflight supersedence.
- A Transaction whose Rail isn't matched by any AggregatingRail's `BundlesActivity` stays at "Posted, BundleId NULL" forever — that's correct, no BundleAssignment will ever happen. The MaxUnbundledAge watch only applies if the Rail IS bundled.

### `PostedRequirements`

Declares the field set that MUST be populated for a Transaction to legitimately have `Status = Posted`. The library refuses to mark `Status = Posted` for a Transaction missing any of these fields; the Transaction stays Pending until a higher-Entry row supplies the missing data.

The library auto-derives PostedRequirements entries from structural declarations:
- Every field in a containing TransferTemplate's `TransferKey`.
- `parent_transfer_id` if the Rail appears as Child in a chain entry with `Required: true`.

The integrator's `PostedRequirements` declaration adds Rail-specific requirements on top of these auto-derived entries.

Examples of integrator-added requirements:
- A card-spend Rail might require `[card_brand, mcc, merchant_descriptor]` because absent any of those, the row isn't reconcilable.
- An ACH Rail might require `[external_reference]` because the trace number is needed to match against the bank statement.

### `MaxPendingAge`

The longest acceptable interval between a Transaction's Pending posting and its transition to Posted. Pending Transactions older than this surface as exceptions ("stale Pending").

- SHOULD constraint per RFC 2119 — surfaces as dashboard exception, not a hard failure.
- Catches systemic ETL failures (a feed stopped delivering settlement files; a queue is backed up; a key field is being dropped at the source) that would otherwise hide behind aggregation.
- Distinct from chain orphan checks (which fire on missing child Transfers) and Conservation (which fires on Posted-leg sums) — those check structure; this one checks ETL liveness.

### `MaxUnbundledAge`

The longest acceptable interval between a Transaction becoming Posted-and-eligible-for-bundling and being assigned a `BundleId`. Posted-and-unbundled Transactions older than this surface as exceptions ("stale Unbundled").

- Only meaningful when the Rail's transactions are bundled (i.e., something else has them in `BundlesActivity`).
- Catches bundler liveness — distinct from MaxPendingAge (which catches incomplete data).

---

## Implementation notes

- Each *deployment* of an L2 instance is fully isolated by its cfg-level `deployment_name` (QS resources) and `db_table_prefix` (DB objects). Every generated database object and every dashboard resource ID is prefixed.
- Production integrators typically run one L2 instance under a stable production deployment_name + db_table_prefix pair. Demo and test runs use ephemeral or fixture-specific prefixes so they never collide.
- The library validates the L2 instance at load time. Configuration errors are reported at load, not at posting time.

### Validation rules

Every rule below is enforced at YAML load time — `load_instance(path)` runs the full cross-entity validation pass before returning, so an integrator authoring a malformed L2 instance fails at parse time rather than at first render. Violations raise `L2ValidationError` with a logical-path message identifying the offending field. (Tests that need to construct intentionally-incomplete instances may opt out via `load_instance(path, validate=False)`.)

- Every `Role` referenced by a Rail or AccountTemplate resolves to either a declared `Account` or an `AccountTemplate`.
- Every `RailName` in a `TransferTemplate.LegRails` or `ChainEntry` exists.
- Every `TransferTemplateName` in a `ChainEntry` or `BundleSelector` exists.
- Every `AccountTemplate.ParentRole` resolves to a singleton `Account` (NOT another `AccountTemplate`).
- Every single-leg Rail (with `Aggregating: false` or unset) is reconciled — appears as a leg of a TransferTemplate AND/OR is matched by an AggregatingRail's `BundlesActivity`.
- Every TransferTemplate contains at most one `LegDirection: Variable` leg.
- Every `TransferTemplate.LegRails` entry references a non-Aggregating Rail. (Aggregating rails sweep on a cadence and don't carry the per-instance identity a TransferKey-grouped template needs.)
- Every `Aggregating: true` Rail is absent from `Child` positions in chains.
- Every `XorGroup` membership is consistent (all members share `Parent`).
- Every `Completion` and `Cadence` literal is in the v1 vocabulary.
- Every `LimitSchedule` `(ParentRole, TransferType)` combination is unique. **(M.2d.2)** Duplicate combinations are ambiguous — the projection into `StoredBalance.Limits` would have two competing caps, and the CASE-branch render order in the limit-breach matview silently picks the first match. Caught at YAML load.
- Every `MaxUnbundledAge` is set only on Rails that appear in some AggregatingRail's `BundlesActivity` (otherwise the watch can never fire).
- Every `BundleSelector` of the form `TransferTemplateName.LegRailName` references a rail that's actually in that template's `LegRails`.
- Every leg of every Rail resolves to an Origin (per the resolution rules in "Per-leg Origin"). Unresolved legs are a load-time configuration error.
- Per-leg overrides (`SourceOrigin`, `DestinationOrigin`) appear only on 2-leg rails. Their presence on a 1-leg rail is a load-time warning (the field is ignored).
- Every L2-instance reference to a `TransferType` string MUST resolve to some Rail's declared `TransferType`. **(M.2d.1)** Concretely: every `LimitSchedule.TransferType` matches some `Rail.TransferType`, and every bare-form (`<name>`, not `Template.LegRail`) entry in an AggregatingRail's `BundlesActivity` resolves to either a declared `Rail.Name` OR some declared `Rail.TransferType`. Catches typos in cap declarations and bundle selectors that would otherwise silently no-op. (The runtime invariant — every *posted* Transaction's `TransferType` matches some Rail — is the L3 surface, slated for M.2d.4 as a SHOULD-constraint matview rather than a load-time validator.)
- Every `TransferKey` field name MUST appear in `MetadataKeys` of every Rail in the template's `LegRails`. **(M.3.13)** TransferKey fields are auto-derived as `PostedRequirements` for every leg_rail; if the field isn't declared in the rail's `MetadataKeys`, the integrator's ETL has no legitimate place to populate it — the column simply doesn't exist on the rail's posting shape — and the leg can never reach `Status = Posted`. Caught at YAML load instead of at first posting attempt.
- Every Variable-direction `SingleLegRail` MUST appear in some `TransferTemplate.LegRails`. **(M.3.13)** Variable closure semantics require a containing template's `ExpectedNet` to compute the leg's amount + direction at posting time. A Variable rail reconciled only by an AggregatingRail (the alternate S3 reconciliation path) has no closure target — the bundler computes its own amount, not a closure. Caught at YAML load.
- Every `XorGroup` MUST have at least 2 members. **(M.3.13)** A single-member XOR group is degenerate: "exactly one of one option happens" trivially holds whenever the parent fires, so the declaration adds no constraint. In practice this is a typo (the second member's `XorGroup` string disagrees) or a leftover from a deletion. Caught at YAML load so the misconfig can't silently weaken the dashboard's XOR-violation detection.
- Every key in a Rail's `MetadataValueExamples` MUST appear in the same Rail's `MetadataKeys`. **(M.4.2b)** `MetadataValueExamples` is the optional per-key example value map the demo seed's broad-mode plant generator uses to render persona-aware metadata cascade values (cycling through declared examples by firing seq; falling back to a synthetic per-firing string when a key has no examples). A typo'd example-list key would silently never be used by the seed picker — the integrator would never see a feedback signal that their example data is wrong. Caught at YAML load.

---

## Worked example shapes

### Singleton account
```yaml
- id: clearing-suspense
  name: Clearing Suspense
  role: ClearingSuspense
  scope: internal
  expected_eod_balance: 0
```

### Account template
```yaml
- role: CustomerSubledger
  scope: internal
  parent_role: CustomerLedger
# Assumes a singleton Account with role: CustomerLedger declared
# elsewhere in the same instance.
```

### Two-leg standalone rail (shared Origin)
```yaml
- name: InternalSweep
  source_role: ClearingSuspense
  destination_role: NorthPool
  expected_net: 0
  origin: InternalInitiated                    # both legs are internal-initiated
  metadata_keys: [business_day]
```

### Two-leg rail with per-leg Origin
```yaml
- name: ExternalRailInbound
  source_role: ExternalCounterparty
  destination_role: ClearingSuspense
  expected_net: 0
  source_origin: ExternalForcePosted           # external party drove the inbound
  destination_origin: InternalInitiated        # we recorded the credit on our books
  metadata_keys: [external_reference, originator_id]
  posted_requirements: [external_reference]    # bank reference number is required (integrator-declared)
  max_pending_age: PT24H                       # ETL should complete within a day
```

### Two-leg rail with union destination role
```yaml
- name: InternalPayout
  source_role: MerchantLedger
  destination_role: (MerchantLedger | CustomerSubledger)   # union — either is admissible
  expected_net: 0
  origin: InternalInitiated
  metadata_keys: [paying_merchant_id, receiving_party_id, party_kind]
  posted_requirements: [party_kind]            # ETL MUST tag which kind of destination this is
```

### Single-leg debit rail
```yaml
- name: SubledgerCharge
  leg_role: CustomerSubledger
  leg_direction: Debit
  origin: InternalInitiated
  metadata_keys: [merchant_id, customer_id, settlement_period]
  max_unbundled_age: PT4H                      # PoolBalancing should sweep within 4 hours
  # TransferKey fields (merchant_id, settlement_period) auto-derived to
  # PostedRequirements via MerchantSettlementCycle below.
```

### Single-leg credit rail (mirror)
```yaml
- name: SubledgerRefund
  leg_role: CustomerSubledger
  leg_direction: Credit
  origin: InternalInitiated
  metadata_keys: [merchant_id, customer_id, settlement_period, original_charge_id]
  max_unbundled_age: PT4H
  # Posted as a leg of MerchantSettlementCycle alongside SubledgerCharge.
```

### Single-leg variable-direction rail
```yaml
- name: SettlementClose
  leg_role: MerchantLedger
  leg_direction: Variable
  origin: InternalInitiated
  metadata_keys: [merchant_id, settlement_period]
  # Direction + amount determined by the TransferTemplate's net-zero
  # requirement; MUST be the last leg posted on its Transfer.
```

### Transfer template
```yaml
- name: MerchantSettlementCycle
  expected_net: 0
  transfer_key: [merchant_id, settlement_period]
  completion: metadata.settlement_period_end
  leg_rails:
    - SubledgerCharge
    - SubledgerRefund
    - SettlementClose
```

### Aggregating rail (two-leg, intraday) — demonstrating BundleSelector forms
```yaml
- name: PoolBalancingNorthToSouth
  source_role: NorthPool
  destination_role: SouthPool
  expected_net: 0
  origin: InternalInitiated
  metadata_keys: [bundled_transfer_type, business_day]
  aggregating: true
  cadence: intraday-2h
  bundles_activity:
    # Leg-scoped — only these specific leg-patterns of MerchantSettlementCycle
    - MerchantSettlementCycle.SubledgerCharge
    - MerchantSettlementCycle.SubledgerRefund
    - MerchantSettlementCycle.SettlementClose
    # RailName form — every Transfer produced by this standalone rail
    - InternalPayout
    # TransferType form — every Transfer of this type, regardless of producing rail
    - cross_world_transfer
```

### Aggregating rail (single-leg, monthly)
```yaml
- name: ExternalFeeAssessment
  leg_role: ExternalCounterparty
  leg_direction: Debit
  origin: ExternalForcePosted
  metadata_keys: [accrual_period]
  aggregating: true
  cadence: monthly-eom
  bundles_activity: [SubledgerCharge]
  # Single-leg aggregating rail — exempt from "must be reconciled by another rail."
  # By design it sweeps drift into an external counterparty.
```

### Chain — XOR alternation with TransferTemplate parent
```yaml
- parent: MerchantSettlementCycle
  children:
    - MerchantPayoutACH
    - MerchantPayoutWire
    - MerchantPayoutInternal
# Multi-children = XOR alternation: exactly one of the three vehicles
# fires per settlement cycle.
```

### Chain — fan-out (one parent, many children)
```yaml
- parent: BatchInbound
  child: PerRecipientCredit
  required: true
# Required: true on a one-to-many fan-out means at least one child
# must fire (typical: many fire, one per item in the batch). The
# child's parent_transfer_id is auto-added to its PostedRequirements.
```

### Limit schedule
```yaml
- parent_role: NorthPool
  cap: 5000.00
```

### End-to-end: a complete merchant-acquiring instance

This example exercises every L2 primitive — singleton accounts, account templates, two-leg + single-leg + variable-direction + aggregating rails, per-leg Origin, union roles, transfer templates, chains with XOR groups, limit schedules, PostedRequirements, MaxPendingAge, MaxUnbundledAge, and BundleSelector in three forms.

```yaml
instance: example_acquirer

# ---- Singleton accounts -----------------------------------------------------
accounts:
  - id: north-pool
    role: NorthPool
    scope: internal

  - id: south-pool
    role: SouthPool
    scope: internal

  - id: clearing-suspense
    role: ClearingSuspense
    scope: internal
    expected_eod_balance: 0

  - id: ext-counter
    role: ExternalCounterparty
    scope: external

# ---- Account templates (multi-instance) -------------------------------------
account_templates:
  - role: CustomerSubledger
    scope: internal
    parent_role: SouthPool

  - role: MerchantLedger
    scope: internal
    parent_role: NorthPool

# ---- Rails ------------------------------------------------------------------
rails:
  # ===== Leg patterns of MerchantSettlementCycle (single-leg) =================

  - name: SubledgerCharge
    leg_role: CustomerSubledger
    leg_direction: Debit
    origin: InternalInitiated
    metadata_keys: [merchant_id, customer_id, settlement_period, settlement_period_end]
    max_pending_age: PT4H        # ETL should complete within 4h
    max_unbundled_age: PT4H      # PoolBalancing should sweep within 4h

  - name: SubledgerRefund
    leg_role: CustomerSubledger
    leg_direction: Credit
    origin: InternalInitiated
    metadata_keys: [merchant_id, customer_id, settlement_period, settlement_period_end, original_charge_id]
    max_pending_age: PT4H
    max_unbundled_age: PT4H

  - name: SettlementClose
    leg_role: MerchantLedger
    leg_direction: Variable      # amount + direction set by Transfer's net-zero
    origin: InternalInitiated
    metadata_keys: [merchant_id, settlement_period, settlement_period_end]
    max_unbundled_age: PT4H

  # ===== Vehicle Transfers (chained children of the settlement cycle) ========

  # Vehicle 1: outbound ACH — per-leg Origin (internal sweep + external landing)
  - name: MerchantPayoutACH
    source_role: MerchantLedger
    destination_role: ExternalCounterparty
    expected_net: 0
    source_origin: InternalInitiated         # we initiated the debit on the merchant
    destination_origin: ExternalForcePosted  # external bank's books are where it lands
    metadata_keys: [merchant_id, settlement_period, external_reference]
    posted_requirements: [external_reference]   # bank trace number required
    max_pending_age: PT24H

  # Vehicle 2: internal payout — union destination role
  - name: MerchantPayoutInternal
    source_role: MerchantLedger
    destination_role: (MerchantLedger | CustomerSubledger)   # could be either
    expected_net: 0
    origin: InternalInitiated
    metadata_keys: [merchant_id, settlement_period, receiving_party_id, receiving_party_kind]
    posted_requirements: [receiving_party_kind]   # disambiguator for the union

  # ===== Aggregating rail (closes pool drift) ================================

  - name: PoolBalancingSouthToNorth
    source_role: SouthPool
    destination_role: NorthPool
    expected_net: 0
    origin: InternalInitiated
    metadata_keys: [bundled_transfer_type, business_day]
    aggregating: true
    cadence: intraday-2h
    bundles_activity:
      # Leg-scoped: just these legs of MerchantSettlementCycle
      - MerchantSettlementCycle.SubledgerCharge
      - MerchantSettlementCycle.SubledgerRefund
      - MerchantSettlementCycle.SettlementClose

# ---- Transfer template ------------------------------------------------------
transfer_templates:
  - name: MerchantSettlementCycle
    expected_net: 0
    transfer_key: [merchant_id, settlement_period]
    completion: metadata.settlement_period_end
    leg_rails:
      - SubledgerCharge
      - SubledgerRefund
      - SettlementClose

# ---- Chains -----------------------------------------------------------------
chains:
  # Exactly one payout vehicle per settled merchant — multi-children
  # row encodes XOR alternation:
  - parent: MerchantSettlementCycle
    children:
      - MerchantPayoutACH
      - MerchantPayoutInternal

# ---- Limit schedules --------------------------------------------------------
limit_schedules:
  - parent_role: SouthPool
    cap: 5000.00       # per-customer daily charge cap
```

What this composes:
- **Charges and refunds** post as single-leg debits/credits to individual customer subledgers as they happen. Both fire as legs of the per-(merchant, settlement_period) shared Transfer. `merchant_id` and `settlement_period` are auto-derived as PostedRequirements via TransferKey; the integrator declares no extra requirements on them.
- At period end, **SettlementClose** fires once per merchant with the net amount and direction needed to bring the shared Transfer to `ExpectedNet=0`. L1 Conservation flags the Transfer if SettlementClose never fires; Timeliness flags it if any leg posts after the period's `settlement_period_end`.
- **PoolBalancingSouthToNorth** runs every 2 hours, sweeping the pool drift the single-leg activity creates. Leg-scoped `BundleSelector`s confine its sweep to MerchantSettlementCycle's leg postings only.
- After SettlementClose, **exactly one of** the two payout vehicles fires per settled merchant (XOR group `PayoutVehicle`):
  - **MerchantPayoutACH** — outbound ACH; per-leg Origin distinguishes the internal merchant-debit (`InternalInitiated`) from the external bank landing (`ExternalForcePosted`). Requires a bank trace number (`external_reference`) before it can be Posted.
  - **MerchantPayoutInternal** — same-system payout to either another merchant OR a customer subledger (union destination role). The integrator's ETL must tag `receiving_party_kind` so the destination role resolves unambiguously.
- **Aging watches** catch operational failures distinctly from structural ones: `MaxPendingAge` flags ETL stuck-Pending; `MaxUnbundledAge` flags bundler-stuck-Posted. Both are operational health checks, not structural exceptions.
- **Auto-derived PostedRequirements** ensure structural integrity: TransferKey fields can't be NULL on leg postings; `parent_transfer_id` can't be NULL on the singleton-children child of a Chain row. Integrators add their own (e.g., `external_reference` on the ACH payout, `receiving_party_kind` on the internal payout) for domain-specific completeness.

---

# Layer 3 — Applications

## Purpose

LAYER 3 is a set of dashboard applications, each answering one **question shape** the L1 primitives and L2 instance can produce. The shipped apps are deliberately small and orthogonal: each answers a question the others cannot, so the user reaches for a specific app based on the shape of the question. Adding a fifth shipped app should require justifying that no existing app's stepback already covers it.

## Question shapes

| App | Question shape | Primary audience | L1 primitives leaned on |
|---|---|---|---|
| **L1 Reconciliation Dashboard** | Are the institution's L1 invariants holding right now? Where are they breaking? | Accountant | `Drift`, `LedgerDrift`, `OutboundFlow` (limit breach), `Age` (stuck pending / unbundled), `Status`, `Supersedes` |
| **L2 Flow Tracing** | Did this transfer (or transfer type) post the way L2 says it should? | Accountant + Integration Engineer | `CurrentTransaction`, `NetOfTransfer`, `PostedRequirements`, `Origin` |
| **Investigation** | What's flowing between accounts, and which flows are anomalous? | Accountant + Third-party (compliance) | `CurrentTransaction` aggregated by (source Account, target Account); pair-rolling statistics; recursive walk over `Transfer.Parent` |
| **Executives** | How large is this institution's activity? Account counts, money moved, period-over-period totals. | Third-party + Business Analyst | `CurrentTransaction` aggregated by (period, dimension); `Account` counts |

## Per-app stepbacks

### L1 Reconciliation Dashboard
Operational integrity at the level of L1 invariants. Every sheet maps to one or more L1 SHOULD-constraints — drift, overdraft, limit breach, expected-EOD-balance, stuck pending, stuck unbundled, supersession audit. The accountant scans today's exception count, drills into the offending row, and routes it to whoever owns the upstream feed. Configured by exactly one L2 instance: feed it `sasquatch_ar.yaml`, get a Sasquatch dashboard; feed it `cascadia.yaml`, get a Cascadia dashboard.

### L2 Flow Tracing
Operational integrity at the level of L2-declared transfer flows. Where L1 asks "is the math right?", L2 FT asks "did the transfer happen the way the institution said it would?" — every Transfer should match a declared Rail, every leg should land on the role the Rail names, every PostedRequirement should be satisfied within the declared Duration. The accountant uses it to triage failed transfers; the integration engineer uses it to validate that a newly-declared Rail actually fires.

### Investigation
Forensic / network analysis. Where L1 and L2 FT ask integrity questions about individual transactions and transfers, Investigation steps back to **accounts and the flows between them** and asks pattern questions: which counterparties does this account talk to? Which pairs are moving anomalous volume relative to their baseline? What chain of transfers connects two accounts? The compliance / AML stakeholder is the primary user; the accountant reaches for it when an L1 exception pattern hints at a broader story (e.g., a single account driving multiple drift events across days).

### Executives
Aggregate scope and scale. Steps further back than Investigation — not "are flows anomalous" but "how large is the institution". Account counts by role, transfer volume by type and period, money moved by counterparty class. The third-party stakeholder (board, regulator, executive sponsor) is the primary user; the business analyst uses it as the headline view when onboarding a new institution.

## What L3 is not

- **Not a query interface.** L3 apps answer fixed question shapes; they don't let the user write arbitrary queries. Integrators who need free-form querying go to the database directly — the apps are scoped to "the questions this institution should be asking every day".
- **Not customer-extended without code.** Adding a sheet to a shipped app means editing the app's `app.py`. L2 cannot add or hide sheets — the app structure is fixed across institutions on purpose, so training and documentation transfer cleanly between deployments.
- **Not where institution-specific quirks live.** Quirks belong in L2 (declare a custom TransferType / Rail / role; the shipped apps will surface the resulting transactions and exceptions automatically). If a customer needs a question shape no shipped app covers, the path is "build a custom app on L1 primitives", not "fork the shipped apps".

---

## Deliberately not in v1

- **Scope predicates.** Earlier drafts considered named groups of accounts/types for scoping L1 constraints. With Roles + per-account typed L1 fields (ExpectedEODBalance, etc.), scope predicates aren't needed in v1. Revisit if a real integrator needs to express something the typed fields can't.
- **Failure category catalogue.** Failure shapes (Stuck, Drift, OutOfBounds, etc.) are scenario-declaration concerns, not L2 primitive concerns; they live in a sibling document.
- **Time-varying limits.** Limit Schedules are time-invariant in v1. Per-day or per-window caps await a real integrator requirement.
- **Cross-instance JOINs.** Two L2 instances coexist via prefixing but cannot be queried together. If federated analytics across instances is needed, that's a higher-layer concern.
- **Bidirectional aggregating rails.** Aggregating rails whose net direction varies day-to-day are modeled as two separate Rails (one per direction). A unified bidirectional shape is deferred until the two-rail pattern proves cumbersome at scale.
