"""
test_relay.py  —  Raspberry Pi
================================
Tests the dual-relay polarity-swap wiring for the linear actuator.

Sequence:
  1. Extend  — RELAY_EXTEND ON, RELAY_RETRACT OFF   (+12V to Wire A)
  2. Stop    — both relays OFF (0.5 s pause)
  3. Retract — RELAY_RETRACT ON, RELAY_EXTEND OFF   (+12V to Wire B)
  4. Stop    — both relays OFF

Run this before main.py to confirm polarity-swap wiring is correct.
The actuator should extend fully, pause, then retract fully.

GPIO pins must match main.py:
  RELAY_EXTEND_PIN  = 14   (GPIO 14, physical Pin 8)
  RELAY_RETRACT_PIN = 15   (GPIO 15, physical Pin 22)

Usage:
    sudo python3 test_relay.py
"""

import RPi.GPIO as GPIO
import time

RELAY_EXTEND_PIN  = 8
RELAY_RETRACT_PIN = 22
RELAY_DEAD_TIME_S = 0.1   # safety gap between switching relays

# Travel time in seconds — adjust to match your actuator's stroke speed.
# The 12" 12V actuator in the BOM typically takes 4-5 s for a full stroke.
TRAVEL_S = 6.0

# ---------------------------------------------------------------------------
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(RELAY_EXTEND_PIN,  GPIO.OUT, initial=GPIO.LOW)
GPIO.setup(RELAY_RETRACT_PIN, GPIO.OUT, initial=GPIO.LOW)

def relays_off():
    GPIO.output(RELAY_EXTEND_PIN,  GPIO.LOW)
    GPIO.output(RELAY_RETRACT_PIN, GPIO.LOW)

try:
    # --- Step 1: Extend ---
    print(f"[1/4] EXTEND  — RELAY_EXTEND ON, RELAY_RETRACT OFF")
    print(f"      Actuator should extend for {TRAVEL_S} s ...")
    relays_off()
    time.sleep(RELAY_DEAD_TIME_S)
    GPIO.output(RELAY_EXTEND_PIN, GPIO.HIGH)
    time.sleep(TRAVEL_S)

    # --- Step 2: Stop briefly ---
    print("[2/4] STOP    — both relays OFF (0.5 s pause)")
    relays_off()
    time.sleep(0.5)

    # --- Step 3: Retract ---
    print(f"[3/4] RETRACT — RELAY_RETRACT ON, RELAY_EXTEND OFF")
    print(f"      Actuator should retract for {TRAVEL_S} s ...")
    time.sleep(RELAY_DEAD_TIME_S)
    GPIO.output(RELAY_RETRACT_PIN, GPIO.HIGH)
    time.sleep(TRAVEL_S)

    # --- Step 4: Stop ---
    print("[4/4] STOP    — both relays OFF")
    relays_off()

    print("\nTest complete.")
    print("  If the actuator extended then retracted: wiring is correct.")
    print("  If it only moved in one direction: swap the two actuator wires")
    print("    on one of the relay NO terminals.")
    print("  If it didn't move at all: check 12V supply and relay LED")
    print("    indicators (they should light when GPIO goes HIGH).")

except KeyboardInterrupt:
    print("\nAborted by user.")

finally:
    relays_off()
    GPIO.cleanup()
