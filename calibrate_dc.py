"""
calibrate_dc_motor.py  —  Raspberry Pi
========================================
Use this script to find the correct number of encoder ticks (degrees)
needed to fully depress your specific door handle using a DC motor
driven by an L298N and tracked by a rotary encoder.

Controls:
  +   run motor forward until you release the key  (watch the handle rotate)
  -   run motor backward until you release the key
  r   reset (return to zero position using encoder feedback)
  q   quit and print the final tick count / angle to enter in main.py CONFIG

L298N Wiring:
  ENA  → MOTOR_ENA_PIN  (PWM-capable GPIO, e.g. GPIO 12)
  IN1  → MOTOR_IN1_PIN  (e.g. GPIO 24)
  IN2  → MOTOR_IN2_PIN  (e.g. GPIO 25)
  Motor terminals → L298N OUT1 / OUT2
  L298N 12V → 12V supply, GND shared with Pi

Rotary Encoder Wiring:
  CLK (A) → ENCODER_CLK_PIN  (e.g. GPIO 5)
  DT  (B) → ENCODER_DT_PIN   (e.g. GPIO 6)
  GND     → Pi GND
  VCC     → Pi 3.3V

Usage:
    python3 calibrate_dc_motor.py
"""

import sys
import tty
import termios
import select
import RPi.GPIO as GPIO
import time

# ── Pin configuration ──────────────────────────────────────────────────────────
MOTOR_ENA_PIN   = 12   # PWM-capable pin (hardware PWM preferred)
MOTOR_IN1_PIN   = 24
MOTOR_IN2_PIN   = 25

ENCODER_CLK_PIN = 5    # Channel A
ENCODER_DT_PIN  = 6    # Channel B

# ── Motor settings ─────────────────────────────────────────────────────────────
PWM_FREQ        = 1000  # Hz
MOTOR_SPEED     = 100    # Duty cycle 0–100 (start low, increase if stalling)

# ── Encoder settings ───────────────────────────────────────────────────────────
TICKS_PER_REV   = 20    # PPR of your encoder (pulses per revolution, rising edges only)
                        # Adjust to match your encoder's spec sheet.
                        # Common values: 20, 100, 360, 600

# ── GPIO setup ─────────────────────────────────────────────────────────────────
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.cleanup()
GPIO.setup(MOTOR_IN1_PIN, GPIO.OUT, initial=GPIO.LOW)
GPIO.setup(MOTOR_IN2_PIN, GPIO.OUT, initial=GPIO.LOW)
GPIO.setup(MOTOR_ENA_PIN, GPIO.OUT, initial=GPIO.LOW)

GPIO.setup(ENCODER_CLK_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(ENCODER_DT_PIN,  GPIO.IN, pull_up_down=GPIO.PUD_UP)

pwm = GPIO.PWM(MOTOR_ENA_PIN, PWM_FREQ)
pwm.start(0)

# ── Encoder state ──────────────────────────────────────────────────────────────
encoder_ticks = 0
_last_clk     = GPIO.input(ENCODER_CLK_PIN)

def _encoder_callback(channel):
    """Interrupt-driven encoder tick counter (single-channel / rising-edge)."""
    global encoder_ticks, _last_clk
    clk_state = GPIO.input(ENCODER_CLK_PIN)
    dt_state  = GPIO.input(ENCODER_DT_PIN)
    if clk_state != _last_clk:          # state changed → a pulse edge
        if dt_state != clk_state:
            encoder_ticks += 1          # clockwise
        else:
            encoder_ticks -= 1          # counter-clockwise
        _last_clk = clk_state

GPIO.add_event_detect(
    ENCODER_CLK_PIN,
    GPIO.BOTH,
    callback=_encoder_callback,
    bouncetime=50,
)

# ── Motor helpers ──────────────────────────────────────────────────────────────
def motor_forward(speed=MOTOR_SPEED):
    GPIO.output(MOTOR_IN1_PIN, GPIO.HIGH)
    GPIO.output(MOTOR_IN2_PIN, GPIO.LOW)
    pwm.ChangeDutyCycle(speed)

def motor_backward(speed=MOTOR_SPEED):
    GPIO.output(MOTOR_IN1_PIN, GPIO.LOW)
    GPIO.output(MOTOR_IN2_PIN, GPIO.HIGH)
    pwm.ChangeDutyCycle(speed)

def motor_stop():
    GPIO.output(MOTOR_IN1_PIN, GPIO.LOW)
    GPIO.output(MOTOR_IN2_PIN, GPIO.LOW)
    pwm.ChangeDutyCycle(0)

# ── Reset: drive back to encoder zero ─────────────────────────────────────────
def reset_to_zero(timeout=10.0):
    """Drive motor until encoder reads 0, with a safety timeout."""
    global encoder_ticks
    print("Resetting to zero …")
    deadline = time.time() + timeout
    while abs(encoder_ticks) > 2 and time.time() < deadline:
        if encoder_ticks > 0:
            motor_backward()
        else:
            motor_forward()
        time.sleep(0.01)
    motor_stop()
    if abs(encoder_ticks) <= 2:
        encoder_ticks = 0
        print("Reset complete. Ticks: 0")
    else:
        print(f"WARNING: Reset timed out. Remaining ticks: {encoder_ticks}")

# ── Terminal helpers ───────────────────────────────────────────────────────────
def _raw_mode(fd):
    old = termios.tcgetattr(fd)
    tty.setraw(fd)
    return old

def _restore(fd, old):
    termios.tcsetattr(fd, termios.TCSADRAIN, old)

def _key_held(fd):
    """Return True while a key is physically held (data in stdin buffer)."""
    return select.select([sys.stdin], [], [], 0)[0] != []

def ticks_to_degrees(ticks):
    return round(ticks / TICKS_PER_REV * 360, 1)

# ── Main calibration loop ──────────────────────────────────────────────────────
print("DC Motor calibration  |  L298N + rotary encoder")
print("Keys:  + (hold=forward)  |  - (hold=backward)  |  r (reset to 0)  |  q (quit)")
print(f"Encoder ticks: {encoder_ticks}  ({ticks_to_degrees(encoder_ticks)}°)\n")

fd  = sys.stdin.fileno()
old = _raw_mode(fd)

try:
    while True:
        ch = sys.stdin.read(1)

        if ch == '+':
            # Run forward for as long as the key is held
            motor_forward()
            while _key_held(fd):          # drain held-key repeats
                sys.stdin.read(1)
            motor_stop()
            print(f"Ticks: {encoder_ticks}  ({ticks_to_degrees(encoder_ticks)}°)")

        elif ch == '-':
            motor_backward()
            while _key_held(fd):
                sys.stdin.read(1)
            motor_stop()
            print(f"Ticks: {encoder_ticks}  ({ticks_to_degrees(encoder_ticks)}°)")

        elif ch == 'r':
            _restore(fd, old)             # restore terminal for any prints
            reset_to_zero()
            old = _raw_mode(fd)
            print(f"Ticks: {encoder_ticks}  ({ticks_to_degrees(encoder_ticks)}°)")

        elif ch == 'q':
            break

except KeyboardInterrupt:
    pass

finally:
    _restore(fd, old)
    motor_stop()
    pwm.stop()
    GPIO.cleanup()
    print(f'\nFinal position — ticks: {encoder_ticks}  ({ticks_to_degrees(encoder_ticks)}°)')
    print(f'Set "MOTOR_TICKS": {encoder_ticks} in main.py CONFIG.')
