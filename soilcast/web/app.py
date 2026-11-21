"""
The routes.

Every stage of the pipeline is one endpoint, the browser draws the results, and
the history of every run is kept in SQLite so a marker can see what was done and
when. The page itself is static/ and templates/index.html; this module only
answers JSON.

    python -m soilcast web              start on http://127.0.0.1:5057
    python -m soilcast web --port 8080
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import math
import os
from datetime import datetime
from pathlib import Path

from flask import (
    Blueprint,
    Flask,
    Response,
    current_app,
    jsonify,
    request,
    send_file,
    send_from_directory,
)

from .. import config
from . import jobs, store

WEB_DIR = Path(__file__).resolve().parent

bp = Blueprint("console", __name__)


# ------------------------------------------------------------------- auth ----
@bp.before_request
def require_password():
    """Optional HTTP basic auth, off unless a password is configured.

    A home network needs none. The moment the port is forwarded, or a tunnel
    points at it, this is the only thing between a stranger and data/raw.csv.
    """
    password = current_app.config["SOIL_PASSWORD"]
    if not password or request.path.startswith("/static/"):
        return None
    supplied = request.authorization
    if supplied and supplied.username == current_app.config["SOIL_USER"]:
        given = hashlib.sha256((supplied.password or "").encode()).digest()
        wanted = hashlib.sha256(password.encode()).digest()
        # hmac, not hashlib: the comparison is done in constant time, so the
        # time it takes says nothing about how much of the password was right.
        if hmac.compare_digest(given, wanted):
            return None
    return Response(
        "This console needs a password.",
        401,
        {"WWW-Authenticate": 'Basic realm="soil console"'},
    )


# ----------------------------------------------------------------- pages ----
@bp.route("/")
def index():
    return send_from_directory(WEB_DIR / "templates", "index.html")


@bp.route("/static/<path:name>")
def static_file(name: str):
    return send_from_directory(WEB_DIR / "static", name)


# ------------------------------------------------------------------ state ----
def _module_present(name: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(name) is not None


@bp.route("/api/status")
def api_status():
    rows = store.read_raw()
    collecting, job = jobs.snapshot()
    return jsonify({
        "rows": len(rows),
        "summary": store.summarise(rows),
        "latest": rows[-1] if rows else None,
        "collect": collecting,
        "job": job,
        "config": {
            "interval_min": config.SAMPLE_INTERVAL_MIN,
            "default_hours": config.COLLECT_HOURS,
            "driver": config.SENSOR_DRIVER,
            "ambient_source": config.AMBIENT_SOURCE,
            "horizon_hours": config.FORECAST_HOURS,
            "watering_threshold": config.WATERING_THRESHOLD,
            "seq_len": config.LSTM_SEQ_LEN,
            "ensemble": config.LSTM_ENSEMBLE,
        },
        "hardware": {
            "ads1115": _module_present("board") and _module_present("adafruit_ads1x15"),
            "gpio": _module_present("gpiozero"),
            "torch": _module_present("torch"),
        },
    })


@bp.route("/api/readings")
def api_readings():
    limit = request.args.get("limit", type=int)
    rows = store.read_raw(limit)
    return jsonify({"rows": rows, "summary": store.summarise(rows)})


@bp.route("/api/job")
def api_job():
    _, job = jobs.snapshot()
    return jsonify(job)


@bp.route("/api/metrics")
def api_metrics():
    return jsonify(store.metrics() or {})


# ------------------------------------------------------------- collection ----
@bp.route("/api/collect/start", methods=["POST"])
def api_collect_start():
    collecting, _ = jobs.snapshot()
    if collecting["running"]:
        return jsonify({"error": "a collection is already running"}), 409
    body = request.get_json(silent=True) or {}
    driver = body.get("driver", config.SENSOR_DRIVER)
    interval = int(body.get("interval", config.SAMPLE_INTERVAL_MIN))
    hours = float(body.get("hours", config.COLLECT_HOURS))
    append = bool(body.get("append", False))
    # A speed of zero means "do not wait at all", which is the same thing the
    # old fast flag did. Both are only offered for the simulator.
    speed = float(body.get("speed", 1.0) or 1.0)
    fast = bool(body.get("fast", False)) or speed <= 0
    if fast:
        speed = math.inf
    elif speed < 1.0:
        speed = 1.0
    if (fast or speed > 1.0) and driver != "simulate":
        return jsonify({
            "error": "winding the clock forward only makes sense for the "
                     "simulator: real soil dries in real time",
        }), 400
    samples = max(1, int(round(hours * 60 / max(1, interval))))

    jobs.start_collect(driver, interval, samples, append, fast, speed)
    return jsonify({"ok": True, "samples": samples})


@bp.route("/api/collect/stop", methods=["POST"])
def api_collect_stop():
    jobs.stop_collect()
    return jsonify({"ok": True})


# ----------------------------------------------------------------- stages ----
@bp.route("/api/preprocess", methods=["POST"])
def api_preprocess():
    busy = jobs.busy()
    if busy:
        return jsonify({"error": f"{busy} is still running"}), 409
    jobs.start_stage("preprocess", [])
    return jsonify({"ok": True})


@bp.route("/api/analyze", methods=["POST"])
def api_analyze():
    busy = jobs.busy()
    if busy:
        return jsonify({"error": f"{busy} is still running"}), 409
    jobs.start_stage("analyze", [])
    return jsonify({"ok": True})


@bp.route("/api/train", methods=["POST"])
def api_train():
    busy = jobs.busy()
    if busy:
        return jsonify({"error": f"{busy} is still running"}), 409
    body = request.get_json(silent=True) or {}
    args: list[str] = []
    if body.get("no_lstm"):
        args.append("--no-lstm")
    if body.get("horizon"):
        args += ["--horizon", str(body["horizon"])]
    jobs.start_stage("train", args)
    return jsonify({"ok": True})


# ---------------------------------------------------------------- history ----
@bp.route("/api/history")
def api_history():
    kind = request.args.get("kind")
    return jsonify({"runs": store.history(kind)})


@bp.route("/api/history/<int:run_id>", methods=["DELETE"])
def api_history_delete(run_id: int):
    store.forget(run_id)
    return jsonify({"ok": True})


# ------------------------------------------------------------------ files ----
@bp.route("/api/upload", methods=["POST"])
def api_upload():
    """Accept a CSV with the four expected columns and replace data/raw.csv."""
    if "file" not in request.files:
        return jsonify({"error": "no file in the request"}), 400
    uploaded = request.files["file"]
    text = uploaded.read().decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    required = {"timestamp", "moisture", "temperature", "humidity"}
    if not required.issubset(set(reader.fieldnames or [])):
        return jsonify({
            "error": "the file needs the columns: " + ", ".join(sorted(required)),
            "found": list(reader.fieldnames or []),
        }), 400
    rows = list(reader)
    if not rows:
        return jsonify({"error": "the file holds no rows"}), 400

    config.RAW_CSV.parent.mkdir(parents=True, exist_ok=True)
    with config.RAW_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "moisture",
                                                    "temperature", "humidity"])
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in writer.fieldnames})
    store.record("import", datetime.now().isoformat(timespec="seconds"), None,
                 len(rows), True, {"rows": len(rows), "file": uploaded.filename})
    return jsonify({"ok": True, "rows": len(rows)})


@bp.route("/api/download")
def api_download():
    what = request.args.get("what", "raw")
    path = config.PROCESSED_CSV if what == "processed" else config.RAW_CSV
    if not path.exists():
        return jsonify({"error": f"{path.name} does not exist yet"}), 404
    return send_file(path, as_attachment=True, download_name=path.name)


@bp.route("/api/clear", methods=["POST"])
def api_clear():
    """Empty data/raw.csv. The old contents are archived rather than lost."""
    if not config.RAW_CSV.exists():
        return jsonify({"ok": True, "rows": 0})
    archive = config.RAW_CSV.with_name(
        f"raw_backup_{datetime.now():%Y%m%d_%H%M%S}.csv"
    )
    config.RAW_CSV.replace(archive)
    return jsonify({"ok": True, "archived": archive.name})


# ------------------------------------------------------------------- app ----
def create_app(password: str | None = None, user: str | None = None) -> Flask:
    """Build the application. The password falls back to the environment."""
    application = Flask(
        __name__,
        static_folder=str(WEB_DIR / "static"),
        template_folder=str(WEB_DIR / "templates"),
    )
    application.config["JSON_SORT_KEYS"] = False
    application.config["SOIL_PASSWORD"] = (
        os.environ.get("SOIL_PASSWORD", "") if password is None else password
    )
    application.config["SOIL_USER"] = user or os.environ.get("SOIL_USER", "pi")
    application.register_blueprint(bp)
    return application
