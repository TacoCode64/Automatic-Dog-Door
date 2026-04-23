"""
actuator_control.py — Raspberry Pi
======================================
Hold-to-run actuator control.
 
 Hold E → actuator extends (release to stop)
 Hold R → actuator retracts (release to stop)
 Q → quit
 
The actuator stops the instant you lift your finger.
No Enter key needed — keypresses are read raw from the terminal.
 
GPIO pins (BCM):
 GPIO 14 → RELAY_EXTEND IN (active HIGH)
 GPIO 15 → RELAY_RETRACT IN (active HIGH)
 
Usage:
 sudo python3 actuator_control.py
"""
 
import RPi.GPIO as GPIO
import time
import sys
import tty
import termios
import select
 
# ── Configuration ──────────────────────────────────────────────────────────
RELAY_EXTEND_PIN = 14
RELAY_RETRACT_PIN = 15
DEAD_TIME_S = 0.1 # gap between relay transitions
POLL_INTERVAL_S = 0.02 # how often to check for key release (50 Hz)
 
# ── GPIO setup ─────────────────────────────────────────────────────────────
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(RELAY_EXTEND_PIN, GPIO.OUT, initial=GPIO.LOW)
GPIO.setup(RELAY_RETRACT_PIN, GPIO.OUT, initial=GPIO.LOW)
 
# ── Relay helpers ──────────────────────────────────────────────────────────
def both_off():
 GPIO.output(RELAY_EXTEND_PIN, GPIO.LOW)
 GPIO.output(RELAY_RETRACT_PIN, GPIO.LOW)
 
def do_extend():
 both_off()
 time.sleep(DEAD_TIME_S)
 GPIO.output(RELAY_EXTEND_PIN, GPIO.HIGH)
 
def do_retract():
 both_off()
 time.sleep(DEAD_TIME_S)
 GPIO.output(RELAY_RETRACT_PIN, GPIO.HIGH)
 
# ── Raw keypress helpers ───────────────────────────────────────────────────
def set_raw(fd):
 """Switch terminal to raw (unbuffered, no echo) mode."""
 old = termios.tcgetattr(fd)
 tty.setraw(fd)
 return old
 
def restore(fd, old):
 """Restore terminal to its previous mode."""
 termios.tcsetattr(fd, termios.TCSADRAIN, old)
 
def key_available(fd):
 """Return True if a keypress is waiting in stdin."""
 return select.select([fd], [], [], 0)[0] != []
 
def read_char(fd):
 """Read one character without blocking (call only after key_available)."""
 return sys.stdin.read(1).lower()
 
# ── Status line ────────────────────────────────────────────────────────────
def status(msg):
 """Overwrite the current terminal line."""
 sys.stdout.write(f"\r {msg:<50}")
 sys.stdout.flush()
 
# ── Main ───────────────────────────────────────────────────────────────────
def main():
 fd = sys.stdin.fileno()
 old = set_raw(fd)
 
 print("\r")
 print(" ┌─────────────────────────────────────────────┐\r")
 print(" │ LINEAR ACTUATOR — HOLD TO RUN │\r")
 print(" ├─────────────────────────────────────────────┤\r")
 print(" │ Hold E → Extend │\r")
 print(" │ Hold R → Retract │\r")
 print(" │ Q → Quit │\r")
 print(" └─────────────────────────────────────────────┘\r")
 print("\r")
 
 status("STOPPED — waiting for input")
 
 # Track which key is currently held so we only transition on change
 held = None
 
 try:
 while True:
 if key_available(fd):
 ch = read_char(fd)
 
 if ch == 'q':
 both_off()
 status("Quitting ...")
 break
 
 if ch in ('e', 'r') and ch != held:
 held = ch
 if ch == 'e':
 do_extend()
 status("EXTENDING — release key to stop")
 else:
 do_retract()
 status("RETRACTING — release key to stop")
 
 else:
 # No key waiting — if something was held, it has been released
 if held is not None:
 held = None
 both_off()
 status("STOPPED — waiting for input")
 
 time.sleep(POLL_INTERVAL_S)
 
 except KeyboardInterrupt:
 pass
 
 finally:
 both_off()
 restore(fd, old)
 GPIO.cleanup()
 print("\r\n Both relays OFF. GPIO cleaned up. Goodbye.\r\n")
 
 
if __name__ == "__main__":
 main()
