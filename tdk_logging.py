"""Persistent, independent TDK electrical measurement log (UTC epochs)."""
from __future__ import annotations
import csv
from contextlib import closing
import datetime as dt
from pathlib import Path
import sqlite3
import threading
import time
from typing import List, Tuple

from app_paths import data_dir
from tdk_lambda import TDKReading


class TDKLogger:
    def __init__(self, folder: Path | None = None):
        self.folder = folder or data_dir()
        self.lock = threading.RLock()
        self._initialized = set()

    def weekly_path(self, timestamp: float) -> Path:
        d = dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc)
        year, week, _ = d.isocalendar()
        return self.folder / f"tdk_measurements_{year}-W{week:02d}.sqlite3"

    def append(self, port: str, name: str, reading: TDKReading) -> None:
        if not reading.ok:
            raise ValueError("Invalid supply reading")
        path = self.weekly_path(reading.timestamp)
        with self.lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(path)) as db:
                if str(path) not in self._initialized:
                    db.execute("""CREATE TABLE IF NOT EXISTS tdk_samples (
                        ts REAL NOT NULL, port TEXT NOT NULL, device_name TEXT NOT NULL,
                        voltage_v REAL NOT NULL, current_a REAL NOT NULL,
                        power_w REAL NOT NULL, voltage_set_v REAL, current_set_a REAL,
                        output_state TEXT, mode TEXT, identity TEXT)""")
                    db.execute("CREATE INDEX IF NOT EXISTS idx_tdk_port_ts ON tdk_samples(port, ts)")
                    self._initialized.add(str(path))
                db.execute("""INSERT INTO tdk_samples
                   (ts, port, device_name, voltage_v, current_a, power_w,
                    voltage_set_v, current_set_a, output_state, mode, identity)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                   (reading.timestamp, port, name, reading.voltage_v, reading.current_a,
                    reading.power_w, reading.voltage_set_v, reading.current_set_a,
                    reading.output_state, reading.mode, reading.identity))
                db.commit()

    def rows(self, port: str, seconds: float | None = None) -> List[Tuple]:
        cutoff = None if seconds is None else time.time() - seconds
        found = []
        with self.lock:
            for path in sorted(self.folder.glob("tdk_measurements_????-W??.sqlite3")):
                try:
                    with closing(sqlite3.connect(path)) as db:
                        if cutoff is None:
                            query = "SELECT ts, voltage_v, current_a, power_w, voltage_set_v, current_set_a, output_state, mode, identity FROM tdk_samples WHERE port=? ORDER BY ts"
                            found.extend(db.execute(query, (port,)).fetchall())
                        else:
                            query = "SELECT ts, voltage_v, current_a, power_w, voltage_set_v, current_set_a, output_state, mode, identity FROM tdk_samples WHERE port=? AND ts>=? ORDER BY ts"
                            found.extend(db.execute(query, (port, cutoff)).fetchall())
                except sqlite3.Error:
                    continue
        return sorted(found, key=lambda row: row[0])

    def export_csv(self, port: str, path: Path) -> int:
        rows = self.rows(port)
        with path.open("w", newline="", encoding="utf-8") as out:
            writer = csv.writer(out)
            writer.writerow(("local_datetime", "timestamp_utc_s", "voltage_v", "current_a", "power_w",
                             "voltage_set_v", "current_set_a", "output_state", "mode", "identity"))
            for row in rows:
                writer.writerow((time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row[0])),) + row)
        return len(rows)
