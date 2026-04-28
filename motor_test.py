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
lgpio.gpio_write(chip, MOTOR_ENA_PIN, 1)
time.sleep(2)

lgpio.gpio_write(chip, MOTOR_IN1_PIN, 0)
lgpio.gpio_write(chip, MOTOR_ENA_PIN, 0)
lgpio.gpiochip_close(chip)
print("Done.")
