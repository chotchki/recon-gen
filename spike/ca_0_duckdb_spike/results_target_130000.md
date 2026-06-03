# CA.0 DuckDB matview perf — base_tx_rows=127,554

- L2 yaml: `tests/l2/sasquatch_pr.yaml`
- density factor: 1.00x default
- baseline window days: 90
- base `<prefix>_transactions` rows: 127,554
- base `<prefix>_daily_balances` rows: 3,928
- seed apply wallclock: 59865 ms
- **bundled refresh wallclock (cold; integrator-visible): 1049 ms**
- DuckDB file size: 101.5 MB

| matview | output rows |
| --- | ---: |
| `current_transactions` | 127507 |
| `current_daily_balances` | 3928 |
| `computed_subledger_balance` | 2740 |
| `computed_ledger_balance` | 96 |
| `drift` | 10 |
| `ledger_drift` | 6 |
| `overdraft` | 1904 |
| `expected_eod_balance_breach` | 0 |
| `limit_breach` | 3 |
| `stuck_pending` | 2 |
| `stuck_unbundled` | 127 |
| `chain_parent_disagreement` | 1 |
| `xor_group_violation` | 6 |
| `transfer_parents` | 5226 |
| `fan_in_disagreement` | 4 |
| `multi_xor_violation` | 8 |
| `daily_statement_summary` | 3928 |
| `l1_exceptions` | 2071 |
| `inv_pair_rolling_anomalies` | 6362 |
| `inv_money_trail_edges` | 48688 |
