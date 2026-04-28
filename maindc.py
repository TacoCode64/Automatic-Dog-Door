"""
Pet Door Main Controller
Raspberry Pi 4B  —  lgpio edition (kernel 6.6+)

System overview:
  - Scans for BLE beacon (dog tag) using bluepy
  - Calculates distance from RSSI
  - When dog is within threshold, activates opening sequence:
      1. DC motor (L298N) runs forward for MOTOR_TURN_S seconds to depress handle
      2. RELAY_EXTEND energised  → +12 V to actuator pin A, GND to pin B
         (actuator extends / door opens)
      3. DOOR_OPEN_HOLD_S second hold timer
      4. RELAY_EXTEND de-energised (brief dead-time to avoid shoot-through)
      5. RELAY_RETRACT energised  → +12 V to actuator pin B, GND to pin A
         (polarity reversed / actuator retracts / door closes)
      6. RELAY_RETRACT de-energised
      7. DC motor runs backward for MOTOR_TURN_S seconds to return handle
  - Pi camera records video clips on each opening event
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

DC Motor (L298N) Wiring:
  ENA  → MOTOR_ENA_PIN  (PWM-capable GPIO, e.g. GPIO 12)
  IN1  → MOTOR_IN1_PIN  (e.g. GPIO 24)
  IN2  → MOTOR_IN2_PIN  (e.g. GPIO 25)
  Motor terminals → L298N OUT1 / OUT2
  L298N 12V → 12V supply, GND shared with Pi

GPIO Pin Assignments (BCM numbering):
  GPIO 14  -> RELAY_EXTEND  IN  (active HIGH)
  GPIO 15  -> RELAY_RETRACT IN  (active HIGH)
  GPIO 12  -> L298N ENA     (PWM)
  GPIO 24  -> L298N IN1
  GPIO 25  -> L298N IN2
  Pin 2    -> 5V power (relay board VCC for both relays)
  Pin 6    -> Ground   (relay board GND for both relays)
  Pin 4    -> 5V power (UBEC output / L298N logic)
  Pin 9    -> Ground   (UBEC output / L298N logic)
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

import lgpio
from bluepy.btle import Scanner, DefaultDelegate
from flask import Flask, jsonify, send_from_directory
import requests

# ---------------------------------------------------------------------------
# Configuration  (edit these values to match your setup)
# ---------------------------------------------------------------------------
CONFIG = {
    # BLE beacon MAC address printed on the DFRobot TEL0168 beacon
    "BEACON_MAC": "06:05:04:03:02:01",

    # RSSI calibration (run calibrate_rssi.py first)
    # A  = RSSI at 1 meter (negative integer, e.g. -65)
    # N  = path-loss exponent (typically 2.0 indoors)
    "RSSI_A": -55,
    "RSSI_N": 2.8,

    # Distance in meters that triggers the opening sequence
    "TRIGGER_DISTANCE_M": 2.0,

    # How long (seconds) to keep the door open before closing
    "DOOR_OPEN_HOLD_S": 5,

    # How long (seconds) to power the actuator in each direction.
    # Slightly longer than the actuator's full-stroke travel time.
    "ACTUATOR_TRAVEL_S": 17.0,

    # How long (seconds) to run the DC motor to fully depress the door handle.
    # Run calibrate_dc.py to find the correct value for your setup.
    "MOTOR_TURN_S": 5,

    # DC motor PWM duty cycle (0–100). Increase if the motor stalls.
    "MOTOR_SPEED": 50,

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
# GPIO pin constants
# ---------------------------------------------------------------------------
RELAY_EXTEND_PIN  = 14   # HIGH → actuator extends  (door opens)
RELAY_RETRACT_PIN = 15   # HIGH → actuator retracts (door closes)

MOTOR_ENA_PIN = 12   # PWM-capable pin
MOTOR_IN1_PIN = 5
MOTOR_IN2_PIN = 6

# Dead-time between relay switching to prevent simultaneous closure
RELAY_DEAD_TIME_S = 0.1

# PWM carrier frequency for the motor enable pin
_PWM_FREQ = 1000  # Hz

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, CONFIG["LOG_LEVEL"]),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/home/pi/petdoor.log"),
    ],
)
log = logging.getLogger("petdoor")

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
state = {
    "door_open": False,
    "last_open": None,
    "dog_detected": False,
    "dog_distance_m": None,
    "events": [],
    "video_clips": [],
}
state_lock = threading.Lock()

os.makedirs(CONFIG["VIDEO_DIR"], exist_ok=True)

# lgpio chip handle — set in gpio_setup()
_chip = None

# ---------------------------------------------------------------------------
# GPIO initialisation
# ---------------------------------------------------------------------------
def gpio_setup():
    global _chip
    _chip = lgpio.gpiochip_open(0)

    # Relay pins — both start LOW (actuator unpowered)
    lgpio.gpio_claim_output(_chip, RELAY_EXTEND_PIN,  0)
    lgpio.gpio_claim_output(_chip, RELAY_RETRACT_PIN, 0)

    # L298N motor driver pins
    lgpio.gpio_claim_output(_chip, MOTOR_IN1_PIN, 0)
    lgpio.gpio_claim_output(_chip, MOTOR_IN2_PIN, 0)
    lgpio.gpio_claim_output(_chip, MOTOR_ENA_PIN, 0)

    log.info("GPIO initialised (DC motor, time-based control).")


def gpio_cleanup():
    if _chip is not None:
        _motor_stop()
        _relays_off()
        lgpio.gpiochip_close(_chip)
    log.info("GPIO cleaned up.")

# ---------------------------------------------------------------------------
# DC motor helpers
# ---------------------------------------------------------------------------
def _motor_forward(speed: int = None):
    if speed is None:
        speed = CONFIG["MOTOR_SPEED"]
    lgpio.gpio_write(_chip, MOTOR_IN1_PIN, 1)
    lgpio.gpio_write(_chip, MOTOR_IN2_PIN, 0)
    lgpio.tx_pwm(_chip, MOTOR_ENA_PIN, _PWM_FREQ, speed)


def _motor_backward(speed: int = None):
    if speed is None:
        speed = CONFIG["MOTOR_SPEED"]
    lgpio.gpio_write(_chip, MOTOR_IN1_PIN, 0)
    lgpio.gpio_write(_chip, MOTOR_IN2_PIN, 1)
    lgpio.tx_pwm(_chip, MOTOR_ENA_PIN, _PWM_FREQ, speed)


def _motor_stop():
    lgpio.gpio_write(_chip, MOTOR_IN1_PIN, 0)
    lgpio.gpio_write(_chip, MOTOR_IN2_PIN, 0)
    lgpio.gpio_write(_chip, MOTOR_ENA_PIN, 0)


def motor_turn_handle():
    """
    Run the DC motor forward for MOTOR_TURN_S seconds to depress the door
    handle, then stop.
    """
    duration = CONFIG["MOTOR_TURN_S"]
    log.info(f"DC motor: rotating handle for {duration}s...")
    _motor_forward()
    time.sleep(duration)
    _motor_stop()
    log.info("DC motor: handle depressed — latch disengaged.")


def motor_reset_handle():
    """
    Run the DC motor backward for MOTOR_TURN_S seconds to return the handle
    to neutral, then stop.
    """
    duration = CONFIG["MOTOR_TURN_S"]
    log.info(f"DC motor: returning handle for {duration}s...")
    _motor_backward()
    time.sleep(duration)
    _motor_stop()
    log.info("DC motor: handle returned to neutral.")

# ---------------------------------------------------------------------------
# Linear actuator relay helpers (polarity-swap, two-relay H-bridge)
# ---------------------------------------------------------------------------
def _relays_off():
    """De-energise both relays. Safe idle state — actuator unpowered."""
    lgpio.gpio_write(_chip, RELAY_EXTEND_PIN,  0)
    lgpio.gpio_write(_chip, RELAY_RETRACT_PIN, 0)


def actuator_extend():
    """Extend the linear actuator (open the door)."""
    _relays_off()
    time.sleep(RELAY_DEAD_TIME_S)
    lgpio.gpio_write(_chip, RELAY_EXTEND_PIN, 1)
    log.debug("Relay EXTEND ON — actuator extending.")


def actuator_retract():
    """Retract the linear actuator (close the door)."""
    _relays_off()
    time.sleep(RELAY_DEAD_TIME_S)
    lgpio.gpio_write(_chip, RELAY_RETRACT_PIN, 1)
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
        "-t", str(duration_s * 1000),
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

    # Step 1: Depress door handle via DC motor (timed)
    log.info("DC motor: rotating to open latch...")
    motor_turn_handle()
    time.sleep(0.3)   # brief pause for latch to fully disengage

    # Step 2: Extend linear actuator
    log.info("Actuator EXTENDING — door opening...")
    actuator_extend()
    time.sleep(CONFIG["ACTUATOR_TRAVEL_S"])
    actuator_stop()

    # Record video and send notification
    clip_name = now.strftime("clip_%Y%m%d_%H%M%S.h264")
    record_clip(clip_name)
    send_notification("🐾 Your pet is at the door — door is opening!")

    # Step 3: Hold door open
    log.info(f"Holding door open for {CONFIG['DOOR_OPEN_HOLD_S']}s...")
    time.sleep(CONFIG["DOOR_OPEN_HOLD_S"])

    # Step 4: Retract linear actuator
    log.info("Actuator RETRACTING — door closing...")
    actuator_retract()
    time.sleep(CONFIG["ACTUATOR_TRAVEL_S"])
    actuator_stop()
    time.sleep(0.2)

    # Step 5: Return DC motor to neutral (timed)
    log.info("DC motor: returning handle to neutral...")
    motor_reset_handle()

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
        pass


def rssi_to_distance(rssi: int) -> float:
    """
    Convert RSSI (dBm) to distance (metres).
    Formula: d = 10 ^ ((A - RSSI) / (10 * N))
    """
    A = CONFIG["RSSI_A"]
    N = CONFIG["RSSI_N"]
    return 10 ** ((A - rssi) / (10 * N))

# ---------------------------------------------------------------------------
# BLE scan loop  (runs in its own daemon thread)
# ---------------------------------------------------------------------------
def ble_scan_loop():
    scanner = Scanner().withDelegate(BLEDelegate())
    target_mac   = CONFIG["BEACON_MAC"].lower()
    trigger_dist = CONFIG["TRIGGER_DISTANCE_M"]
    cooldown     = CONFIG["COOLDOWN_S"]

    rssi_window = deque(maxlen=3)
    last_trigger_time = 0.0
    door_thread = None

    log.info(f"BLE scan loop started. Target MAC: {target_mac}")

    while True:
        try:
            devices = scanner.scan(1.0)
            beacon_found = False

            for dev in devices:
                if dev.addr.lower() == target_mac:
                    beacon_found = True
                    rssi_window.append(dev.rssi)

                    if len(rssi_window) < 3:
                        log.debug(
                            f"Beacon RSSI={dev.rssi} dBm — "
                            f"building average ({len(rssi_window)}/3)"
                        )
                        continue

                    avg_rssi = sum(rssi_window) / len(rssi_window)
                    dist = rssi_to_distance(avg_rssi)

                    with state_lock:
                        state["dog_detected"]   = True
                        state["dog_distance_m"] = round(dist, 2)

                    log.debug(f"Beacon avg RSSI={avg_rssi:.1f} dBm  dist≈{dist:.2f}m")

                    now_t     = time.time()
                    door_busy = door_thread and door_thread.is_alive()
                    since     = now_t - last_trigger_time

                    if dist <= trigger_dist and not door_busy and since >= cooldown:
                        log.info(f"Dog within {dist:.2f}m — triggering door.")
                        last_trigger_time = now_t
                        door_thread = threading.Thread(
                            target=open_door_sequence, daemon=True
                        )
                        door_thread.start()

            if not beacon_found:
                with state_lock:
                    state["dog_detected"]   = False
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

    ble_thread = threading.Thread(target=ble_scan_loop, daemon=True)
    ble_thread.start()

    log.info("Pet Door controller running. Dashboard at http://localhost:5000")

    try:
        app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        log.info("Shutting down.")
    finally:
        _motor_stop()
        actuator_stop()
        gpio_cleanup()


if __name__ == "__main__":
    main()
