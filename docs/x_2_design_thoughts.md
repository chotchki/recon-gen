# Research Spikes

## Client side Interaction Framework: - Proven
- HTMX with some extensions

## Client Side Logging: - Proven
- This should be for dev/testing only
- Check out https://github.com/chotchki/skylander-portal-controller/blob/main/phone/src/dev_log.rs and https://github.com/chotchki/skylander-portal-controller/blob/main/crates/server/src/http.rs#L2131
  - In this project it allows use to run a server that auto emits what client is doing as part of its logging.
  - An htmx event forwarder may be extremely useful - https://htmx.org/docs/#events


## Diagrams
- D3js wrapped by htmx
- sankey interaction and testable
- can d3js replace graphviz? yes, maybe

## CSS - Tailwindcss?
- can it style d3js too? yes


# Overall Application Flow design thoughts for post phase X
- quicksight-gen still the core binary

- There is an input -> configs -> output flow still here
  - Input Options
    - You can still hand edit a yaml
    - or App 1 is the yaml/etl helper (X.4/X.5)
  - Configs
    - Primary: The L2 Shape, serialized to the YAML and STRONGLY validated still is the anchor for all of this
    - Second: Environment Specific data, the config.yaml - still hand created
  - Output Options (quicksight-gen can apply for you)
    - Schema for your chosen database (Postgres/Oracle/sqlite X.3)
    - Demo Data from the L2 Shape (sized based on your)
    - Materialized View / Data Refresh (Data refresh for sqlite since it doesn't have materialized views, truncate and select into should be fine and keep feature parity)
    - View Layer
      - Quicksight JSON
      - MkDocs for documenation (for static hosting or portable file)
      - Audit PDF
      - App 2 (for hosting or running locally)
        - Feature partity with quicksight, minus the bugs
        - MkDocs embedded

## Personas / Users
- Integrator, did I design my yaml right?
  - Runs 100% local with the sqlite backend for quick iteration
- ETL engineer
  - did I load all the data I expected to?
  - Run against a remote database to ensure the data load process still works right

- Both users will want to be able to see the dashboards locally or in quicksight to make sure things are working properly
- Final hosting may be using the App 2 or Quicksight depending on needs

## Shared common
- /dev_log (POST)
  - use to understand client actions vs the server in testing

## App 1 - UI/UX

### Yaml Builder
- A page that at the top shows a force directed view of the yaml and all the relationships
  - clicking an item on the force directed view, hides everything not directly connected to the item
- Underneath a set of filter toggles allows the showing or hiding of the the categories of the L2 setup
  - There needs to be a reset filters button
- Underneath is a bunch of cards/rows allowing the editing of each L2 item.
  - Hitting save on a item will reload the list with cascading updates.
    - For example if you rename an an account template, the linked the values should update too
  - Cascade mechanism: PUT the entity, server applies + computes ripple, response always returns the new entity body. When the change rippled, the response ALSO emits `HX-Trigger: l2-cascade-reload`. Client-side the L2-shape view + force-directed canvas listen for that event and `hx-get` themselves. No client logic to compute the cascade; the server owns the rewrite, response semantics drive refresh scope.

### ETL
- Provides two data loading paths:
  - Build up a list of SQLs to copy data into the core tables, saved to a separate `etl.yaml` file (NOT `config.yaml` — that stays env-only per the V.1.b allowlist; ETL is its own concern)
  - OR can run the data generator side to load synthetic data
- Can load the core tables repeatedly up to a defined date. (This is to allow simulations of business over time)
- Upon loading, can view the force directed yaml for highlights of data coverage. aka is there data or not that meets these definitions loaded

## Shared primitive: force-directed visual
The d3-force visual we spiked under X.2 serves THREE surfaces:
- App 2 dashboard visual (a sheet visual like Sankey / KPI / etc.)
- App 1 L2 editor canvas (top-of-page topology + click-to-filter)
- App 1 ETL data-coverage overlay (color/saturation by whether rows exist for that primitive)

One renderer, three contexts. The visual primitive is in `common/tree/visuals.py` already; phase.1 wires the data overlays per surface.

### Force Directed thoughts
- Getting this right can be challenging, being able to tweak easily will be key
- I don't know what knobs we have to play with, would like to know when its getting hard (not saying we shouldn't end there but generally I find fighting the library is a code smell until we know what we're doing)
- Edges of the graph should be merged only if their direction matches
- Merged Edges should merge their text boxes
- We should try to spread to fill the available space.
- Parents

## CLI surface additions
- `data apply --end-date <ISO date>` — emit-time filter, drops records past the cutoff (does NOT change the seed generator; just truncates the timeline at write). Trivial in `emit_full_seed`. Enables timeline-truncation for "what did the data look like as of X" scenarios.
- (Future, not in scope yet) `data apply --density-factor <N>` — multiplier on row-count for perf testing at scale. Punted to its own phase when needed.

## App 1 - HTTP REST Design (WIP)
UI/UX:
- /l2_shape
  - GET produces the data to drive a force directed view of the yaml and all the relationships
    - use query params to show/hide and highlight
  - /accounts
    - CRUD Interface
  - /rails
  - /chains
  - /transfer_templates
  - /theme

## App 2 - HTTP REST Design (Almost All HTTP GETs)
- /dashboards
  - show list of dashboards
- /dashboards/:id
  - redirect to first sheet?
  - /sheets
    - redirect to first sheet?
  - /sheets/:id
    - provide the sheet content
    - /visuals
      - 404, the sheets provide the specific visual (unsure if we should do this differently)
    - /visuals/:id
      - content for a given visual
    - /visuals/:id/data
      - (unsure if this is needed to support d3js refresh)

## HTTP Query Params Design (properly encoded)
- Meta
  - the query section is used to pass parameters for any controls on a given sheet/visual
  - if the parameters are NOT provided a default is used (hopefully the L2 already encodes it)
  - unknown parameters for a target should result in a HTTP error to help with easy error reporting
- Examples
  - Pagination
    - page_offset = 0
    - page_size = 50
  - Date Filter (ISO8601 based)
    - start_date = 2026-05-05T00:00:00-12:00
    - end_date = 2026-05-05T23:59:59-12:00
  - Column Operations (probably needs to be additive)
    - sort_column=id:desc,sort_column=name,asc
    - filter=id:equals:Foo
