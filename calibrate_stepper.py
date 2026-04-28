import sys
import tty
import termios
import RPi.GPIO as GPIO
import time

STEPPER_PINS = [17, 27, 22, 23]

FULL_STEP_SEQ = [
    [1,0,1,0],
    [0,1,1,0],
    [0,1,0,1],
    [1,0,0,1],
]

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
for p in STEPPER_PINS:
    GPIO.setup(p, GPIO.OUT, initial=GPIO.LOW)

total_steps = 0
step_index  = 0

def _step(direction=1):
    global step_index
    step_index = (step_index + direction) % 4
    for i, p in enumerate(STEPPER_PINS):
        GPIO.output(p, FULL_STEP_SEQ[step_index % 4][i])
    time.sleep(0.002)

def rotate(n, reverse=False):
    global total_steps
    d = -1 if reverse else 1
    for _ in range(n):
        _step(d)
    if not reverse:
        total_steps += n
    else:
        total_steps -= n
    for p in STEPPER_PINS:
        GPIO.output(p, GPIO.LOW)

def getch():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

print("Stepper calibration. Keys: + (forward 10) | - (back 10) | r (reset) | q (quit)")
print(f"Current steps: {total_steps}")

try:
    while True:
        ch = getch()
        if ch == '+':
            rotate(10)
            print(f"Steps: {total_steps}")
        elif ch == '-':
            rotate(10, reverse=True)
            print(f"Steps: {total_steps}")
        elif ch == 'r':
            rotate(total_steps, reverse=True)
            print("Reset. Steps: 0")
        elif ch == 'q':
            break
except KeyboardInterrupt:
    pass
finally:
    for p in STEPPER_PINS:
        GPIO.output(p, GPIO.LOW)
    GPIO.cleanup()
    print(f'\nSet "STEPPER_STEPS": {total_steps} in main.py CONFIG.')
