"""
The command line.

One stage per subcommand, and every stage keeps its own flags and its own
--help. This module only decides which one runs, so adding a stage means adding
one line to STAGES rather than another parser.

    python -m soilcast --help
    python -m soilcast all --demo
    python -m soilcast train --no-lstm
    python -m soilcast web
"""

from __future__ import annotations

import argparse
import importlib
import subprocess
import sys

from . import __version__, config

# stage -> (module that owns the code, one-line description)
STAGES: dict[str, tuple[str, str]] = {
    "ambient": ("soilcast.processing.ambient_table",
                "download the climate data set, reduce it to hourly means"),
    "collect": ("soilcast.sensing.collect",
                "read the probe on a fixed interval into data/raw.csv"),
    "preprocess": ("soilcast.processing.preprocess",
                   "resample, fill gaps, denoise, build the features"),
    "analyze": ("soilcast.processing.analyze",
                "descriptive statistics and the diagnostic figures"),
    "train": ("soilcast.modelling.train",
              "fit both models, score them on the held-out tail, plot"),
    "all": ("", "run ambient, collect, preprocess, analyze and train in order"),
    "web": ("soilcast.web.serve", "serve the browser console"),
}

USAGE = f"""
soilcast {__version__} — soil moisture monitoring and forecasting

  python -m soilcast <stage> [options]

Stages
""" + "\n".join(
    f"  {name:<11} {description}" for name, (_, description) in STAGES.items()
) + """

Each stage takes --help. A typical first run, with the simulator in place of the
probe:

  python -m soilcast all --demo
  python -m soilcast web
"""


def run_all(argv: list[str]) -> int:
    """The whole pipeline, one child process per stage.

    Collection takes days by design, so it is skipped when the data is already
    there; everything after it is fast.
    """
    parser = argparse.ArgumentParser(
        prog="python -m soilcast all",
        description="Run the pipeline end to end.",
    )
    parser.add_argument("--demo", action="store_true",
                        help="full-length run with the clock advanced by hand")
    parser.add_argument("--reuse", action="store_true",
                        help="skip collection and use the existing data/raw.csv")
    parser.add_argument("--collect", type=float, default=None, metavar="HOURS",
                        help="collect for this many hours with the configured sensor")
    parser.add_argument("--driver", default=config.SENSOR_DRIVER,
                        choices=["ads1115", "gpio", "simulate"])
    parser.add_argument("--no-lstm", action="store_true",
                        help="baseline only, for when torch is not installed")
    parser.add_argument("--skip-ambient", action="store_true",
                        help="do not touch data/external/ambient.csv")
    args = parser.parse_args(argv)

    def stage(name: str, *arguments: str) -> None:
        command = [sys.executable, "-m", "soilcast", name, *arguments]
        print()
        print("=" * 72)
        print("python", " ".join(command[2:]))
        print("=" * 72)
        result = subprocess.run(command, cwd=str(config.ROOT))
        if result.returncode != 0:
            print(f"{name} exited with code {result.returncode}", file=sys.stderr)
            raise SystemExit(result.returncode)

    needs_ambient = (config.AMBIENT_SOURCE == "dataset"
                     and not args.skip_ambient
                     and not config.AMBIENT_CSV.exists())
    if needs_ambient:
        stage("ambient")

    if not args.reuse:
        if args.demo:
            # A full-length run with the clock advanced by hand, so the file has
            # the same span as a real collection but arrives in a second.
            samples = int(round(config.COLLECT_HOURS * 60 / config.SAMPLE_INTERVAL_MIN))
            stage("collect", "--samples", str(samples), "--fast",
                  "--driver", args.driver)
        elif args.collect is not None:
            stage("collect", "--hours", str(args.collect), "--driver", args.driver)
        elif not config.RAW_CSV.exists():
            stage("collect", "--hours", str(config.COLLECT_HOURS),
                  "--driver", args.driver)

    stage("preprocess")
    stage("analyze")
    stage("train", *(["--no-lstm"] if args.no_lstm else []))

    print()
    print("=" * 72)
    print(f"data      {config.DATA_DIR}")
    print(f"figures   {config.FIGURE_DIR}")
    print(f"metrics   {config.MODEL_DIR / 'metrics.json'}")
    print("=" * 72)
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)

    if not arguments or arguments[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if arguments[0] in ("-v", "-V", "--version"):
        print(f"soilcast {__version__}")
        return 0

    command, rest = arguments[0], arguments[1:]
    if command == "all":
        return run_all(rest)

    entry = STAGES.get(command)
    if entry is None:
        print(f"unknown stage {command!r}", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2

    module = importlib.import_module(entry[0])
    return int(module.main(rest) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
