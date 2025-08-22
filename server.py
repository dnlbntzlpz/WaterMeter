#!/usr/bin/env python3
"""
Simple HTTP Server for Arduino Web Serial Dashboard
Serves the index.html file and provides basic web server functionality
"""

import http.server
import socketserver
import os
import sys
from pathlib import Path

# Configuration
PORT = 8000
DIRECTORY = Path(__file__).parent

class CustomHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Custom HTTP request handler with CORS support"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIRECTORY), **kwargs)
    
    def end_headers(self):
        """Add CORS headers for Web Serial API"""
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()
    
    def do_OPTIONS(self):
        """Handle preflight requests for CORS"""
        self.send_response(200)
        self.end_headers()

def main():
    """Main function to start the server"""
    print("=" * 60)
    print("🚀 Arduino Web Serial Dashboard Server")
    print("=" * 60)
    print(f"📁 Serving directory: {DIRECTORY}")
    print(f"🌐 Server URL: http://localhost:{PORT}")
    print(f"📱 Dashboard: http://localhost:{PORT}/index.html")
    print("=" * 60)
    print("📋 Instructions:")
    print("1. Upload arduino_simulator.ino to your Arduino")
    print("2. Open http://localhost:8000 in Chrome/Edge")
    print("3. Click 'Connect Serial' or 'Mock Data'")
    print("=" * 60)
    print("Press Ctrl+C to stop the server")
    print("=" * 60)
    
    try:
        with socketserver.TCPServer(("", PORT), CustomHTTPRequestHandler) as httpd:
            print(f"✅ Server started on port {PORT}")
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Server stopped by user")
    except OSError as e:
        if e.errno == 48:  # Address already in use
            print(f"❌ Port {PORT} is already in use")
            print("💡 Try a different port or stop the existing server")
        else:
            print(f"❌ Server error: {e}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")

if __name__ == "__main__":
    main()
