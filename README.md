# Water Meter Monitoring System

## 📖 Overview
This project is a **non-invasive water meter monitoring system** that uses an **XIAO ESP32S3-CAM** to capture images of a mechanical water meter dial.  
Captured images are streamed to a **Flask-based web dashboard**, where users can request captures, view results, and eventually run image recognition to automatically extract meter readings.  

The goal is to demonstrate **remote water monitoring** without cutting into pipes or installing inline sensors, making it suitable for demo setups, educational purposes, and continuous monitoring.

---

## ✨ Features
- Capture images of a water meter using ESP32-CAM.  
- Web dashboard (Flask + HTML/JS frontend).  
- API endpoints for capture and polling.  
- Displays latest captured image in the browser.  
- Designed for non-invasive monitoring (no flow sensors needed).  

**Planned features:**
- Image recognition to read dial numbers automatically.  
- Continuous monitoring + logging of meter values.  
- Integration with a demo setup: tank + pump + mechanical dial meter.  

---

## 🛠️ Hardware Setup
- **ESP32-CAM** (with OV3660 sensor).  
- Mechanical dial water meter (demo type).  
- Optional: water tank + pump for demo flow.  
- Camera mount / stand (to keep alignment fixed).  

---

## 💻 Software Setup

### ESP32-CAM Firmware
1. Open the provided Arduino sketch (`camsketch.ino`).  
2. Update `secrets.h` with your WiFi and server details:  
   ```cpp
   #define WIFI_SSID "your_wifi"
   #define WIFI_PASS "your_password"
   #define SERVER_HOST "192.168.xxx.xxx"
   #define SERVER_PORT 5000
   ```
3. Flash the sketch to the ESP32-CAM.  

---

### Flask Server
1. Clone this repo and set up a Python virtual environment:  
   ```bash
   git clone https://github.com/dnlbntzlpz/webDashboardWaterMeter.git
   cd webDashboardWaterMeter
   python -m venv .venv
   source .venv/bin/activate  # or .venv\Scripts\activate on Windows
   pip install -r requirements.txt
   ```
2. Run the Flask app:  
   ```bash
   python app.py
   ```
3. Open [http://localhost:5000](http://localhost:5000) in your browser.  

---

## 📂 Folder Structure
```
webDashboardWaterMeter/
│── app.py              # Flask server
│── static/
│   ├── app.js          # Frontend JS (polls API, updates UI)
│   └── styles.css      # (if any custom CSS)
│   └── index.html      # Web dashboard
│── camsketch.ino       # ESP32 camera sketch
│── secrets.h           # WiFi/server settings for ESP32
```

---

## 🚀 Usage
1. Power on the ESP32-CAM (it connects to WiFi + Flask server).  
2. Open the dashboard in your browser.  
3. Press **Capture** → ESP32 takes a picture → Flask serves it on the dashboard.  
4. Repeat as needed.  

---

## 📝 Next Steps / TODO
- [X] Fix "one image behind" lag issue.  
- [ ] Add endpoint to serve latest capture immediately.  
- [ ] Implement image recognition (digit/needle detection).  
- [ ] Build demo setup (tank + pump + mechanical meter).  
- [ ] Add logging + chart of usage over time.  
