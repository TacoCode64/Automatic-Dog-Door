"""
detect_dog.py  —  Standalone Dog Image Recognition
====================================================
Runs a MobileNet-SSD object detector to identify whether a dog is visible.
Operates completely independently of main.py — do NOT import this from
main.py; run it as a separate process so it doesn't compete for CPU/RAM
with the door controller.

Supports two camera sources (choose one or both):
  --source pi      → Pi camera (via picamera2)
  --source esp32   → ESP32-CAM MJPEG stream (via HTTP)
  --source both    → Pi camera + ESP32-CAM simultaneously (two windows)

Detection model:
  MobileNet-SSD trained on the Pascal VOC dataset (20 classes).
  "dog" is class index 12. Runs on CPU — no GPU required.
  Achieves ~3–6 FPS on a Raspberry Pi 4B at VGA resolution.

──────────────────────────────────────────────────────────────────────
Model download (run once):
  mkdir -p models
  wget -q -O models/MobileNetSSD_deploy.prototxt \\
    https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/master/MobileNetSSD_deploy.prototxt
  wget -q -O models/MobileNetSSD_deploy.caffemodel \\
    https://drive.usercontent.google.com/download?id=0B3gersZ2cHIxRm5PMWRoTkdHdHc
──────────────────────────────────────────────────────────────────────

Installation:
  pip3 install opencv-python-headless picamera2 requests \\
               --break-system-packages

Usage examples:
  # Pi camera only
  python3 detect_dog.py --source pi

  # ESP32-CAM stream only
  python3 detect_dog.py --source esp32 --esp32-ip 192.168.1.100

  # Both cameras at once
  python3 detect_dog.py --source both --esp32-ip 192.168.1.100

  # Save frames that contain a dog to ./detections/
  python3 detect_dog.py --source esp32 --esp32-ip 192.168.1.100 --save

  # Send ntfy push notification when a dog is detected
  python3 detect_dog.py --source pi --ntfy-topic my-pet-door-12345

  # Adjust confidence threshold (default 0.5)
  python3 detect_dog.py --source pi --confidence 0.6

  # Run headlessly (no display window) — useful over SSH
  python3 detect_dog.py --source esp32 --esp32-ip 192.168.1.100 --no-display

Output:
  - Terminal: timestamped lines whenever a dog enters/leaves the frame
  - Optional on-screen window with bounding boxes (disable with --no-display)
  - Optional saved JPEG frames in ./detections/
  - Optional ntfy.sh push notifications
  - Log file: detect_dog.log

Ctrl+C to stop cleanly.
"""

import argparse
import logging
import os
import time
import threading
from datetime import datetime

import cv2
import numpy as np
import requests

# ── Try importing picamera2 — only needed for --source pi / both ──────────────
try:
    from picamera2 import Picamera2
    _PICAMERA2_AVAILABLE = True
except ImportError:
    _PICAMERA2_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Pascal VOC class labels for MobileNet-SSD (index 0 = background)
VOC_CLASSES = [
    "background", "aeroplane", "bicycle", "bird", "boat", "bottle",
    "bus", "car", "cat", "chair", "cow", "diningtable", "dog",
    "horse", "motorbike", "person", "pottedplant", "sheep", "sofa",
    "train", "tvmonitor",
]
DOG_CLASS_IDX = VOC_CLASSES.index("dog")  # 12

# Default model paths (relative to this script's directory)
_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PROTO  = os.path.join(_SCRIPT_DIR, "models", "MobileNetSSD_deploy.prototxt")
DEFAULT_MODEL  = os.path.join(_SCRIPT_DIR, "models", "MobileNetSSD_deploy.caffemodel")

# MobileNet-SSD input normalisation
_SCALE = 0.007843          # 1/127.5
_MEAN  = (127.5, 127.5, 127.5)
_INPUT_SIZE = (300, 300)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("detect_dog.log"),
    ],
)
log = logging.getLogger("detect_dog")

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(
        description="Standalone dog image-recognition for the pet door project."
    )
    p.add_argument(
        "--source", choices=["pi", "esp32", "both"], default="pi",
        help="Camera source: 'pi' (picamera2), 'esp32' (HTTP stream), or 'both'.",
    )
    p.add_argument(
        "--esp32-ip", default="192.168.1.100",
        help="IP address of the ESP32-CAM (used when source is esp32 or both).",
    )
    p.add_argument(
        "--confidence", type=float, default=0.50,
        help="Minimum detection confidence 0–1 (default 0.50).",
    )
    p.add_argument(
        "--proto", default=DEFAULT_PROTO,
        help="Path to MobileNetSSD_deploy.prototxt.",
    )
    p.add_argument(
        "--model", default=DEFAULT_MODEL,
        help="Path to MobileNetSSD_deploy.caffemodel.",
    )
    p.add_argument(
        "--save", action="store_true",
        help="Save JPEG frames containing a dog to ./detections/",
    )
    p.add_argument(
        "--ntfy-topic", default="",
        help="ntfy.sh topic for push notifications on dog detection (optional).",
    )
    p.add_argument(
        "--no-display", action="store_true",
        help="Disable the on-screen preview window (required if running headlessly).",
    )
    p.add_argument(
        "--pi-width", type=int, default=640,
        help="Pi camera capture width in pixels (default 640).",
    )
    p.add_argument(
        "--pi-height", type=int, default=480,
        help="Pi camera capture height in pixels (default 480).",
    )
    return p.parse_args()

# ---------------------------------------------------------------------------
# Model loader
# ---------------------------------------------------------------------------
def load_model(proto_path: str, model_path: str) -> cv2.dnn.Net:
    """Load and return the MobileNet-SSD Caffe model."""
    if not os.path.isfile(proto_path):
        raise FileNotFoundError(
            f"Prototxt not found: {proto_path}\n"
            "Run the wget commands in the script docstring to download models."
        )
    if not os.path.isfile(model_path):
        raise FileNotFoundError(
            f"Caffemodel not found: {model_path}\n"
            "Run the wget commands in the script docstring to download models."
        )
    net = cv2.dnn.readNetFromCaffe(proto_path, model_path)
    log.info("MobileNet-SSD model loaded.")
    return net

# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------
def detect_dogs(net: cv2.dnn.Net, frame: np.ndarray, conf_threshold: float):
    """
    Run MobileNet-SSD on a single BGR frame.

    Returns:
        detections: list of (confidence, x1, y1, x2, y2) for every dog found
        annotated:  copy of frame with bounding boxes drawn
    """
    h, w = frame.shape[:2]
    blob = cv2.dnn.blobFromImage(
        cv2.resize(frame, _INPUT_SIZE),
        scalefactor=_SCALE,
        size=_INPUT_SIZE,
        mean=_MEAN,
    )
    net.setInput(blob)
    output = net.forward()  # shape: (1, 1, N, 7)

    annotated   = frame.copy()
    dog_detections = []

    for i in range(output.shape[2]):
        class_id   = int(output[0, 0, i, 1])
        confidence = float(output[0, 0, i, 2])

        if class_id != DOG_CLASS_IDX or confidence < conf_threshold:
            continue

        # Bounding box in pixel coordinates
        x1 = int(output[0, 0, i, 3] * w)
        y1 = int(output[0, 0, i, 4] * h)
        x2 = int(output[0, 0, i, 5] * w)
        y2 = int(output[0, 0, i, 6] * h)

        dog_detections.append((confidence, x1, y1, x2, y2))

        # Draw bounding box and label
        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 200, 0), 2)
        label = f"dog {confidence:.0%}"
        (lw, lh), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        cv2.rectangle(annotated, (x1, y1 - lh - 6), (x1 + lw, y1), (0, 200, 0), -1)
        cv2.putText(
            annotated, label, (x1, y1 - 4),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2,
        )

    return dog_detections, annotated

# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------
def notify(topic: str, message: str):
    """Send a push notification via ntfy.sh (non-blocking, best-effort)."""
    if not topic:
        return
    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=message.encode(),
            headers={"Title": "Dog Detected", "Priority": "default"},
            timeout=4,
        )
    except Exception as e:
        log.warning(f"ntfy notification failed: {e}")

# ---------------------------------------------------------------------------
# Frame sources
# ---------------------------------------------------------------------------
def pi_camera_frames(width: int, height: int):
    """
    Generator that yields BGR frames from the Pi camera (picamera2).
    Raises RuntimeError if picamera2 is not installed.
    """
    if not _PICAMERA2_AVAILABLE:
        raise RuntimeError(
            "picamera2 is not installed. "
            "Run: pip3 install picamera2 --break-system-packages"
        )
    cam = Picamera2()
    cam.configure(cam.create_preview_configuration(
        main={"format": "RGB888", "size": (width, height)}
    ))
    cam.start()
    log.info(f"Pi camera started ({width}×{height}).")
    try:
        while True:
            frame_rgb = cam.capture_array()
            yield cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
    finally:
        cam.stop()
        log.info("Pi camera stopped.")


def esp32_frames(ip: str):
    """
    Generator that yields BGR frames from the ESP32-CAM MJPEG stream.
    Automatically reconnects on disconnect.
    """
    url = f"http://{ip}/stream"
    log.info(f"Connecting to ESP32-CAM stream: {url}")
    while True:
        cap = cv2.VideoCapture(url)
        if not cap.isOpened():
            log.warning(f"Cannot open ESP32 stream at {url} — retrying in 3s...")
            time.sleep(3)
            continue
        log.info("ESP32-CAM stream connected.")
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    log.warning("ESP32 stream read failed — reconnecting...")
                    break
                yield frame
        finally:
            cap.release()
        time.sleep(1)  # brief pause before reconnect attempt

# ---------------------------------------------------------------------------
# Per-source detection loop
# ---------------------------------------------------------------------------
def run_detection_loop(
    source_name: str,
    frame_gen,
    net: cv2.dnn.Net,
    conf_threshold: float,
    save_frames: bool,
    ntfy_topic: str,
    show_display: bool,
    save_dir: str,
):
    """
    Consume frames from frame_gen, run detection, display/save/notify.

    dog_present tracks whether a dog was in the previous frame so we only
    log and notify on state *changes* rather than every single frame.
    """
    dog_present = False
    last_notify_time = 0.0
    notify_cooldown  = 30.0  # seconds between repeated notifications
    window_name      = f"Dog Detector — {source_name}"

    log.info(f"[{source_name}] Detection loop started.")

    for frame in frame_gen:
        detections, annotated = detect_dogs(net, frame, conf_threshold)
        dog_now = len(detections) > 0

        # ── State-change logging ───────────────────────────────────────────
        if dog_now and not dog_present:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            best_conf = max(d[0] for d in detections)
            log.info(f"[{source_name}] DOG DETECTED  conf={best_conf:.0%}  @ {ts}")

            # Notify (with cooldown to avoid spamming)
            now_t = time.time()
            if ntfy_topic and (now_t - last_notify_time) >= notify_cooldown:
                threading.Thread(
                    target=notify,
                    args=(ntfy_topic, f"🐾 Dog detected by {source_name}!"),
                    daemon=True,
                ).start()
                last_notify_time = now_t

        elif not dog_now and dog_present:
            log.info(f"[{source_name}] Dog left frame.")

        dog_present = dog_now

        # ── Overlay source label and detection count ───────────────────────
        status_text = (
            f"{source_name} | {'DOG DETECTED' if dog_now else 'No dog'}"
        )
        cv2.putText(
            annotated, status_text, (8, 24),
            cv2.FONT_HERSHEY_SIMPLEX, 0.65,
            (0, 200, 0) if dog_now else (80, 80, 80),
            2,
        )

        # ── Save frame if dog detected ────────────────────────────────────
        if save_frames and dog_now:
            ts_file = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            fname = os.path.join(save_dir, f"{source_name}_{ts_file}.jpg")
            cv2.imwrite(fname, annotated)

        # ── Display ───────────────────────────────────────────────────────
        if show_display:
            cv2.imshow(window_name, annotated)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                log.info(f"[{source_name}] 'q' pressed — stopping.")
                break

    if show_display:
        cv2.destroyWindow(window_name)
    log.info(f"[{source_name}] Detection loop ended.")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    args = parse_args()

    # ── Load model ────────────────────────────────────────────────────────
    net = load_model(args.proto, args.model)

    # ── Create save directory ─────────────────────────────────────────────
    save_dir = os.path.join(_SCRIPT_DIR, "detections")
    if args.save:
        os.makedirs(save_dir, exist_ok=True)
        log.info(f"Saving dog frames to: {save_dir}")

    show_display = not args.no_display

    # ── Build source list ─────────────────────────────────────────────────
    sources = []  # list of (name, generator)

    if args.source in ("pi", "both"):
        sources.append((
            "pi_cam",
            pi_camera_frames(args.pi_width, args.pi_height),
        ))

    if args.source in ("esp32", "both"):
        sources.append((
            "esp32_cam",
            esp32_frames(args.esp32_ip),
        ))

    if not sources:
        log.error("No camera source selected. Use --source pi, esp32, or both.")
        return

    # ── Single source: run in main thread ────────────────────────────────
    if len(sources) == 1:
        name, gen = sources[0]
        try:
            run_detection_loop(
                source_name=name,
                frame_gen=gen,
                net=net,
                conf_threshold=args.confidence,
                save_frames=args.save,
                ntfy_topic=args.ntfy_topic,
                show_display=show_display,
                save_dir=save_dir,
            )
        except KeyboardInterrupt:
            log.info("Stopped by user.")

    # ── Two sources: each runs in its own thread ──────────────────────────
    else:
        # Each thread gets its OWN net instance — cv2.dnn.Net is not
        # thread-safe, so we reload the model for the second thread.
        net2 = load_model(args.proto, args.model)
        model_map = {sources[0][0]: net, sources[1][0]: net2}

        threads = []
        for name, gen in sources:
            t = threading.Thread(
                target=run_detection_loop,
                kwargs=dict(
                    source_name=name,
                    frame_gen=gen,
                    net=model_map[name],
                    conf_threshold=args.confidence,
                    save_frames=args.save,
                    ntfy_topic=args.ntfy_topic,
                    show_display=show_display,
                    save_dir=save_dir,
                ),
                daemon=True,
                name=f"detect-{name}",
            )
            t.start()
            threads.append(t)
            log.info(f"Started detection thread for: {name}")

        try:
            for t in threads:
                t.join()
        except KeyboardInterrupt:
            log.info("Stopped by user.")

    if show_display:
        cv2.destroyAllWindows()
    log.info("detect_dog.py exited cleanly.")


if __name__ == "__main__":
    main()
