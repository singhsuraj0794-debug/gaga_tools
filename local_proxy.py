#!/usr/bin/env python3
"""
local_proxy.py — Tiny local HTTP/CONNECT proxy that forwards every request to
the Webshare rotating residential proxy with the credentials baked in.

Chrome can't pass credentials via --proxy-server (ERR_NO_SUPPORTED_PROXIES),
so we run this on localhost:9224 and point Chrome at http://localhost:9224.
The actual Webshare auth happens here.

Usage:
  python3 local_proxy.py [listen_port] [upstream_proxy_url]
Default:
  listen 9224, upstream http://uvuqatrj-in-rotate:fd9sp5s4yg8q@p.webshare.io:80
"""
import base64
import socket
import socketserver
import sys
import threading
from urllib.parse import urlsplit

LISTEN_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9224
UPSTREAM = sys.argv[2] if len(sys.argv) > 2 else "http://uvuqatrj-in-rotate:fd9sp5s4yg8q@p.webshare.io:80"

_u = urlsplit(UPSTREAM)
UP_HOST = _u.hostname
UP_PORT = _u.port or 80
AUTH = "Basic " + base64.b64encode(f"{_u.username}:{_u.password}".encode()).decode()


def _pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except Exception:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except Exception:
            pass


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        client = self.request
        try:
            first = b""
            while b"\r\n\r\n" not in first and len(first) < 65536:
                chunk = client.recv(4096)
                if not chunk:
                    return
                first += chunk
            head = first.split(b"\r\n\r\n")[0].decode("latin1", "replace")
            lines = head.split("\r\n")
            req_line = lines[0]
            method, target, _ = req_line.split(" ", 2)

            if method.upper() == "CONNECT":
                host, _, port = target.partition(":")
                port = int(port or 443)
            else:
                u = urlsplit(target)
                host = u.hostname
                port = u.port or (443 if u.scheme == "https" else 80)

            upstream = socket.create_connection((UP_HOST, UP_PORT), timeout=30)
            upstream.settimeout(60)

            if method.upper() == "CONNECT":
                upstream.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\nProxy-Authorization: {AUTH}\r\n\r\n".encode())
                resp = b""
                while b"\r\n\r\n" not in resp:
                    d = upstream.recv(4096)
                    if not d:
                        break
                    resp += d
                status = resp.split(b" ", 2)[1] if b" " in resp else b""
                if status != b"200":
                    client.sendall(resp)
                    upstream.close()
                    return
                # Send 200 to the browser, then pipe the tunnel
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                rest = first.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in first else b""
                if rest:
                    upstream.sendall(rest)
                t1 = threading.Thread(target=_pipe, args=(client, upstream))
                t2 = threading.Thread(target=_pipe, args=(upstream, client))
                t1.start(); t2.start(); t1.join(); t2.join()
            else:
                headers = [l for l in lines[1:] if l and not l.lower().startswith("proxy-")]
                headers.append(f"Proxy-Authorization: {AUTH}")
                new_head = f"{req_line}\r\n" + "\r\n".join(headers) + "\r\n\r\n"
                upstream.sendall(new_head.encode())
                body = first.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in first else b""
                if body:
                    upstream.sendall(body)
                _pipe(upstream, client)
            upstream.close()
        except Exception as e:
            print(f"[proxy] error: {e}", flush=True)
        finally:
            try:
                client.close()
            except Exception:
                pass


class ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    print(f"[proxy] listening on {LISTEN_PORT} -> {UPSTREAM}", flush=True)
    ThreadingTCPServer(("127.0.0.1", LISTEN_PORT), Handler).serve_forever()
