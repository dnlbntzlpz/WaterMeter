#include <WiFi.h>
#include <HTTPClient.h>
#include "esp_camera.h"

//WiFi Details
#include "secrets.h"

// ---------- WiFi ----------
//Handled in secrets.h

// ---------- Server ----------
//Handled in secrets.h

// ---------- SELECT ONE CAMERA CONFIG ----------

// // (1) AI-Thinker ESP32-CAM module (reference mapping)
// void configureCamera() {
//   camera_config_t config;
//   config.ledc_channel = LEDC_CHANNEL_0;
//   config.ledc_timer   = LEDC_TIMER_0;
//   config.pin_d0 = 5;   config.pin_d1 = 18;  config.pin_d2 = 19;  config.pin_d3 = 21;
//   config.pin_d4 = 36;  config.pin_d5 = 39;  config.pin_d6 = 34;  config.pin_d7 = 35;
//   config.pin_xclk = 0; config.pin_pclk = 22;
//   config.pin_vsync = 25; config.pin_href = 23;
//   config.pin_sccb_sda = 26; config.pin_sccb_scl = 27;
//   config.pin_pwdn = 32; config.pin_reset = -1;
//   config.xclk_freq_hz = 20000000;
//   config.pixel_format = PIXFORMAT_JPEG;
//   config.frame_size = FRAMESIZE_VGA; // QVGA/VGA/SVGA etc.
//   config.jpeg_quality = 12;          // 10..20 = good
//   config.fb_count = 1;

//   esp_err_t err = esp_camera_init(&config);
//   if (err != ESP_OK) {
//     Serial.printf("Camera init failed 0x%x\n", err);
//     while(true) delay(1000);
//   }
// }

// (2) Timer Camera X (EXAMPLE mapping; adjust if needed)
//Uncomment this block and comment the one above if you want to try it.
void configureCamera() {
  // Optional but helpful on USB-only power:
  //WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);

  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;

  // D0..D7
  config.pin_d0 = 32;
  config.pin_d1 = 35;
  config.pin_d2 = 34;
  config.pin_d3 = 5;
  config.pin_d4 = 39;
  config.pin_d5 = 18;
  config.pin_d6 = 36;
  config.pin_d7 = 19;

  // Sync / clock
  config.pin_xclk  = 27;   // ⬅ XCLK is 27 on Timer Cam X
  config.pin_pclk  = 21;
  config.pin_vsync = 22;
  config.pin_href  = 26;

  // SCCB (I2C to sensor)
  config.pin_sccb_sda = 25;
  config.pin_sccb_scl = 23;

  // Power/reset
  config.pin_pwdn  = -1;   // not used on this board
  config.pin_reset = 15;

  config.xclk_freq_hz = 20000000;   // 20 MHz
  config.pixel_format = PIXFORMAT_JPEG;

  // Start conservative; you can raise later
  config.frame_size   = FRAMESIZE_VGA; // try QVGA/VGA/SVGA/UXGA later
  config.jpeg_quality = 12;            // 10..20 good
  config.fb_count     = 1;
  config.grab_mode    = CAMERA_GRAB_LATEST;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed 0x%x\n", err);
    while (true) delay(1000);
  }
}

void setup() {
  Serial.begin(115200);
  delay(200);

  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("WiFi connecting");
  while (WiFi.status() != WL_CONNECTED) {
    Serial.print(".");
    delay(400);
  }
  Serial.print("\nWiFi connected. IP: ");
  Serial.println(WiFi.localIP());

  configureCamera();
  Serial.println("Camera ready");
}

void loop() {
  // Take a picture
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb || fb->len == 0) {
    Serial.println("Capture failed");
    if (fb) esp_camera_fb_return(fb);
    delay(2000);
    return;
  }

  // POST raw JPEG bytes
  if (WiFi.status() == WL_CONNECTED) {
    HTTPClient http;
    http.begin(SERVER_URL);
    http.addHeader("Content-Type", "image/jpeg");

    int code = http.POST(fb->buf, fb->len);
    Serial.printf("POST code: %d, size: %u bytes\n", code, (unsigned)fb->len);
    if (code > 0) {
      Serial.println(http.getString());
    } else {
      Serial.printf("POST failed: %s\n", http.errorToString(code).c_str());
    }
    http.end();
  } else {
    Serial.println("WiFi not connected");
  }

  esp_camera_fb_return(fb);

  // Every 5 seconds (adjust as you wish)
  delay(5000);
}
