"""
What the console keeps on disk.

Two things, and they are unrelated to Flask: the readings in data/raw.csv, and a
small SQLite table in which every run is recorded. Keeping them in one module
means the routes stay about HTTP and the storage stays about files.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime

from .. import config


# ---------------------------------------------------------------- history ----
def db() -> sqlite3.Connection:
    connection = sqlite3.connect(config.HISTORY_DB, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    config.HISTORY_DB.parent.mkdir(parents=True, exist_ok=True)
    with db() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                kind        TEXT NOT NULL,
                started_at  TEXT NOT NULL,
                finished_at TEXT,
                driver      TEXT,
                samples     INTEGER,
                ok          INTEGER,
                payload     TEXT
            )
            """
        )


def record(kind: str, started: str, driver: str | None, samples: int | None,
           ok: bool, payload: dict | None) -> None:
    with db() as connection:
        connection.execute(
            "INSERT INTO runs (kind, started_at, finished_at, driver, samples, ok, payload)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (kind, started, datetime.now().isoformat(timespec="seconds"), driver,
             samples, int(ok), json.dumps(payload or {})),
        )


def history(kind: str | None = None, limit: int = 40) -> list[dict]:
    query = "SELECT * FROM runs"
    parameters: list = []
    if kind:
        query += " WHERE kind = ?"
        parameters.append(kind)
    query += " ORDER BY id DESC LIMIT ?"
    parameters.append(limit)
    with db() as connection:
        rows = connection.execute(query, parameters).fetchall()
    output = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item["payload"] or "{}")
        output.append(item)
    return output


def forget(run_id: int) -> None:
    with db() as connection:
        connection.execute("DELETE FROM runs WHERE id = ?", (run_id,))


# --------------------------------------------------------------- readings ----
def read_raw(limit: int | None = None) -> list[dict]:
    path = config.RAW_CSV
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        for key in ("moisture", "temperature", "humidity"):
            try:
                row[key] = float(row[key])
            except (TypeError, ValueError):
                row[key] = None
    return rows[-limit:] if limit else rows


def summarise(rows: list[dict]) -> dict:
    if not rows:
        return {"rows": 0}
    moisture = [r["moisture"] for r in rows if r["moisture"] is not None]
    temperature = [r["temperature"] for r in rows if r["temperature"] is not None]
    humidity = [r["humidity"] for r in rows if r["humidity"] is not None]

    def stats(values: list[float]) -> dict:
        if not values:
            return {}
        ordered = sorted(values)
        count = len(ordered)
        mean = sum(ordered) / count
        variance = sum((v - mean) ** 2 for v in ordered) / count
        return {
            "min": round(ordered[0], 2),
            "max": round(ordered[-1], 2),
            "mean": round(mean, 2),
            "median": round(ordered[count // 2], 2),
            "std": round(variance ** 0.5, 2),
            "last": round(values[-1], 2),
        }

    return {
        "rows": len(rows),
        "first": rows[0]["timestamp"],
        "last": rows[-1]["timestamp"],
        "moisture": stats(moisture),
        "temperature": stats(temperature),
        "humidity": stats(humidity),
    }


# ---------------------------------------------------------------- metrics ----
def metrics() -> dict | None:
    path = config.MODEL_DIR / "metrics.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
