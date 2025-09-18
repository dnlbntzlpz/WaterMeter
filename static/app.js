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
let readingChart = null;
let readingData = [];
let lastReadingTs = 0;
let readingHistory = [];

// Initialize dashboard
document.addEventListener("DOMContentLoaded", async function () {
  console.log("[init] DOM ready");
  initializeCharts();
  // initializeMap();
  updateConnectionStatus();

  // OCR button hook (manual analyze optional)
  const analyzeBtn = document.getElementById("meterAnalyzeBtn");
  if (analyzeBtn) {
    analyzeBtn.addEventListener("click", async () => {
      const fileInput = document.getElementById("meterFile");
      const out = document.getElementById("meterResult");
      const f = fileInput?.files?.[0];
      if (!f) { alert("Choose an image first."); return; }
      out.textContent = "Analyzing…";
      try {
        const data = await analyzeMeter(f);
        out.textContent = JSON.stringify(data, null, 2);
        const wmEl = document.getElementById("wmValue");
        if (wmEl && typeof data?.reading !== "undefined") {
          wmEl.textContent = String(data.reading);
          appendReadingPoint(data.reading);
        }
      } catch (e) {
        out.textContent = "Error: " + (e?.message || String(e));
      }
    });
  }

  // Show whatever latest exists at startup
  try {
    const meta0 = await getLatestMeta();
    if (meta0?.hasImage && meta0?.imageUrl) {
      // fija latestTs antes de pintar
      latestTs = meta0.result?.ts ?? 0;
      setLatestImage(latestTs, meta0.imageUrl);
      lastReadingTs = latestTs;
    } else {
      latestTs = 0;
      lastReadingTs = 0;
    }
  } catch (e) {
    console.warn("[init] latest fetch failed:", e);
    latestTs = 0;
    lastReadingTs = 0;
  }

  // Start polling for latest metadata (includes OCR results)
  try { await refreshLatest(); } catch (e) {}
  setInterval(() => { if (!isCapturing) refreshLatest().catch(() => {}); }, 3000);

  // ROBUST: Capture Now
  const captureBtn = document.getElementById("captureNowBtn");
  console.log("[init] captureNowBtn found?", !!captureBtn);
  if (captureBtn) captureBtn.addEventListener("click", captureNow);

  if (captureBtn) {
    captureBtn.addEventListener("click", async () => {
      console.log("[click] Capture Now");
      const statusEl = document.getElementById("meterResult"); // reuse as status area
      const prevStatus = statusEl?.textContent ?? "";

      captureBtn.disabled = true;
      captureBtn.textContent = "Capturing…";
      if (statusEl) statusEl.textContent = "📸 Requesting capture…";

      try {
        // 1) snapshot current timestamp BEFORE triggering capture
        const before = await getLatestMeta();
        const prevTs = before?.result?.ts ?? 0;

        // 2) trigger capture on server (ESP32 will detect via /capture/next)
        await requestRemoteCapture();
        if (statusEl) statusEl.textContent = "📥 Waiting for device upload…";

        // 3) wait until we see a strictly newer image
        const fresh = await waitForNewImage(prevTs, {
          timeoutMs: 20000,
          intervalMs: 400,
        });

        // 4) update the <img> with cache-busted URL
        setLatestImage(fresh.result.ts, fresh.imageUrl);

        if (statusEl)
          statusEl.textContent = `✅ New image loaded (ts: ${fresh.result.ts})`;
      } catch (e) {
        console.error(e);
        alert(e.message || "Capture failed");
        if (statusEl)
          statusEl.textContent = `❌ ${e.message || "Capture failed"}`;
      } finally {
        captureBtn.disabled = false;
        captureBtn.textContent = "Capture Now";
        // Optional: clear success status after a moment
        if (statusEl && statusEl.textContent.startsWith("✅")) {
          setTimeout(() => {
            statusEl.textContent = prevStatus;
          }, 3000);
        }
      }
    });
  }

  // Relay activation
  const relayBtn = document.getElementById("relayBtn");
  if (relayBtn) {
    relayBtn.addEventListener("click", async () => {
      const statusEl = document.getElementById("meterResult");
      const prev = statusEl?.textContent ?? "";
      const restoreText = relayBtn.textContent;
      try {
        relayBtn.disabled = true;
        relayBtn.textContent = "Activating…";
        if (statusEl) statusEl.textContent = "⚡ Requesting relay activation…";
        const r = await fetch("/api/device/relay/activate", { method: "POST" });
        if (!r.ok) throw new Error(`relay request ${r.status}`);
        const j = await r.json().catch(() => ({}));
        if (statusEl) statusEl.textContent = `✅ Relay activation requested (seq: ${j.seq ?? "?"})`;
        // Optional: auto-restore status message
        if (statusEl) setTimeout(() => { statusEl.textContent = prev; }, 3000);
      } catch (e) {
        if (statusEl) statusEl.textContent = `❌ ${e.message || "Relay activation failed"}`;
      } finally {
        relayBtn.disabled = false;
        relayBtn.textContent = restoreText;
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
    console.error("Serial connection error:", error);
    alert("Failed to connect: " + error.message);
  }
}

let dataBuffer = "";

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
      const lines = dataBuffer.split("\n");
      dataBuffer = lines.pop() || ""; // Keep incomplete line in buffer

      for (const line of lines) {
        if (line.trim()) {
          processSerialLine(line.trim());
        }
      }
    }
  } catch (error) {
    console.error("Serial read error:", error);
    disconnectSerial();
  }
}

function processSerialLine(line) {
  try {
    const data = JSON.parse(line);

    // Check if it's a response to a command
    if (data.hasOwnProperty("ok")) {
      console.log("Command response:", data);
      return;
    }

    // Process sensor data
    if (data.hasOwnProperty("temp_c")) {
      processSensorData(data);
    }
  } catch (error) {
    // Only log if it's not an empty or partial line
    if (line.length > 5) {
      console.warn("Invalid JSON line:", line);
    }
  }
}

async function sendCommand(command) {
  if (!isConnected && !isMockMode) {
    alert("Not connected to Arduino");
    return;
  }

  if (isConnected && port && port.writable) {
    const writer = port.writable.getWriter();
    const encoder = new TextEncoder();
    await writer.write(encoder.encode(command + "\n"));
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
      console.log("Port already closed");
    }
    port = null;
  }

  // Clear any existing reader
  if (reader) {
    try {
      reader.releaseLock();
    } catch (error) {
      console.log("Reader already released");
    }
    reader = null;
  }

  // Clear data buffer
  dataBuffer = "";

  // Update UI
  updateConnectionStatus();

  console.log("Serial connection closed");
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
    gps_sat: Math.floor(Math.random() * 8) + 5, // 5-12 range
  };

  processSensorData(data);
}

// Data processing
function processSensorData(data) {
  // Update metrics if elements exist (most cards hidden)
  const elTemp = document.getElementById("tempValue");
  if (elTemp) elTemp.textContent = data.temp_c.toFixed(1);
  const elHum = document.getElementById("humValue");
  if (elHum) elHum.textContent = data.hum_pct.toFixed(1);
  const elLat = document.getElementById("latValue");
  if (elLat) elLat.textContent = data.gps_lat.toFixed(4);
  const elLon = document.getElementById("lonValue");
  if (elLon) elLon.textContent = data.gps_lon.toFixed(4);
  const elSat = document.getElementById("satValue");
  if (elSat) elSat.textContent = data.gps_sat;

  // Update reading rate
  const now = Date.now();
  const timeDiff = (now - lastReadTime) / 1000;
  const rate = timeDiff > 0 ? (1 / timeDiff).toFixed(1) : "0.0";
  document.getElementById("rateValue").textContent = rate;
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
  // If Chart.js failed to load (offline/CDN blocked), skip charts so the rest of the UI still works
  if (typeof Chart === "undefined") {
    console.warn("[charts] Chart.js not available; disabling charts");
    return;
  }
  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    scales: {
      x: {
        display: true,
        title: {
          display: true,
          text: "Time",
        },
      },
      y: {
        display: true,
        title: {
          display: true,
          text: "Value",
        },
      },
    },
    plugins: {
      legend: {
        display: false,
      },
    },
  };

  // Temperature chart
  const tempCanvas = document.getElementById("tempChart");
  if (tempCanvas) {
    const tempCtx = tempCanvas.getContext("2d");
    tempChart = new Chart(tempCtx, {
      type: "line",
      data: {
        labels: [],
        datasets: [
          {
            label: "Temperature",
            data: [],
            borderColor: "rgb(255, 99, 132)",
            backgroundColor: "rgba(255, 99, 132, 0.1)",
            tension: 0.4,
          },
        ],
      },
      options: chartOptions,
    });
  }

  // Humidity chart
  const humCanvas = document.getElementById("humChart");
  if (humCanvas) {
    const humCtx = humCanvas.getContext("2d");
    humChart = new Chart(humCtx, {
      type: "line",
      data: {
        labels: [],
        datasets: [
          {
            label: "Humidity",
            data: [],
            borderColor: "rgb(54, 162, 235)",
            backgroundColor: "rgba(54, 162, 235, 0.1)",
            tension: 0.4,
          },
        ],
      },
      options: chartOptions,
    });
  }

  // Reading trend chart
  const readingCanvas = document.getElementById("readingChart");
  if (readingCanvas) {
    const ctx = readingCanvas.getContext("2d");
    readingChart = new Chart(ctx, {
      type: "line",
      data: {
        labels: [],
        datasets: [
          {
            label: "Meter Reading",
            data: [],
            borderColor: "#20c997",
            backgroundColor: "rgba(32, 201, 151, 0.1)",
            tension: 0.3,
            pointRadius: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { display: true, title: { display: true, text: "Time" } },
          y: { display: true, title: { display: true, text: "Reading" } },
        },
        plugins: { legend: { display: false } },
      },
    });
  }
}

function appendReadingPoint(reading, ts) {
  const canvasPresent = !!document.getElementById("readingChart");
  if (!canvasPresent) return;
  const value = parseFloat(reading);
  if (!isFinite(value)) return;
  const when = ts ? new Date(ts) : new Date();
  const label = when.toLocaleTimeString(); // chart label (time only)
  const labelDateTime = when.toLocaleString(); // history label (date + time)

  readingData.push({ x: label, y: value });
  if (readingData.length > 60) readingData.shift();

  // History table (keep last 30)
  readingHistory.push({ ts, label: labelDateTime, value });
  if (readingHistory.length > 30) readingHistory.shift();
  renderReadingHistory();

  if (readingChart) {
    readingChart.data.labels = readingData.map(d => d.x);
    readingChart.data.datasets[0].data = readingData.map(d => d.y);
    readingChart.update("none");
  }
}

function renderReadingHistory() {
  const tbody = document.getElementById("readingHistory");
  if (!tbody) return;
  // Render newest first
  const rows = [...readingHistory].slice(-30).reverse().map(r =>
    `<tr><td>${r.label}</td><td class="text-end">${r.value}</td></tr>`
  ).join("");
  tbody.innerHTML = rows || "";
}

function updateCharts(data) {
  const timeLabel = new Date(data.ts_ms).toLocaleTimeString();

  // Update temperature chart
  tempData.push({ x: timeLabel, y: data.temp_c });
  if (tempData.length > 60) tempData.shift();

  tempChart.data.labels = tempData.map((d) => d.x);
  tempChart.data.datasets[0].data = tempData.map((d) => d.y);
  tempChart.update("none");

  // Update humidity chart
  humData.push({ x: timeLabel, y: data.hum_pct });
  if (humData.length > 60) humData.shift();

  humChart.data.labels = humData.map((d) => d.x);
  humChart.data.datasets[0].data = humData.map((d) => d.y);
  humChart.update("none");
}

// Map functions
function initializeMap() {
  map = L.map("map").setView([19.4326, -99.1332], 13);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap contributors",
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
  const table = document.getElementById("readingsTable");
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
    alert("No data to download");
    return;
  }

  const headers = [
    "Timestamp",
    "Temperature (°C)",
    "Humidity (%)",
    "GPS Lat",
    "GPS Lon",
    "Satellites",
  ];
  const csvContent = [
    headers.join(","),
    ...sensorData.map((data) =>
      [
        new Date(data.ts_ms).toISOString(),
        data.temp_c,
        data.hum_pct,
        data.gps_lat,
        data.gps_lon,
        data.gps_sat,
      ].join(",")
    ),
  ].join("\n");

  const blob = new Blob([csvContent], { type: "text/csv" });
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `sensor_data_${new Date()
    .toISOString()
    .slice(0, 19)
    .replace(/:/g, "-")}.csv`;
  a.click();
  window.URL.revokeObjectURL(url);
}

// Status functions
function updateConnectionStatus() {
  const statusIndicator = document.getElementById("statusIndicator");
  const statusText = document.getElementById("connectionStatus");
  const connectBtn = document.getElementById("connectBtn");
  const disconnectBtn = document.getElementById("disconnectBtn");
  const mockBtn = document.getElementById("mockBtn");

  // Controls panel may be commented out in HTML; bail safely if missing
  if (!statusIndicator || !statusText || !connectBtn || !disconnectBtn || !mockBtn) {
    return;
  }

  if (isConnected) {
    statusIndicator.className = "status-indicator status-connected";
    statusText.textContent = "Connected to Arduino";
    connectBtn.innerHTML = '<i class="fas fa-plug"></i> Disconnect';
    connectBtn.className = "btn btn-danger btn-control";
    disconnectBtn.style.display = "none";
    mockBtn.disabled = true;
  } else if (isMockMode) {
    statusIndicator.className = "status-indicator status-mock";
    statusText.textContent = "Mock Data Mode";
    connectBtn.innerHTML = '<i class="fas fa-plug"></i> Connect Serial';
    connectBtn.className = "btn btn-primary btn-control";
    disconnectBtn.style.display = "none";
    mockBtn.textContent = "Stop Mock";
    mockBtn.classList.add("active");
  } else {
    statusIndicator.className = "status-indicator status-disconnected";
    statusText.textContent = "Disconnected";
    connectBtn.innerHTML = '<i class="fas fa-plug"></i> Connect Serial';
    connectBtn.className = "btn btn-primary btn-control";
    disconnectBtn.style.display = "none";
    mockBtn.textContent = "Mock Data";
    mockBtn.disabled = false;
    mockBtn.classList.remove("active");
  }
}

// Handle page unload
window.addEventListener("beforeunload", function () {
  if (isConnected && port) {
    port.close();
  }
  if (mockInterval) {
    clearInterval(mockInterval);
  }
});

// ---- Water Meter OCR (GPT) ----
let latestTs = 0; // último ts conocido de latest.jpg
let isCapturing = false; // bloquea cualquier refresco paralelo

async function analyzeMeter(file) {
  const fd = new FormData();
  fd.append("image", file);
  const res = await fetch("/api/watermeter/analyze", {
    method: "POST",
    body: fd,
  });
  if (!res.ok) {
    // try to show readable error message
    let msg = "Request failed";
    try {
      msg = await res.text();
    } catch (e) {}
    throw new Error(msg);
  }
  return res.json();
}

async function requestRemoteCapture() {
  const res = await fetch("/api/watermeter/capture", { method: "POST" });
  if (!res.ok) throw new Error(await res.text());
  return res.json(); // {ok:true, seq:n}
}

async function getLatestMeta() {
  // Always bypass any caches
  const res = await fetch(`/api/watermeter/latest?nocache=${Date.now()}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`latest meta ${res.status}`);
  return res.json(); // { hasImage, imageUrl, result: { ts } }
}

function setLatestImage(ts, imageUrl) {
  const img = document.getElementById("latestImg");
  const url = `${imageUrl}?t=${ts}`;
  const tmp = new Image();
  tmp.onload = () => { img.src = url; latestTs = ts; };
  tmp.src = url;
}

async function analyzeLatest() {
  // Cache-bust using the ts your API already exposes
  const metaRes = await fetch('/api/watermeter/latest');
  const meta = await metaRes.json();
  if (!meta.hasImage) {
    return { error: 'No image yet' };
  }

  const imgRes = await fetch(`/latest.jpg?ts=${meta.result.ts}`);
  const blob = await imgRes.blob();
  const file = new File([blob], 'latest.jpg', { type: blob.type || 'image/jpeg' });

  const form = new FormData();
  form.append('image', file);

  const res = await fetch('/api/watermeter/analyze', { method: 'POST', body: form });
  if (!res.ok) {
    const txt = await res.text();
    throw new Error(`Analyze failed: ${res.status} ${txt}`);
  }
  return res.json(); // { reading, confidence, notes } OR { raw, warning }
}

// Optionally wait until new image lands (simple retry loop)
async function waitForNewImage(
  prevTs,
  { timeoutMs = 15000, intervalMs = 500 } = {}
) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    const meta = await getLatestMeta();
    const ts = meta?.result?.ts ?? 0;
    if (ts > prevTs && meta?.hasImage && meta?.imageUrl) {
      return meta; // new image is ready
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error("Timed out waiting for new image upload");
}

async function captureNow() {
  const btn = document.getElementById("captureNowBtn");
  const statusEl = document.getElementById("meterResult");
  const restore = statusEl?.textContent ?? "";

  if (isCapturing) return; // ya hay una en curso
  isCapturing = true;

  btn.disabled = true;
  if (statusEl) statusEl.textContent = "📸 Requesting capture…";

  try {
    // (1) Solicita token (REQUESTED)
    const r = await fetch("/api/watermeter/capture", { method: "POST" });
    if (!r.ok) throw new Error(`capture request ${r.status}`);
    const { ok, token } = await r.json();
    if (!ok || !token) throw new Error("no token");

    // Switch to simple wait-on-latest (compatible with legacy uploader)
    if (statusEl) statusEl.textContent = "⬆️ Waiting for latest.jpg…";
    await waitForNewImage(latestTs, { timeoutMs: 25000, intervalMs: 400 });
    await refreshLatest();

  } catch (e) {
    if (statusEl) statusEl.textContent = `❌ ${e.message || "Capture failed"}`;
  } finally {
    isCapturing = false;
    btn.disabled = false;
    if (statusEl && statusEl.textContent.startsWith("✅")) {
      setTimeout(() => statusEl.textContent = restore, 3000);
    }
  }
}

async function refreshLatest() {
  if (isCapturing) return; // nada de pisarnos en medio
  try {
    const j = await getLatestMeta();
    if (j?.hasImage && j?.imageUrl) {
      const ts = j.result?.ts ?? 0;
      if (ts > latestTs) setLatestImage(ts, j.imageUrl);
    }
    // Update OCR metric from server-provided metadata when available
    const reading = j?.result?.reading;
    const ts2 = j?.result?.ts ?? 0;
    const wmEl = document.getElementById("wmValue");
    if (typeof reading !== "undefined") {
      if (wmEl) wmEl.textContent = String(reading);
      if (ts2 > lastReadingTs) {
        appendReadingPoint(reading, ts2);
        lastReadingTs = ts2;
      }
    }
    const out = document.getElementById("meterResult");
    if (out && j?.result) out.textContent = JSON.stringify(j.result, null, 2);
  } catch (e) {
    console.warn("refreshLatest failed", e);
  }
}


async function pollCaptureState(token, { untilState, timeoutMs=20000, intervalMs=250 } = {}) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    const r = await fetch(`/api/watermeter/capture/state?token=${encodeURIComponent(token)}&nocache=${Date.now()}`, { cache: "no-store" });
    if (r.ok) {
      const s = await r.json();
      if (s.ok && (!untilState || s.state === untilState)) return s;
    }
    await new Promise(res => setTimeout(res, intervalMs));
  }
  throw new Error(`Timeout waiting for state=${untilState}`);
}

function setLatestImage(ts, imageUrl) {
  const img = document.getElementById("latestImg");
  const url = `${imageUrl}?t=${ts}`;
  const tmp = new Image();
  tmp.onload = () => { img.src = url; latestTs = ts; };
  tmp.src = url;
}


// Expose functions for inline onclick handlers immediately
window.connectSerial = connectSerial;
window.disconnectSerial = disconnectSerial;
window.toggleMockData = toggleMockData;
window.sendCommand = sendCommand;
window.downloadCSV = downloadCSV;
window.analyzeMeter = analyzeMeter;
