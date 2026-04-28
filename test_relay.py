import RPi.GPIO as GPIO
import time

RELAY_EXTEND_PIN  = 14
RELAY_RETRACT_PIN = 15
RELAY_DEAD_TIME_S = 0.1

TRAVEL_S = 6.0

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(RELAY_EXTEND_PIN,  GPIO.OUT, initial=GPIO.LOW)
GPIO.setup(RELAY_RETRACT_PIN, GPIO.OUT, initial=GPIO.LOW)

def relays_off():
    GPIO.output(RELAY_EXTEND_PIN,  GPIO.LOW)
    GPIO.output(RELAY_RETRACT_PIN, GPIO.LOW)

try:
    print(f"[1/4] EXTEND  — RELAY_EXTEND ON, RELAY_RETRACT OFF")
    print(f"      Actuator should extend for {TRAVEL_S} s ...")
    relays_off()
    time.sleep(RELAY_DEAD_TIME_S)
    GPIO.output(RELAY_EXTEND_PIN, GPIO.HIGH)
    time.sleep(TRAVEL_S)

    print("[2/4] STOP    — both relays OFF (0.5 s pause)")
    relays_off()
    time.sleep(0.5)

    print(f"[3/4] RETRACT — RELAY_RETRACT ON, RELAY_EXTEND OFF")
    print(f"      Actuator should retract for {TRAVEL_S} s ...")
    time.sleep(RELAY_DEAD_TIME_S)
    GPIO.output(RELAY_RETRACT_PIN, GPIO.HIGH)
    time.sleep(TRAVEL_S)

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
