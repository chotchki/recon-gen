# BX SQLite matview perf — base_tx_rows=127,554

- L2 yaml: `tests/l2/sasquatch_pr.yaml`
- density factor: 1.00x default
- base `<prefix>_transactions` rows: 127,554
- base `<prefix>_daily_balances` rows: 3,928
- seed apply wallclock: 6296 ms
- **bundled refresh wallclock (cold; integrator-visible): 9532 ms**
- per-matview selective-rebuild total (warm; hot-spot proxy): 1425 ms
  - per-matview numbers are *selective rebuilds* against a pre-built matview graph — they identify which matview's CREATE AS SELECT dominates, but they don't sum to the bundled cold cost (page cache + index-rebuild cascade is in the cold delta).
- SQLite DB file size: 195.6 MB

| matview | input rows | output rows | refresh wallclock (ms) |
| --- | ---: | ---: | ---: |
| `current_transactions` | 127,554 | 127507 | 490.4 |
| `current_daily_balances` | 127,554 | 3928 | 7.3 |
| `computed_subledger_balance` | 127,554 | 2740 | 5.5 |
| `computed_ledger_balance` | 127,554 | 96 | 5.6 |
| `drift` | 127,554 | 2285 | 6.0 |
| `ledger_drift` | 127,554 | 6 | 2.3 |
| `overdraft` | 127,554 | 1904 | 3.6 |
| `expected_eod_balance_breach` | 127,554 | 0 | 1.5 |
| `limit_breach` | 127,554 | 3 | 229.2 |
| `stuck_pending` | 127,554 | 2 | 2.6 |
| `stuck_unbundled` | 127,554 | 127 | 43.1 |
| `chain_parent_disagreement` | 127,554 | 1 | 2.5 |
| `xor_group_violation` | 127,554 | 6 | 23.1 |
| `transfer_parents` | 127,554 | 5226 | 9.1 |
| `fan_in_disagreement` | 127,554 | 4 | 3.0 |
| `multi_xor_violation` | 127,554 | 8 | 60.5 |
| `daily_statement_summary` | 127,554 | 3928 | 166.2 |
| `l1_exceptions` | 127,554 | 4346 | 6.0 |
| `inv_pair_rolling_anomalies` | 127,554 | 6362 | 131.3 |
| `inv_money_trail_edges` | 127,554 | 48688 | 226.2 |
