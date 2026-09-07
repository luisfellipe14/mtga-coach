import argparse
import json
from pathlib import Path
import tempfile
import threading
import urllib.request

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
            print("MTGA Coach: servidor local e API verificados.")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            service.store.close()


def main():
    parser = argparse.ArgumentParser(description="MTGA Coach — app local de revisão")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18731)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--import-current", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if args.smoke:
        smoke()
        return
    service = CoachService(data_dir=args.data_dir)
    if args.import_current:
        try:
            service.import_configured()
        except (OSError, ValueError):
            print("Importação inicial indisponível; use Importar logs na interface.")
    server = create_server(service, args.host, args.port)
    print(f"MTGA Coach em http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.store.close()


if __name__ == "__main__":
    main()
