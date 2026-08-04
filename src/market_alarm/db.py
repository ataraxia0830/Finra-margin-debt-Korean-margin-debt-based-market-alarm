from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


SCHEMA = """
CREATE TABLE IF NOT EXISTS points (
  source TEXT NOT NULL,
  series TEXT NOT NULL,
  symbol TEXT NOT NULL DEFAULT '',
  ts TEXT NOT NULL,
  value REAL NOT NULL,
  meta_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (source, series, symbol, ts)
);
CREATE TABLE IF NOT EXISTS states (
  state_key TEXT PRIMARY KEY,
  state_value TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  details TEXT
);
"""


class Store:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
        # NOTE: opening the store must never delete rows.  ``data/market_alarm.sqlite3``
        # is the committed production history, and a migration that runs on every
        # single job would rewrite it with no way back.  Unparseable timestamps are
        # filtered on read in ``frame()`` instead, which fixes the same bug without
        # touching what is on disk.

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert_points(
        self,
        source: str,
        series: str,
        rows: Iterable[tuple[str, float, dict[str, Any] | None]],
        symbol: str = "",
    ) -> int:
        payload = [
            (
                source,
                series,
                symbol,
                ts,
                float(value),
                json.dumps(meta or {}, ensure_ascii=False, sort_keys=True),
            )
            for ts, value, meta in rows
        ]
        if not payload:
            return 0
        with self.connect() as conn:
            before = conn.total_changes
            conn.executemany(
                """
                INSERT INTO points(source, series, symbol, ts, value, meta_json)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, series, symbol, ts)
                DO UPDATE SET value=excluded.value, meta_json=excluded.meta_json
                """,
                payload,
            )
            return conn.total_changes - before

    def frame(self, source: str, series: str, symbol: str = "") -> pd.DataFrame:
        with self.connect() as conn:
            df = pd.read_sql_query(
                """
                SELECT ts, value, meta_json FROM points
                WHERE source=? AND series=? AND symbol=?
                ORDER BY ts
                """,
                conn,
                params=(source, series, symbol),
            )
        if df.empty:
            return pd.DataFrame(columns=["value", "meta"])
        # ``ts`` is stored as ``Timestamp.isoformat()``.  Binance publishes some
        # timestamps with millisecond drift (e.g. fundingTime ...008) while
        # others land exactly on the second, so a single column mixes
        # ``...T00:00:00+00:00`` and ``...T00:00:00.008000+00:00``.  Without an
        # explicit format pandas infers one layout from the first row and
        # coerces every row that does not match it to NaT, which later blows up
        # any time-based ``rolling`` window with
        # "ValueError: index values must not have NaT".  ``format="ISO8601"``
        # accepts every valid ISO-8601 precision; anything still unparseable is
        # genuinely corrupt and is dropped rather than kept as a NaT index.
        df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce", format="ISO8601")
        df = df.loc[df["ts"].notna()]
        if df.empty:
            return pd.DataFrame(columns=["value", "meta"])
        df["meta"] = df["meta_json"].map(json.loads)
        frame = df.drop(columns=["meta_json"]).set_index("ts").sort_index()
        # String ordering in SQL is not identical to timestamp ordering once
        # precision varies, so re-sort and collapse duplicates deterministically.
        return frame[~frame.index.duplicated(keep="last")]

    # An ISO-8601 timestamp always starts with YYYY-MM-DD, so this pattern keeps
    # only rows whose ``ts`` can actually be parsed back into a date.
    _TS_PATTERN = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*"

    def latest(self, source: str, series: str, symbol: str = "") -> dict[str, Any] | None:
        # Text ordering puts a stray "NaT" *after* every real timestamp ("N" > "9"
        # in ASCII), so plain ``ORDER BY ts DESC`` hands back the corrupt row
        # first. Callers then do ``pd.Timestamp(ts)`` -> NaT and crash on
        # ``.timestamp()`` with "NaTType does not support timestamp". Filter
        # unparseable rows out of the read; nothing is deleted from disk.
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT ts, value, meta_json FROM points
                WHERE source=? AND series=? AND symbol=? AND ts GLOB ?
                ORDER BY ts DESC LIMIT 1
                """,
                (source, series, symbol, self._TS_PATTERN),
            ).fetchone()
        if not row:
            return None
        return {"ts": row[0], "value": row[1], "meta": json.loads(row[2])}

    def get_state(self, key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT state_value, payload_json, updated_at FROM states WHERE state_key=?",
                (key,),
            ).fetchone()
        if not row:
            return None
        return {"state": row[0], "payload": json.loads(row[1]), "updated_at": row[2]}

    def set_state(self, key: str, state: str, payload: dict[str, Any]) -> bool:
        previous = self.get_state(key)
        changed = previous is None or previous["state"] != state
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO states(state_key, state_value, payload_json, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(state_key) DO UPDATE SET
                  state_value=excluded.state_value,
                  payload_json=excluded.payload_json,
                  updated_at=excluded.updated_at
                """,
                (key, state, json.dumps(payload, ensure_ascii=False, sort_keys=True), now),
            )
        return changed

    def start_run(self, task: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO runs(task, started_at, status) VALUES(?, ?, 'RUNNING')",
                (task, now),
            )
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, details: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                "UPDATE runs SET finished_at=?, status=?, details=? WHERE id=?",
                (now, status, details[:4000], run_id),
            )

