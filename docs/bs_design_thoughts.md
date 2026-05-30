# Refocusing on the Studio/Dashboards thoughts

## Usage Models
- ETL Development / Training Models
  - App2(Studio+Dashboards+Docs)
    - Training Data or ETL Populated
    - Oracle/Postgres/Sqlite Database Backends
  - Quicksight
    - Dashboards only
    - Training Data or ETL Data
    - Oracle or Postgres Database Backend
- Production Deployment Models
  - Quicksight
    - Dashboards only
    - ETL Data
    - Oracle or Postgres Database Backend
  - App2+Dashboards
    - Dashboards + Docs
    - ETL Data
    - Oracle/Postgres/Sqlite Database Backends

## User Personas
- Integrator: Editor of the YAML to define the L2 shape
- ETL Engineer: Needs to implement the datafeed and understand if the data they are providing aligns with the L2 shape
- Trainer: Needs to be able to show okay and violations and how that changes the dashboards.
- End Users: Use the dashboards to learn OR operate

## Known Gaps
- Unified Top Level Navigation <- Gap
  - If only one of the three parts is deployed, no top level nav bar should be present. If 2 or more are deployed, then we need a top nav.
  - The studio part of the application needs clearer navigation and modes <- Gap

  - Studio Part - /studio
    - Meta: these three modes are not clearly differentiated <- Gap
    - L2 Editor -> Diagram Viewer + YAML Editor
      - Still can't completely dogfood the YAML <- Gap
    - ETL Support -> Execute an external ETL process and diagnose how much of the YAML is covered
      - Needs to find its own identity <- Gap
      - Ideas:
          - Let's focus in on a part of the yaml
            - what is this part needing to see on balances and transaction to match?
          - execute the etl process
            - how much of the shape landed?
              - rails/templates/metadata/chains
          - exception triage
            - this row didn't match but if we edited the yaml it would
              - for example, account_role matched but not rail
              - maybe a parts matched / not matched view?
    - Training Support -> Generate Scenerios that have L1/L2 errors, deploy the scenarios by ETL execution or straight test data generation
      - Only L1 plants show right now, we should be showing L2 also <- Gap
  - Dashboards Part - /dashboards
    - L1
    - L2
    - Investigation
    - Executive
  - Documentation - /docs
    - Existing mkDocs training site
    - Needs a revamp

- Need to better define how the yaml is used and its relationship to the _kv table
  - The L2-YAML is still the authoritative source of truth for the shape.
    - The _kv is derived from it

  - Fundamentally the _kv table provides a runtime accessible source of the L2 shape.
    - Without that, we've effectively indirectly encoded the L2 into the various views/mat views/controls/etc
    - We could simplify the sql generation to be more static (I think) <- Gap/Opportunity

  - An open question would be should the /docs also depend on _kv?
