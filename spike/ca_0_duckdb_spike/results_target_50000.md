# CA.0 DuckDB matview perf — base_tx_rows=12,844

- L2 yaml: `tests/l2/spec_example.yaml`
- density factor: 1.00x default
- baseline window days: 90
- base `<prefix>_transactions` rows: 12,844
- base `<prefix>_daily_balances` rows: 2,381
- seed apply wallclock: 6350 ms
- **bundled refresh wallclock (cold; integrator-visible): 182 ms**
- DuckDB file size: 9.4 MB

| matview | output rows |
| --- | ---: |
| `current_transactions` | 12801 |
| `current_daily_balances` | 2381 |
| `computed_subledger_balance` | 2012 |
| `computed_ledger_balance` | 5 |
| `drift` | 10 |
| `ledger_drift` | 5 |
| `overdraft` | 813 |
| `expected_eod_balance_breach` | 0 |
| `limit_breach` | 185 |
| `stuck_pending` | 2 |
| `stuck_unbundled` | 119 |
| `chain_parent_disagreement` | 0 |
| `xor_group_violation` | 2 |
| `transfer_parents` | 466 |
| `fan_in_disagreement` | 56 |
| `multi_xor_violation` | 8 |
| `daily_statement_summary` | 2381 |
| `l1_exceptions` | 1200 |
| `inv_pair_rolling_anomalies` | 989 |
| `inv_money_trail_edges` | 6179 |
