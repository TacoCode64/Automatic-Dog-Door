"""
calibrate_dc.py  —  Raspberry Pi  (lgpio, time-based)
=======================================================
Use this script to find the correct number of seconds needed to fully
depress your door handle with the DC motor (L298N driver).

No rotary encoder needed — just run the motor forward while watching
the handle, then note the elapsed time shown on screen.  Enter that
value as MOTOR_TURN_S in maindc.py CONFIG.

Controls:
  +   run motor forward (hold key to keep running, release to stop)
  -   run motor backward (hold to reverse, release to stop)
  r   run backward for the last recorded forward duration (test the reset)
  q   quit and print the final time to enter in maindc.py CONFIG

L298N Wiring:
  ENA  → MOTOR_ENA_PIN  (PWM-capable GPIO, e.g. GPIO 12)
  IN1  → MOTOR_IN1_PIN  (e.g. GPIO 24)
  IN2  → MOTOR_IN2_PIN  (e.g. GPIO 25)
  Motor terminals → L298N OUT1 / OUT2
  L298N 12V → 12V supply, GND shared with Pi

Usage:
    python3 calibrate_dc.py
"""

import sys
import tty
import termios
import select
import time
import lgpio

# ── Pin configuration ──────────────────────────────────────────────────────────
MOTOR_ENA_PIN = 12
MOTOR_IN1_PIN = 24
MOTOR_IN2_PIN = 25

# ── Motor settings ─────────────────────────────────────────────────────────────
PWM_FREQ    = 1000   # Hz
MOTOR_SPEED = 100    # Duty cycle 0–100 (start high for calibration; lower in main if needed)

# ── lgpio setup ────────────────────────────────────────────────────────────────
CHIP = lgpio.gpiochip_open(0)
lgpio.gpio_claim_output(CHIP, MOTOR_IN1_PIN, 0)
lgpio.gpio_claim_output(CHIP, MOTOR_IN2_PIN, 0)
lgpio.gpio_claim_output(CHIP, MOTOR_ENA_PIN, 0)

# ── Motor helpers ──────────────────────────────────────────────────────────────
def motor_forward(speed=MOTOR_SPEED):
    lgpio.gpio_write(CHIP, MOTOR_IN1_PIN, 1)
    lgpio.gpio_write(CHIP, MOTOR_IN2_PIN, 0)
    lgpio.tx_pwm(CHIP, MOTOR_ENA_PIN, PWM_FREQ, speed)

def motor_backward(speed=MOTOR_SPEED):
    lgpio.gpio_write(CHIP, MOTOR_IN1_PIN, 0)
    lgpio.gpio_write(CHIP, MOTOR_IN2_PIN, 1)
    lgpio.tx_pwm(CHIP, MOTOR_ENA_PIN, PWM_FREQ, speed)

def motor_stop():
    lgpio.gpio_write(CHIP, MOTOR_IN1_PIN, 0)
    lgpio.gpio_write(CHIP, MOTOR_IN2_PIN, 0)
    lgpio.gpio_write(CHIP, MOTOR_ENA_PIN, 0)

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

# ── State ──────────────────────────────────────────────────────────────────────
last_forward_s = 0.0   # duration of the most recent forward run

# ── Main calibration loop ──────────────────────────────────────────────────────
print("DC Motor calibration  |  L298N  (time-based, no encoder)")
print("Keys:  + (hold=forward)  |  - (hold=backward)  |  r (test reset)  |  q (quit)")
print(f"Motor speed: {MOTOR_SPEED}%\n")

fd  = sys.stdin.fileno()
old = _raw_mode(fd)

try:
    while True:
        ch = sys.stdin.read(1)

        if ch == '+':
            t_start = time.monotonic()
            motor_forward()
            while _key_held(fd):
                sys.stdin.read(1)
            motor_stop()
            elapsed = time.monotonic() - t_start
            last_forward_s = round(elapsed, 3)
            print(f"Forward run: {last_forward_s:.3f}s")

        elif ch == '-':
            t_start = time.monotonic()
            motor_backward()
            while _key_held(fd):
                sys.stdin.read(1)
            motor_stop()
            elapsed = time.monotonic() - t_start
            print(f"Backward run: {elapsed:.3f}s")

        elif ch == 'r':
            if last_forward_s <= 0:
                print("No forward run recorded yet — press + first.")
            else:
                _restore(fd, old)
                print(f"Testing reset: running backward for {last_forward_s:.3f}s...")
                motor_backward()
                time.sleep(last_forward_s)
                motor_stop()
                print("Reset complete.")
                old = _raw_mode(fd)

        elif ch == 'q':
            break

except KeyboardInterrupt:
    pass

finally:
    _restore(fd, old)
    motor_stop()
    lgpio.gpiochip_close(CHIP)
    print(f'\nLast forward duration: {last_forward_s:.3f}s')
    print(f'Set  "MOTOR_TURN_S": {last_forward_s}  in maindc.py CONFIG.')
