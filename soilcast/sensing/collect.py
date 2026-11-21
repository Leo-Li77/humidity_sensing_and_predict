"""
Collect soil moisture, temperature and humidity into data/raw.csv.

The loop reads the probe, reads the ambient source, and appends one row. A row
is flushed straight away, so a crash or a Ctrl-C costs at most the sample that
was in flight rather than the whole run.

With the simulator the clock can be wound forward. The rows keep their spacing —
one every interval — and only the waiting is divided by the speed, so a run of
five simulated days can be watched in a couple of minutes.

    python -m soilcast collect                       # 120 hours, every 30 minutes
    python -m soilcast collect --hours 6 --interval 10
    python -m soilcast collect --samples 20 --fast    # 20 rows, no waiting
    python -m soilcast collect --speed 600            # five days in 12 minutes
    python -m soilcast collect --driver ads1115       # real probe on the ADC
"""

from __future__ import annotations

import argparse
import csv
import math
import time
from datetime import datetime, timedelta
from pathlib import Path

from .. import config
from . import ambient, sensor

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
    progress=None,
    should_stop=None,
    speed: float = 1.0,
) -> int:
    """Run the loop and write rows. Returns the number of samples written.

    progress is called as progress(written, total, row) after each sample, and
    should_stop is polled before each one. Both are optional; the web interface
    uses them to draw a live gauge and to offer a stop button, and the command
    line leaves them out.

    speed multiplies the clock: 600 writes five simulated days in twelve minutes
    of real time. The rows are still one interval apart — only the waiting is
    divided — so the file looks exactly like a real five-day run. speed=inf, or
    fast=True, skips the waiting altogether. Speeding up a real probe would only
    shorten the gaps between readings without the soil drying any faster, so the
    caller is expected to restrict this to the simulator.
    """
    if fast:
        speed = math.inf
    if speed <= 0:
        raise ValueError("speed must be positive; use fast=True for no waiting")
    accelerated = speed != 1.0

    probe = sensor.build_sensor(driver)
    air = ambient.AmbientSource()
    interval_s = interval_min * 60

    total = limit if limit is not None else int(round(hours * 3600 / interval_s))
    output.parent.mkdir(parents=True, exist_ok=True)
    resuming = append and output.exists()
    already = _existing_rows(output) if resuming else 0

    span_h = total * interval_min / 60.0
    if not accelerated:
        plan = f"{total} samples at {interval_min} min, about {span_h:.1f} h"
    elif math.isfinite(speed):
        real_min = span_h * 60 / speed
        when = ("under a minute of real time" if real_min < 1
                else f"about {real_min:.1f} min of real time")
        plan = (f"{total} samples at {interval_min} min, {span_h:.1f} h of "
                f"readings at {speed:g}x — {when}")
    else:
        plan = (f"{total} samples at {interval_min} min, {span_h:.1f} h of "
                f"readings, written as fast as the disk allows")

    print(f"sensor     : {probe.name}")
    print(f"ambient    : {air.source} ({air.describe()})")
    print(f"output     : {output}")
    print(f"plan       : {plan}")
    if already:
        print(f"resuming   : {already} rows already in the file")
    if probe.name == "gpio":
        # Said once, at the point where the choice becomes visible, because the
        # file it produces looks fine and is useless for the models.
        print("note       : the digital output reports wet or dry only, so this "
              "file will hold 100 and 0 and no drying curve")
    print()

    written = 0
    started = time.monotonic()

    # With the clock wound forward the timestamps are still spaced by the
    # interval, but the run is not actually waited out. Using datetime.now() for
    # every row would put all of them in the same minute, and the resampling step
    # downstream would then collapse them into a single row. The clock is
    # therefore advanced by hand, ending at the moment the run starts.
    anchor = datetime.now().replace(microsecond=0) - timedelta(minutes=interval_min * total)

    try:
        with output.open("a" if resuming else "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if not resuming:
                writer.writerow(HEADER)

            for index in range(total):
                if should_stop is not None and should_stop():
                    print("stopped on request")
                    break
                if accelerated:
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

                if progress is not None:
                    progress(written, total, {
                        "timestamp": moment.strftime("%Y-%m-%d %H:%M:%S"),
                        "moisture": moisture,
                        "temperature": temperature,
                        "humidity": humidity,
                    })

                remaining = total - written
                print(f"  [{written:>4}/{total}] {moment:%H:%M:%S}  "
                      f"moisture {moisture:5.1f} %  "
                      f"{temperature:5.1f} C  {humidity:5.1f} %   "
                      f"{remaining} left")

                # Speed 1 keeps real time, so the sleep is the interval itself.
                # Above that the wait is divided, and an infinite speed means
                # there is nothing to wait for.
                if index + 1 < total and math.isfinite(speed):
                    time.sleep(interval_s / speed)

    except KeyboardInterrupt:
        print("\ninterrupted, every completed sample is already on disk")
    finally:
        probe.close()
        air.close()

    elapsed = time.monotonic() - started
    print()
    if accelerated:
        print(f"done: {written} samples written to {output} in {elapsed:.1f} s "
              f"of real time")
    else:
        print(f"done: {written} samples written to {output}")
    if written < total:
        print(f"note: {total - written} samples short of the plan")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m soilcast collect",
                                     description=__doc__)
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
    parser.add_argument("--speed", type=float, default=1.0, metavar="N",
                        help="run the clock N times faster: the rows keep their "
                             "spacing, only the waiting shrinks. Simulator only")
    parser.add_argument("--fast", action="store_true",
                        help="no waiting between samples, for a smoke test")
    args = parser.parse_args(argv)

    if args.speed <= 0:
        parser.error("--speed must be greater than zero")
    if args.speed != 1.0 and args.driver != "simulate":
        parser.error("--speed only applies to the simulator: a real probe dries "
                     "in real time, so winding the clock forward would just "
                     "produce rows that are closer together than the soil changed")

    written = collect(
        hours=args.hours,
        interval_min=args.interval,
        driver=args.driver,
        output=Path(args.output),
        append=args.append,
        fast=args.fast,
        limit=args.samples,
        speed=args.speed,
    )
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
