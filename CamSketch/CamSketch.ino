#include <WiFi.h>
#include <WiFiClient.h>
#include <HTTPClient.h>
#include "esp_camera.h"

// Wi-Fi / Server settings
#include "secrets.h"     // define WIFI_SSID, WIFI_PASS, SERVER_HOST, SERVER_PORT

// ---------- SELECT YOUR CAMERA MODEL ----------
// For M5 Timer Camera X (OV3660, DFOV 66.5°) this mapping matches:
#define CAMERA_MODEL_XIAO_ESP32S3   // <-- choose the model that matches your device

// Pin map for the selected model:
#include "camera_pins.h"

// ---------- Camera init (uses camera_pins.h macros) ----------
static void configureCamera() {
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;

  config.pin_pwdn     = PWDN_GPIO_NUM;
  config.pin_reset    = RESET_GPIO_NUM;
  config.pin_xclk     = XCLK_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;

  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d0 = Y2_GPIO_NUM;

  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href  = HREF_GPIO_NUM;
  config.pin_pclk  = PCLK_GPIO_NUM;

  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;

  // Choose conservative defaults; adapt if PSRAM missing
  bool has_psram = psramFound();
  Serial.printf("PSRAM detected: %s\n", has_psram ? "yes" : "no");
  config.frame_size   = has_psram ? FRAMESIZE_VGA  : FRAMESIZE_QVGA;
  config.jpeg_quality = 12;
  config.fb_count     = has_psram ? 2 : 1;
  config.fb_location  = has_psram ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  config.grab_mode    = (config.fb_count > 1) ? CAMERA_GRAB_LATEST : CAMERA_GRAB_WHEN_EMPTY;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed 0x%x, retrying with safer settings...\n", err);
    // Retry with minimal memory usage
    config.frame_size   = FRAMESIZE_QVGA;
    config.jpeg_quality = 15;
    config.fb_count     = 1;
    config.fb_location  = CAMERA_FB_IN_DRAM;
    config.grab_mode    = CAMERA_GRAB_WHEN_EMPTY;
    err = esp_camera_init(&config);
    if (err != ESP_OK) {
      Serial.printf("Camera init failed again 0x%x\n", err);
      while (true) delay(1000);
    }
  }
}

// ---------- Tiny HTTP helpers ----------
static bool httpGet(const String& path, String& bodyOut) {
  WiFiClient client;
  if (!client.connect(SERVER_HOST, SERVER_PORT)) {
    Serial.println("connect failed");
    return false;
  }
  client.printf("GET %s HTTP/1.1\r\nHost: %s:%u\r\nAccept: application/json\r\nConnection: close\r\n\r\n",
                path.c_str(), SERVER_HOST, SERVER_PORT);

  // Status line
  String status = client.readStringUntil('\n'); status.trim();

  // Skip headers
  while (client.connected()) {
    String line = client.readStringUntil('\n');
    if (line == "\r") break;
  }

  // Body (small JSON)
  bodyOut = "";
  unsigned long t0 = millis();
  while (client.connected() || client.available()) {
    while (client.available()) bodyOut += client.readString();
    if (millis() - t0 > 2000) break;  // 2s max wait
    delay(10);
  }
  client.stop();

  // Debug
  // Serial.print("HTTP status: "); Serial.println(status);
  // Serial.print("Body: "); Serial.println(bodyOut);

  return status.indexOf("200") > 0;
}

static bool parseCaptureJson(const String& body, bool& capture, int& seq) {
  int sIdx = body.indexOf("\"seq\"");
  int cIdx = body.indexOf("\"capture\"");
  if (sIdx < 0 || cIdx < 0) return false;

  int sc = body.indexOf(':', sIdx);
  if (sc < 0) return false;
  int sEnd = body.indexOf(',', sc);
  String seqStr = body.substring(sc + 1, (sEnd > 0 ? sEnd : body.length()));
  seqStr.trim();
  seq = seqStr.toInt();

  int cc = body.indexOf(':', cIdx);
  if (cc < 0) return false;
  String capStr = body.substring(cc + 1);
  capStr.trim();
  capture = capStr.startsWith("true");
  return true;
}

static bool parseRelayJson(const String& body, bool& activate, int& seq) {
  int sIdx = body.indexOf("\"seq\"");
  int aIdx = body.indexOf("\"activate\"");
  if (sIdx < 0 || aIdx < 0) return false;

  int sc = body.indexOf(':', sIdx);
  if (sc < 0) return false;
  int sEnd = body.indexOf(',', sc);
  String seqStr = body.substring(sc + 1, (sEnd > 0 ? sEnd : body.length()));
  seqStr.trim();
  seq = seqStr.toInt();

  int ac = body.indexOf(':', aIdx);
  if (ac < 0) return false;
  String actStr = body.substring(ac + 1);
  actStr.trim();
  activate = actStr.startsWith("true");
  return true;
}

static bool postJpegRaw(const uint8_t* data, size_t len) {
  WiFiClient client;
  HTTPClient http;
  String url = String("http://") + SERVER_HOST + ":" + String(SERVER_PORT) + "/upload";
  if (!http.begin(client, url)) {
    Serial.println("http.begin failed");
    return false;
  }
  http.addHeader("Content-Type", "image/jpeg");
  http.addHeader("Connection", "close");
  http.setTimeout(15000);   // 15s
  http.useHTTP10(true);     // disable chunked
  http.setReuse(false);  // be explicit about no keep-alive reuse


  // Cast away const because ESP32 HTTPClient API wants uint8_t*
  int code = http.POST((uint8_t*)data, len);
  Serial.printf("POST code: %d, size: %u bytes\n", code, (unsigned)len);
  if (code > 0) {
    String resp = http.getString();
    // Serial.println(resp);
  }
  http.end();
  return (code > 0 && code < 400);
}

// Grab-and-discard one frame, then grab the fresh one.
// Returns a *fresh* fb ready to POST, or nullptr on failure.
static camera_fb_t* snapFreshJpeg(uint16_t warmup_delay_ms = 40) {
  // 1) Flush any stale buffer
  camera_fb_t* fb = esp_camera_fb_get();
  if (fb) esp_camera_fb_return(fb);

  // (short delay helps sensor/ISP settle)
  if (warmup_delay_ms) delay(warmup_delay_ms);

  // 2) Grab the real one we will upload
  camera_fb_t* fresh = esp_camera_fb_get();
  if (!fresh || fresh->len == 0) {
    if (fresh) esp_camera_fb_return(fresh);
    return nullptr;
  }
  return fresh;
}

// ---------- Setup / Loop ----------
// Relay control pin (avoid camera pins; on XIAO ESP32S3, GPIO10 is XCLK)
#ifndef RELAY_PIN
#define RELAY_PIN D3
#endif
void setup() {
  Serial.begin(115200);
  delay(200);

  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("WiFi connecting");
  while (WiFi.status() != WL_CONNECTED) { Serial.print("."); delay(400); }
  Serial.print("\nWiFi connected. IP: "); Serial.println(WiFi.localIP());

  configureCamera();
  Serial.println("Camera ready");

  // Relay pin setup
  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, LOW);
}

void loop() {
  static int lastSeq = 0;
  static unsigned long lastPollMs = 0;
  static int lastRelaySeq = 0;
  static unsigned long lastRelayPollMs = 0;
  static int randRelayTime = 0;

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi dropped, reconnecting...");
    WiFi.reconnect();
    delay(1000);
    return;
  }

  // Poll server every ~1s
  if (millis() - lastPollMs > 1000) {
    lastPollMs = millis();

    String body;
    String path = String("/api/watermeter/capture/next?since=") + lastSeq;
    if (!httpGet(path, body)) {
      Serial.println("poll failed");
      return;
    }

    bool doCapture = false;
    int seq = lastSeq;
    if (!parseCaptureJson(body, doCapture, seq)) {
      Serial.println("bad json");
      return;
    }

    if (doCapture) {
      Serial.printf("Capture requested (seq=%d)\n", seq);

      // Grab a truly fresh frame (fixes "one behind")
      camera_fb_t* fb = snapFreshJpeg(50);  // 40–60ms is a good range
      if (!fb) {
        Serial.println("capture failed");
        return;
      }

      bool ok = postJpegRaw(fb->buf, fb->len);
      esp_camera_fb_return(fb);

      if (ok) {
        lastSeq = seq;   // acknowledge only on success
        Serial.println("upload ok");
      } else {
        Serial.println("upload failed");
      }
    }
  }

  // Poll relay trigger every ~1s
  if (millis() - lastRelayPollMs > 1000) {
    lastRelayPollMs = millis();

    String body;
    String path = String("/api/device/relay/next?since=") + lastRelaySeq;
    if (!httpGet(path, body)) {
      // Serial.println("relay poll failed");
      return;
    }

    bool doActivate = false;
    int rseq = lastRelaySeq;
    if (!parseRelayJson(body, doActivate, rseq)) {
      // Serial.println("relay bad json");
      return;
    }

    if (doActivate) {
      Serial.printf("Relay activation requested (seq=%d)\n", rseq);
      randRelayTime = random(20000, 60000);
      Serial.printf("Activiting Relay for %d ms\n", randRelayTime);
      digitalWrite(RELAY_PIN, HIGH);
      //delay(10000); //ten seconds
      delay(randRelayTime);
      digitalWrite(RELAY_PIN, LOW);
      lastRelaySeq = rseq;  // acknowledge after action
      Serial.println("relay done");

      // Immediately trigger a capture after relay cycle completes
      // This uses the existing capture flow by querying for a new request once
      // and, if none, we fall back to pushing a legacy upload directly.
      // Simple approach: request a fresh frame and upload it.
      camera_fb_t* fb = snapFreshJpeg(60);
      if (fb) {
        bool ok = postJpegRaw(fb->buf, fb->len);
        esp_camera_fb_return(fb);
        Serial.println(ok ? "post-capture upload ok" : "post-capture upload failed");
      }
    }
  }
}
