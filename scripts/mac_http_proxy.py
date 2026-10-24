#!/usr/bin/env python3
"""Tiny HTTP/HTTPS (CONNECT) proxy, stdlib only.

Purpose: give the scraper box a second egress. Amazon blocks the box's ISP IP,
but not another machine's, so the box can send ONLY Amazon traffic through here:

    # on the machine with the good IP (bound to its Tailscale address)
    python3 scripts/mac_http_proxy.py --bind 100.127.173.9 --port 8899

    # on the scraper box, in artifacts/api-server/.env
    AMAZON_PROXY=http://100.127.173.9:8899

TLS is tunnelled (CONNECT), so there are no certificate issues. Nothing is
logged beyond connection errors, and it only binds to the address you pass -
by default the Tailscale interface, not the LAN.

It is a stopgap for an IP-level block: as long as this process and the machine
run, the proxied traffic exits through here.
"""

from __future__ import annotations

import argparse
import select
import socket
import socketserver
import sys
import threading
from urllib.parse import urlsplit

BUFFER = 65536
TIMEOUT = 40


def _pipe(a: socket.socket, b: socket.socket) -> None:
    """Relay bytes both ways until either side closes."""
    try:
        while True:
            readable, _, _ = select.select([a, b], [], [], TIMEOUT)
            if not readable:
                break
            for sock in readable:
                data = sock.recv(BUFFER)
                if not data:
                    return
                (b if sock is a else a).sendall(data)
    except Exception:
        pass



class Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        client = self.request
        client.settimeout(TIMEOUT)
        upstream = None
        try:
            head = client.recv(BUFFER)
            if not head:
                return
            request_line = head.split(b"\r\n", 1)[0].decode("latin1", "ignore")
            parts = request_line.split(" ")
            if len(parts) < 2:
                return
            method, target = parts[0], parts[1]

            if method.upper() == "CONNECT":
                host, _, port = target.partition(":")
                upstream = socket.create_connection((host, int(port or 443)), timeout=TIMEOUT)
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            else:
                fields = urlsplit(target if "://" in target else "http://" + target)
                if not fields.hostname:
                    return
                upstream = socket.create_connection(
                    (fields.hostname, fields.port or 80), timeout=TIMEOUT
                )
                path = fields.path or "/"
                if fields.query:
                    path += "?" + fields.query
                header_block, _, body = head.partition(b"\r\n\r\n")
                lines = header_block.split(b"\r\n")
                lines[0] = f"{method} {path} {parts[2] if len(parts) > 2 else 'HTTP/1.1'}".encode()
                # strip hop-by-hop headers the upstream must not inherit
                keep = [
                    ln for ln in lines[1:]
                    if not ln.lower().startswith((b"proxy-connection", b"connection:"))
                ]
                upstream.sendall(b"\r\n".join([lines[0]] + keep) + b"\r\n\r\n" + body)

            _pipe(client, upstream)
        except Exception as exc:  # pragma: no cover - network noise
            print(f"proxy: {type(exc).__name__}: {exc}", file=sys.stderr)
        finally:
            for s in (upstream, client):
                try:
                    if s:
                        s.close()
                except Exception:
                    pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8899)
    args = ap.parse_args()
    with Server((args.bind, args.port), Handler) as srv:
        print(f"proxy listening on {args.bind}:{args.port}", flush=True)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
