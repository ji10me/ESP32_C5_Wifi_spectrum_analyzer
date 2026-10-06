# SPDX-License-Identifier: GPL-3.0-or-later
"""Flash the ESP-SDR firmware (ESPARGOS, GPL-3.0) onto an ESP32-C5.

Uses the prebuilt images from the official browser installer
(https://espargos.net/espsdr/app/firmware/), verifies their SHA-256 against
the manifest, then writes them with esptool.

    python flash_c5.py COM6            # flash local images (download if missing)
    python flash_c5.py COM6 --update   # re-download the latest images first
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.request

BASE = "https://espargos.net/espsdr/app/firmware/"
VARIANT = "esp32c5"
HERE = os.path.dirname(os.path.abspath(__file__))
FW_DIR = os.path.join(HERE, "firmware")


def fetch(url, path):
    print(f"download {url}")
    with urllib.request.urlopen(url, timeout=30) as r, open(path, "wb") as f:
        f.write(r.read())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", help="serial port of the ESP32-C5, e.g. COM6")
    ap.add_argument("--update", action="store_true", help="download the latest manifest and images")
    ap.add_argument("--baud", default="921600")
    args = ap.parse_args()

    os.makedirs(os.path.join(FW_DIR, VARIANT), exist_ok=True)
    manifest_path = os.path.join(FW_DIR, "manifest.json")
    if args.update or not os.path.exists(manifest_path):
        fetch(BASE + "manifest.json", manifest_path)
    with open(manifest_path, encoding="utf-8") as f:
        variant = json.load(f)["variants"][VARIANT]

    flash_args = []
    for part in variant["parts"]:
        path = os.path.join(FW_DIR, VARIANT, part["name"])
        if args.update or not os.path.exists(path):
            fetch(f"{BASE}{VARIANT}/{part['name']}", path)
        with open(path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        if digest != part["sha256"]:
            sys.exit(f"SHA-256 mismatch for {part['name']}; rerun with --update")
        flash_args += [hex(part["offset"]), path]

    fs = variant["flash_settings"]
    cmd = [sys.executable, "-m", "esptool", "--chip", VARIANT, "--port", args.port, "-b", args.baud,
           "--before", "default-reset", "--after", "hard-reset", "write-flash",
           "--flash-mode", fs["flash_mode"], "--flash-freq", fs["flash_freq"], "--flash-size", "keep",
           *flash_args]
    print(f"firmware {variant['version'][:10]} ({variant.get('build_date', '?')})")
    print(" ".join(cmd))
    sys.exit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
