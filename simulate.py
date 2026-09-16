#!/usr/bin/python3
"""
Simulate the barcode scanner + serial scale for local/Docker testing.

This is the container entrypoint. It creates virtual devices, launches
listen.py pointed at them, then reads commands from stdin.

Usage (once running):
    scan A123-4        -> simulate scanning barcode "A123-4"
    A123-4             -> shorthand: anything that isn't a number is a scan
    weight 65.5        -> simulate the scale sending 65.5
    65.5               -> shorthand: anything that parses as a float is a weight

Ctrl-C or EOF exits and tears everything down.
"""

import glob
import os
import pty
import signal
import stat
import subprocess
import sys
import time
from shutil import which

from evdev import InputDevice, UInput, ecodes

SCANNER_NAME = "Simulated Scanner"

# Keys the barcode scanner can emit (mirrors listen.py's key_map).
SCANNER_KEYS = [ecodes.KEY_0, ecodes.KEY_1, ecodes.KEY_2, ecodes.KEY_3,
                ecodes.KEY_4, ecodes.KEY_5, ecodes.KEY_6, ecodes.KEY_7,
                ecodes.KEY_8, ecodes.KEY_9, ecodes.KEY_MINUS,
                ecodes.KEY_ENTER] + \
               [getattr(ecodes, f"KEY_{c}") for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]

CHAR_TO_KEY = {str(d): f"KEY_{d}" for d in range(10)}
CHAR_TO_KEY.update({c: f"KEY_{c}" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"})
CHAR_TO_KEY["-"] = "KEY_MINUS"


def find_scanner_node(name, timeout=5.0):
    """Find and expose the /dev/input/eventN node created by uinput."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for sysfs_path in glob.glob("/sys/class/input/event*"):
            try:
                with open(os.path.join(sysfs_path, "device/name")) as name_file:
                    if name_file.read().strip() != name:
                        continue
            except FileNotFoundError:
                continue

            event_name = os.path.basename(sysfs_path)
            device_path = os.path.join("/dev/input", event_name)
            if not os.path.exists(device_path):
                with open(os.path.join(sysfs_path, "dev")) as dev_file:
                    major, minor = map(int, dev_file.read().strip().split(":"))
                os.makedirs("/dev/input", exist_ok=True)
                try:
                    os.mknod(
                        device_path,
                        stat.S_IFCHR | 0o600,
                        os.makedev(major, minor),
                    )
                except FileExistsError:
                    pass

            dev = InputDevice(device_path)
            dev.close()
            return device_path
        time.sleep(0.1)
    raise RuntimeError(f"uinput device {name!r} did not appear")


def resolve_listener():
    """Find the listener script to launch.

    Order: $LISTENER_BIN, a sibling script next to this one
    (slaughter-measurements-listen, then listen.py), then anything
    resolvable on PATH. Returns an argv list beginning with python.
    """
    candidate = os.environ.get("LISTENER_BIN")
    candidates = []
    if candidate:
        candidates.append(candidate)
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.extend([
        os.path.join(here, "listen-slaughter-measurements"),
        os.path.join(here, "listen.py"),
        "listen-slaughter-measurements",
    ])
    for c in candidates:
        if os.path.isfile(c):
            return [sys.executable, c]
        found = which(c)
        if found:
            return [sys.executable, found]
    raise RuntimeError("could not find listener script; set LISTENER_BIN")


def main():
    # --- Scale: pty pair ---------------------------------------------
    scale_master, scale_slave = pty.openpty()
    scale_slave_path = os.ttyname(scale_slave)
    print(f"[sim] scale pty slave at {scale_slave_path}", flush=True)

    # --- Scanner: uinput virtual keyboard ----------------------------
    ui = UInput(name=SCANNER_NAME, events={ecodes.EV_KEY: SCANNER_KEYS})
    scanner_node = find_scanner_node(SCANNER_NAME)
    print(f"[sim] scanner uinput at {scanner_node}", flush=True)

    # --- Launch listen-slaughter-measurements ------------------------
    env = os.environ.copy()
    env["SCANNER_DEVICE"] = scanner_node
    env["SCALE_DEVICE"] = scale_slave_path

    listener_cmd = resolve_listener()

    listener = subprocess.Popen(
        listener_cmd,
        env=env,
    )
    print(f"[sim] {listener_cmd[1]} started (pid {listener.pid})", flush=True)
    print("[sim] type an animal ID + Enter to scan, a number + Enter to weigh",
          flush=True)
    print(flush=True)

    def inject_scan(text):
        for ch in text.upper():
            key_name = CHAR_TO_KEY.get(ch)
            if key_name is None:
                continue
            code = getattr(ecodes, key_name)
            ui.write(ecodes.EV_KEY, code, 1)  # key down
            ui.syn()
            ui.write(ecodes.EV_KEY, code, 0)  # key up
            ui.syn()
        ui.write(ecodes.EV_KEY, ecodes.KEY_ENTER, 1)
        ui.syn()
        ui.write(ecodes.EV_KEY, ecodes.KEY_ENTER, 0)
        ui.syn()
        print(f"[sim] scanned {text!r}", flush=True)

    def inject_weight(value):
        os.write(scale_master, f"{value}\n".encode("ascii"))
        print(f"[sim] sent weight {value}", flush=True)

    def shutdown():
        listener.send_signal(signal.SIGINT)
        try:
            listener.wait(timeout=3)
        except subprocess.TimeoutExpired:
            listener.kill()
        ui.close()
        os.close(scale_master)
        os.close(scale_slave)
        print("[sim] stopped", flush=True)

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            parts = line.split(maxsplit=1)
            cmd = parts[0].lower()

            if cmd == "scan" and len(parts) == 2:
                inject_scan(parts[1])
            elif cmd == "weight" and len(parts) == 2:
                inject_weight(parts[1])
            else:
                # Heuristic shorthand: a bare float is a weight, else a scan.
                try:
                    float(line)
                    inject_weight(line)
                except ValueError:
                    inject_scan(line)
    except KeyboardInterrupt:
        pass
    finally:
        shutdown()


if __name__ == "__main__":
    main()
