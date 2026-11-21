"""
Download a public indoor climate data set and reduce it to 24 hourly means.

Source: UCI Machine Learning Repository, Occupancy Detection (set id 357). It
records temperature and humidity once a minute in an office room, which is the
closest freely available stand-in for the conditions around a potted plant.

The full file is around 20 000 rows. What the pipeline needs is a small lookup
table, so this script reduces it to one mean per hour of the day and writes
data/external/ambient.csv with the columns hour, temperature, humidity.

    python -m soilcast ambient              # download and reduce
    python -m soilcast ambient --synthetic   # skip the download, write a curve
    python -m soilcast ambient --from-file path/to/datatraining.txt
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

from .. import config

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")

SOURCES = [
    "https://archive.ics.uci.edu/static/public/357/occupancy+detection.zip",
    "https://archive.ics.uci.edu/ml/machine-learning-databases/00357/occupancy_data.zip",
]


def _download(url: str, timeout: int = 60) -> bytes:
    """Fetch the archive with the standard library, so no extra package is needed."""
    import urllib.request

    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (project-sem3 dataset fetch)"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _parse_rows(text: str) -> list[tuple[str, float, float]]:
    """Pull date, temperature and humidity out of the file, whatever its layout.

    The three files in the archive differ: some have a leading row-number column
    that the header does not mention, so the position of the timestamp is not
    fixed. Rather than trust the header, each line is scanned for the first field
    that looks like a date, and the two numbers after it are taken as temperature
    and humidity. Header lines simply do not match and are skipped.
    """
    rows: list[tuple[str, float, float]] = []
    for line in text.splitlines():
        fields = [f.strip().strip('"') for f in line.replace("\t", ",").split(",")]
        for index, field in enumerate(fields):
            if not _DATE.match(field):
                continue
            try:
                rows.append((field, float(fields[index + 1]), float(fields[index + 2])))
            except (IndexError, ValueError):
                pass
            break
    return rows


def _pick_member(archive: zipfile.ZipFile) -> str:
    """Prefer the training file, fall back to the largest one."""
    names = [n for n in archive.namelist() if n.lower().endswith(".txt")]
    if not names:
        raise ValueError("no .txt file inside the archive")
    training = [n for n in names if "train" in n.lower()]
    if training:
        return training[0]
    return max(names, key=lambda n: archive.getinfo(n).file_size)


def _rows_from_zip(payload: bytes) -> list[tuple[str, float, float]]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        name = _pick_member(archive)
        print(f"  reading {name}")
        raw = archive.read(name).decode("utf-8", errors="replace")
    return _parse_rows(raw)


def _rows_from_text(path: Path) -> list[tuple[str, float, float]]:
    return _parse_rows(path.read_text(encoding="utf-8", errors="replace"))


def _hour_of(stamp: str) -> int:
    """Accept 2015-02-04 17:51:00 and 2015-02-04T17:51:00 alike."""
    time_part = stamp.replace("T", " ").split(" ")[-1]
    return int(time_part.split(":")[0])


def _reduce(rows: list[tuple[str, float, float]]) -> list[tuple[int, float, float]]:
    buckets: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for stamp, temperature, humidity in rows:
        try:
            hour = _hour_of(stamp)
        except (ValueError, IndexError):
            continue
        if 0 <= hour <= 23:
            buckets[hour].append((temperature, humidity))

    if len(buckets) < 24:
        raise ValueError(f"only {len(buckets)} hours present, expected 24")

    table = []
    for hour in range(24):
        values = buckets[hour]
        temperature = sum(v[0] for v in values) / len(values)
        humidity = sum(v[1] for v in values) / len(values)
        table.append((hour, round(temperature, 2), round(humidity, 2)))
    return table


def _synthetic_table() -> list[tuple[int, float, float]]:
    table = []
    for hour in range(24):
        phase = (hour - 9.0) / 24.0 * 2 * math.pi
        table.append(
            (hour, round(22.0 + 4.0 * math.sin(phase), 2),
             round(58.0 - 12.0 * math.sin(phase), 2))
        )
    return table


def _write(table: list[tuple[int, float, float]]) -> Path:
    config.AMBIENT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with config.AMBIENT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["hour", "temperature", "humidity"])
        writer.writerows(table)
    return config.AMBIENT_CSV


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m soilcast ambient",
                                     description=__doc__)
    parser.add_argument("--synthetic", action="store_true",
                        help="skip the download and write a model curve instead")
    parser.add_argument("--from-file", type=Path,
                        help="use a local copy of datatraining.txt")
    args = parser.parse_args(argv)

    if args.synthetic:
        path = _write(_synthetic_table())
        print(f"wrote a synthetic 24-hour table to {path}")
        return 0

    if args.from_file:
        rows = _rows_from_text(args.from_file)
        print(f"read {len(rows)} rows from {args.from_file}")
    else:
        payload = None
        for url in SOURCES:
            try:
                print(f"downloading {url}")
                payload = _download(url)
                break
            except Exception as error:                      # noqa: BLE001
                print(f"  failed: {error}")
        if payload is None:
            print("could not reach either mirror. Download the archive by hand and "
                  "run  python -m soilcast ambient --from-file <file>", file=sys.stderr)
            return 1
        rows = _rows_from_zip(payload)
        print(f"read {len(rows)} rows from the archive")

    table = _reduce(rows)
    path = _write(table)
    print(f"wrote 24 hourly means to {path}")
    print(f"  temperature {min(t for _, t, _ in table):.1f} to "
          f"{max(t for _, t, _ in table):.1f} C")
    print(f"  humidity    {min(h for _, _, h in table):.1f} to "
          f"{max(h for _, _, h in table):.1f} %")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
