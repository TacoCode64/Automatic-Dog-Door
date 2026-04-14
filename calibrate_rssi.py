"""
calibrate_rssi.py  —  Raspberry Pi
===================================
Run this script with the beacon held EXACTLY 1 metre from the Pi's antenna.
It prints the average RSSI over 30 scans, which should be entered as
RSSI_A in main.py CONFIG.

Usage:
    python3 calibrate_rssi.py AA:BB:CC:DD:EE:FF
"""

import sys
import time
from bluepy.btle import Scanner, DefaultDelegate

class _D(DefaultDelegate):
    def __init__(self): super().__init__()
    def handleDiscovery(self, *a): pass

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 calibrate_rssi.py <BEACON_MAC>")
        sys.exit(1)

    target = sys.argv[1].lower()
    scanner = Scanner().withDelegate(_D())
    readings = []
    print(f"Scanning for {target} … hold beacon 1 m from the Pi antenna.")

    while len(readings) < 30:
        for dev in scanner.scan(1.0):
            if dev.addr.lower() == target:
                readings.append(dev.rssi)
                print(f"  [{len(readings):2d}/30]  RSSI = {dev.rssi} dBm")

    avg = sum(readings) / len(readings)
    print(f"\nAverage RSSI at 1 m: {avg:.1f} dBm")
    print(f'Enter this value as "RSSI_A": {int(avg)} in main.py CONFIG.')

if __name__ == "__main__":
    main()
