import RPi.GPIO as GPIO
import time
import sys
import tty
import termios

RELAY_EXTEND_PIN  = 14
RELAY_RETRACT_PIN = 15
DEAD_TIME_S       = 0.1

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(RELAY_EXTEND_PIN,  GPIO.OUT, initial=GPIO.LOW)
GPIO.setup(RELAY_RETRACT_PIN, GPIO.OUT, initial=GPIO.LOW)

def both_off():
    GPIO.output(RELAY_EXTEND_PIN,  GPIO.LOW)
    GPIO.output(RELAY_RETRACT_PIN, GPIO.LOW)

def do_extend():
    both_off()
    time.sleep(DEAD_TIME_S)
    GPIO.output(RELAY_EXTEND_PIN, GPIO.HIGH)

def do_retract():
    both_off()
    time.sleep(DEAD_TIME_S)
    GPIO.output(RELAY_RETRACT_PIN, GPIO.HIGH)

def set_raw(fd):
    """Switch terminal to raw (unbuffered, no echo) mode."""
    old = termios.tcgetattr(fd)
    tty.setraw(fd)
    return old

def restore(fd, old):
    """Restore terminal to its previous mode."""
    termios.tcsetattr(fd, termios.TCSADRAIN, old)

def read_char(fd):
    """Read one character without blocking (call only after key_available)."""
    return sys.stdin.read(1).lower()

def status(msg):
    """Overwrite the current terminal line."""
    sys.stdout.write(f"\r  {msg:<50}")
    sys.stdout.flush()

def main():
    fd  = sys.stdin.fileno()
    old = set_raw(fd)

    status("STOPPED — waiting for input")

    try:
        while True:
            ch = read_char(fd)

            if ch == 'q':
                both_off()
                status("Quitting ...")
                break
            elif ch == 'e':
                do_extend()
                status("EXTENDING  — press S to stop")
            elif ch == 'r':
                do_retract()
                status("RETRACTING — press S to stop")
            elif ch == 's':
                both_off()
                status("STOPPED — waiting for input")

    except KeyboardInterrupt:
        pass

    finally:
        both_off()
        restore(fd, old)
        GPIO.cleanup()
        print("\r\n  Both relays OFF. GPIO cleaned up. Goodbye.\r\n")


if __name__ == "__main__":
    main()
