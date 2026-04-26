"""
calibrate_rssi.py  —  Raspberry Pi
=====================================
Systematic calibration of RSSI_A and RSSI_N for BLE distance estimation.

Formula used throughout:  distance = 10 ^ ((A - RSSI) / (10 * N))

Strategy
--------
Phase 1 — Data collection
  Stand at known distances (1 m, 2 m, 3 m) and collect average RSSI at each.
  These three measured RSSI values are the raw data everything else is derived from.

Phase 2 — Grid sweep (isolate A, then isolate N)
  Sweeps A from -55 to -65 in 0.5 steps while holding N fixed at the midpoint (3.4).
  Sweeps N from 2.8 to 4.0 in 0.1 steps while holding A fixed at the midpoint (-60).
  For each combination, compute estimated distance at every measured point and
  compare to the actual distance. Prints mean absolute error (MAE) for each.

Phase 3 — Combined best-fit search
  Tests every (A, N) combination in the full grid and finds the pair with the
  lowest MAE across all measured distances.

Phase 4 — Recommendation
  Prints the best A and N values to enter into main.py CONFIG.
  Saves all results to rssi_calibration.csv for review.

Usage:
    sudo python3 calibrate_rssi.py <BEACON_MAC>
    e.g.  sudo python3 calibrate_rssi.py AA:BB:CC:DD:EE:FF

Requirements:
    pip3 install bluepy
"""

import sys
import time
import math
import csv
import statistics
from bluepy.btle import Scanner, DefaultDelegate

# ── Configuration ─────────────────────────────────────────────────────────────
SAMPLES_PER_POSITION = 30      # RSSI readings averaged per distance
SCAN_WINDOW_S        = 1.0     # seconds per BLE scan call
DISTANCES_M          = [1.0, 2.0, 3.0]   # known stand positions (metres)

A_MIN, A_MAX, A_STEP = 55, 65, 0.5       # sweep range for A (stored as positive)
N_MIN, N_MAX, N_STEP = 2.8, 4.0, 0.1    # sweep range for N

A_MID = (A_MIN + A_MAX) / 2   # 60.0 — held fixed when isolating N
N_MID = (N_MIN + N_MAX) / 2   # 3.4  — held fixed when isolating A

# ── BLE helpers ───────────────────────────────────────────────────────────────
class _D(DefaultDelegate):
    def __init__(self): super().__init__()
    def handleDiscovery(self, *a): pass

def collect_rssi(mac: str, n_samples: int) -> float:
    """Scan until n_samples RSSI readings collected for mac. Return mean."""
    scanner = Scanner().withDelegate(_D())
    readings = []
    print(f"    Collecting {n_samples} samples ", end="", flush=True)
    while len(readings) < n_samples:
        for dev in scanner.scan(SCAN_WINDOW_S):
            if dev.addr.lower() == mac.lower():
                readings.append(dev.rssi)
                if len(readings) % 5 == 0:
                    print(".", end="", flush=True)
                if len(readings) >= n_samples:
                    break
    mean = statistics.mean(readings)
    stdev = statistics.stdev(readings) if len(readings) > 1 else 0
    print(f" done.  mean={mean:.1f} dBm  stdev=±{stdev:.1f}")
    return mean

# ── Distance formula ──────────────────────────────────────────────────────────
def rssi_to_dist(rssi: float, A: float, N: float) -> float:
    """A is passed as positive (e.g. 60); negated inside formula."""
    return 10 ** ((-A - rssi) / (10 * N))

def mae(measured_rssi_list, actual_distances, A, N):
    """Mean absolute error in metres across all measured positions."""
    errors = []
    for rssi, actual in zip(measured_rssi_list, actual_distances):
        estimated = rssi_to_dist(rssi, A, N)
        errors.append(abs(estimated - actual))
    return statistics.mean(errors)

# ── Sweep helpers ─────────────────────────────────────────────────────────────
def frange(start, stop, step):
    """Inclusive float range."""
    vals = []
    v = start
    while v <= stop + 1e-9:
        vals.append(round(v, 4))
        v += step
    return vals

def sweep(rssi_readings, label_var, fixed_label, fixed_val,
          var_vals, fixed_is_A):
    """
    Sweep one variable while the other is fixed.
    Returns list of (var_val, mae_val) sorted by MAE ascending.
    """
    results = []
    for v in var_vals:
        A = v          if fixed_is_A is False else fixed_val
        N = fixed_val  if fixed_is_A is False else v
        err = mae(rssi_readings, DISTANCES_M, A, N)
        results.append((v, err))
    results.sort(key=lambda x: x[1])
    return results

# ── Pretty print table ────────────────────────────────────────────────────────
def print_sweep_table(results, var_name, fixed_name, fixed_val, best_n=5):
    print(f"\n  {var_name} sweep  ({fixed_name} fixed at -{fixed_val})")
    print(f"  {'─'*42}")
    print(f"  {'Rank':<6} {var_name:<10} {'MAE (m)':<12} {'Notes'}")
    print(f"  {'─'*42}")
    for rank, (val, err) in enumerate(results[:best_n], 1):
        note = "  ◄ best" if rank == 1 else ""
        print(f"  {rank:<6} -{val:<10.1f} {err:<12.4f}{note}")
    print(f"  {'─'*42}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print("Usage: sudo python3 calibrate_rssi.py <BEACON_MAC>")
        sys.exit(1)

    mac = sys.argv[1].strip()
    print(f"\n{'='*58}")
    print(f"  RSSI Calibration — BLE Distance Tuning")
    print(f"  Beacon MAC: {mac}")
    print(f"{'='*58}\n")

    # ── PHASE 1: Data collection ───────────────────────────────────────────────
    print("PHASE 1 — Collect RSSI at known distances")
    print("  You will be prompted to stand at each distance in turn.\n")

    rssi_readings = []
    for dist in DISTANCES_M:
        input(f"  Stand exactly {dist:.0f} m from the Pi antenna, then press Enter ...")
        print(f"  Measuring at {dist:.0f} m:")
        mean_rssi = collect_rssi(mac, SAMPLES_PER_POSITION)
        rssi_readings.append(mean_rssi)
        print(f"  → Stored: {mean_rssi:.2f} dBm at {dist:.0f} m\n")

    print("\n  Collected RSSI summary:")
    for d, r in zip(DISTANCES_M, rssi_readings):
        print(f"    {d:.0f} m  →  {r:.2f} dBm")

    # ── PHASE 2A: Isolate A (N fixed at midpoint) ──────────────────────────────
    print(f"\n{'='*58}")
    print("PHASE 2A — Isolate A  (N held fixed at midpoint = -N_MID)")
    print(f"           Sweeping A from -{A_MIN} to -{A_MAX} in {A_STEP} steps")
    print(f"{'='*58}")

    A_vals = frange(A_MIN, A_MAX, A_STEP)
    A_sweep_results = sweep(rssi_readings, "A", "N", N_MID,
                            A_vals, fixed_is_A=False)
    print_sweep_table(A_sweep_results, "A", "N", N_MID)
    best_A_isolated = A_sweep_results[0][0]

    # ── PHASE 2B: Isolate N (A fixed at midpoint) ──────────────────────────────
    print(f"\n{'='*58}")
    print("PHASE 2B — Isolate N  (A held fixed at midpoint = -A_MID)")
    print(f"           Sweeping N from {N_MIN} to {N_MAX} in {N_STEP} steps")
    print(f"{'='*58}")

    N_vals = frange(N_MIN, N_MAX, N_STEP)
    N_sweep_results = sweep(rssi_readings, "N", "A", A_MID,
                            N_vals, fixed_is_A=True)

    print(f"\n  N sweep  (A fixed at -{A_MID})")
    print(f"  {'─'*42}")
    print(f"  {'Rank':<6} {'N':<10} {'MAE (m)':<12} {'Notes'}")
    print(f"  {'─'*42}")
    for rank, (val, err) in enumerate(N_sweep_results[:5], 1):
        note = "  ◄ best" if rank == 1 else ""
        print(f"  {rank:<6} {val:<10.2f} {err:<12.4f}{note}")
    print(f"  {'─'*42}")
    best_N_isolated = N_sweep_results[0][0]

    # ── PHASE 3: Full grid search ──────────────────────────────────────────────
    print(f"\n{'='*58}")
    print("PHASE 3 — Full grid search  (all A × N combinations)")
    print(f"{'='*58}")

    grid_results = []
    for A in A_vals:
        for N in N_vals:
            err = mae(rssi_readings, DISTANCES_M, A, N)
            grid_results.append((A, N, err))
    grid_results.sort(key=lambda x: x[2])

    best_A, best_N, best_err = grid_results[0]

    print(f"\n  Top 5 (A, N) combinations by MAE:")
    print(f"  {'─'*50}")
    print(f"  {'Rank':<6} {'A':<10} {'N':<10} {'MAE (m)':<12} {'Notes'}")
    print(f"  {'─'*50}")
    for rank, (A, N, err) in enumerate(grid_results[:5], 1):
        note = "  ◄ best" if rank == 1 else ""
        print(f"  {rank:<6} -{A:<10.1f} {N:<10.2f} {err:<12.4f}{note}")
    print(f"  {'─'*50}")

    # ── PHASE 4: Recommendation ────────────────────────────────────────────────
    print(f"\n{'='*58}")
    print("PHASE 4 — Recommendation")
    print(f"{'='*58}")
    print(f"\n  Isolation results:")
    print(f"    Best A (N fixed at -{N_MID}): -{best_A_isolated:.1f}")
    print(f"    Best N (A fixed at -{A_MID}): {best_N_isolated:.2f}")
    print(f"\n  Combined grid best:")
    print(f"    Best A: -{best_A:.1f}")
    print(f"    Best N: {best_N:.2f}")
    print(f"    MAE:    {best_err:.4f} m  ({best_err*100:.1f} cm average error)")
    print(f"\n  ┌─────────────────────────────────────────────┐")
    print(f"  │  Enter these values in main.py CONFIG:      │")
    print(f"  │                                             │")
    print(f"  │    \"RSSI_A\": -{best_A:.1f},".ljust(46) + "│")
    print(f"  │    \"RSSI_N\":  {best_N:.2f},".ljust(46) + "│")
    print(f"  │                                             │")
    print(f"  └─────────────────────────────────────────────┘")

    # ── Save CSV ───────────────────────────────────────────────────────────────
    csv_path = "rssi_calibration.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["section", "A", "N", "MAE_m"])
        for A, err in A_sweep_results:
            writer.writerow(["isolate_A", f"-{A}", N_MID, f"{err:.4f}"])
        for N, err in N_sweep_results:
            writer.writerow(["isolate_N", f"-{A_MID}", N, f"{err:.4f}"])
        for A, N, err in grid_results:
            writer.writerow(["grid", f"-{A}", N, f"{err:.4f}"])

    print(f"\n  Full results saved to {csv_path}")
    print(f"  Measured RSSI values: {[round(r,1) for r in rssi_readings]}")
    print(f"  At distances (m):     {DISTANCES_M}\n")

if __name__ == "__main__":
    main()
