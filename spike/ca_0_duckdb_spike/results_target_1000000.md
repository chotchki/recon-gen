# CA.0 DuckDB matview perf — base_tx_rows=933,410

- L2 yaml: `tests/l2/sasquatch_pr.yaml`
- density factor: 1.00x default
- baseline window days: 666
- base `<prefix>_transactions` rows: 933,410
- base `<prefix>_daily_balances` rows: 28,696
- seed apply wallclock: 480717 ms
- **bundled refresh wallclock (cold; integrator-visible): 4602 ms**
- DuckDB file size: 613.7 MB

| matview | output rows |
| --- | ---: |
| `current_transactions` | 933363 |
| `current_daily_balances` | 28696 |
| `computed_subledger_balance` | 20020 |
| `computed_ledger_balance` | 672 |
| `drift` | 10 |
| `ledger_drift` | 6 |
| `overdraft` | 14473 |
| `expected_eod_balance_breach` | 0 |
| `limit_breach` | 4 |
| `stuck_pending` | 2 |
| `stuck_unbundled` | 951 |
| `chain_parent_disagreement` | 1 |
| `xor_group_violation` | 6 |
| `transfer_parents` | 37695 |
| `fan_in_disagreement` | 4 |
| `multi_xor_violation` | 8 |
| `daily_statement_summary` | 28696 |
| `l1_exceptions` | 15465 |
| `inv_pair_rolling_anomalies` | 46133 |
| `inv_money_trail_edges` | 355093 |
