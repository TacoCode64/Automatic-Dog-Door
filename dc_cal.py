import sys
import tty
import termios
import select
import time
import lgpio

MOTOR_ENA_PIN   = 12
MOTOR_IN1_PIN   = 24
MOTOR_IN2_PIN   = 25

ENCODER_CLK_PIN = 17
ENCODER_DT_PIN  = 27

PWM_FREQ        = 1000
MOTOR_SPEED     = 100

TICKS_PER_REV   = 20

CHIP = lgpio.gpiochip_open(0)

lgpio.gpio_claim_output(CHIP, MOTOR_IN1_PIN, 0)
lgpio.gpio_claim_output(CHIP, MOTOR_IN2_PIN, 0)
lgpio.gpio_claim_output(CHIP, MOTOR_ENA_PIN, 0)

lgpio.gpio_claim_input(CHIP, ENCODER_CLK_PIN, lgpio.SET_PULL_UP)
lgpio.gpio_claim_input(CHIP, ENCODER_DT_PIN,  lgpio.SET_PULL_UP)

def _set_pwm(duty):
    """duty: 0–100"""
    if duty == 0:
        lgpio.gpio_write(CHIP, MOTOR_ENA_PIN, 0)
    else:
        lgpio.tx_pwm(CHIP, MOTOR_ENA_PIN, PWM_FREQ, duty)

encoder_ticks = 0
_last_clk     = lgpio.gpio_read(CHIP, ENCODER_CLK_PIN)

def _encoder_callback(chip, gpio, level, tick):
    """Interrupt-driven encoder tick counter (both-edges on CLK)."""
    global encoder_ticks, _last_clk
    clk_state = level
    dt_state  = lgpio.gpio_read(CHIP, ENCODER_DT_PIN)
    if clk_state != _last_clk:
        if dt_state != clk_state:
            encoder_ticks += 1
        else:
            encoder_ticks -= 1
        _last_clk = clk_state

_cb_handle = lgpio.callback(CHIP, ENCODER_CLK_PIN, lgpio.BOTH_EDGES, _encoder_callback)

def motor_forward(speed=MOTOR_SPEED):
    lgpio.gpio_write(CHIP, MOTOR_IN1_PIN, 1)
    lgpio.gpio_write(CHIP, MOTOR_IN2_PIN, 0)
    _set_pwm(speed)

def motor_backward(speed=MOTOR_SPEED):
    lgpio.gpio_write(CHIP, MOTOR_IN1_PIN, 0)
    lgpio.gpio_write(CHIP, MOTOR_IN2_PIN, 1)
    _set_pwm(speed)

def motor_stop():
    lgpio.gpio_write(CHIP, MOTOR_IN1_PIN, 0)
    lgpio.gpio_write(CHIP, MOTOR_IN2_PIN, 0)
    _set_pwm(0)

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

def cleanup():
    motor_stop()
    _cb_handle.cancel()
    lgpio.gpiochip_close(CHIP)

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

print("DC Motor calibration  |  L298N + rotary encoder  (lgpio)")
print("Keys:  + (hold=forward)  |  - (hold=backward)  |  r (reset to 0)  |  q (quit)")
print(f"Encoder ticks: {encoder_ticks}  ({ticks_to_degrees(encoder_ticks)}°)\n")

fd  = sys.stdin.fileno()
old = _raw_mode(fd)

try:
    while True:
        ch = sys.stdin.read(1)

        if ch == '+':
            motor_forward()
            while _key_held(fd):
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
            _restore(fd, old)
            reset_to_zero()
            old = _raw_mode(fd)
            print(f"Ticks: {encoder_ticks}  ({ticks_to_degrees(encoder_ticks)}°)")

        elif ch == 'q':
            break

except KeyboardInterrupt:
    pass

finally:
    _restore(fd, old)
    cleanup()
    print(f'\nFinal position — ticks: {encoder_ticks}  ({ticks_to_degrees(encoder_ticks)}°)')
    print(f'Set "MOTOR_TICKS": {encoder_ticks} in main.py CONFIG.')
