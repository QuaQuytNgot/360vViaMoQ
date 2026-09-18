"""Small independent TCP throughput probe for the isolated P2 veth path.

It is deliberately outside the MOQT adapter: a failed capacity check must not
be mistaken for a priority-scheduling result.
"""

from __future__ import annotations

import argparse
import json
import socket
import time


def serve(address: str, port: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((address, port))
        listener.listen(1)
        connection, peer = listener.accept()
        with connection:
            started = time.monotonic_ns()
            total = 0
            while data := connection.recv(1 << 20):
                total += len(data)
            ended = time.monotonic_ns()
    print(json.dumps({"role": "server", "peer": peer[0], "received_bytes": total,
                      "started_ts_ns": started, "ended_ts_ns": ended,
                      "duration_s": (ended - started) / 1_000_000_000}, sort_keys=True))
    return 0


def client(address: str, port: int, payload_bytes: int) -> int:
    chunk = b"\0" * min(1 << 20, payload_bytes)
    with socket.create_connection((address, port), timeout=10) as connection:
        started = time.monotonic_ns()
        remaining = payload_bytes
        while remaining:
            sent = connection.send(chunk[:min(len(chunk), remaining)])
            if sent <= 0:
                raise RuntimeError("TCP probe made no forward progress")
            remaining -= sent
        connection.shutdown(socket.SHUT_WR)
        ended = time.monotonic_ns()
    duration_s = (ended - started) / 1_000_000_000
    print(json.dumps({"role": "client", "sent_bytes": payload_bytes,
                      "started_ts_ns": started, "ended_ts_ns": ended,
                      "duration_s": duration_s,
                      "goodput_bps": payload_bytes * 8 / duration_s if duration_s else None}, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Independent P2 isolated-path TCP throughput probe.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--server", action="store_true")
    group.add_argument("--client", action="store_true")
    parser.add_argument("--address", required=True)
    parser.add_argument("--port", type=int, default=5202)
    parser.add_argument("--payload-bytes", type=int, default=48_000_000)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535 or args.payload_bytes <= 0:
        parser.error("port and payload-bytes must be positive")
    return serve(args.address, args.port) if args.server else client(args.address, args.port, args.payload_bytes)


if __name__ == "__main__":
    raise SystemExit(main())
