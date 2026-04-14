/*
 * ESP32-CAM Outdoor Camera Firmware
 * ===================================
 * Board  : AI-Thinker ESP32-CAM (OV2640 sensor)
 * IDE    : Arduino IDE 2.x  with  esp32 board package v2.0+
 *
 * Function:
 *   - Connects to your home WiFi
 *   - Streams MJPEG video accessible at  http://<esp32-ip>/stream
 *   - Serves snapshots at               http://<esp32-ip>/snapshot
 *   - Saves timestamped JPEGs to SD card when /record endpoint is called
 *   - The Raspberry Pi calls /record when it opens the door and /stop
 *     when the door closes, so only door-event footage is saved
 *
 * Board Library:
 *   Tools → Board → esp32 → "AI Thinker ESP32-CAM"
 *   (Install "esp32" package by Espressif from Boards Manager)
 *
 * Arduino Libraries required  (Sketch → Include Library → Manage Libraries):
 *   - "ESP32" board support (Espressif Systems) — includes WebServer,
 *     WiFi, SD_MMC, camera drivers  — NO separate install needed once
 *     the board package is installed.
 *
 * Wiring notes:
 *   - Power the ESP32-CAM from the MiniBooster (5V output → 5V pin)
 *   - GND → GND
 *   - The OV2640 ribbon cable plugs directly into the camera connector
 *   - Flash the board with the FTDI programmer (GPIO0 → GND during upload)
 *   - Remove GPIO0 → GND jumper after flashing
 */

#include "esp_camera.h"
#include <WiFi.h>
#include <WebServer.h>
#include "SD_MMC.h"
#include "FS.h"
#include <time.h>

// ---------------------------------------------------------------------------
// Configuration  ← EDIT THESE
// ---------------------------------------------------------------------------
const char* WIFI_SSID     = "YourWiFiSSID";
const char* WIFI_PASSWORD = "YourWiFiPassword";

// NTP time server for timestamping SD card files
const char* NTP_SERVER    = "pool.ntp.org";
const long  GMT_OFFSET_S  = -21600;  // CST = UTC-6 (adjust for your timezone)
const int   DST_OFFSET_S  = 3600;   // 1 hour DST

// ---------------------------------------------------------------------------
// AI-Thinker ESP32-CAM pin map (do not change)
// ---------------------------------------------------------------------------
#define PWDN_GPIO_NUM   32
#define RESET_GPIO_NUM  -1
#define XCLK_GPIO_NUM    0
#define SIOD_GPIO_NUM   26
#define SIOC_GPIO_NUM   27
#define Y9_GPIO_NUM     35
#define Y8_GPIO_NUM     34
#define Y7_GPIO_NUM     39
#define Y6_GPIO_NUM     36
#define Y5_GPIO_NUM     21
#define Y4_GPIO_NUM     19
#define Y3_GPIO_NUM     18
#define Y2_GPIO_NUM      5
#define VSYNC_GPIO_NUM  25
#define HREF_GPIO_NUM   23
#define PCLK_GPIO_NUM   22

WebServer server(80);

bool    recording    = false;
uint32_t frameNumber = 0;
String  sessionDir   = "";

// ---------------------------------------------------------------------------
// Camera init
// ---------------------------------------------------------------------------
bool initCamera() {
  camera_config_t config;
  config.ledc_channel  = LEDC_CHANNEL_0;
  config.ledc_timer    = LEDC_TIMER_0;
  config.pin_d0        = Y2_GPIO_NUM;
  config.pin_d1        = Y3_GPIO_NUM;
  config.pin_d2        = Y4_GPIO_NUM;
  config.pin_d3        = Y5_GPIO_NUM;
  config.pin_d4        = Y6_GPIO_NUM;
  config.pin_d5        = Y7_GPIO_NUM;
  config.pin_d6        = Y8_GPIO_NUM;
  config.pin_d7        = Y9_GPIO_NUM;
  config.pin_xclk      = XCLK_GPIO_NUM;
  config.pin_pclk      = PCLK_GPIO_NUM;
  config.pin_vsync     = VSYNC_GPIO_NUM;
  config.pin_href      = HREF_GPIO_NUM;
  config.pin_sccb_sda  = SIOD_GPIO_NUM;
  config.pin_sccb_scl  = SIOC_GPIO_NUM;
  config.pin_pwdn      = PWDN_GPIO_NUM;
  config.pin_reset     = RESET_GPIO_NUM;
  config.xclk_freq_hz  = 20000000;
  config.pixel_format  = PIXFORMAT_JPEG;

  if (psramFound()) {
    config.frame_size   = FRAMESIZE_VGA;   // 640×480
    config.jpeg_quality = 12;
    config.fb_count     = 2;
  } else {
    config.frame_size   = FRAMESIZE_CIF;   // 352×288
    config.jpeg_quality = 15;
    config.fb_count     = 1;
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", err);
    return false;
  }

  // Flip image if camera is mounted upside-down
  sensor_t* s = esp_camera_sensor_get();
  s->set_vflip(s, 1);
  s->set_hmirror(s, 1);

  return true;
}

// ---------------------------------------------------------------------------
// SD card init
// ---------------------------------------------------------------------------
bool initSD() {
  if (!SD_MMC.begin()) {
    Serial.println("SD card mount failed — recording disabled.");
    return false;
  }
  Serial.println("SD card ready.");
  return true;
}

// ---------------------------------------------------------------------------
// HTTP handlers
// ---------------------------------------------------------------------------

// GET /snapshot  — returns a single JPEG frame
void handleSnapshot() {
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    server.send(500, "text/plain", "Camera capture failed");
    return;
  }
  server.sendHeader("Content-Disposition", "inline; filename=snapshot.jpg");
  server.send_P(200, "image/jpeg", (const char*)fb->buf, fb->len);
  esp_camera_fb_return(fb);
}

// GET /stream  — MJPEG stream
void handleStream() {
  WiFiClient client = server.client();
  client.println("HTTP/1.1 200 OK");
  client.println("Content-Type: multipart/x-mixed-replace; boundary=frame");
  client.println();

  while (client.connected()) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) break;

    client.printf(
      "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n",
      fb->len
    );
    client.write(fb->buf, fb->len);
    client.println();
    esp_camera_fb_return(fb);
    delay(100);  // ~10 fps
  }
}

// POST /record  — start saving frames to SD
void handleRecordStart() {
  if (recording) { server.send(200, "text/plain", "Already recording"); return; }

  // Create a session directory named by current timestamp
  struct tm ti;
  if (getLocalTime(&ti)) {
    char buf[32];
    strftime(buf, sizeof(buf), "/rec_%Y%m%d_%H%M%S", &ti);
    sessionDir = String(buf);
  } else {
    sessionDir = "/rec_" + String(millis());
  }
  SD_MMC.mkdir(sessionDir.c_str());

  recording   = true;
  frameNumber = 0;
  Serial.printf("Recording started: %s\n", sessionDir.c_str());
  server.send(200, "text/plain", "Recording started: " + sessionDir);
}

// POST /stop  — stop recording
void handleRecordStop() {
  recording = false;
  Serial.printf("Recording stopped. %u frames saved to %s\n",
                frameNumber, sessionDir.c_str());
  server.send(200, "text/plain",
    "Stopped. Frames: " + String(frameNumber) + " Dir: " + sessionDir);
}

// GET /  — status page
void handleRoot() {
  String ip   = WiFi.localIP().toString();
  String html = "<h2>ESP32-CAM Pet Door</h2>"
                "<p>IP: " + ip + "</p>"
                "<p>Recording: " + (recording ? "YES" : "NO") + "</p>"
                "<p><a href='/stream'>Live stream</a> | "
                "<a href='/snapshot'>Snapshot</a></p>";
  server.send(200, "text/html", html);
}

// ---------------------------------------------------------------------------
// Recording task (runs on core 0)
// ---------------------------------------------------------------------------
void recordingTask(void* param) {
  for (;;) {
    if (recording) {
      camera_fb_t* fb = esp_camera_fb_get();
      if (fb) {
        String path = sessionDir + "/frame_" + String(frameNumber++) + ".jpg";
        File f = SD_MMC.open(path.c_str(), FILE_WRITE);
        if (f) {
          f.write(fb->buf, fb->len);
          f.close();
        }
        esp_camera_fb_return(fb);
      }
    }
    vTaskDelay(200 / portTICK_PERIOD_MS);  // ~5 fps to SD
  }
}

// ---------------------------------------------------------------------------
// Setup & loop
// ---------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  Serial.println("\nESP32-CAM Pet Door — booting ...");

  if (!initCamera()) { while (1) delay(1000); }
  initSD();

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  Serial.printf("\nConnected. IP: %s\n", WiFi.localIP().toString().c_str());

  // Sync time from NTP
  configTime(GMT_OFFSET_S, DST_OFFSET_S, NTP_SERVER);

  // Register routes
  server.on("/",         HTTP_GET,  handleRoot);
  server.on("/snapshot", HTTP_GET,  handleSnapshot);
  server.on("/stream",   HTTP_GET,  handleStream);
  server.on("/record",   HTTP_POST, handleRecordStart);
  server.on("/stop",     HTTP_POST, handleRecordStop);
  server.begin();
  Serial.println("HTTP server started.");

  // Start background recording task on core 0
  xTaskCreatePinnedToCore(recordingTask, "recTask", 4096, NULL, 1, NULL, 0);
}

void loop() {
  server.handleClient();
}
