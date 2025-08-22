// Global variables
let port = null;
let reader = null;
let writer = null;
let isConnected = false;
let isMockMode = false;
let mockInterval = null;
let readingsCount = 0;
let lastReadTime = Date.now();

// Data storage
let sensorData = [];
let tempData = [];
let humData = [];
let gpsMarker = null;
let map = null;

// Charts
let tempChart = null;
let humChart = null;

// Initialize dashboard
document.addEventListener('DOMContentLoaded', function() {
    initializeCharts();
    initializeMap();
    updateConnectionStatus();
  
    // (Optional) OCR button hook if you added that card:
    const btn = document.getElementById('meterAnalyzeBtn');
    if (btn) {
      btn.addEventListener('click', async () => {
        const fileInput = document.getElementById('meterFile');
        const out = document.getElementById('meterResult');
        const f = fileInput?.files?.[0];
        if (!f) { alert('Choose an image first.'); return; }
        out.textContent = 'Analyzing…';
        try {
          const data = await analyzeMeter(f);
          out.textContent = JSON.stringify(data, null, 2);
        } catch (e) {
          out.textContent = 'Error: ' + e.message;
        }
      });
    }
  });
  


// Web Serial API functions
async function connectSerial() {
    // If already connected, disconnect
    if (isConnected) {
        disconnectSerial();
        return;
    }
    
    try {
        port = await navigator.serial.requestPort();
        await port.open({ baudRate: 115200 });
        
        const textDecoder = new TextDecoderStream();
        const readableStreamClosed = port.readable.pipeTo(textDecoder.writable);
        reader = textDecoder.readable.getReader();
        
        isConnected = true;
        isMockMode = false;
        updateConnectionStatus();
        
        // Stop mock data if running
        if (mockInterval) {
            clearInterval(mockInterval);
            mockInterval = null;
        }
        
        // Start reading data
        readSerialData(reader);
        
    } catch (error) {
        console.error('Serial connection error:', error);
        alert('Failed to connect: ' + error.message);
    }
}

let dataBuffer = '';

async function readSerialData(reader) {
    try {
        while (true) {
            const { value, done } = await reader.read();
            if (done) {
                reader.releaseLock();
                break;
            }
            
            // Add new data to buffer
            dataBuffer += value;
            
            // Process complete lines
            const lines = dataBuffer.split('\n');
            dataBuffer = lines.pop() || ''; // Keep incomplete line in buffer
            
            for (const line of lines) {
                if (line.trim()) {
                    processSerialLine(line.trim());
                }
            }
        }
    } catch (error) {
        console.error('Serial read error:', error);
        disconnectSerial();
    }
}

function processSerialLine(line) {
    try {
        const data = JSON.parse(line);
        
        // Check if it's a response to a command
        if (data.hasOwnProperty('ok')) {
            console.log('Command response:', data);
            return;
        }
        
        // Process sensor data
        if (data.hasOwnProperty('temp_c')) {
            processSensorData(data);
        }
        
    } catch (error) {
        // Only log if it's not an empty or partial line
        if (line.length > 5) {
            console.warn('Invalid JSON line:', line);
        }
    }
}

async function sendCommand(command) {
    if (!isConnected && !isMockMode) {
        alert('Not connected to Arduino');
        return;
    }
    
    if (isConnected && port && port.writable) {
        const writer = port.writable.getWriter();
        const encoder = new TextEncoder();
        await writer.write(encoder.encode(command + '\n'));
        writer.releaseLock();
    }
}

function disconnectSerial() {
    isConnected = false;
    
    // Close port if it exists
    if (port) {
        try {
            port.close();
        } catch (error) {
            console.log('Port already closed');
        }
        port = null;
    }
    
    // Clear any existing reader
    if (reader) {
        try {
            reader.releaseLock();
        } catch (error) {
            console.log('Reader already released');
        }
        reader = null;
    }
    
    // Clear data buffer
    dataBuffer = '';
    
    // Update UI
    updateConnectionStatus();
    
    console.log('Serial connection closed');
}

// Mock data functions
function toggleMockData() {
    if (isMockMode) {
        stopMockData();
    } else {
        startMockData();
    }
}

function startMockData() {
    isMockMode = true;
    isConnected = false;
    updateConnectionStatus();
    
    // Generate initial data
    generateMockData();
    
    // Set interval for continuous data
    mockInterval = setInterval(generateMockData, 500);
}

function stopMockData() {
    isMockMode = false;
    if (mockInterval) {
        clearInterval(mockInterval);
        mockInterval = null;
    }
    updateConnectionStatus();
}

function generateMockData() {
    const now = Date.now();
    const data = {
        ts_ms: now,
        temp_c: 25 + (Math.random() - 0.5) * 14, // 18-32 range
        hum_pct: 50 + (Math.random() - 0.5) * 60, // 20-80 range
        gps_lat: 19.4326 + (Math.random() - 0.5) * 0.01,
        gps_lon: -99.1332 + (Math.random() - 0.5) * 0.01,
        gps_sat: Math.floor(Math.random() * 8) + 5 // 5-12 range
    };
    
    processSensorData(data);
}

// Data processing
function processSensorData(data) {
    // Update metrics
    document.getElementById('tempValue').textContent = data.temp_c.toFixed(1);
    document.getElementById('humValue').textContent = data.hum_pct.toFixed(1);
    document.getElementById('latValue').textContent = data.gps_lat.toFixed(4);
    document.getElementById('lonValue').textContent = data.gps_lon.toFixed(4);
    document.getElementById('satValue').textContent = data.gps_sat;
    
    // Update reading rate
    const now = Date.now();
    const timeDiff = (now - lastReadTime) / 1000;
    const rate = timeDiff > 0 ? (1 / timeDiff).toFixed(1) : '0.0';
    document.getElementById('rateValue').textContent = rate;
    lastReadTime = now;
    
    // Store data
    sensorData.push(data);
    if (sensorData.length > 1000) {
        sensorData.shift();
    }
    
    // Update charts
    updateCharts(data);
    
    // Update map
    updateMap(data);
    
    // Update table
    updateTable(data);
    
    readingsCount++;
}

// Chart functions
function initializeCharts() {
    const chartOptions = {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
            x: {
                display: true,
                title: {
                    display: true,
                    text: 'Time'
                }
            },
            y: {
                display: true,
                title: {
                    display: true,
                    text: 'Value'
                }
            }
        },
        plugins: {
            legend: {
                display: false
            }
        }
    };
    
    // Temperature chart
    const tempCtx = document.getElementById('tempChart').getContext('2d');
    tempChart = new Chart(tempCtx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'Temperature',
                data: [],
                borderColor: 'rgb(255, 99, 132)',
                backgroundColor: 'rgba(255, 99, 132, 0.1)',
                tension: 0.4
            }]
        },
        options: chartOptions
    });
    
    // Humidity chart
    const humCtx = document.getElementById('humChart').getContext('2d');
    humChart = new Chart(humCtx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'Humidity',
                data: [],
                borderColor: 'rgb(54, 162, 235)',
                backgroundColor: 'rgba(54, 162, 235, 0.1)',
                tension: 0.4
            }]
        },
        options: chartOptions
    });
}

function updateCharts(data) {
    const timeLabel = new Date(data.ts_ms).toLocaleTimeString();
    
    // Update temperature chart
    tempData.push({x: timeLabel, y: data.temp_c});
    if (tempData.length > 60) tempData.shift();
    
    tempChart.data.labels = tempData.map(d => d.x);
    tempChart.data.datasets[0].data = tempData.map(d => d.y);
    tempChart.update('none');
    
    // Update humidity chart
    humData.push({x: timeLabel, y: data.hum_pct});
    if (humData.length > 60) humData.shift();
    
    humChart.data.labels = humData.map(d => d.x);
    humChart.data.datasets[0].data = humData.map(d => d.y);
    humChart.update('none');
}

// Map functions
function initializeMap() {
    map = L.map('map').setView([19.4326, -99.1332], 13);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© OpenStreetMap contributors'
    }).addTo(map);
}

function updateMap(data) {
    if (gpsMarker) {
        map.removeLayer(gpsMarker);
    }
    
    gpsMarker = L.marker([data.gps_lat, data.gps_lon]).addTo(map);
    map.setView([data.gps_lat, data.gps_lon]);
}

// Table functions
function updateTable(data) {
    const table = document.getElementById('readingsTable');
    const row = table.insertRow(0);
    
    const time = new Date(data.ts_ms).toLocaleTimeString();
    row.innerHTML = `
        <td>${time}</td>
        <td>${data.temp_c.toFixed(1)}</td>
        <td>${data.hum_pct.toFixed(1)}</td>
        <td>${data.gps_lat.toFixed(4)}</td>
        <td>${data.gps_lon.toFixed(4)}</td>
        <td>${data.gps_sat}</td>
    `;
    
    // Keep only last 10 rows
    while (table.rows.length > 10) {
        table.deleteRow(table.rows.length - 1);
    }
}

// CSV download
function downloadCSV() {
    if (sensorData.length === 0) {
        alert('No data to download');
        return;
    }
    
    const headers = ['Timestamp', 'Temperature (°C)', 'Humidity (%)', 'GPS Lat', 'GPS Lon', 'Satellites'];
    const csvContent = [
        headers.join(','),
        ...sensorData.map(data => [
            new Date(data.ts_ms).toISOString(),
            data.temp_c,
            data.hum_pct,
            data.gps_lat,
            data.gps_lon,
            data.gps_sat
        ].join(','))
    ].join('\n');
    
    const blob = new Blob([csvContent], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `sensor_data_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.csv`;
    a.click();
    window.URL.revokeObjectURL(url);
}

// Status functions
function updateConnectionStatus() {
    const statusIndicator = document.getElementById('statusIndicator');
    const statusText = document.getElementById('connectionStatus');
    const connectBtn = document.getElementById('connectBtn');
    const disconnectBtn = document.getElementById('disconnectBtn');
    const mockBtn = document.getElementById('mockBtn');
    
    if (isConnected) {
        statusIndicator.className = 'status-indicator status-connected';
        statusText.textContent = 'Connected to Arduino';
        connectBtn.innerHTML = '<i class="fas fa-plug"></i> Disconnect';
        connectBtn.className = 'btn btn-danger btn-control';
        disconnectBtn.style.display = 'none';
        mockBtn.disabled = true;
    } else if (isMockMode) {
        statusIndicator.className = 'status-indicator status-mock';
        statusText.textContent = 'Mock Data Mode';
        connectBtn.innerHTML = '<i class="fas fa-plug"></i> Connect Serial';
        connectBtn.className = 'btn btn-primary btn-control';
        disconnectBtn.style.display = 'none';
        mockBtn.textContent = 'Stop Mock';
        mockBtn.classList.add('active');
    } else {
        statusIndicator.className = 'status-indicator status-disconnected';
        statusText.textContent = 'Disconnected';
        connectBtn.innerHTML = '<i class="fas fa-plug"></i> Connect Serial';
        connectBtn.className = 'btn btn-primary btn-control';
        disconnectBtn.style.display = 'none';
        mockBtn.textContent = 'Mock Data';
        mockBtn.disabled = false;
        mockBtn.classList.remove('active');
    }
}

// Handle page unload
window.addEventListener('beforeunload', function() {
    if (isConnected && port) {
        port.close();
    }
    if (mockInterval) {
        clearInterval(mockInterval);
    }
});

// ---- Water Meter OCR (GPT) ----
async function analyzeMeter(file){
    const fd = new FormData();
    fd.append('image', file);
    const res = await fetch('/api/watermeter/analyze', { method:'POST', body: fd });
    if (!res.ok) {
      // try to show readable error message
      let msg = 'Request failed';
      try { msg = await res.text(); } catch(e){}
      throw new Error(msg);
    }
    return res.json();
  }

// Expose functions for inline onclick handlers immediately
window.connectSerial = connectSerial;
window.disconnectSerial = disconnectSerial;
window.toggleMockData = toggleMockData;
window.sendCommand = sendCommand;
window.downloadCSV = downloadCSV;
window.analyzeMeter = analyzeMeter;
