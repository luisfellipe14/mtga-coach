import argparse
import json
import sys
import threading
import tempfile
import urllib.request
import webbrowser
from pathlib import Path

from .http_api import create_server
from .service import CoachService


def smoke():
    with tempfile.TemporaryDirectory(prefix="mtga-coach-smoke-") as directory:
        service = CoachService(data_dir=Path(directory))
        server = create_server(service, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/summary", timeout=5) as response:
                summary = json.load(response)
            if summary["games"] != 0:
                raise RuntimeError("Unexpected initial data in smoke test")
            print("MTGA Coach: local server and API verified.")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            service.store.close()


def main():
    parser = argparse.ArgumentParser(description="MTGA Coach — local match review")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18731)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--import-current", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    # A packaged build is double-clicked, so it opens the page itself; a developer run
    # keeps the terminal free unless the flag is given.
    frozen = getattr(sys, "frozen", False)
    parser.add_argument("--open", dest="open_browser", action="store_true", default=frozen)
    parser.add_argument("--no-open", dest="open_browser", action="store_false")
    args = parser.parse_args()
    if args.smoke:
        smoke()
        return
    service = CoachService(data_dir=args.data_dir)
    if args.import_current:
        try:
            service.import_configured()
        except (OSError, ValueError):
            print("Initial import unavailable; use Import logs in the interface.")
    server = create_server(service, args.host, args.port)
    address = f"http://127.0.0.1:{server.server_port}/"
    print(f"MTGA Coach is running at {address}", flush=True)
    print("Leave this window open while you play; close it to stop.", flush=True)
    if args.open_browser:
        threading.Timer(0.6, webbrowser.open, args=(address,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.store.close()


if __name__ == "__main__":
    main()
