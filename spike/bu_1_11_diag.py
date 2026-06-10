"""BU.1.11 diagnostic — analyze high-z rows from inv_pair_rolling_anomalies
under TRAINER_CLEAN + spec_example.yaml + fresh DuckDB."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from recon_gen.common.config import Config
from recon_gen.common.db import connect_demo_db, execute_script, make_demo_database_url
from recon_gen.common.l2.deploy_pipeline import run_deploy_pipeline
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.pipeline_overlays import TRAINER_CLEAN
from recon_gen.common.l2.schema import emit_schema
from recon_gen.common.sql.dialect import Dialect
from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "demo.duckdb"
        cfg = Config(
            aws_account_id="111122223333",
            aws_region="us-east-1",
            deployment_name="recon-spec-example",
            db_table_prefix=DEFAULT_PREFIX,
            datasource_arn="arn:aws:quicksight:us-east-1:1:datasource/x",
            demo_database_url=make_demo_database_url(Dialect.DUCKDB, db_path),
            dialect=Dialect.DUCKDB,
        )
        inst = load_instance("tests/l2/spec_example.yaml")

        # Emit schema
        sql = emit_schema(inst, prefix=cfg.db_table_prefix, dialect=cfg.dialect)
        conn = connect_demo_db(cfg)
        cur = conn.cursor()
        execute_script(cur, sql, dialect=cfg.dialect)
        conn.commit()
        cur.close()
        conn.close()

        # Run TRAINER_CLEAN
        asyncio.run(
            run_deploy_pipeline(cfg, inst, dev_log=None, overlays=TRAINER_CLEAN)
        )

        # Reconnect for diagnostics
        conn = connect_demo_db(cfg)
        cur = conn.cursor()

        prefix = cfg.db_table_prefix
        matview = f"{prefix}_inv_pair_rolling_anomalies"
        txn_table = f"{prefix}_transactions"

        # ---- Sanity: total rows, max z ----
        cur.execute(f"SELECT COUNT(*), MAX(z_score), MIN(z_score), AVG(z_score) FROM {matview}")
        total, max_z, min_z, avg_z = cur.fetchall()[0]
        print("=== MATVIEW SUMMARY ===")
        print(f"total rows: {total}")
        print(f"max z_score: {max_z}")
        print(f"min z_score: {min_z}")
        print(f"avg z_score: {avg_z}")

        # ---- pop_mean, pop_stddev (constants in matview) ----
        cur.execute(f"SELECT DISTINCT pop_mean, pop_stddev FROM {matview} LIMIT 5")
        print(f"\n=== POPULATION STATS ===")
        for r in cur.fetchall():
            print(f"pop_mean={r[0]}, pop_stddev={r[1]}")

        # ---- Distinct (sender,recipient) pairs in matview ----
        cur.execute(
            f"SELECT COUNT(DISTINCT (sender_account_id, recipient_account_id)) "
            f"FROM {matview}"
        )
        unique_pairs = cur.fetchall()[0][0]
        print(f"\n=== UNIQUE PAIRS IN MATVIEW: {unique_pairs} ===")

        # ---- (a) High-z rows with full context ----
        cur.execute(
            f"""
            SELECT
                sender_account_name,
                sender_account_type,
                recipient_account_name,
                recipient_account_type,
                window_start,
                window_end,
                window_sum,
                transfer_count,
                z_score
            FROM {matview}
            WHERE z_score >= 4
            ORDER BY z_score DESC
            """
        )
        high_z_rows = cur.fetchall()
        print(f"\n=== HIGH-Z ROWS (z >= 4): {len(high_z_rows)} rows ===")
        print(
            "sender_name | sender_type | recipient_name | recipient_type | "
            "win_start | win_end | window_sum | xfer_count | z_score"
        )
        for r in high_z_rows:
            print(" | ".join(str(x) for x in r))

        # ---- (b)/(c) Per-pair traffic in baseline window ----
        # Pull distinct (sender,recipient) for the high-z rows
        cur.execute(
            f"""
            SELECT DISTINCT sender_account_id, recipient_account_id
            FROM {matview}
            WHERE z_score >= 4
            """
        )
        high_z_pair_ids = cur.fetchall()
        print(f"\n=== DISTINCT HIGH-Z PAIRS (z>=4): {len(high_z_pair_ids)} ===")

        # For each, count distinct business days + total volume in the 90-day baseline
        print(
            "\n=== PER-PAIR TRAFFIC (across baseline window) ===\n"
            "sender_id | recipient_id | n_days_with_traffic | total_volume_money | "
            "max_day_volume | distinct_transfer_ids"
        )
        per_pair_stats: list[tuple] = []
        for sender_id, recipient_id in high_z_pair_ids:
            cur.execute(
                f"""
                SELECT
                  COUNT(DISTINCT CAST(recipient.posting AS DATE)) AS n_days,
                  SUM(recipient.amount_money)                     AS total_vol,
                  MAX(daily.day_sum)                              AS max_day_vol,
                  COUNT(DISTINCT recipient.transfer_id)           AS n_transfers
                FROM {txn_table} recipient
                JOIN {txn_table} sender
                  ON sender.transfer_id = recipient.transfer_id
                 AND sender.amount_money < 0
                JOIN (
                  SELECT CAST(r2.posting AS DATE) AS d,
                         SUM(r2.amount_money) AS day_sum
                  FROM {txn_table} r2
                  JOIN {txn_table} s2
                    ON s2.transfer_id = r2.transfer_id
                   AND s2.amount_money < 0
                  WHERE r2.amount_money > 0
                    AND r2.status = 'Posted'
                    AND s2.status = 'Posted'
                    AND r2.account_id = ?
                    AND s2.account_id = ?
                  GROUP BY CAST(r2.posting AS DATE)
                ) daily ON daily.d = CAST(recipient.posting AS DATE)
                WHERE recipient.amount_money > 0
                  AND recipient.status = 'Posted'
                  AND sender.status = 'Posted'
                  AND recipient.account_id = ?
                  AND sender.account_id = ?
                """,
                [recipient_id, sender_id, recipient_id, sender_id],
            )
            row = cur.fetchall()[0]
            per_pair_stats.append((sender_id, recipient_id, *row))
            print(
                f"{sender_id} | {recipient_id} | {row[0]} | {row[1]} | "
                f"{row[2]} | {row[3]}"
            )

        # ---- (d) 10 BUSIEST pairs (most days with traffic) + their max z ----
        cur.execute(
            f"""
            WITH pair_legs AS (
                SELECT
                  sender.account_id    AS sender_account_id,
                  recipient.account_id AS recipient_account_id,
                  CAST(recipient.posting AS DATE) AS d,
                  recipient.amount_money AS amt
                FROM {txn_table} recipient
                JOIN {txn_table} sender
                  ON sender.transfer_id = recipient.transfer_id
                 AND sender.amount_money < 0
                WHERE recipient.amount_money > 0
                  AND recipient.status = 'Posted'
                  AND sender.status = 'Posted'
                  AND recipient.account_scope = 'internal'
                  AND recipient.account_parent_role IS NOT NULL
            ),
            pair_summary AS (
                SELECT sender_account_id, recipient_account_id,
                       COUNT(DISTINCT d) AS n_days,
                       SUM(amt) AS total_vol
                FROM pair_legs
                GROUP BY sender_account_id, recipient_account_id
            )
            SELECT
              ps.sender_account_id,
              ps.recipient_account_id,
              ps.n_days,
              ps.total_vol,
              (SELECT MAX(z_score) FROM {matview} mv
                 WHERE mv.sender_account_id = ps.sender_account_id
                   AND mv.recipient_account_id = ps.recipient_account_id) AS max_z
            FROM pair_summary ps
            ORDER BY ps.n_days DESC, ps.total_vol DESC
            LIMIT 10
            """
        )
        print(f"\n=== TOP 10 BUSIEST PAIRS (for comparison) ===")
        print("sender_id | recipient_id | n_days | total_vol | max_z_score")
        for r in cur.fetchall():
            print(" | ".join(str(x) for x in r))

        # ---- Distribution of n_days per pair across ALL pairs in matview ----
        cur.execute(
            f"""
            WITH pair_legs AS (
                SELECT
                  sender.account_id    AS sender_account_id,
                  recipient.account_id AS recipient_account_id,
                  CAST(recipient.posting AS DATE) AS d
                FROM {txn_table} recipient
                JOIN {txn_table} sender
                  ON sender.transfer_id = recipient.transfer_id
                 AND sender.amount_money < 0
                WHERE recipient.amount_money > 0
                  AND recipient.status = 'Posted'
                  AND sender.status = 'Posted'
                  AND recipient.account_scope = 'internal'
                  AND recipient.account_parent_role IS NOT NULL
            ),
            pair_summary AS (
                SELECT sender_account_id, recipient_account_id,
                       COUNT(DISTINCT d) AS n_days
                FROM pair_legs
                GROUP BY sender_account_id, recipient_account_id
            )
            SELECT
              SUM(CASE WHEN n_days = 1 THEN 1 ELSE 0 END) AS pairs_1day,
              SUM(CASE WHEN n_days = 2 THEN 1 ELSE 0 END) AS pairs_2day,
              SUM(CASE WHEN n_days BETWEEN 3 AND 5 THEN 1 ELSE 0 END) AS pairs_3_5,
              SUM(CASE WHEN n_days BETWEEN 6 AND 10 THEN 1 ELSE 0 END) AS pairs_6_10,
              SUM(CASE WHEN n_days > 10 THEN 1 ELSE 0 END) AS pairs_gt_10,
              COUNT(*) AS total_pairs,
              AVG(n_days) AS avg_days_per_pair,
              MAX(n_days) AS max_days_per_pair
            FROM pair_summary
            """
        )
        row = cur.fetchall()[0]
        print(f"\n=== PAIR n_days HISTOGRAM ===")
        print(
            f"1 day: {row[0]} | 2 days: {row[1]} | 3-5: {row[2]} | "
            f"6-10: {row[3]} | >10: {row[4]} | total: {row[5]} | "
            f"avg: {row[6]} | max: {row[7]}"
        )

        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
