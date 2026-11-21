"""
Start the console.

Nothing here touches the routes. It parses the flags, prints the addresses
another device can use, initialises the history table and hands the application
to waitress, which serves it from eight threads and says nothing until something
goes wrong.
"""

from __future__ import annotations

import argparse
import socket
import threading
import webbrowser

from .. import config
from . import store
from .app import create_app


def local_addresses() -> list[str]:
    """Addresses another device on the network could use to reach this one."""
    found: list[str] = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.settimeout(0.4)
        # No packet is sent; this only asks the OS which interface would be used.
        probe.connect(("8.8.8.8", 80))
        found.append(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address not in found and not address.startswith("127."):
                found.append(address)
    except OSError:
        pass
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m soilcast web",
        description="Serve the browser console.",
    )
    parser.add_argument("--host", default="0.0.0.0",
                        help="0.0.0.0 lets other devices connect; 127.0.0.1 keeps it local")
    parser.add_argument("--port", type=int, default=5057)
    parser.add_argument("--no-browser", action="store_true",
                        help="do not open a browser window")
    parser.add_argument("--password", default=None,
                        help="require a password; also read from SOIL_PASSWORD")
    args = parser.parse_args(argv)

    application = create_app(password=args.password)
    store.init_db()

    print()
    print("  Soil Console")
    print("  " + "-" * 50)
    print(f"  this device        http://127.0.0.1:{args.port}")
    if args.host in ("0.0.0.0", "::"):
        for address in local_addresses():
            print(f"  other devices      http://{address}:{args.port}")
    print(f"  password           "
          f"{'required' if application.config['SOIL_PASSWORD'] else 'not set'}")
    print(f"  data               {config.RAW_CSV}")
    print("  stop               Ctrl+C")
    print()

    if not args.no_browser:
        threading.Timer(
            1.2, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")
        ).start()

    try:
        from waitress import serve

        # Eight threads is comfortable for a handful of browsers on a local
        # network, and waitress does not print a warning banner on every start.
        serve(application, host=args.host, port=args.port, threads=8,
              ident="soil-console")
    except ImportError:
        print("  waitress is not installed, falling back to the flask server.")
        print("  pip install waitress   fixes this.\n")
        application.run(host=args.host, port=args.port, debug=False, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
