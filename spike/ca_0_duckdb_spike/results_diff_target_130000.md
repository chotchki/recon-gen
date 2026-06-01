# CA.0 DuckDB ↔ SQLite 3-way diff — target 130,000 base tx

- L2 yaml: `tests/l2/spec_example.yaml`
- DuckDB base tx rows: 12,844; SQLite base tx rows: 12,844

## Matview row-count parity

| matview | DuckDB rows | SQLite rows | Δ |
| --- | ---: | ---: | ---: |
| `current_transactions` | 12801 | 12801 | 0 |
| `current_daily_balances` | 2381 | 2381 | 0 |
| `computed_subledger_balance` | 2012 | 2012 | 0 |
| `computed_ledger_balance` | 5 | 5 | 0 |
| `drift` | 10 | 10 | 0 |
| `ledger_drift` | 5 | 5 | 0 |
| `overdraft` | 813 | 813 | 0 |
| `expected_eod_balance_breach` | 0 | 0 | 0 |
| `limit_breach` | 185 | 185 | 0 |
| `stuck_pending` | 2 | 2 | 0 |
| `stuck_unbundled` | 119 | 119 | 0 |
| `chain_parent_disagreement` | 0 | 0 | 0 |
| `xor_group_violation` | 2 | 2 | 0 |
| `transfer_parents` | 466 | 466 | 0 |
| `fan_in_disagreement` | 56 | 56 | 0 |
| `multi_xor_violation` | 8 | 8 | 0 |
| `daily_statement_summary` | 2381 | 2381 | 0 |
| `l1_exceptions` | 1200 | 1200 | 0 |
| `inv_pair_rolling_anomalies` | 989 | 989 | 0 |
| `inv_money_trail_edges` | 6179 | 6179 | 0 |

## computed_subledger_balance row-by-row diff

- DuckDB rows: 2,012, SQLite rows: 2,012
- only-in-DuckDB keys: 0
- only-in-SQLite keys: 0
- value mismatches (|delta| > $0.005): 0
