# Ghost Shell (Go Edition)

**Ghost Shell** is an ultra-low latency, high-performance remote desktop control software designed for local area networks (LAN).

> **Note**: This is the rewritten **Go version**, offering significantly better performance, lower latency, and zero-dependency deployment compared to the previous Python versions.

## 🚀 Key Features

* **Ultra-Low Latency**: ~30ms capture-to-display delay in LAN environments.
* **High Performance**: 30 FPS steady streaming with Smart Frame Skipping (Deduplication).
* **Audio Sync**: Real-time system audio capture (WASAPI) with adaptive buffering.
* **Zero Dependencies**: Single binary executable (`.exe`). No Python/Node.js installation required.
* **Web Client**: Works on any device with a modern browser (iOS, Android, Mac, Linux, Windows).
* **Traffic Monitor**: Real-time bandwidth and FPS monitoring.

## 📦 Installation & Usage

### For the Host (Windows PC)

**Requirements**: Windows 10 or Windows 11.

1. Download or Build `ghost_shell.exe`.
2. Run `ghost_shell.exe`.
3. Allow network access in the Windows Firewall prompt if asked.
4. The server will listen on `0.0.0.0:8000`.

### For the Client (Viewer)

**Requirements**: Any device with a web browser (Chrome/Safari/Edge recommended).

1. Open your browser.
2. Navigate to `http://[Host-IP-Address]:8000`.
    * *Example*: `http://192.168.1.10:8000`
3. Click anywhere to start the stream and enable audio.

## 🔧 Building from Source

To build the project yourself (requires Go 1.23+):

```bash
# Clone the repository
git clone https://github.com/YourUsername/ghost_shell.git

# Enter directory
cd ghost_shell

# Build (Standard)
go build -o ghost_shell.exe .

# Build (Release - Smaller Size)
go build -ldflags "-s -w" -o ghost_shell.exe .
```

## 🛠 Advanced Configuration

The application currently auto-configures itself for best performance.
* **Port**: 8000 (Default)
* **Audio**: WASAPI Loopback (Auto)
* **Video**: GDI Capture + MJPEG (Auto)

## 📂 Legacy Versions

Previous Python implementations are archived in the `legacy/` directory:
* `legacy/webrtc`: Original WebRTC+Python implementation.
* `legacy/python_websocket`: Python WebSocket implementation.
