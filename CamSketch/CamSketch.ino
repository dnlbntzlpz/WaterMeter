#include <WiFi.h>
#include <WiFiClient.h>
#include <HTTPClient.h>
#include "esp_camera.h"
#include "secrets.h"   // defines WIFI_SSID, WIFI_PASS, SERVER_HOST, SERVER_PORT
#include "camera_pins.h"

#define CAMERA_MODEL_XIAO_ESP32S3

// ---------------- Camera config (XIAO ESP32S3 Sense + OV2640) ----------------
// Pins taken from Seeed/XIAO-S3 Sense camera mapping
// D0..D7 = Y2..Y9 on OV2640
void configureCamera() {
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;

  // Data lines (OV2640 Y2..Y9)
  config.pin_d0 = 15;  // Y2
  config.pin_d1 = 17;  // Y3
  config.pin_d2 = 18;  // Y4
  config.pin_d3 = 16;  // Y5
  config.pin_d4 = 14;  // Y6
  config.pin_d5 = 12;  // Y7
  config.pin_d6 = 11;  // Y8
  config.pin_d7 = 48;  // Y9

  // Clock / sync
  config.pin_xclk  = 10;  // XCLK
  config.pin_pclk  = 13;  // PCLK
  config.pin_vsync = 38;  // VSYNC
  config.pin_href  = 47;  // HREF

  // SCCB (camera I2C)
  config.pin_sccb_sda = 40; // SDA
  config.pin_sccb_scl = 39; // SCL

  // Power/Reset not wired on XIAO Sense
  config.pin_pwdn  = -1;
  config.pin_reset = -1;

  // Timing / image format
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;

  // Start modest; you can raise once stable
  config.frame_size   = FRAMESIZE_VGA; // 640x480
  config.jpeg_quality = 12;            // 10..20 reasonable
  config.fb_count     = 2;             // XIAO Sense has PSRAM, use 2 framebuffers
  config.grab_mode    = CAMERA_GRAB_LATEST;

  // Enable PSRAM-aware frame buffer location (helps stability on S3)
  config.fb_location  = CAMERA_FB_IN_PSRAM;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed 0x%x\n", err);
    while (true) delay(1000);
  }

  // Optional: set camera sensor tweaks if desired
  sensor_t* s = esp_camera_sensor_get();
  if (s) {
    // s->set_brightness(s, 0);
    // s->set_contrast(s, 0);
    // s->set_saturation(s, 0);
    // s->set_gainceiling(s, GAINCEILING_2X);
  }
}

// ---------------- Simple HTTP helpers (unchanged) ----------------
bool httpGet(const String& path, String& bodyOut) {
  WiFiClient client;
  if (!client.connect(SERVER_HOST, SERVER_PORT)) return false;
  client.printf("GET %s HTTP/1.1\r\nHost: %s:%u\r\nConnection: close\r\n\r\n",
                path.c_str(), SERVER_HOST, SERVER_PORT);

  // skip headers
  while (client.connected()) {
    String line = client.readStringUntil('\n');
    if (line == "\r") break;
  }
  bodyOut.reserve(256);
  while (client.available()) bodyOut += client.readString();
  client.stop();
  return true;
}

// Very small JSON sniff for {"capture":true, "seq":N}
bool parseCaptureJson(const String& body, bool& capture, int& seq) {
  int sIdx = body.indexOf("\"seq\"");
  int cIdx = body.indexOf("\"capture\"");
  if (sIdx < 0 || cIdx < 0) return false;
  int sc = body.indexOf(':', sIdx);
  int sEnd = body.indexOf(',', sc);
  seq = body.substring(sc + 1, sEnd > 0 ? sEnd : body.length()).toInt();

  int cc = body.indexOf(':', cIdx);
  String cap = body.substring(cc + 1);
  cap.trim();
  capture = cap.startsWith("true");
  return true;
}

bool postJpegRaw(const uint8_t* data, size_t len) {
  WiFiClient client;
  HTTPClient http;
  String url = String("http://") + SERVER_HOST + ":" + String(SERVER_PORT) + "/upload";
  if (!http.begin(client, url)) return false;
  http.addHeader("Content-Type", "image/jpeg");
  http.addHeader("Connection", "close");
  http.setTimeout(15000);
  http.useHTTP10(true); // disable chunked
  int code = http.POST((uint8_t*)data, len);
  Serial.printf("POST code: %d, size: %u bytes\n", code, (unsigned)len);
  if (code > 0) Serial.println(http.getString());
  http.end();
  return code > 0 && code < 400;
}

// ---------------- Setup/Loop (unchanged aside from PSRAM note) ----------------
void setup() {
  Serial.begin(115200);
  delay(200);

  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("WiFi connecting");
  while (WiFi.status() != WL_CONNECTED) { Serial.print("."); delay(400); }
  Serial.print("\nWiFi connected. IP: "); Serial.println(WiFi.localIP());

  // (Optional) check PSRAM availability on XIAO Sense
  if (!psramFound()) {
    Serial.println("Warning: PSRAM not found/enabled; consider enabling it in Tools menu.");
  }

  configureCamera();
  Serial.println("Camera ready");
}

void loop() {
  static int lastSeq = 0;
  static unsigned long lastPoll = 0;

  if (WiFi.status() != WL_CONNECTED) {
    WiFi.reconnect();
    delay(1000);
    return;
  }

  // Poll server every ~1s for capture requests
  if (millis() - lastPoll > 1000) {
    lastPoll = millis();

    String body;
    if (!httpGet(String("/api/watermeter/capture/next?since=") + lastSeq, body)) {
      Serial.println("poll failed");
      return;
    }

    bool doCapture = false;
    int seq = lastSeq;
    if (!parseCaptureJson(body, doCapture, seq)) {
      Serial.println("bad JSON");
      return;
    }

    if (doCapture) {
      camera_fb_t* fb = esp_camera_fb_get();
      if (!fb || fb->len == 0) {
        Serial.println("capture failed");
        if (fb) esp_camera_fb_return(fb);
        return;
      }

      bool ok = postJpegRaw(fb->buf, fb->len);
      esp_camera_fb_return(fb);

      if (ok) lastSeq = seq;  // acknowledge only on success
    }
  }
}
