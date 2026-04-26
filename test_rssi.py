"""
test_ble.py  —  Raspberry Pi
==============================
Scans for the BLE beacon, collects exactly 10 readings, then stops.
Prints tab-separated rows that can be selected, copied, and pasted
directly into Google Sheets or Excel. Each row is one reading.

Terminal output format (tab-separated):
    No    Timestamp    RSSI_dBm    Distance_m    Actual_m

The header row is printed once at startup. All data rows use the same
tab-separated format so a paste into Sheets auto-splits into columns.

The CSV log (ble_log.csv) is also written as a backup.

Usage:
    sudo python3 test_ble.py <MAC> [RSSI_A] [N] [actual_distance_m]

Arguments:
    MAC               beacon MAC address  (required)
    RSSI_A            RSSI at 1 m, negative int  (default: -60)
    N                 path-loss exponent          (default: 3.4)
    actual_distance_m known distance you are standing at (default: 0.0)
                      set this so Distance_m vs Actual_m columns
                      are ready to compare in Sheets

Example — standing 2 m away, using calibrated values:
    sudo python3 test_ble.py AA:BB:CC:DD:EE:FF -62 3.2 2.0
"""

import sys
import csv
import os
import http.server
import socketserver
from datetime import datetime
from bluepy.btle import Scanner, DefaultDelegate

COLLECTION_LIMIT = 10

class _D(DefaultDelegate):
    def __init__(self): super().__init__()
    def handleDiscovery(self, *a): pass


def rssi_to_dist(rssi, A, N):
    return 10 ** ((A - rssi) / (10 * N))


def main():
    if len(sys.argv) < 2:
        print("Usage: sudo python3 test_ble.py <MAC> [RSSI_A] [N] [actual_m]")
        sys.exit(1)

    mac      = sys.argv[1].strip().lower()
    A        = float(sys.argv[2]) if len(sys.argv) > 2 else -55.0
    N        = float(sys.argv[3]) if len(sys.argv) > 3 else 2.8
    actual_m = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0

    scanner = Scanner().withDelegate(_D())

    # ── Run info printed above the data block (# lines are ignored by Sheets) ──
    print(f"# BLE Distance Log — {COLLECTION_LIMIT} readings")
    print(f"# MAC: {mac}  |  RSSI_A: {A}  |  N: {N}  |  Actual_m: {actual_m}")
    print(f"# Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # ── Header row — tab-separated ────────────────────────────────────────────
    print("No\tTimestamp\tRSSI_dBm\tDistance_m\tActual_m")

    count = 0

    with open("ble_log.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["No", "Timestamp", "RSSI_dBm", "Distance_m", "Actual_m"])

        while count < COLLECTION_LIMIT:
            for dev in scanner.scan(1.0):
                if dev.addr.lower() == mac and count < COLLECTION_LIMIT:
                    count += 1
                    ts   = datetime.now().strftime("%H:%M:%S")
                    dist = round(rssi_to_dist(dev.rssi, A, N), 3)

                    print(f"{count}\t{ts}\t{dev.rssi}\t{dist}\t{actual_m}")

                    writer.writerow([count, ts, dev.rssi, dist, actual_m])
                    f.flush()

    print()
    print(f"# Done — {COLLECTION_LIMIT} readings collected. Log saved to ble_log.csv")
    
    import http.server, socketserver, os, threading

os.chdir(os.path.dirname(os.path.abspath("ble_log.csv")))
PORT = 8080
with socketserver.TCPServer(("", PORT), http.server.SimpleHTTPRequestHandler) as httpd:
    print(f"\n# Download at: http://<pi-ip>:{PORT}/ble_log.csv")
    print("# Press Ctrl+C when done.")
    httpd.serve_forever()


if __name__ == "__main__":
    main()

