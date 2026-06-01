# BX SQLite matview perf — base_tx_rows=248,913

- L2 yaml: `tests/l2/sasquatch_pr.yaml`
- density factor: 1.00x default
- base `<prefix>_transactions` rows: 248,913
- base `<prefix>_daily_balances` rows: 7,626
- seed apply wallclock: 12669 ms
- **bundled refresh wallclock (cold; integrator-visible): 29022 ms**
- per-matview selective-rebuild total (warm; hot-spot proxy): 9935 ms
  - per-matview numbers are *selective rebuilds* against a pre-built matview graph — they identify which matview's CREATE AS SELECT dominates, but they don't sum to the bundled cold cost (page cache + index-rebuild cascade is in the cold delta).
- SQLite DB file size: 379.9 MB

| matview | input rows | output rows | refresh wallclock (ms) |
| --- | ---: | ---: | ---: |
| `current_transactions` | 248,913 | 248866 | 1001.3 |
| `current_daily_balances` | 248,913 | 7626 | 12.1 |
| `computed_subledger_balance` | 248,913 | 5320 | 7043.4 |
| `computed_ledger_balance` | 248,913 | 182 | 13.1 |
| `drift` | 248,913 | 10 | 4.1 |
| `ledger_drift` | 248,913 | 6 | 2.4 |
| `overdraft` | 248,913 | 3713 | 5.3 |
| `expected_eod_balance_breach` | 248,913 | 0 | 1.7 |
| `limit_breach` | 248,913 | 3 | 460.8 |
| `stuck_pending` | 248,913 | 2 | 2.7 |
| `stuck_unbundled` | 248,913 | 251 | 86.0 |
| `chain_parent_disagreement` | 248,913 | 1 | 2.7 |
| `xor_group_violation` | 248,913 | 6 | 43.8 |
| `transfer_parents` | 248,913 | 10061 | 16.0 |
| `fan_in_disagreement` | 248,913 | 4 | 3.5 |
| `multi_xor_violation` | 248,913 | 8 | 119.2 |
| `daily_statement_summary` | 248,913 | 7626 | 379.5 |
| `l1_exceptions` | 248,913 | 4004 | 5.9 |
| `inv_pair_rolling_anomalies` | 248,913 | 12352 | 267.4 |
| `inv_money_trail_edges` | 248,913 | 94880 | 463.6 |
