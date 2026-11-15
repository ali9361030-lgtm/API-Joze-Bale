"""Start the local Android/desktop UI without importing server state on --help."""
import argparse
import socket
import threading
import time
import webbrowser


def port_number(value):
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def auto_open_browser(port, cancelled=None):
    """Wait for the local listener, rather than racing a fixed startup sleep."""
    cancelled = cancelled or threading.Event()
    deadline = time.monotonic() + 15
    while not cancelled.is_set() and time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                pass
            if not cancelled.is_set():
                webbrowser.open(f"http://127.0.0.1:{port}/")
            return
        except OSError:
            cancelled.wait(0.2)
        except Exception:
            return  # A missing browser handler must not stop the server.


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the local Jozve Android web UI")
    parser.add_argument("port", nargs="?", type=port_number, default=8080)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser automatically")
    args = parser.parse_args(argv)
    try:
        import requests  # noqa: F401 - fail before serving a half-working app
        import docx  # noqa: F401
        from server import run_server
    except ImportError as exc:
        parser.exit(1, f"Missing dependency: {exc}. Run: python -m pip install -r requirements.txt\n")
    cancelled = threading.Event()
    try:
        if not args.no_browser:
            threading.Thread(target=auto_open_browser, args=(args.port, cancelled), daemon=True).start()
        run_server(args.port)
        return 0
    except OSError as exc:
        parser.exit(1, f"Cannot start local server on port {args.port}: {exc}\n")
    finally:
        cancelled.set()


if __name__ == "__main__":
    raise SystemExit(main())
