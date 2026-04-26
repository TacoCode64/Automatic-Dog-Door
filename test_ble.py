"""
test_ble.py  —  Raspberry Pi
==============================
Continuously prints the detected distance of the BLE beacon.
Useful for Test 3 (Element H III.3): walk the beacon toward the door
and record RSSI vs measured distance in a spreadsheet.

Usage:
    python3 test_ble.py AA:BB:CC:DD:EE:FF [-65] [2.0]

Arguments:
    MAC     — beacon MAC address
    RSSI_A  — RSSI at 1 m (from calibrate_rssi.py, default -65)
    N       — path-loss exponent (default 2.0)

Output is also saved to ble_log.csv in the current directory.

Note: actual update rate is limited by the beacon's advertisement interval
(typically 100–1000 ms, set in beacon firmware).  The 0.1 s scan window
means the script prints as soon as each packet arrives — up to ~10/s —
rather than batching them into 1-second buckets.
"""

import sys
import time
import csv
from datetime import datetime
from bluepy.btle import Scanner, DefaultDelegate

class _D(DefaultDelegate):
    def __init__(self): super().__init__()
    def handleDiscovery(self, *a): pass

def rssi_to_dist(rssi, A, N):
    return 10 ** ((A - rssi) / (10 * N))

def main():
    mac    = sys.argv[1].lower() if len(sys.argv) > 1 else "aa:bb:cc:dd:ee:ff"
    A      = int(sys.argv[2])    if len(sys.argv) > 2 else -55
    N      = float(sys.argv[3])  if len(sys.argv) > 3 else 2.8

    scanner = Scanner().withDelegate(_D())
    print(f"Scanning for {mac}  (RSSI_A={A}, N={N})")
    print("Press Ctrl+C to stop.\n")
    print(f"{'Time':>10}  {'RSSI':>6}  {'Distance (m)':>14}")
    print("-" * 36)

    with open("ble_log.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "rssi_dbm", "distance_m"])

        try:
            scanner.start()
            while True:
                scanner.process(0.1)        # block for 100 ms, no socket overhead
                dev = scanner.scanned.get(mac)
                if dev is not None:
                    dist = rssi_to_dist(dev.rssi, A, N)
                    ts   = datetime.now().strftime("%H:%M:%S.%f")
                    print(f"{ts:>1}  {dev.rssi:>6}  {dist:>14.2f}")
                    writer.writerow([ts, dev.rssi, round(dist, 3)])
                    f.flush()
        except KeyboardInterrupt:
            print("\nLog saved to ble_log.csv")
        finally:
            scanner.stop()

if __name__ == "__main__":
    main()
