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

# ── State ──────────────────────────────────────────────────────────────────────
PULSE_S        = 0.5   # seconds the motor runs per keypress — adjust freely
total_forward_s = 0.0  # accumulated forward time

# ── Main calibration loop ──────────────────────────────────────────────────────
print("DC Motor calibration  |  L298N  (time-based, no encoder)")
print(f"Each + or - press runs the motor for {PULSE_S}s.")
print("Keys:  + (forward pulse)  |  - (backward pulse)  |  r (test reset)  |  q (quit)\n")

fd  = sys.stdin.fileno()
old = _raw_mode(fd)

try:
    while True:
        ch = sys.stdin.read(1)

        if ch == '+':
            motor_forward()
            time.sleep(PULSE_S)
            motor_stop()
            total_forward_s = round(total_forward_s + PULSE_S, 3)
            print(f"Forward pulse {PULSE_S}s  |  total forward: {total_forward_s:.3f}s")

        elif ch == '-':
            motor_backward()
            time.sleep(PULSE_S)
            motor_stop()
            total_forward_s = round(total_forward_s - PULSE_S, 3)
            print(f"Backward pulse {PULSE_S}s  |  total forward: {total_forward_s:.3f}s")

        elif ch == 'r':
            if total_forward_s <= 0:
                print("Nothing to reset — press + first.")
            else:
                _restore(fd, old)
                print(f"Testing reset: running backward for {total_forward_s:.3f}s...")
                motor_backward()
                time.sleep(total_forward_s)
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
    print(f'\nTotal forward duration: {total_forward_s:.3f}s')
    print(f'Set  "MOTOR_TURN_S": {total_forward_s}  in maindc.py CONFIG.')
