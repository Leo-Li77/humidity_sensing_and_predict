"""
Background work for the console.

Two shapes of work, and they need different treatment.

A collection is a thread: it has to report progress while it waits half an hour
between samples, and it has to stop when the browser asks it to.

Preprocessing, analysis and training are child processes. They are short, they
print a lot, and keeping them out of this process means a crash in one of them
cannot take the server down. They are launched as  python -m soilcast <stage>,
which is exactly what the command line would run.
"""

from __future__ import annotations

import math
import subprocess
import sys
import threading
from datetime import datetime

from .. import config
from ..sensing import collect as collector
from . import store

LOCK = threading.Lock()

COLLECT = {
    "running": False,
    "stop": False,
    "written": 0,
    "total": 0,
    "last": None,
    "label": "",
    "speed": 1.0,
}

JOB = {"running": False, "name": "", "log": "", "ok": None, "started": None}


# ------------------------------------------------------------------- state ----
def snapshot() -> tuple[dict, dict]:
    """Both state records, copied under the lock so the routes never race."""
    with LOCK:
        return dict(COLLECT), dict(JOB)


def busy() -> str | None:
    """The name of the stage that is running, or None."""
    with LOCK:
        return JOB["name"] if JOB["running"] else None


# ------------------------------------------------------------------- stage ----
def run_script(name: str, args: list[str], timeout: int = 900) -> None:
    """Run one pipeline stage as a child process and keep what it printed."""
    started = datetime.now().isoformat(timespec="seconds")
    with LOCK:
        JOB.update({"running": True, "name": name, "log": "", "ok": None,
                    "started": started})
    try:
        finished = subprocess.run(
            [sys.executable, "-m", "soilcast", name, *args],
            cwd=str(config.ROOT), capture_output=True, text=True, timeout=timeout,
        )
        output = (finished.stdout or "") + (finished.stderr or "")
        ok = finished.returncode == 0
    except subprocess.TimeoutExpired:
        output = f"{name} ran longer than {timeout} seconds and was stopped."
        ok = False
    except Exception as error:                        # noqa: BLE001
        output = f"{name} could not start: {error}"
        ok = False

    with LOCK:
        JOB.update({"running": False, "log": output, "ok": ok})

    payload: dict = {}
    if name == "train" and ok:
        payload = store.metrics() or {}
    if name == "preprocess" and ok:
        payload = store.summarise(store.read_raw())
    store.record(name, started, None, payload.get("rows"), ok, payload)


def start_stage(name: str, args: list[str]) -> None:
    threading.Thread(target=run_script, args=(name, args), daemon=True).start()


# --------------------------------------------------------------- collection ----
def _worker(driver: str, interval: int, samples: int, append: bool,
            fast: bool, speed: float = 1.0) -> None:
    started = datetime.now().isoformat(timespec="seconds")
    written = 0
    try:
        written = collector.collect(
            hours=samples * interval / 60.0,
            interval_min=interval,
            driver=driver,
            output=config.RAW_CSV,
            append=append,
            fast=fast,
            limit=samples,
            progress=_on_sample,
            should_stop=lambda: COLLECT["stop"],
            speed=speed,
        )
    except Exception as error:                        # noqa: BLE001
        with LOCK:
            COLLECT["label"] = f"failed: {error}"
    finally:
        with LOCK:
            COLLECT["running"] = False
        store.record("collect", started, driver, written, written > 0,
                     {"rows": written, "interval_min": interval,
                      "speed": None if not math.isfinite(speed) else speed})


def _on_sample(done: int, total: int, row: dict) -> None:
    with LOCK:
        COLLECT["written"] = done
        COLLECT["total"] = total
        COLLECT["last"] = row
        COLLECT["label"] = f"{done} of {total} samples{_speed_suffix(COLLECT['speed'])}"


def _speed_suffix(speed: float) -> str:
    if speed == 1.0:
        return ""
    if not math.isfinite(speed):
        return ", instant"
    return f" at {speed:g}x"


def start_collect(driver: str, interval: int, samples: int, append: bool,
                  fast: bool, speed: float = 1.0) -> None:
    """Queue a collection. fast wins over speed, since it means no waiting."""
    if fast or not math.isfinite(speed):
        speed = math.inf
    with LOCK:
        COLLECT.update({"running": True, "stop": False, "written": 0,
                        "total": samples, "last": None, "speed": speed,
                        "label": f"0 of {samples} samples{_speed_suffix(speed)}"})
    threading.Thread(
        target=_worker, args=(driver, interval, samples, append, False, speed),
        daemon=True,
    ).start()


def stop_collect() -> None:
    with LOCK:
        COLLECT["stop"] = True
