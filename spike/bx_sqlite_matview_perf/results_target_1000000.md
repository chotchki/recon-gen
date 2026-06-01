# BX SQLite matview perf — base_tx_rows=986,433

- L2 yaml: `tests/l2/sasquatch_pr.yaml`
- density factor: 1.00x default
- base `<prefix>_transactions` rows: 986,433
- base `<prefix>_daily_balances` rows: 30,373
- seed apply wallclock: 50956 ms
- **bundled refresh wallclock (cold; integrator-visible): 252906 ms**
- per-matview selective-rebuild total (warm; hot-spot proxy): 146002 ms
  - per-matview numbers are *selective rebuilds* against a pre-built matview graph — they identify which matview's CREATE AS SELECT dominates, but they don't sum to the bundled cold cost (page cache + index-rebuild cascade is in the cold delta).
- SQLite DB file size: 1505.9 MB

| matview | input rows | output rows | refresh wallclock (ms) |
| --- | ---: | ---: | ---: |
| `current_transactions` | 986,433 | 986386 | 4165.4 |
| `current_daily_balances` | 986,433 | 30373 | 45.9 |
| `computed_subledger_balance` | 986,433 | 21190 | 132711.8 |
| `computed_ledger_balance` | 986,433 | 711 | 126.3 |
| `drift` | 986,433 | 10 | 10.8 |
| `ledger_drift` | 986,433 | 6 | 5.0 |
| `overdraft` | 986,433 | 15330 | 15.5 |
| `expected_eod_balance_breach` | 986,433 | 0 | 2.7 |
| `limit_breach` | 986,433 | 4 | 2044.9 |
| `stuck_pending` | 986,433 | 2 | 3.0 |
| `stuck_unbundled` | 986,433 | 1005 | 345.3 |
| `chain_parent_disagreement` | 986,433 | 1 | 4.0 |
| `xor_group_violation` | 986,433 | 6 | 175.7 |
| `transfer_parents` | 986,433 | 39850 | 75.2 |
| `fan_in_disagreement` | 986,433 | 4 | 6.9 |
| `multi_xor_violation` | 986,433 | 8 | 478.1 |
| `daily_statement_summary` | 986,433 | 30373 | 2641.3 |
| `l1_exceptions` | 986,433 | 16376 | 16.5 |
| `inv_pair_rolling_anomalies` | 986,433 | 48765 | 1179.5 |
| `inv_money_trail_edges` | 986,433 | 375278 | 1948.5 |
