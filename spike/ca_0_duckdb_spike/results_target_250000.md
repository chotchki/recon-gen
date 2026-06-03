# CA.0 DuckDB matview perf — base_tx_rows=233,236

- L2 yaml: `tests/l2/sasquatch_pr.yaml`
- density factor: 1.00x default
- baseline window days: 166
- base `<prefix>_transactions` rows: 233,236
- base `<prefix>_daily_balances` rows: 7,196
- seed apply wallclock: 109807 ms
- **bundled refresh wallclock (cold; integrator-visible): 1451 ms**
- DuckDB file size: 166.5 MB

| matview | output rows |
| --- | ---: |
| `current_transactions` | 233189 |
| `current_daily_balances` | 7196 |
| `computed_subledger_balance` | 5020 |
| `computed_ledger_balance` | 172 |
| `drift` | 10 |
| `ledger_drift` | 6 |
| `overdraft` | 3501 |
| `expected_eod_balance_breach` | 0 |
| `limit_breach` | 3 |
| `stuck_pending` | 2 |
| `stuck_unbundled` | 235 |
| `chain_parent_disagreement` | 1 |
| `xor_group_violation` | 6 |
| `transfer_parents` | 9415 |
| `fan_in_disagreement` | 4 |
| `multi_xor_violation` | 8 |
| `daily_statement_summary` | 7196 |
| `l1_exceptions` | 3776 |
| `inv_pair_rolling_anomalies` | 11578 |
| `inv_money_trail_edges` | 88925 |
