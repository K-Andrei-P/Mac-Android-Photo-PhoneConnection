#!/usr/bin/env python3
"""
pi_relay.py

Runs on the Raspberry Pi. Does two jobs at once:

  1. Receiver: a lightweight HTTP server (same pattern as the MacBook
     receiver) that catches photos the phone couldn't deliver to the Mac
     and drops them into a local holding folder (~/offline_queue/).

  2. Sync daemon: a background loop that periodically checks whether the
     Mac is reachable again. When it is, it drains the queue by pushing
     each queued photo to the Mac's receiver and deleting it locally
     only after the Mac confirms it was saved.

Run it:
    python3 pi_relay.py
"""

import os
import re
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

# ---- Configuration ----
LISTEN_PORT = 8000
OFFLINE_QUEUE_DIR = os.path.expanduser("~/offline_queue")

MAC_URL = "http://100.95.7.104:8000/upload"   # MacBook's Tailscale IP
SYNC_POLL_INTERVAL_SECONDS = 15           
REQUEST_TIMEOUT_SECONDS = 6


# ---------------------------------------------------------------------
# Shared multipart parsing
# ---------------------------------------------------------------------
def parse_multipart(body: bytes, content_type: str):
    match = re.search(r'boundary=(.+)', content_type)
    if not match:
        raise ValueError("No boundary found in Content-Type header")
    boundary = match.group(1).strip()
    boundary_bytes = ("--" + boundary).encode()

    parts = body.split(boundary_bytes)
    fields = []

    for part in parts:
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue

        header_blob, _, data = part.partition(b"\r\n\r\n")
        if not header_blob:
            continue

        headers_text = header_blob.decode(errors="replace")
        disposition_match = re.search(
            r'name="([^"]*)"(?:; filename="([^"]*)")?', headers_text
        )
        if not disposition_match:
            continue

        name = disposition_match.group(1)
        filename = disposition_match.group(2)

        if data.endswith(b"\r\n"):
            data = data[:-2]

        fields.append({"name": name, "filename": filename, "data": data})

    return fields


# ---------------------------------------------------------------------
# Job 1: The Receiver (inbound from phone)
# ---------------------------------------------------------------------
class ReceiverHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Pi relay is running. POST a file to /upload.\n")

    def do_POST(self):
        content_type = self.headers.get("Content-Type", "")
        content_length = int(self.headers.get("Content-Length", 0))

        if "multipart/form-data" not in content_type:
            self.send_error(400, "Expected multipart/form-data")
            return

        body = self.rfile.read(content_length)

        try:
            fields = parse_multipart(body, content_type)
        except ValueError as e:
            self.send_error(400, str(e))
            return

        saved = []
        for field in fields:
            if not field["filename"]:
                continue
            safe_name = os.path.basename(field["filename"])
            if not safe_name:
                continue

            os.makedirs(OFFLINE_QUEUE_DIR, exist_ok=True)
            dest_path = os.path.join(OFFLINE_QUEUE_DIR, safe_name)
            with open(dest_path, "wb") as f:
                f.write(field["data"])
            saved.append(safe_name)
            print(f"[QUEUE] Received & queued: {safe_name}")

        if saved:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"OK: queued {', '.join(saved)}\n".encode())
        else:
            self.send_error(400, "No file field found in upload")

    def log_message(self, format, *args):
        print(f"[{self.client_address[0]}] {format % args}")


def run_receiver_server():
    os.makedirs(OFFLINE_QUEUE_DIR, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), ReceiverHandler)
    print(f"Receiver listening on port {LISTEN_PORT}, queueing to: {OFFLINE_QUEUE_DIR}")
    server.serve_forever()


# ---------------------------------------------------------------------
# Job 2: The Queue-Flush Loop (sync daemon)
# ---------------------------------------------------------------------
def forward_file_to_mac(path):
    """POST one queued file to the Mac. Returns True only on confirmed success."""
    filename = os.path.basename(path)
    with open(path, "rb") as f:
        files = {"file": (filename, f)}
        response = requests.post(MAC_URL, files=files, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return True


def run_sync_daemon():
    print(f"Sync daemon watching {OFFLINE_QUEUE_DIR}, target: {MAC_URL}")
    while True:
        try:
            queued = sorted(os.listdir(OFFLINE_QUEUE_DIR))
        except FileNotFoundError:
            queued = []

        if not queued:
            time.sleep(SYNC_POLL_INTERVAL_SECONDS)
            continue

        for name in queued:
            path = os.path.join(OFFLINE_QUEUE_DIR, name)
            try:
                forward_file_to_mac(path)
                os.remove(path)  
                print(f"[FLUSHED] {name} -> Mac, removed from queue")
            except (requests.ConnectionError, requests.Timeout) as e:
                print(f"[WAIT] Mac still unreachable ({e}); "
                      f"{len(queued)} file(s) remain queued.")
                break  # stop this pass; Mac is offline.
            except requests.HTTPError as e:
                print(f"[ERROR] Mac rejected {name} ({e}); leaving it queued.")
                break

        time.sleep(SYNC_POLL_INTERVAL_SECONDS)


def main():
    receiver_thread = threading.Thread(target=run_receiver_server, daemon=True)
    receiver_thread.start()

    try:
        run_sync_daemon()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()