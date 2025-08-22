# Arduino Web Serial Dashboard

🚀 **Complete Arduino Web Serial API Workshop** - Real-time sensor monitoring dashboard with Web Serial API

## 📁 Project Files

- `arduino_simulator.ino` - Arduino code that simulates sensor data
- `index.html` - Complete web dashboard with real-time charts and maps
- `server.py` - Simple Python HTTP server
- `README.md` - Complete documentation
- `.gitignore` - Git ignore file

## ⚡ Quick Start

### 1. Clone and Run
```bash
git clone https://github.com/rafaelhernandezrios/webdashboardwitharduino.git
cd webdashboardwitharduino
python server.py
```

### 2. Open Dashboard
- Navigate to: `http://localhost:8000`
- Click "Mock Data" to test without hardware
- Or click "Connect Serial" to connect Arduino

### 3. Arduino Setup (Optional)
- Upload `arduino_simulator.ino` to Arduino
- Install ArduinoJson library in Arduino IDE
- Connect Arduino and select port in browser

## ✨ Features

### 🎮 Real-time Control
- **Web Serial API** - Direct browser-to-Arduino communication
- **Mock Data Mode** - Test dashboard without hardware
- **Live Commands** - Pause, Resume, Rate control
- **Auto-reconnect** - Robust connection handling

### 📊 Data Visualization
- **Real-time Charts** - Temperature and humidity trends
- **Interactive Map** - GPS location tracking
- **Live Metrics** - Current sensor values
- **Data Table** - Recent readings history

### 💾 Data Management
- **CSV Export** - Download sensor data
- **1000+ Data Points** - Extended data storage
- **Real-time Updates** - Live dashboard refresh

## 🛠️ Prerequisites

### Arduino Setup
1. **Arduino IDE** - Download from [arduino.cc](https://www.arduino.cc/en/software)
2. **ArduinoJson Library** - Install via Arduino IDE Library Manager
3. **Arduino Board** - Uno, Nano, or Mega (or any compatible board)

### Web Browser
- **Chrome/Edge** (desktop version) - Web Serial API is only supported in these browsers
- **HTTPS or localhost** - Web Serial API requires secure context

## 🚀 Installation

### Method 1: Python Server (Recommended)
```bash
# Clone repository
git clone https://github.com/rafaelhernandezrios/webdashboardwitharduino
cd webdashboardwitharduino

# Run server
python server.py

# Open browser
# Navigate to: http://localhost:8000
```

### Method 2: Alternative Servers
```bash
# Using Python built-in server
python -m http.server 8000

# Using Node.js
npx http-server -p 8000

# Using Live Server (VS Code)
# Right-click index.html > "Open with Live Server"
```

## 🔧 Arduino Setup

### Step 1: Install Dependencies
1. **Arduino IDE** - Download from [arduino.cc](https://www.arduino.cc/en/software)
2. **ArduinoJson Library** - Open Arduino IDE → Sketch → Include Library → Manage Libraries → Search "ArduinoJson" → Install

### Step 2: Upload Code
1. Open `arduino_simulator.ino` in Arduino IDE
2. Select your board: **Tools → Board → Arduino Uno/Nano/Mega**
3. Select port: **Tools → Port → COM3/COM7** (Windows) or `/dev/ttyACM0` (Linux/Mac)
4. Click **Upload** button (→)
5. Open **Serial Monitor** and set baud rate to **115200**
6. You should see: `{"ok":true,"msg":"Arduino simulator ready"}`

## 📖 Usage Instructions

### Connecting to Arduino
1. **Connect Serial**: Click "Connect Serial" button
2. **Select Port**: Choose your Arduino port from the browser dialog
3. **Verify Connection**: Status should show "Connected to Arduino"
4. **View Data**: Real-time sensor data will appear in the dashboard

### Using Mock Data (No Hardware Required)
1. **Enable Mock Mode**: Click "Mock Data" button
2. **View Simulated Data**: Dashboard will show simulated sensor readings
3. **Test Features**: All dashboard features work with mock data

### Dashboard Features

#### Control Panel
- **Connect Serial**: Connect to Arduino via Web Serial API
- **Mock Data**: Enable simulated data for testing
- **Pause/Resume**: Control data transmission
- **Rate Control**: Change sampling rate (500ms, 1000ms)

#### Real-time Metrics
- **Temperature**: Current temperature in Celsius
- **Humidity**: Current humidity percentage
- **GPS Coordinates**: Latitude and longitude
- **Satellites**: Number of GPS satellites
- **Readings/sec**: Data transmission rate

#### Charts
- **Temperature Trend**: Line chart showing last 60 temperature readings
- **Humidity Trend**: Line chart showing last 60 humidity readings

#### Map
- **GPS Location**: Interactive map showing current position
- **Real-time Updates**: Marker updates with new GPS coordinates

#### Data Table
- **Recent Readings**: Last 10 sensor readings with timestamps
- **Download CSV**: Export up to 1000 data points as CSV file

## 🔧 Arduino Commands

The Arduino responds to these serial commands:

- `RATE:500` - Set sampling rate to 500ms
- `RATE:1000` - Set sampling rate to 1000ms
- `PAUSE` - Pause data transmission
- `RESUME` - Resume data transmission

## 📊 Data Format

### Sensor Data (Arduino → Web)
```json
{
  "ts_ms": 1723500000000,
  "temp_c": 25.7,
  "hum_pct": 45.3,
  "gps_lat": 19.4327,
  "gps_lon": -99.1330,
  "gps_sat": 8
}
```

### Command Response (Arduino → Web)
```json
{"ok": true, "msg": "Rate changed to 500ms"}
{"ok": false, "error": "Invalid rate. Use 200-5000ms"}
```

## 🛠️ Troubleshooting

### Common Issues

#### Web Serial API Not Available
- **Problem**: "Connect Serial" button doesn't work
- **Solution**: Use Chrome or Edge desktop browser (not mobile)

#### Port Not Found
- **Problem**: Arduino port not appearing in selection dialog
- **Solution**: 
  - Check Arduino IDE Serial Monitor is closed
  - Disconnect and reconnect Arduino
  - Try different USB cable
  - Restart browser

#### Permission Denied (Windows)
- **Problem**: "Access is denied" error
- **Solution**:
  - Close Arduino IDE Serial Monitor
  - Close any other programs using the port
  - Run browser as administrator
  - Check Windows Device Manager for port conflicts

#### Permission Denied (macOS/Linux)
- **Problem**: "Permission denied" error
- **Solution**:
  - Add user to dialout group: `sudo usermod -a -G dialout $USER`
  - Log out and log back in
  - Check port permissions: `ls -l /dev/tty*`

#### No Data Received
- **Problem**: Connected but no data appears
- **Solution**:
  - Check Arduino Serial Monitor shows data
  - Verify baud rate is 115200
  - Check Arduino code uploaded correctly
  - Try "Mock Data" to test dashboard

#### Charts Not Updating
- **Problem**: Charts show but don't update
- **Solution**:
  - Check browser console for JavaScript errors
  - Refresh page
  - Clear browser cache
  - Check data is being received (table should update)

### Browser Console Debugging

Open browser Developer Tools (F12) and check:
- **Console tab**: For JavaScript errors
- **Network tab**: For failed requests
- **Serial tab**: For Web Serial API errors

### Arduino Debugging

1. **Serial Monitor**: Check Arduino IDE Serial Monitor for:
   - Initialization message
   - Command responses
   - Data transmission

2. **LED Indicators**: Arduino board LED should blink during upload

3. **Reset Button**: Press Arduino reset button if needed

## 🚀 Advanced Features

### Custom Sampling Rates
Send custom rate commands via browser console:
```javascript
sendCommand('RATE:200');  // 200ms (fastest)
sendCommand('RATE:5000'); // 5000ms (slowest)
```

### Data Export
- **CSV Download**: Click "Download CSV" for spreadsheet analysis
- **Real-time Logging**: Check browser console for data logs
- **Custom Processing**: Modify JavaScript for custom data handling

### Extending the Project

#### Adding Real Sensors
Replace simulation functions in Arduino code:
```cpp
float generateTemperature() {
    // Replace with actual sensor reading
    return dht.readTemperature();
}
```

#### Adding More Charts
Add new chart containers and update JavaScript:
```html
<canvas id="newChart"></canvas>
```

#### Custom Commands
Add new command handling in Arduino:
```cpp
else if (command == "CUSTOM") {
    // Handle custom command
    sendResponse(true, "Custom command executed");
}
```

## 🔒 Security Notes

- Web Serial API requires user permission
- Only works on localhost or HTTPS
- No data is sent to external servers
- All processing happens locally in browser

## 🌐 Browser Compatibility

- **Chrome**: 89+ (Full support)
- **Edge**: 89+ (Full support)
- **Firefox**: Not supported
- **Safari**: Not supported
- **Mobile**: Not supported

## 📚 Resources

- [Web Serial API Documentation](https://web.dev/serial/)
- [ArduinoJson Library](https://arduinojson.org/)
- [Chart.js Documentation](https://www.chartjs.org/)
- [Leaflet Maps](https://leafletjs.com/)

## 💬 Support

For issues or questions:
1. Check troubleshooting section above
2. Verify all prerequisites are met
3. Test with mock data first
4. Check browser console for errors
5. Verify Arduino code uploaded successfully
