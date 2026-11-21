"""
Run the whole pipeline in order.

    python run_all.py               # collect 48 h with the simulator, then process
    python run_all.py --demo        # 20 samples, no waiting, for a quick look
    python run_all.py --reuse       # skip collection, reuse whatever is in data/
    python run_all.py --collect 48  # collect for real, then process

Collection takes a long time by design, so it is the one stage that is skipped
when the data is already there. Everything after it is fast.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import config


def run(script: str, *arguments: str) -> None:
    command = [sys.executable, script, *arguments]
    print()
    print("=" * 72)
    print(" ".join(Path(part).name if part.endswith(".py") else part
                   for part in command[1:]))
    print("=" * 72)
    result = subprocess.run(command, cwd=config.ROOT)
    if result.returncode != 0:
        print(f"{script} exited with code {result.returncode}", file=sys.stderr)
        raise SystemExit(result.returncode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true",
                        help="20 samples with no waiting, overwrites data/raw.csv")
    parser.add_argument("--reuse", action="store_true",
                        help="skip collection and use the existing data/raw.csv")
    parser.add_argument("--collect", type=float, default=None, metavar="HOURS",
                        help="collect for this many hours with the configured sensor")
    parser.add_argument("--driver", default=config.SENSOR_DRIVER,
                        choices=["ads1115", "gpio", "simulate"])
    parser.add_argument("--no-lstm", action="store_true")
    parser.add_argument("--skip-ambient", action="store_true",
                        help="do not touch data/external/ambient.csv")
    args = parser.parse_args(argv)

    if not args.reuse and not config.RAW_CSV.exists():
        if config.AMBIENT_SOURCE == "dataset" and not args.skip_ambient \
                and not config.AMBIENT_CSV.exists():
            run("fetch_ambient.py")

    if not args.reuse:
        if args.demo:
            # A full-length run with the clock advanced by hand, so the file has
            # the same span as a real collection but arrives in a second.
            samples = int(round(config.COLLECT_HOURS * 60 / config.SAMPLE_INTERVAL_MIN))
            run("collect.py", "--samples", str(samples), "--fast",
                "--driver", args.driver)
        elif args.collect is not None:
            run("collect.py", "--hours", str(args.collect), "--driver", args.driver)
        elif not config.RAW_CSV.exists():
            run("collect.py", "--hours", str(config.COLLECT_HOURS), "--driver", args.driver)

    run("preprocess.py")
    run("analyze.py")
    run("train.py", *(["--no-lstm"] if args.no_lstm else []))

    print()
    print("=" * 72)
    print(f"data      {config.DATA_DIR}")
    print(f"figures   {config.FIGURE_DIR}")
    print(f"metrics   {config.MODEL_DIR / 'metrics.json'}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
