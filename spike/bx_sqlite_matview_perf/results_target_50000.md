# BX SQLite matview perf — base_tx_rows=127,554

- L2 yaml: `tests/l2/sasquatch_pr.yaml`
- density factor: 1.00x default
- base `<prefix>_transactions` rows: 127,554
- base `<prefix>_daily_balances` rows: 3,928
- seed apply wallclock: 6355 ms
- **bundled refresh wallclock (cold; integrator-visible): 11323 ms**
- per-matview selective-rebuild total (warm; hot-spot proxy): 3122 ms
  - per-matview numbers are *selective rebuilds* against a pre-built matview graph — they identify which matview's CREATE AS SELECT dominates, but they don't sum to the bundled cold cost (page cache + index-rebuild cascade is in the cold delta).
- SQLite DB file size: 194.7 MB

| matview | input rows | output rows | refresh wallclock (ms) |
| --- | ---: | ---: | ---: |
| `current_transactions` | 127,554 | 127507 | 487.7 |
| `current_daily_balances` | 127,554 | 3928 | 7.2 |
| `computed_subledger_balance` | 127,554 | 2740 | 1704.3 |
| `computed_ledger_balance` | 127,554 | 96 | 6.0 |
| `drift` | 127,554 | 10 | 3.3 |
| `ledger_drift` | 127,554 | 6 | 2.2 |
| `overdraft` | 127,554 | 1904 | 3.5 |
| `expected_eod_balance_breach` | 127,554 | 0 | 1.4 |
| `limit_breach` | 127,554 | 3 | 231.7 |
| `stuck_pending` | 127,554 | 2 | 2.7 |
| `stuck_unbundled` | 127,554 | 127 | 45.4 |
| `chain_parent_disagreement` | 127,554 | 1 | 2.5 |
| `xor_group_violation` | 127,554 | 6 | 23.6 |
| `transfer_parents` | 127,554 | 5226 | 9.2 |
| `fan_in_disagreement` | 127,554 | 4 | 3.0 |
| `multi_xor_violation` | 127,554 | 8 | 58.7 |
| `daily_statement_summary` | 127,554 | 3928 | 164.1 |
| `l1_exceptions` | 127,554 | 2071 | 4.1 |
| `inv_pair_rolling_anomalies` | 127,554 | 6362 | 130.3 |
| `inv_money_trail_edges` | 127,554 | 48688 | 231.1 |
