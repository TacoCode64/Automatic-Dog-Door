import time
import threading
import logging
from collections import deque
from datetime import datetime

import lgpio
from bluepy.btle import Scanner, DefaultDelegate
from flask import Flask, jsonify
import requests

CONFIG = {
    "BEACON_MAC": "06:05:04:03:02:01",

    "RSSI_A": -55,
    "RSSI_N": 2.8,

    "TRIGGER_DISTANCE_M": 1.8,

    "DOOR_OPEN_HOLD_S": 5,

    "ACTUATOR_TRAVEL_S": 21.0,

    "MOTOR_TURN_S": 5,

    "MOTOR_SPEED": 50,

    "COOLDOWN_S": 15,

    "NTFY_TOPIC": "my-pet-door-12345",

    "ESP32_CAM_IP": "192.168.1.100",

    "ESP32_TIMEOUT_S": 3,

    "LOG_LEVEL": "INFO",
}

RELAY_EXTEND_PIN  = 14
RELAY_RETRACT_PIN = 15
MOTOR_ENA_PIN = 12
MOTOR_IN1_PIN = 5
MOTOR_IN2_PIN = 6

RELAY_DEAD_TIME_S = 0.1

_PWM_FREQ = 1000  # Hz

logging.basicConfig(
    level=getattr(logging, CONFIG["LOG_LEVEL"]),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/home/pi/petdoor.log"),
    ],
)
log = logging.getLogger("petdoor")

state = {
    "door_open":       False,
    "last_open":       None,
    "dog_detected":    False,
    "dog_distance_m":  None,
    "esp32_recording": False,
    "events":          [],
}
state_lock = threading.Lock()

_chip = None

def gpio_setup():
    global _chip
    _chip = lgpio.gpiochip_open(0)

    lgpio.gpio_claim_output(_chip, RELAY_EXTEND_PIN,  0)
    lgpio.gpio_claim_output(_chip, RELAY_RETRACT_PIN, 0)

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

def esp32_record_start():
    """
    Tell the ESP32-CAM to start recording to its SD card.
    Called the moment the door opens (dog is now outside).
    Failure is logged but does NOT abort the door sequence.
    """
    ip = CONFIG.get("ESP32_CAM_IP", "")
    if not ip:
        log.debug("ESP32_CAM_IP not set — skipping record start.")
        return
    try:
        resp = requests.post(
            f"http://{ip}/record",
            timeout=CONFIG["ESP32_TIMEOUT_S"],
        )
        log.info(f"ESP32 record start → {resp.status_code}: {resp.text.strip()}")
        with state_lock:
            state["esp32_recording"] = True
    except Exception as e:
        log.warning(f"ESP32 record start failed: {e}")


def esp32_record_stop():
    """
    Tell the ESP32-CAM to stop recording and finalise the session on SD.
    Called after the door has fully retracted (dog is back inside).
    """
    ip = CONFIG.get("ESP32_CAM_IP", "")
    if not ip:
        log.debug("ESP32_CAM_IP not set — skipping record stop.")
        return
    try:
        resp = requests.post(
            f"http://{ip}/stop",
            timeout=CONFIG["ESP32_TIMEOUT_S"],
        )
        log.info(f"ESP32 record stop  → {resp.status_code}: {resp.text.strip()}")
        with state_lock:
            state["esp32_recording"] = False
    except Exception as e:
        log.warning(f"ESP32 record stop failed: {e}")

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

    log.info("DC motor: rotating to open latch...")
    motor_turn_handle()
    time.sleep(0.3)

    log.info("Actuator EXTENDING — door opening...")
    actuator_extend()
    time.sleep(CONFIG["ACTUATOR_TRAVEL_S"])
    actuator_stop()

    log.info("ESP32-CAM: starting outdoor recording...")
    esp32_record_start()

    send_notification("🐾 Your pet is at the door — door is opening!")

    log.info(f"Holding door open for {CONFIG['DOOR_OPEN_HOLD_S']}s...")
    time.sleep(CONFIG["DOOR_OPEN_HOLD_S"])

    log.info("DC motor: returning handle to neutral...")
    motor_reset_handle()

    log.info("Actuator RETRACTING — door closing...")
    actuator_retract()
    time.sleep(CONFIG["ACTUATOR_TRAVEL_S"]+1.275)
    actuator_stop()
    time.sleep(0.2)

    log.info("ESP32-CAM: stopping recording...")
    esp32_record_stop()

    with state_lock:
        state["door_open"] = False
        state["events"].insert(0, {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "event": "Door closed",
        })

    log.info("--- Opening sequence END ---")

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
  .rec-yes       { color:#e67e22; font-weight:bold; }
  .rec-no        { color:#95a5a6; }
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
      document.getElementById('esp32-rec').textContent =
        data.esp32_recording ? 'RECORDING' : 'Idle';
      document.getElementById('esp32-rec').className =
        data.esp32_recording ? 'rec-yes' : 'rec-no';
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
  <p>ESP32-CAM: <strong><span id="esp32-rec">—</span></strong></p>
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
