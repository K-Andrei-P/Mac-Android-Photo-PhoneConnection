#!/usr/bin/env python3
"""
phone_sender.py

Watches the Android camera folder in Termux and auto-uploads new photos
to your MacBook over Tailscale. If the Mac is unreachable (e.g. laptop
closed), it falls back to sending the same photo to a Raspberry Pi.

Run in Termux:
    python3 phone_sender.py

Recommended: run it under `termux-wake-lock` (or as a Termux:Boot /
Termux:Widget script) so Android doesn't kill it or throttle networking
while the screen is off.
"""

import os
import time
import json
import requests

# ---- Configuration ----
MAC_URL = "http://100.95.7.104:8000/upload"     # MacBook's Tailscale IP
PI_URL = "http://100.77.138.60:8000/upload"      # Raspberry Pi's Tailscale IP (fallback)

WATCH_DIR = os.path.expanduser("~/storage/dcim/Camera")
STATE_FILE = os.path.expanduser("~/.phone_sender_state.json")

POLL_INTERVAL_SECONDS = 10       
STABLE_CHECK_DELAY = 2           
                                  
REQUEST_TIMEOUT_SECONDS = 6     
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic"}


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_state(sent_files):
    with open(STATE_FILE, "w") as f:
        json.dump(sorted(sent_files), f)


def is_file_stable(path):
    """Check that a file's size isn't still changing (still being written)."""
    try:
        size1 = os.path.getsize(path)
    except OSError:
        return False
    time.sleep(STABLE_CHECK_DELAY)
    try:
        size2 = os.path.getsize(path)
    except OSError:
        return False
    return size1 == size2 and size1 > 0


def send_file(path, url):
    """Attempt to POST a file to the given URL. Returns True on success."""
    filename = os.path.basename(path)
    with open(path, "rb") as f:
        files = {"file": (filename, f)}
        response = requests.post(url, files=files, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return True


def send_with_fallback(path):
    """
    The Logic Switch: try the Mac first. If it fails or times out
    (laptop closed, off the tailnet, etc.), reroute the same file to
    the Pi instead.
    """
    filename = os.path.basename(path)

    try:
        send_file(path, MAC_URL)
        print(f"[OK] {filename} -> Mac")
        return True
    except (requests.ConnectionError, requests.Timeout) as e:
        print(f"[WARN] Mac unreachable for {filename} ({e}); trying Pi...")

    try:
        send_file(path, PI_URL)
        print(f"[OK] {filename} -> Pi (fallback)")
        return True
    except (requests.ConnectionError, requests.Timeout) as e:
        print(f"[FAIL] {filename} -> both Mac and Pi unreachable ({e})")
        return False


def seed_state_with_existing_files():
    """
    On first run (no state file yet), mark every file already in the
    watch folder as 'seen' without sending it. This prevents the script
    from trying to upload your entire existing camera roll -- it will
    only react to files that appear after this point.
    """
    try:
        existing = set(os.listdir(WATCH_DIR))
    except FileNotFoundError:
        existing = set()
    save_state(existing)
    print(f"First run: seeded state with {len(existing)} existing files "
          f"(they will NOT be uploaded).")
    return existing


def main():
    if os.path.exists(STATE_FILE):
        sent_files = load_state()
        print(f"Watching {WATCH_DIR} for new photos...")
        print(f"Already tracked: {len(sent_files)} files")
    else:
        sent_files = seed_state_with_existing_files()
        print(f"Watching {WATCH_DIR} for new photos...")

    while True:
        try:
            entries = os.listdir(WATCH_DIR)
        except FileNotFoundError:
            print(f"[ERROR] Watch directory not found: {WATCH_DIR}")
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        for name in entries:
            if name in sent_files:
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in IMAGE_EXTENSIONS:
                continue

            full_path = os.path.join(WATCH_DIR, name)
            if not is_file_stable(full_path):
                continue

            success = send_with_fallback(full_path)
            if success:
                sent_files.add(name)
                save_state(sent_files)

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")