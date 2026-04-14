# Pet Door Automatic Opener — Software Guide
**Project by Adrian Leon, Henry Wilson, Zachary Brambilla**

---

## System Overview

| Component | Hardware | Code file |
|---|---|---|
| Main controller | Raspberry Pi 4B | `raspberry_pi/main.py` |
| Outdoor camera | ESP32-CAM (OV2640) | `esp32_cam/esp32_cam_petdoor.ino` |
| Dog tag | DFRobot TEL0168 BLE beacon | *(firmware pre-loaded, no changes needed)* |

---

## 1. Raspberry Pi Setup

### 1.1 OS & System Packages

Flash **Raspberry Pi OS Lite (64-bit)** with Raspberry Pi Imager.  
Enable SSH in the imager settings so you can connect headlessly.

```bash
# Update the system
sudo apt update && sudo apt upgrade -y

# Bluetooth stack
sudo apt install -y bluetooth bluez bluez-tools python3-bluez

# libcamera (for Pi Camera recording)
sudo apt install -y libcamera-apps

# Python package manager
sudo apt install -y python3-pip
```

### 1.2 Python Libraries

```bash
cd ~/petdoor/raspberry_pi
pip3 install -r requirements.txt
```

| Library | Purpose |
|---|---|
| `bluepy` | BLE scanning — reads RSSI from the dog-tag beacon |
| `flask` | Local web dashboard at http://\<pi-ip\>:5000 |
| `requests` | Push notifications via ntfy.sh |
| `RPi.GPIO` | GPIO control (relay + stepper) — usually pre-installed |

> **Note:** `bluepy` requires Bluetooth hardware access.
> Run with `sudo` OR add your user to the `bluetooth` group:
> `sudo usermod -aG bluetooth pi` then log out and back in.

### 1.3 Hardware Wiring (BCM pin numbers)

```
Raspberry Pi GPIO   →  Component
──────────────────────────────────────────────────────────
GPIO 14 (Pin 8)    →  RELAY_EXTEND  board  IN
GPIO 15 (Pin 22)   →  RELAY_RETRACT board  IN
Pin 2   (5V)       →  Both relay boards    VCC
Pin 6   (GND)      →  Both relay boards    GND

GPIO 17 (Pin 11)   →  ULN2003  IN1
GPIO 27 (Pin 13)   →  ULN2003  IN2
GPIO 22 (Pin 15)   →  ULN2003  IN3
GPIO 23 (Pin 16)   →  ULN2003  IN4
Pin 4   (5V)       →  ULN2003  +5V
Pin 9   (GND)      →  ULN2003  GND

CSI ribbon cable   →  Pi Camera module (inside housing)
```

### 1.4 Dual-Relay Linear Actuator Wiring

The linear actuator reverses direction when its supply polarity is swapped.
Two SPDT relays wired as a polarity-swap circuit achieve this without any
additional motor driver IC.

**Parts needed:** 2× SPDT relay module (5 V "JD-VCC" style boards work fine —
search "5V single channel relay module", ~$2 each on Amazon).

```
12 V PSU (+) ──────────┬───────────────────────────────┐
                       │                               │
                 RELAY_EXTEND                    RELAY_RETRACT
                  (GPIO 14)                       (GPIO 15)
                 COM ← 12V+                      COM ← 12V+
                 NO  → Actuator Wire A           NO  → Actuator Wire B
                 NC  → leave open                NC  → leave open

12 V PSU (–) ──────────┬───────────────────────────────┐
                       │                               │
             wire to Actuator Wire B      wire to Actuator Wire A
             (second screw terminal       (second screw terminal
              on RELAY_EXTEND board)       on RELAY_RETRACT board)
```

**Truth table:**

| RELAY_EXTEND | RELAY_RETRACT | Wire A | Wire B | Result |
|:---:|:---:|:---:|:---:|:---|
| ON  | OFF | +12 V | GND   | Actuator **extends** (door opens)   |
| OFF | ON  | GND   | +12 V | Actuator **retracts** (door closes) |
| OFF | OFF | open  | open  | Unpowered — safe idle               |
| ON  | ON  | ⚠️ short | ⚠️ short | **NEVER — hardware damage**  |

The code enforces a 100 ms dead-time between switching relays and never sets
both GPIO pins HIGH simultaneously. Verify your wiring carefully before
powering on — a simultaneous short across the 12 V supply will damage both
the power supply and the actuator.

#### Additional hardware you MUST add:
| Item | Why |
|---|---|
| **2× SPDT relay modules** (5 V coil) | One for extend, one for retract. Replaces the single relay from the original BOM. |
| **ULN2003 stepper motor driver board** | The Pi GPIO cannot source enough current to drive the stepper motor directly. (~$2 on Amazon, search "ULN2003 stepper driver") |
| **1N4007 flyback diode × 2** | One across each relay coil (cathode to +V). Protects the Pi from inductive kickback. |
| **100 µF electrolytic capacitor** | Place between the Pi's 5V and GND rails near the GPIO header to smooth voltage spikes from stepper switching. |

### 1.5 Configuration

Edit the `CONFIG` dictionary at the top of `main.py`:

```python
CONFIG = {
    "BEACON_MAC":          "AA:BB:CC:DD:EE:FF",  # ← your beacon's MAC
    "RSSI_A":              -65,    # ← from calibrate_rssi.py
    "RSSI_N":              2.0,    # increase if walls cause inaccuracy
    "TRIGGER_DISTANCE_M":  1.5,    # metres from door to trigger open
    "DOOR_OPEN_HOLD_S":    5,      # seconds door stays open
    "ACTUATOR_TRAVEL_S":   5.0,    # seconds to power actuator each direction
    "STEPPER_STEPS":       200,    # ← from calibrate_stepper.py
    "COOLDOWN_S":          15,     # min seconds between events
    "NTFY_TOPIC":          "my-pet-door-XXXXX",  # or "" to disable
    ...
}
```

> **Tuning `ACTUATOR_TRAVEL_S`:** Start at 5.0 s. Watch the actuator during a
> manual test — increase the value if the door doesn't open/close fully, or
> decrease it if the actuator hammers into its end-stop for an extended time.
> A small amount of end-stop contact is fine; most 12 V actuators have built-in
> stall protection.

### 1.6 Calibration (run once after wiring)

**Step A — Find beacon MAC address:**
```bash
sudo hcitool lescan
# Look for the DFRobot beacon in the list and copy its MAC address
```

**Step B — Calibrate RSSI at 1 metre:**
```bash
python3 calibrate_rssi.py AA:BB:CC:DD:EE:FF
# Hold the beacon exactly 1 m from the Pi.
# Enter the printed value as RSSI_A in main.py CONFIG.
```

**Step C — Calibrate stepper steps for your door handle:**
```bash
sudo python3 calibrate_stepper.py
# Use + / - keys to rotate until the handle fully depresses.
# Enter the printed value as STEPPER_STEPS in main.py CONFIG.
```

**Step D — Test the dual-relay / linear actuator:**
```bash
sudo python3 test_relay.py
# The actuator should extend fully, pause 0.5 s, then retract fully.
# If it only moves in one direction, swap the two actuator wires on
# one of the relay NO terminals and re-run.
```

### 1.7 Running the Controller

**Manual (for testing):**
```bash
sudo python3 main.py
```

**Auto-start on boot (recommended):**
```bash
sudo cp petdoor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable petdoor
sudo systemctl start petdoor

# Check logs:
sudo journalctl -u petdoor -f
```

### 1.8 Web Dashboard

Open a browser on any device on your home network:
```
http://<raspberry-pi-ip>:5000
```
Shows live door status, dog distance, and event log.

### 1.9 Push Notifications

1. Install the free **ntfy** app on your phone (iOS or Android).
2. Subscribe to the topic you set in `NTFY_TOPIC` (e.g., `my-pet-door-12345`).
3. You will receive a notification every time the door opens.

---

## 2. ESP32-CAM Setup

### 2.1 Arduino IDE Configuration

1. Open **Arduino IDE 2.x**.
2. Go to **File → Preferences** and add to "Additional boards manager URLs":
   ```
   https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
   ```
3. Go to **Tools → Board → Boards Manager**, search `esp32`, install the package by **Espressif Systems**.
4. Select board: **Tools → Board → esp32 → AI Thinker ESP32-CAM**.

### 2.2 Required Libraries

No extra libraries needed beyond the `esp32` board package, which includes:
- `esp_camera.h`
- `WiFi.h`
- `WebServer.h`
- `SD_MMC.h`

### 2.3 Configuration

Edit lines near the top of `esp32_cam_petdoor.ino`:
```cpp
const char* WIFI_SSID     = "YourWiFiSSID";
const char* WIFI_PASSWORD = "YourWiFiPassword";
const long  GMT_OFFSET_S  = -21600;  // adjust for your timezone
```

### 2.4 Flashing

1. Connect the FTDI programmer:
   ```
   FTDI VCC (3.3V) → ESP32-CAM 3.3V
   FTDI GND        → ESP32-CAM GND
   FTDI TX         → ESP32-CAM U0R (RX)
   FTDI RX         → ESP32-CAM U0T (TX)
   ESP32 GPIO0     → GND  ← required for flash mode
   ```
2. Click **Upload** in Arduino IDE.
3. After "Connecting…" appears, press the **RST** button on the ESP32-CAM.
4. After upload completes, **remove the GPIO0 → GND wire**.
5. Press **RST** again to boot normally.

### 2.5 Usage

After boot, the serial monitor (115200 baud) prints the ESP32's IP address.

| Endpoint | Method | Description |
|---|---|---|
| `http://<ip>/` | GET | Status page |
| `http://<ip>/stream` | GET | Live MJPEG video stream |
| `http://<ip>/snapshot` | GET | Single JPEG snapshot |
| `http://<ip>/record` | POST | Start recording to SD card |
| `http://<ip>/stop` | POST | Stop recording |

The Raspberry Pi `main.py` automatically calls `/record` when the door opens
and `/stop` 15 seconds later. You can also view the stream on any browser.

---

## 3. Test Procedure Reference

Aligned with Element H test plan:

| Test | Script / Method |
|---|---|
| T1 Ease of install | Stopwatch during physical install |
| T2 Manual door function | Force gauge comparison before/after install |
| T3 BLE distance | `python3 test_ble.py <MAC>` — exports `ble_log.csv` |
| T4 Visual animal detection | View stream at `http://<esp32-ip>/stream` |
| Incremental: relay | `python3 test_relay.py` |
| Incremental: stepper | `python3 calibrate_stepper.py` |
| Incremental: BLE beacon | `python3 test_ble.py` |

---

## 4. File Structure

```
petdoor/
├── raspberry_pi/
│   ├── main.py               ← Main controller (run this)
│   ├── calibrate_rssi.py     ← RSSI calibration utility
│   ├── calibrate_stepper.py  ← Stepper step-count calibration
│   ├── test_relay.py         ← Relay / actuator test
│   ├── test_ble.py           ← BLE distance logger for Test 3
│   ├── requirements.txt      ← Python dependencies
│   └── petdoor.service       ← systemd service for auto-start
└── esp32_cam/
    └── esp32_cam_petdoor/
        └── esp32_cam_petdoor.ino  ← Arduino firmware
```

---

## 5. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Beacon not detected | Wrong MAC in CONFIG | Run `sudo hcitool lescan` to verify MAC |
| Door triggers too early/late | RSSI_A or N needs tuning | Repeat `calibrate_rssi.py` at different distances |
| Stepper doesn't turn handle fully | STEPPER_STEPS too low | Re-run `calibrate_stepper.py` |
| Actuator doesn't move at all | 12 V supply off, or relay not switching | Check relay LED indicators light when GPIO goes HIGH; run `test_relay.py` |
| Actuator extends but won't retract (or vice versa) | Actuator wires swapped on relay NO terminals | Swap the two actuator wires on one relay's NO terminal |
| Actuator slams end-stop repeatedly | `ACTUATOR_TRAVEL_S` too long | Reduce value in CONFIG by 0.5 s increments |
| ESP32 stream not accessible | Wrong WiFi credentials | Check Serial Monitor output |
| `bluepy` permission error | Not running as root / wrong group | Use `sudo` or add user to `bluetooth` group |
