"""
motor_test.py — bare minimum motor test, no raw terminal, no lgpio PWM
Just runs the motor forward for 2 seconds then stops.
If the motor moves, your wiring and lgpio are fine.
If not, check wiring/power.
"""
import lgpio
import time

MOTOR_ENA_PIN = 12
MOTOR_IN1_PIN = 24
MOTOR_IN2_PIN = 25

chip = lgpio.gpiochip_open(0)
lgpio.gpio_claim_output(chip, MOTOR_IN1_PIN, 0)
lgpio.gpio_claim_output(chip, MOTOR_IN2_PIN, 0)
lgpio.gpio_claim_output(chip, MOTOR_ENA_PIN, 0)

print("Running motor forward for 2 seconds...")
lgpio.gpio_write(chip, MOTOR_IN1_PIN, 1)
lgpio.gpio_write(chip, MOTOR_IN2_PIN, 0)
lgpio.gpio_write(chip, MOTOR_ENA_PIN, 1)  # full on, no PWM
time.sleep(2)

lgpio.gpio_write(chip, MOTOR_IN1_PIN, 0)
lgpio.gpio_write(chip, MOTOR_ENA_PIN, 0)
lgpio.gpiochip_close(chip)
print("Done.")
