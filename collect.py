"""
Collect soil moisture, temperature and humidity into data/raw.csv.

The loop reads the probe, reads the ambient source, and appends one row. A row
is flushed straight away, so a crash or a Ctrl-C costs at most the sample that
was in flight rather than the whole run.

    python collect.py                        # 48 hours, one sample every 30 minutes
    python collect.py --hours 6 --interval 10
    python collect.py --samples 20 --fast     # 20 rows, no waiting, for a smoke test
    python collect.py --driver ads1115        # real probe on the ADC
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime, timedelta

import ambient
import config
import sensor

HEADER = ["timestamp", "moisture", "temperature", "humidity"]


def _existing_rows(path) -> int:
    if not path.exists():
        return 0
    with path.open(newline="", encoding="utf-8") as handle:
        return max(0, sum(1 for _ in handle) - 1)


def collect(
    hours: float,
    interval_min: int,
    driver: str,
    output,
    append: bool = False,
    fast: bool = False,
    limit: int | None = None,
) -> int:
    """Run the loop and write rows. Returns the number of samples written."""
    probe = sensor.build_sensor(driver)
    air = ambient.AmbientSource()
    interval_s = interval_min * 60

    total = limit if limit is not None else int(round(hours * 3600 / interval_s))
    output.parent.mkdir(parents=True, exist_ok=True)
    resuming = append and output.exists()
    already = _existing_rows(output) if resuming else 0

    print(f"sensor     : {probe.name}")
    print(f"ambient    : {air.source} ({air.describe()})")
    print(f"output     : {output}")
    print(f"plan       : {total} samples at {interval_min} min"
          f"{'' if fast else f', about {total * interval_min / 60:.1f} h'}")
    if already:
        print(f"resuming   : {already} rows already in the file")
    print()

    written = 0
    started = time.monotonic()

    # In fast mode the timestamps are still spaced by the interval, but the run
    # is not actually waited out. Using datetime.now() for every row would put
    # all of them in the same minute, and the resampling step downstream would
    # then collapse them into a single row. The clock is therefore advanced by
    # hand, ending at the moment the run starts.
    anchor = datetime.now().replace(microsecond=0) - timedelta(minutes=interval_min * total)

    try:
        with output.open("a" if resuming else "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if not resuming:
                writer.writerow(HEADER)

            for index in range(total):
                if fast:
                    moment = anchor + timedelta(minutes=interval_min * (index + 1))
                else:
                    moment = datetime.now().replace(microsecond=0)
                elapsed_h = index * interval_min / 60.0
                moisture = round(probe.read(elapsed_h), 2)
                temperature, humidity = air.read(moment)

                writer.writerow([
                    moment.strftime("%Y-%m-%d %H:%M:%S"),
                    moisture,
                    temperature,
                    humidity,
                ])
                handle.flush()
                written += 1

                remaining = total - written
                print(f"  [{written:>4}/{total}] {moment:%H:%M:%S}  "
                      f"moisture {moisture:5.1f} %  "
                      f"{temperature:5.1f} C  {humidity:5.1f} %   "
                      f"{remaining} left")

                if index + 1 < total and not fast:
                    time.sleep(interval_s)

    except KeyboardInterrupt:
        print("\ninterrupted, every completed sample is already on disk")
    finally:
        probe.close()
        air.close()

    elapsed = time.monotonic() - started
    print()
    print(f"done: {written} samples written to {output} in {elapsed:.1f} s"
          if fast else f"done: {written} samples written to {output}")
    if written < total:
        print(f"note: {total - written} samples short of the plan")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=float, default=config.COLLECT_HOURS)
    parser.add_argument("--interval", type=int, default=config.SAMPLE_INTERVAL_MIN,
                        help="minutes between samples")
    parser.add_argument("--driver", default=config.SENSOR_DRIVER,
                        choices=["ads1115", "gpio", "simulate"])
    parser.add_argument("--output", default=str(config.RAW_CSV))
    parser.add_argument("--append", action="store_true",
                        help="add to an existing file instead of replacing it")
    parser.add_argument("--samples", type=int, default=None,
                        help="stop after this many samples, overrides --hours")
    parser.add_argument("--fast", action="store_true",
                        help="no waiting between samples, for a smoke test")
    args = parser.parse_args(argv)

    from pathlib import Path

    written = collect(
        hours=args.hours,
        interval_min=args.interval,
        driver=args.driver,
        output=Path(args.output),
        append=args.append,
        fast=args.fast,
        limit=args.samples,
    )
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
