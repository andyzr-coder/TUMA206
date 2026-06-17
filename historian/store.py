"""M3b - Historian.

Stores every tag snapshot in a SQLite database so the dashboard can draw trend
charts and the AI assistant can read recent history. Also logs alarm events and
can export the full history to CSV (for evidence collection in the demo).

Tables
------
samples : one row per update tick, with dedicated columns for the numeric tags
          plus a JSON blob of the full snapshot.
alarms  : one row each time the alarm code changes.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Dict, List, Optional

import config


class Historian:
    def __init__(self, db_path: str = config.DB_PATH) -> None:
        self.db_path = db_path
        # check_same_thread=False so the background engine thread and the
        # dashboard thread can share the connection (guarded by a lock).
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._last_alarm: Optional[int] = None
        self._create_tables()

    def _create_tables(self) -> None:
        numeric_cols = ", ".join(f"{tag} REAL" for tag in config.NUMERIC_TAGS)
        with self._lock:
            self._conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    {numeric_cols},
                    plc_state TEXT,
                    alarm_code INTEGER,
                    stage_state TEXT,
                    snapshot TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alarms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    alarm_code INTEGER,
                    label TEXT,
                    description TEXT
                )
                """
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    def record(self, snapshot: Dict) -> None:
        """Persist one tag snapshot and log alarm transitions."""
        ts = snapshot.get("ts", time.time())
        numeric_values = [snapshot.get(tag) for tag in config.NUMERIC_TAGS]
        placeholders = ", ".join(["?"] * (len(config.NUMERIC_TAGS) + 5))
        columns = ", ".join(
            ["ts", *config.NUMERIC_TAGS, "plc_state", "alarm_code",
             "stage_state", "snapshot"]
        )
        alarm_code = int(snapshot.get("alarm_code", config.ALARM_NONE))

        with self._lock:
            self._conn.execute(
                f"INSERT INTO samples ({columns}) VALUES ({placeholders})",
                [
                    ts,
                    *numeric_values,
                    snapshot.get("plc_state"),
                    alarm_code,
                    snapshot.get("stage_state"),
                    json.dumps(snapshot),
                ],
            )

            if alarm_code != self._last_alarm:
                self._conn.execute(
                    "INSERT INTO alarms (ts, alarm_code, label, description) "
                    "VALUES (?, ?, ?, ?)",
                    [
                        ts,
                        alarm_code,
                        config.ALARM_LABELS.get(alarm_code, str(alarm_code)),
                        config.ALARM_DESCRIPTIONS.get(alarm_code, ""),
                    ],
                )
                self._last_alarm = alarm_code

            self._conn.commit()

    # ------------------------------------------------------------------
    def recent(self, window_s: float = config.HISTORY_WINDOW_S) -> List[Dict]:
        """Return sample rows from the last ``window_s`` seconds (oldest first)."""
        since = time.time() - window_s
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM samples WHERE ts >= ? ORDER BY ts ASC",
                [since],
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_alarms(self, limit: int = 20) -> List[Dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM alarms ORDER BY ts DESC LIMIT ?",
                [limit],
            ).fetchall()
        return [dict(row) for row in rows]

    def export_csv(self, path: str = config.CSV_EXPORT_PATH) -> str:
        """Export all samples to a CSV file and return the path."""
        import csv

        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM samples ORDER BY ts ASC"
            ).fetchall()
        if not rows:
            return path
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
            writer.writeheader()
            for row in rows:
                writer.writerow(dict(row))
        return path

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM samples")
            self._conn.execute("DELETE FROM alarms")
            self._conn.commit()
        self._last_alarm = None

    def close(self) -> None:
        with self._lock:
            self._conn.close()
