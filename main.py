"""
Pet Door Main Controller
Raspberry Pi 4B

System overview:
  - Scans for BLE beacon (dog tag) using bluepy
  - Calculates distance from RSSI
  - When dog is within threshold, activates opening sequence:
      1. Stepper motor rotates to turn door handle
      2. RELAY_EXTEND energised  → +12 V to actuator pin A, GND to pin B
         (actuator extends / door opens)
      3. 5-second hold timer
      4. RELAY_EXTEND de-energised (brief dead-time to avoid shoot-through)
      5. RELAY_RETRACT energised  → +12 V to actuator pin B, GND to pin A
         (polarity reversed / actuator retracts / door closes)
      6. RELAY_RETRACT de-energised
      7. Stepper motor resets
  - Pi camera records video clips each opening event
  - Sends push notification via ntfy.sh (free, no account required)
  - Web dashboard served locally at http://<pi-ip>:5000

Linear Actuator Dual-Relay Wiring (polarity-swap / H-bridge style)
---------------------------------------------------------------------
Use two identical SPDT 12 V relays (e.g. SRD-12VDC-SL-C).

  12 V supply (+) ──┬──────────────────────────────────────┐
                    │                                      │
               RELAY_EXTEND                          RELAY_RETRACT
               COM = 12 V+                          COM = 12 V+
               NO  → Actuator Wire A                NO  → Actuator Wire B
               NC  → (leave open)                  NC  → (leave open)

  12 V supply (–) ──┬──────────────────────────────────────┐
                    │                                      │
               (wire to Actuator Wire B               (wire to Actuator Wire A
                via second terminal                    via second terminal
                on RELAY_EXTEND board)                 on RELAY_RETRACT board)

  Simplified truth table:
    RELAY_EXTEND ON,  RELAY_RETRACT OFF  →  A=+12V, B=GND  →  EXTEND
    RELAY_EXTEND OFF, RELAY_RETRACT ON   →  A=GND,  B=+12V →  RETRACT
    Both OFF                             →  actuator unpowered (safe idle)
    *** NEVER energise both relays simultaneously ***

GPIO Pin Assignments (BCM numbering):
  GPIO 14  -> RELAY_EXTEND  IN  (active HIGH)
  GPIO 15  -> RELAY_RETRACT IN  (active HIGH)
  GPIO 17  -> Stepper motor IN1 (via ULN2003 driver)
  GPIO 27  -> Stepper motor IN2 (via ULN2003 driver)
  GPIO 22  -> Stepper motor IN3
  GPIO 23  -> Stepper motor IN4
  Pin 2    -> 5V power (relay board VCC for both relays)
  Pin 6    -> Ground   (relay board GND for both relays)
  Pin 4    -> 5V power (UBEC output)
  Pin 9    -> Ground   (UBEC output)
  CSI port -> Pi Camera ribbon cable
"""

import time
import threading
import logging
import json
import os
import subprocess
from collections import deque
from datetime import datetime

import RPi.GPIO as GPIO
from bluepy.btle import Scanner, DefaultDelegate
from flask import Flask, render_template, jsonify, send_from_directory
import requests

# ---------------------------------------------------------------------------
# Configuration  (edit these values to match your setup)
# ---------------------------------------------------------------------------
CONFIG = {
    # BLE beacon MAC address printed on the DFRobot TEL0168 beacon
    "BEACON_MAC": "01:00:00:00:00:00",

    # RSSI calibration (run calibrate_rssi.py first)
    # A  = RSSI at 1 meter (negative integer, e.g. -65)
    # N  = path-loss exponent (typically 2.0 indoors)
    "RSSI_A": -55,
    "RSSI_N": 2.8,

    # Distance in meters that triggers the opening sequence
    "TRIGGER_DISTANCE_M": 1.5,

    # How long (seconds) to keep the door open before closing
    "DOOR_OPEN_HOLD_S": 5,

    # How long (seconds) to power the actuator in each direction.
    # This should be slightly longer than the actuator's full-stroke travel
    # time so it reaches its end-stop. For the 12" 12V actuator in the BOM
    # (typically ~4-5 s full stroke) start with 5.0 and adjust as needed.
    "ACTUATOR_TRAVEL_S": 7.0,

    # Stepper motor: how many steps to rotate the door handle
    # Run calibrate_stepper.py to find the right value for your handle
    "STEPPER_STEPS": 200,

    # Minimum seconds between opening events (prevents rapid re-triggers)
    "COOLDOWN_S": 15,

    # ntfy.sh topic for push notifications (set to "" to disable)
    "NTFY_TOPIC": "my-pet-door-12345",

    # Where to save video clips
    "VIDEO_DIR": "/home/pi/petdoor_videos",

    # Length of each video clip in seconds
    "VIDEO_CLIP_S": 15,

    # Logging level: DEBUG, INFO, WARNING, ERROR
    "LOG_LEVEL": "INFO",
}

# ---------------------------------------------------------------------------
# GPIO Setup
# ---------------------------------------------------------------------------
# Two relays control the linear actuator by swapping polarity.
# NEVER assert both pins HIGH at the same time.
RELAY_EXTEND_PIN  = 14   # HIGH → actuator extends  (door opens)
RELAY_RETRACT_PIN = 15   # HIGH → actuator retracts (door closes)

STEPPER_PINS = [17, 27, 22, 23]  # IN1, IN2, IN3, IN4 on ULN2003

# Dead-time (seconds) between de-energising one relay and energising the other.
# Prevents both relays from being closed simultaneously during switching.
RELAY_DEAD_TIME_S = 0.1

# 4-phase half-step sequence for 28BYJ-48 style stepper
FULL_STEP_SEQ = [
    [1, 0, 1, 0],
    [0, 1, 1, 0],
    [0, 1, 0, 1],
    [1, 0, 0, 1],
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, CONFIG["LOG_LEVEL"]),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/home/vickypi/petdoor.log"),
    ],
)
log = logging.getLogger("petdoor")

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
state = {
    "door_open": False,
    "last_open": None,
    "dog_detected": False,
    "dog_distance_m": None,
    "events": [],          # list of dicts for dashboard
    "video_clips": [],
}
state_lock = threading.Lock()

os.makedirs(CONFIG["VIDEO_DIR"], exist_ok=True)

# ---------------------------------------------------------------------------
# GPIO initialisation
# ---------------------------------------------------------------------------
def gpio_setup():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    # Both relay pins start LOW (both relays de-energised = actuator unpowered)
    GPIO.setup(RELAY_EXTEND_PIN,  GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(RELAY_RETRACT_PIN, GPIO.OUT, initial=GPIO.LOW)
    for pin in STEPPER_PINS:
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
    log.info("GPIO initialised.")


def gpio_cleanup():
    GPIO.cleanup()
    log.info("GPIO cleaned up.")

# ---------------------------------------------------------------------------
# Stepper motor helpers
# ---------------------------------------------------------------------------
def _step(step_index: int):
    """Apply one half-step."""
    for i, pin in enumerate(STEPPER_PINS):
        GPIO.output(pin, FULL_STEP_SEQ[step_index % 4][i])


def stepper_rotate(steps: int, delay_s: float = 0.002, reverse: bool = False):
    """
    Rotate the stepper motor a given number of half-steps.
    Positive steps = clockwise (tightens wire = turns handle down).
    Set reverse=True to return the handle to neutral.
    """
    direction = -1 if reverse else 1
    for i in range(steps):
        _step(i * direction)
        time.sleep(delay_s)
    # De-energise coils to prevent heat build-up
    for pin in STEPPER_PINS:
        GPIO.output(pin, GPIO.LOW)


# ---------------------------------------------------------------------------
# Linear actuator relay helpers (polarity-swap, two-relay H-bridge)
# ---------------------------------------------------------------------------
def _relays_off():
    """De-energise both relays. Safe idle state — actuator unpowered."""
    GPIO.output(RELAY_EXTEND_PIN,  GPIO.LOW)
    GPIO.output(RELAY_RETRACT_PIN, GPIO.LOW)


def actuator_extend():
    """
    Extend the linear actuator (open the door).
    Energises RELAY_EXTEND only. RELAY_RETRACT must already be LOW.
    """
    _relays_off()                          # ensure retract relay is off first
    time.sleep(RELAY_DEAD_TIME_S)
    GPIO.output(RELAY_EXTEND_PIN, GPIO.HIGH)
    log.debug("Relay EXTEND ON — actuator extending.")


def actuator_retract():
    """
    Retract the linear actuator (close the door).
    De-energises RELAY_EXTEND, waits for dead-time, then energises
    RELAY_RETRACT, reversing polarity to the actuator.
    """
    _relays_off()                          # drop extend relay first
    time.sleep(RELAY_DEAD_TIME_S)          # dead-time: contacts fully open
    GPIO.output(RELAY_RETRACT_PIN, GPIO.HIGH)
    log.debug("Relay RETRACT ON — actuator retracting.")


def actuator_stop():
    """De-energise both relays — actuator coasts to stop."""
    _relays_off()
    log.debug("Both relays OFF — actuator stopped.")

# ---------------------------------------------------------------------------
# Camera helpers
# ---------------------------------------------------------------------------
def record_clip(filename: str, duration_s: int = CONFIG["VIDEO_CLIP_S"]):
    """Record a video clip using libcamera-vid (Raspberry Pi camera)."""
    path = os.path.join(CONFIG["VIDEO_DIR"], filename)
    cmd = [
        "libcamera-vid",
        "-t", str(duration_s * 1000),  # milliseconds
        "-o", path,
        "--width", "1280",
        "--height", "720",
        "--framerate", "15",
        "--nopreview",
    ]
    try:
        subprocess.Popen(cmd)
        log.info(f"Recording clip: {path}")
        with state_lock:
            state["video_clips"].append(filename)
            if len(state["video_clips"]) > 50:
                state["video_clips"].pop(0)
    except Exception as e:
        log.error(f"Camera error: {e}")

# ---------------------------------------------------------------------------
# Push notification
# ---------------------------------------------------------------------------
def send_notification(message: str):
    topic = CONFIG.get("NTFY_TOPIC", "")
    if not topic:
        return
    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers={"Title": "Pet Door", "Priority": "default"},
            timeout=5,
        )
        log.info(f"Notification sent: {message}")
    except Exception as e:
        log.warning(f"Notification failed: {e}")

# ---------------------------------------------------------------------------
# Door open/close sequence
# ---------------------------------------------------------------------------
def open_door_sequence():
    """Full open-then-close sequence. Runs in its own thread."""
    now = datetime.now()
    with state_lock:
        state["door_open"] = True
        state["last_open"] = now.isoformat()
        state["events"].insert(0, {
            "time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "event": "Door opened",
        })
        if len(state["events"]) > 100:
            state["events"].pop()

    log.info("--- Opening sequence START ---")

    # Step 1: Turn door handle via stepper motor
    log.info("Stepper: rotating to open latch...")
    stepper_rotate(CONFIG["STEPPER_STEPS"])
    time.sleep(0.3)  # brief pause for latch to disengage

    # Step 2: Extend linear actuator (RELAY_EXTEND ON, RELAY_RETRACT OFF)
    log.info("Actuator EXTENDING — door opening...")
    actuator_extend()

    # Allow time for the actuator to travel to full extension.
    # Adjust ACTUATOR_TRAVEL_S in CONFIG if the door doesn't open fully.
    time.sleep(CONFIG["ACTUATOR_TRAVEL_S"])

    # Cut power once fully extended so motor isn't stalled against end-stop
    actuator_stop()

    # Record a video clip of the event
    clip_name = now.strftime("clip_%Y%m%d_%H%M%S.h264")
    record_clip(clip_name)

    # Send notification
    send_notification("🐾 Your pet is at the door — door is opening!")

    # Step 3: Hold door open
    log.info(f"Holding door open for {CONFIG['DOOR_OPEN_HOLD_S']}s...")
    time.sleep(CONFIG["DOOR_OPEN_HOLD_S"])

    # Step 4: Retract linear actuator (polarity reversed —
    #         RELAY_EXTEND OFF, dead-time, RELAY_RETRACT ON)
    log.info("Actuator RETRACTING — door closing...")
    actuator_retract()

    # Allow same travel time for full retraction
    time.sleep(CONFIG["ACTUATOR_TRAVEL_S"])

    # De-energise retract relay once fully retracted
    actuator_stop()
    time.sleep(0.2)

    # Step 5: Return stepper motor to neutral (reset handle)
    log.info("Stepper: returning handle to neutral...")
    stepper_rotate(CONFIG["STEPPER_STEPS"], reverse=True)

    with state_lock:
        state["door_open"] = False
        state["events"].insert(0, {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "event": "Door closed",
        })

    log.info("--- Opening sequence END ---")


# ---------------------------------------------------------------------------
# BLE distance calculation
# ---------------------------------------------------------------------------
class BLEDelegate(DefaultDelegate):
    def __init__(self):
        super().__init__()

    def handleDiscovery(self, dev, isNewDev, isNewData):
        pass  # handled in scan loop


def rssi_to_distance(rssi: int) -> float:
    """
    Convert RSSI (dBm) to distance (metres).
    Formula: d = 10 ^ ((A - RSSI) / (10 * N))
    where A = RSSI at 1 m, N = path-loss exponent.
    """
    A = CONFIG["RSSI_A"]
    N = CONFIG["RSSI_N"]
    return 10 ** ((A - rssi) / (10 * N))


# ---------------------------------------------------------------------------
# BLE scan loop  (runs in its own daemon thread)
# ---------------------------------------------------------------------------
def ble_scan_loop():
    scanner = Scanner().withDelegate(BLEDelegate())
    target_mac = CONFIG["BEACON_MAC"].lower()
    trigger_dist = CONFIG["TRIGGER_DISTANCE_M"]
    cooldown = CONFIG["COOLDOWN_S"]

    rssi_window = deque(maxlen=3)

    last_trigger_time = 0.0
    door_thread = None

    log.info(f"BLE scan loop started. Target MAC: {target_mac}")

    while True:
        try:
            devices = scanner.scan(1.0)  # scan for 1 second
            beacon_found = False
            
            for dev in devices:
                if dev.addr.lower() == target_mac:
                    beacon_found = True
                    rssi_window.append(dev.rssi)

                    # Only compute distance once we have a full 3-second window
                    if len(rssi_window) < 3:
                        log.debug(f"Beacon RSSI={dev.rssi} dBm — building average ({len(rssi_window)}/3)")
                        continue

                    avg_rssi = sum(rssi_window) / len(rssi_window)
                    dist = rssi_to_distance(avg_rssi)

                    with state_lock:
                        state["dog_detected"] = True
                        state["dog_distance_m"] = round(dist, 2)

                    log.debug(f"Beacon avg RSSI={avg_rssi:.1f} dBm  dist≈{dist:.2f}m")

                    now = time.time()
                    door_busy = door_thread and door_thread.is_alive()
                    since_last = now - last_trigger_time

                    if dist <= trigger_dist and not door_busy and since_last >= cooldown:
                        log.info(f"Dog within {dist:.2f}m — triggering door.")
                        last_trigger_time = now
                        door_thread = threading.Thread(target=open_door_sequence, daemon=True)
                        door_thread.start()

            if not beacon_found:
                with state_lock:
                    state["dog_detected"] = False
                    state["dog_distance_m"] = None

        except Exception as e:
            log.error(f"BLE scan error: {e}")
            time.sleep(2)
# ---------------------------------------------------------------------------
# Flask web dashboard
# ---------------------------------------------------------------------------
app = Flask(__name__)

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pet Door Dashboard</title>
<style>
  body { font-family: Arial, sans-serif; background:#f4f4f4; margin:0; padding:20px; }
  h1   { color:#333; }
  .card { background:#fff; border-radius:8px; padding:16px; margin-bottom:16px;
          box-shadow:0 2px 4px rgba(0,0,0,.1); }
  .status-open   { color:#e74c3c; font-weight:bold; }
  .status-closed { color:#27ae60; font-weight:bold; }
  table { border-collapse:collapse; width:100%; }
  th,td { text-align:left; padding:8px; border-bottom:1px solid #ddd; }
  th    { background:#f0f0f0; }
</style>
<script>
  function refresh() {
    fetch('/api/state').then(r=>r.json()).then(data=>{
      document.getElementById('door-status').textContent =
        data.door_open ? 'OPEN' : 'CLOSED';
      document.getElementById('door-status').className =
        data.door_open ? 'status-open' : 'status-closed';
      document.getElementById('dog-dist').textContent =
        data.dog_distance_m !== null ? data.dog_distance_m + ' m' : 'Not detected';
      let rows = data.events.slice(0,20).map(e=>
        `<tr><td>${e.time}</td><td>${e.event}</td></tr>`).join('');
      document.getElementById('events-body').innerHTML = rows;
    });
  }
  setInterval(refresh, 2000);
  window.onload = refresh;
</script>
</head>
<body>
<h1>🐾 Pet Door Dashboard</h1>
<div class="card">
  <h2>Door Status: <span id="door-status">—</span></h2>
  <p>Dog distance: <strong><span id="dog-dist">—</span></strong></p>
</div>
<div class="card">
  <h2>Recent Events</h2>
  <table>
    <thead><tr><th>Time</th><th>Event</th></tr></thead>
    <tbody id="events-body"></tbody>
  </table>
</div>
</body>
</html>
"""

@app.route("/")
def dashboard():
    return DASHBOARD_HTML

@app.route("/api/state")
def api_state():
    with state_lock:
        return jsonify(dict(state))

@app.route("/videos/<path:filename>")
def serve_video(filename):
    return send_from_directory(CONFIG["VIDEO_DIR"], filename)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    gpio_setup()

    # Start BLE scan in background thread
    ble_thread = threading.Thread(target=ble_scan_loop, daemon=True)
    ble_thread.start()

    log.info("Pet Door controller running. Dashboard at http://localhost:5000")

    try:
        # Run Flask (blocking)
        app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        log.info("Shutting down.")
    finally:
        actuator_stop()   # ensure both relays de-energised on exit
        gpio_cleanup()


if __name__ == "__main__":
    main()
