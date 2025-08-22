/*
 * Arduino Sensor Simulator for Web Serial Workshop
 * Simulates temperature, humidity, and GPS data
 * Responds to serial commands for control
 * 
 * No physical sensors connected - simulation only
 * 
 * Commands:
 * RATE:<ms> - Change sampling rate (200-5000ms)
 * PAUSE - Pause data transmission
 * RESUME - Resume data transmission
 */

#include <ArduinoJson.h>

// Simulation parameters
unsigned long lastReadTime = 0;
unsigned long sampleRate = 500; // Default 500ms
bool isPaused = false;

// GPS base coordinates (Mexico City)
float baseLat = 19.4326;
float baseLon = -99.1332;

// Buffer for incoming commands
String commandBuffer = "";

void setup() {
  Serial.begin(115200);
  
  // Wait for serial to be ready
  while (!Serial) {
    delay(10);
  }
  
  // Initialize random seed
  randomSeed(analogRead(0));
  
  // Send initial status
  sendResponse(true, "Arduino simulator ready");
}

void loop() {
  unsigned long currentTime = millis();
  
  // Check for incoming commands
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      processCommand(commandBuffer);
      commandBuffer = "";
    } else {
      commandBuffer += c;
    }
  }
  
  // Send sensor data if not paused and time interval reached
  if (!isPaused && (currentTime - lastReadTime >= sampleRate)) {
    sendSensorData();
    lastReadTime = currentTime;
  }
}

void processCommand(String command) {
  command.trim();
  if (command.length() == 0) return;
  
  if (command.startsWith("RATE:")) {
    // Extract rate value
    String rateStr = command.substring(5);
    int newRate = rateStr.toInt();
    
    if (newRate >= 200 && newRate <= 5000) {
      sampleRate = newRate;
      sendResponse(true, "Rate changed to " + String(sampleRate) + "ms");
    } else {
      sendResponse(false, "Invalid rate. Use 200-5000ms");
    }
  }
  else if (command == "PAUSE") {
    isPaused = true;
    sendResponse(true, "Data transmission paused");
  }
  else if (command == "RESUME") {
    isPaused = false;
    sendResponse(true, "Data transmission resumed");
  }
  else {
    sendResponse(false, "Unknown command: " + command);
  }
}

void sendSensorData() {
  // Create JSON document
  StaticJsonDocument<256> doc;
  
  // Generate simulated sensor data
  unsigned long timestamp = millis();
  float temperature = generateTemperature();
  float humidity = generateHumidity();
  float gpsLat = generateGPSLat();
  float gpsLon = generateGPSLon();
  int satellites = generateSatellites();
  
  // Fill JSON document
  doc["ts_ms"] = timestamp;
  doc["temp_c"] = round(temperature * 100) / 100.0; // Round to 2 decimal places
  doc["hum_pct"] = round(humidity * 100) / 100.0;   // Round to 2 decimal places
  doc["gps_lat"] = round(gpsLat * 10000) / 10000.0; // Round to 4 decimal places
  doc["gps_lon"] = round(gpsLon * 10000) / 10000.0; // Round to 4 decimal places
  doc["gps_sat"] = satellites;
  
  // Serialize and send in one line
  serializeJson(doc, Serial);
  Serial.println();
  
  // Ensure data is sent immediately
  Serial.flush();
}

void sendResponse(bool ok, String message) {
  StaticJsonDocument<128> doc;
  doc["ok"] = ok;
  
  if (ok) {
    doc["msg"] = message;
  } else {
    doc["error"] = message;
  }
  
  // Serialize and send in one line
  serializeJson(doc, Serial);
  Serial.println();
  
  // Ensure data is sent immediately
  Serial.flush();
}

// Sensor simulation functions
float generateTemperature() {
  // Base temperature around 25C with some variation
  float baseTemp = 25.0;
  float variation = (random(-100, 100) / 100.0) * 7.0; // +/- 7 degrees
  float temp = baseTemp + variation;
  
  // Add some noise
  float noise = (random(-10, 10) / 100.0);
  temp += noise;
  
  // Clamp to range 18.0-32.0
  return constrain(temp, 18.0, 32.0);
}

float generateHumidity() {
  // Base humidity around 50% with variation
  float baseHumidity = 50.0;
  float variation = (random(-100, 100) / 100.0) * 30.0; // +/- 30%
  float humidity = baseHumidity + variation;
  
  // Add some noise
  float noise = (random(-10, 10) / 100.0);
  humidity += noise;
  
  // Clamp to range 20.0-80.0
  return constrain(humidity, 20.0, 80.0);
}

float generateGPSLat() {
  // Vary around base latitude
  float variation = (random(-100, 100) / 100.0) * 0.01; // +/- 0.01 degrees
  return baseLat + variation;
}

float generateGPSLon() {
  // Vary around base longitude
  float variation = (random(-100, 100) / 100.0) * 0.01; // +/- 0.01 degrees
  return baseLon + variation;
}

int generateSatellites() {
  // Random number of satellites between 5-12
  return random(5, 13);
}
