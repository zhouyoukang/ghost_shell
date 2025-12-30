package main

import (
	"bytes"
	"embed"
	"encoding/json"
	"fmt"
	"image"

	"image/jpeg"
	"log"
	"net/http"
	"strconv"
	"sync"
	"syscall"
	"time"
	"unsafe"

	"github.com/gorilla/websocket"
)

//go:embed static/*
var staticFiles embed.FS

// Windows API
// Windows API
var (
	user32                       = syscall.NewLazyDLL("user32.dll")
	gdi32                        = syscall.NewLazyDLL("gdi32.dll")
	procGetDC                    = user32.NewProc("GetDC")
	procReleaseDC                = user32.NewProc("ReleaseDC")
	procGetSystemMetrics         = user32.NewProc("GetSystemMetrics")
	procGetForegroundWindow      = user32.NewProc("GetForegroundWindow")
	procGetWindowTextW           = user32.NewProc("GetWindowTextW")
	procGetWindowThreadProcessId = user32.NewProc("GetWindowThreadProcessId")
	procAttachThreadInput        = user32.NewProc("AttachThreadInput")
	procGetWindowDC              = user32.NewProc("GetWindowDC")
	procCreateCompatibleDC       = gdi32.NewProc("CreateCompatibleDC")
	procCreateCompatibleBitmap   = gdi32.NewProc("CreateCompatibleBitmap")
	procSelectObject             = gdi32.NewProc("SelectObject")
	procBitBlt                   = gdi32.NewProc("BitBlt")
	procDeleteDC                 = gdi32.NewProc("DeleteDC")
	procDeleteObject             = gdi32.NewProc("DeleteObject")
	procGetDIBits                = gdi32.NewProc("GetDIBits")
)

const (
	SM_CXSCREEN = 0
	SM_CYSCREEN = 1
	// Multi-monitor virtual screen support
	SM_XVIRTUALSCREEN  = 76 // Left of virtual screen
	SM_YVIRTUALSCREEN  = 77 // Top of virtual screen
	SM_CXVIRTUALSCREEN = 78 // Width of virtual screen
	SM_CYVIRTUALSCREEN = 79 // Height of virtual screen
	SRCCOPY            = 0x00CC0020
	BI_RGB             = 0
)

type BITMAPINFOHEADER struct {
	BiSize          uint32
	BiWidth         int32
	BiHeight        int32
	BiPlanes        uint16
	BiBitCount      uint16
	BiCompression   uint32
	BiSizeImage     uint32
	BiXPelsPerMeter int32
	BiYPelsPerMeter int32
	BiClrUsed       uint32
	BiClrImportant  uint32
}

type BITMAPINFO struct {
	BmiHeader BITMAPINFOHEADER
	BmiColors [1]uint32
}

// Server state
var (
	frameDelay  = time.Millisecond * 40 // 25 FPS (Safe for Audio)
	jpegQuality = 60
	mu          sync.RWMutex
	upgrader    = websocket.Upgrader{
		CheckOrigin: func(r *http.Request) bool { return true },
	}
	// Capture Mode state
	captureMode                  = "foreground" // "fullscreen" or "foreground" or "locked"
	lockedWindowHwnd     uintptr = 0
	originalWindowStates         = make(map[uintptr]RECT)
)

func main() {
	// Enable Per-Monitor DPI Awareness (same as Python: shcore.SetProcessDpiAwareness(2))
	// 2 = PROCESS_PER_MONITOR_DPI_AWARE
	shcore := syscall.NewLazyDLL("shcore.dll")
	procSetProcessDpiAwareness := shcore.NewProc("SetProcessDpiAwareness")
	ret, _, _ := procSetProcessDpiAwareness.Call(2)
	if ret != 0 {
		// Fallback to older API if SetProcessDpiAwareness fails
		procSetProcessDPIAware := user32.NewProc("SetProcessDPIAware")
		procSetProcessDPIAware.Call()
	}

	fmt.Println("🚀 Ghost Shell Go v1.0")
	fmt.Println("📍 Starting server on :8000")

	// APIs
	http.HandleFunc("/", handleRoot)
	http.HandleFunc("/status", handleStatus)
	http.HandleFunc("/windows", handleWindows)
	http.HandleFunc("/lock", handleLock)
	http.HandleFunc("/lock_current", handleLockCurrent) // Lock to current foreground window
	http.HandleFunc("/stream", handleStream)
	http.HandleFunc("/stream/audio", handleAudioStream) // Audio endpoint
	http.HandleFunc("/capture", handleCapture)
	http.HandleFunc("/launch", handleLaunch)     // Quick Launch
	http.HandleFunc("/interact", handleInteract) // HTTP endpoint for input commands

	// Start audio broadcast loop
	go broadcastAudio()

	// Start server
	log.Fatal(http.ListenAndServe(":8000", nil))
}

func handleAudioStream(w http.ResponseWriter, r *http.Request) {
	ws, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}

	audioLock.Lock()
	audioConns[ws] = true
	audioLock.Unlock()

	log.Println("Audio client connected")

	// Keep connection alive until closed
	for {
		if _, _, err := ws.ReadMessage(); err != nil {
			break
		}
	}

	audioLock.Lock()
	delete(audioConns, ws)
	audioLock.Unlock()
	ws.Close()
	log.Println("Audio client disconnected")
}

func handleRoot(w http.ResponseWriter, r *http.Request) {
	data, err := staticFiles.ReadFile("static/ghost_client.html")
	if err != nil {
		http.Error(w, "Client not found", 404)
		return
	}
	w.Header().Set("Content-Type", "text/html")
	w.Write(data)
}

func handleStatus(w http.ResponseWriter, r *http.Request) {
	status := map[string]interface{}{
		"status":  "running",
		"version": "go-1.0",
		"fps":     1000 / int(frameDelay.Milliseconds()),
		"quality": jpegQuality,
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(status)
}

func handleWindows(w http.ResponseWriter, r *http.Request) {
	// Get all windows
	windows := enumWindows()

	// Get foreground
	hwnd, _, _ := procGetForegroundWindow.Call()
	currentTitle := getWindowTitle(hwnd)

	response := map[string]interface{}{
		"windows": windows,
		"current": currentTitle,
		"locked":  nil, // Not implementing locking yet, just returns null
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

type LockRequest struct {
	Title string `json:"title"`
}

func handleLock(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		http.Error(w, "Method not allowed", 405)
		return
	}

	var req LockRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), 400)
		return
	}

	// Unlock if empty title
	if req.Title == "" {
		mu.Lock()
		captureMode = "foreground"
		lockedWindowHwnd = 0
		mu.Unlock()

		response := map[string]interface{}{
			"status":      "unlocked",
			"message":     "已解锁，恢复自动跟随",
			"auto_follow": true,
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(response)
		return
	}

	// Find window by title and activate
	found := false
	windows := enumWindows() // Refresh list
	for _, win := range windows {
		if win.Title == req.Title {
			activateWindow(win.Hwnd)

			// Lock Logic
			mu.Lock()
			captureMode = "locked"
			lockedWindowHwnd = win.Hwnd
			mu.Unlock()

			found = true
			break
		}
	}

	status := "locked"
	msg := "已锁定: " + req.Title
	if !found {
		// If explicit lock fails, revert to foreground
		mu.Lock()
		captureMode = "foreground"
		lockedWindowHwnd = 0
		mu.Unlock()

		status = "error"
		msg = "未找到窗口: " + req.Title
	}

	response := map[string]interface{}{
		"status":      status,
		"message":     msg,
		"title":       req.Title,
		"auto_follow": status != "locked",
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// handleLockCurrent locks to the currently visible foreground window
func handleLockCurrent(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		http.Error(w, "Method not allowed", 405)
		return
	}

	// Get current foreground window
	hwnd, _, _ := procGetForegroundWindow.Call()
	if hwnd == 0 {
		response := map[string]string{
			"status":  "error",
			"message": "没有找到可锁定的窗口",
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(response)
		return
	}

	// Get window title
	title := getWindowTitle(hwnd)

	// Lock to this window
	mu.Lock()
	captureMode = "locked"
	lockedWindowHwnd = hwnd
	mu.Unlock()

	response := map[string]interface{}{
		"status":      "locked",
		"message":     "已锁定: " + title,
		"title":       title,
		"auto_follow": false,
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func handleCapture(w http.ResponseWriter, r *http.Request) {
	// Simple capture of full screen for snapshot API
	img, _ := captureScreen(RECT{}) // Empty rect means full screen default
	if img == nil {
		http.Error(w, "Capture failed", 500)
		return
	}

	w.Header().Set("Content-Type", "image/jpeg")
	jpeg.Encode(w, img, &jpeg.Options{Quality: jpegQuality})
}

// handleInteract processes HTTP POST requests for mouse/keyboard input
func handleInteract(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		http.Error(w, "Method not allowed", 405)
		return
	}

	var cmd Command
	if err := json.NewDecoder(r.Body).Decode(&cmd); err != nil {
		http.Error(w, err.Error(), 400)
		return
	}

	// Get current window offset (same as WebSocket handler)
	mu.RLock()
	locked := lockedWindowHwnd
	mode := captureMode
	mu.RUnlock()

	// Calculate absolute coordinates
	var absX, absY int
	if mode == "locked" && locked != 0 {
		// Get locked window rect
		var rect RECT
		procGetWindowRect.Call(locked, uintptr(unsafe.Pointer(&rect)))
		absX = cmd.X + int(rect.Left)
		absY = cmd.Y + int(rect.Top)
	} else {
		// Get foreground window rect
		hwnd, _, _ := procGetForegroundWindow.Call()
		if hwnd != 0 {
			var rect RECT
			procGetWindowRect.Call(hwnd, uintptr(unsafe.Pointer(&rect)))
			absX = cmd.X + int(rect.Left)
			absY = cmd.Y + int(rect.Top)
		} else {
			absX = cmd.X
			absY = cmd.Y
		}
	}

	// Process command with absolute coordinates
	switch cmd.Action {
	case "move":
		moveMouse(absX, absY)
	case "click":
		button := cmd.Button
		if button == "" {
			button = "left" // default to left click
		}
		clickMouse(absX, absY, button)
	case "scroll":
		// Parse scroll amount from Text field if Delta is 0
		delta := cmd.Delta
		if delta == 0 && cmd.Text != "" {
			if parsed, err := strconv.Atoi(cmd.Text); err == nil {
				delta = parsed
			}
		}
		if delta != 0 {
			scrollMouse(delta)
		}
	case "key":
		simulateKey(cmd.Key)
	case "type", "text":
		if cmd.Text != "" {
			simulateText(cmd.Text)
		}
	case "hotkey":
		// Handle hotkey combinations like "ctrl+c"
		simulateKey(cmd.Key)
	case "adapt_phone":
		// Resize to phone dimensions
		hwnd := lockedWindowHwnd
		if hwnd == 0 {
			h, _, _ := procGetForegroundWindow.Call()
			hwnd = h
		}
		if hwnd != 0 {
			mu.Lock()
			if _, ok := originalWindowStates[hwnd]; !ok {
				var rect RECT
				procGetWindowRect.Call(hwnd, uintptr(unsafe.Pointer(&rect)))
				originalWindowStates[hwnd] = rect
			}
			mu.Unlock()

			// Python Logic: match ghost_server.py
			monLeft, monTop, monWidth, monHeight := getMonitorWorkArea(hwnd)
			isHorizontal := monWidth > monHeight

			var targetW, targetH, targetX, targetY int
			if isHorizontal {
				// Horizontal: 1/3 width, full height, right align
				targetW = monWidth / 3
				targetH = monHeight
				targetX = monLeft + monWidth - targetW
				targetY = monTop
			} else {
				// Vertical: width = height/3, height = width, bottom-right align
				targetW = monHeight / 3
				targetH = monWidth
				targetX = monLeft + monWidth - targetW
				targetY = monTop + monHeight - targetH
			}

			moveAndResizeWindow(hwnd, targetX, targetY, targetW, targetH)
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(map[string]interface{}{
				"status":  "adapted",
				"size":    []int{targetW, targetH},
				"message": fmt.Sprintf("已适配 (%dx%d)", targetW, targetH),
			})
			return
		}
	case "restore_window":
		// Restore to default
		hwnd := lockedWindowHwnd
		if hwnd == 0 {
			h, _, _ := procGetForegroundWindow.Call()
			hwnd = h
		}
		if hwnd != 0 {
			mu.Lock()
			rect, ok := originalWindowStates[hwnd]
			if ok {
				delete(originalWindowStates, hwnd)
			}
			mu.Unlock()

			if ok {
				width := int(rect.Right - rect.Left)
				height := int(rect.Bottom - rect.Top)
				moveAndResizeWindow(hwnd, int(rect.Left), int(rect.Top), width, height)
				w.Header().Set("Content-Type", "application/json")
				json.NewEncoder(w).Encode(map[string]interface{}{
					"status":  "restored",
					"message": fmt.Sprintf("已恢复原窗口大小 (%dx%d)", width, height),
				})
			} else {
				resizeWindow(hwnd, 1280, 720)
				w.Header().Set("Content-Type", "application/json")
				json.NewEncoder(w).Encode(map[string]interface{}{
					"status":  "restored",
					"message": "已恢复默认大小 (1280x720) - 无保存状态",
				})
			}
			return
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

// handleLaunch opens Windows Search (Win+S) and types the app name
func handleLaunch(w http.ResponseWriter, r *http.Request) {
	// CORS headers
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

	if r.Method == "OPTIONS" {
		return
	}

	if r.Method != "POST" {
		http.Error(w, "Method not allowed", 405)
		return
	}

	var req struct {
		App string `json:"app"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), 400)
		return
	}

	log.Printf("[Launch] Opening search for: %s", req.App)

	// Use goroutine to avoid blocking HTTP response
	go func() {
		// 1. Press Win+S to open search
		log.Printf("[Launch] 1. Pressing Win+S (VK_LWIN + VK_S)")
		simulateHotkey(VK_LWIN, VK_S)

		// 2. Wait 1 second for search to open
		time.Sleep(1 * time.Second)

		// 3. Copy app name to clipboard
		log.Printf("[Launch] 2. Setting Clipboard: %s", req.App)
		if err := setClipboard(req.App); err != nil {
			log.Printf("[Launch] Clipboard error: %v", err)
			return
		}

		// 4. Paste with Ctrl+V (Wait a bit for clipboard to settle)
		log.Printf("[Launch] 3. Pasting (Ctrl+V)")
		time.Sleep(100 * time.Millisecond)
		simulateHotkey(VK_CONTROL, VK_V)

		// 5. Wait 0.5s for search results
		time.Sleep(500 * time.Millisecond)

		// 6. Press Enter to launch
		log.Printf("[Launch] 4. Pressing Enter")
		simulateKeyVk(VK_RETURN)
	}()

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{
		"status":  "launched",
		"message": "已启动: " + req.App,
	})
}

// Command structure from client
type Command struct {
	Action string `json:"action"`
	Type   string `json:"type"` // For type-based commands like lock_current
	X      int    `json:"x"`
	Y      int    `json:"y"`
	Button string `json:"button"`
	Text   string `json:"text"`
	Key    string `json:"key"`
	Delta  int    `json:"delta"`
	Fps    int    `json:"fps"`
	Mode   string `json:"mode"`
}

func handleStream(w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("WebSocket upgrade failed: %v", err)
		return
	}
	defer conn.Close()

	log.Println("📱 Client connected")

	// Control channel
	stopCh := make(chan struct{})
	defer close(stopCh)

	// Video State
	videoEnabled := true
	videoMu := sync.RWMutex{}

	// Input Offset
	var offsetMu sync.RWMutex
	var currentOffsetX, currentOffsetY int

	// Start write loop (Screen Capture)
	go func() {
		var lastFrame *image.RGBA
		// Bandwidth Analysis Stats
		var bytesSent int64
		var framesSent int
		statTicker := time.NewTicker(2 * time.Second)
		defer statTicker.Stop()

		for {
			select {
			case <-statTicker.C:
				if framesSent > 0 {
					avg := bytesSent / int64(framesSent)
					mbps := float64(bytesSent) / 1024 / 1024 / 2.0 // MB/s over 2 seconds
					log.Printf("[TRAFFIC] %d frames, Avg Size: %d KB, Rate: %.2f MB/s", framesSent, avg/1024, mbps)
					framesSent = 0
					bytesSent = 0
				}
			default:
			}
			frameStart := time.Now()
			select {
			case <-stopCh:
				return
			default:
				// Check if video is enabled
				videoMu.RLock()
				enabled := videoEnabled
				videoMu.RUnlock()

				if !enabled {
					time.Sleep(200 * time.Millisecond) // Low power mode
					continue
				}

				// Determine capture rect
				var targetRect RECT
				var targetHwnd uintptr = 0

				mu.RLock()
				mode := captureMode
				locked := lockedWindowHwnd
				mu.RUnlock()

				if mode == "locked" && locked != 0 {
					targetHwnd = locked
				} else if mode == "foreground" {
					targetHwnd, _, _ = procGetForegroundWindow.Call()
				}

				// Update window rect if locked
				if lockedWindowHwnd != 0 {
					targetRect = getWindowVisualBounds(lockedWindowHwnd)
				} else if targetHwnd != 0 {
					// Get Rect for dimensions for foreground window
					procGetWindowRect.Call(targetHwnd, uintptr(unsafe.Pointer(&targetRect)))
				}

				// Capture screen area (simple BitBlt from screen DC)
				img, origin := captureScreen(targetRect)

				// --- Smart Frame Skipping (Deduplication) ---
				// Check for content similarity (ignore minor noise/cursor blinking)
				skipped := false
				if lastFrame != nil && img != nil {
					if framesAreSimilar(img, lastFrame) {
						skipped = true
						img = nil // Skip processing and sending
					}
				}

				if !skipped && img != nil {
					lastFrame = img // Update reference
				}
				// ---------------------------------------------

				// Update Offsets for Input Mapping
				offsetMu.Lock()
				currentOffsetX = origin.X
				currentOffsetY = origin.Y
				offsetMu.Unlock()

				if img != nil {
					buf := encodeJPEG(img)

					// === WAN Optimization: Write Timeout ===
					// Set 150ms deadline - if network is slow, skip this frame
					conn.SetWriteDeadline(time.Now().Add(150 * time.Millisecond))

					mu.Lock()
					err := conn.WriteMessage(websocket.BinaryMessage, buf)
					mu.Unlock()

					if err != nil {
						// Check if it's a timeout (network congestion)
						if netErr, ok := err.(interface{ Timeout() bool }); ok && netErr.Timeout() {
							// Network congestion - skip frame, slow down
							mu.Lock()
							if frameDelay < 200*time.Millisecond {
								frameDelay = frameDelay * 15 / 10 // +50% delay
								log.Printf("[WAN] Congestion detected, FPS reduced to %.1f", 1000.0/float64(frameDelay.Milliseconds()))
							}
							if jpegQuality > 30 {
								jpegQuality -= 10 // Reduce quality
								log.Printf("[WAN] Quality reduced to %d", jpegQuality)
							}
							mu.Unlock()
							// Don't return, continue trying
						} else {
							// Real error (disconnect)
							return
						}
					} else {
						// Success - gradually recover FPS/Quality
						mu.Lock()
						if frameDelay > 40*time.Millisecond {
							frameDelay = frameDelay * 95 / 100 // -5% delay (faster)
						}
						if jpegQuality < 60 {
							jpegQuality++ // Slowly recover quality
						}
						mu.Unlock()
					}

					// Clear deadline for next iteration
					conn.SetWriteDeadline(time.Time{})

					// Stats Update
					bytesSent += int64(len(buf))
					framesSent++
				}

				// Dynamic delay logic (Adaptive FPS)
				mu.RLock()
				targetInterval := frameDelay
				mu.RUnlock()

				elapsed := time.Since(frameStart)
				if elapsed < targetInterval {
					time.Sleep(targetInterval - elapsed)
				} else {
					// Doing work took longer than frame budget.
					// CRITICAL: Must yield significantly to allow Audio thread to run.
					// 2ms is a safe minimum to force un-scheduling of this tight loop.
					time.Sleep(2 * time.Millisecond)
				}
			}
		}
	}()

	// Read loop (Input Commands)
	for {
		var cmd Command
		err := conn.ReadJSON(&cmd)
		if err != nil {
			log.Printf("WebSocket read error: %v", err)
			break
		}

		// Apply Offset
		offsetMu.RLock()
		offX, offY := currentOffsetX, currentOffsetY
		offsetMu.RUnlock()

		realX := cmd.X + offX
		realY := cmd.Y + offY

		// Process command - use Type for type-based commands, Action otherwise
		cmdType := cmd.Action
		if cmd.Type != "" {
			cmdType = cmd.Type
		}

		switch cmdType {
		case "move":
			moveMouse(realX, realY)
		case "click":
			clickMouse(realX, realY, cmd.Button)
		case "scroll":
			// Parse scroll amount from Text field if Delta is 0
			delta := cmd.Delta
			if delta == 0 && cmd.Text != "" {
				if parsed, err := strconv.Atoi(cmd.Text); err == nil {
					delta = parsed
				}
			}
			if delta != 0 {
				scrollMouse(delta)
			}
		case "key":
			simulateKey(cmd.Key)
		case "type", "text":
			if cmd.Text != "" {
				simulateText(cmd.Text)
			}
		case "fps":
			if cmd.Fps > 0 {
				mu.Lock()
				frameDelay = time.Second / time.Duration(cmd.Fps)
				mu.Unlock()
			}
		case "set_mode":
			videoMu.Lock()
			if cmd.Mode == "audio_only" {
				videoEnabled = false
				log.Println("📺 Video disabled (Audio Only Mode)")
			} else {
				videoEnabled = true
				log.Println("📺 Video enabled")
			}
			videoMu.Unlock()
		case "lock_current":
			// Lock to current foreground window
			hwnd, _, _ := procGetForegroundWindow.Call()
			if hwnd != 0 {
				title := getWindowTitle(hwnd)
				mu.Lock()
				captureMode = "locked"
				lockedWindowHwnd = hwnd
				mu.Unlock()
				response := map[string]interface{}{
					"type":        "lock_result",
					"status":      "locked",
					"title":       title,
					"message":     "已锁定: " + title,
					"auto_follow": false,
				}
				conn.WriteJSON(response)
			} else {
				conn.WriteJSON(map[string]string{
					"type":    "lock_result",
					"status":  "error",
					"message": "没有找到可锁定的窗口",
				})
			}
		case "unlock":
			mu.Lock()
			captureMode = "foreground"
			lockedWindowHwnd = 0
			mu.Unlock()
			conn.WriteJSON(map[string]interface{}{
				"type":        "lock_result",
				"status":      "unlocked",
				"message":     "已解锁，恢复自动跟随",
				"auto_follow": true,
			})
		case "adapt_phone":
			// Resize to phone dimensions based on screen size (0.85 height, 9:16 aspect)
			hwnd := lockedWindowHwnd
			if hwnd == 0 {
				h, _, _ := procGetForegroundWindow.Call()
				hwnd = h
			}
			if hwnd != 0 {
				// Save original state if not already saved
				mu.Lock()
				if _, ok := originalWindowStates[hwnd]; !ok {
					var rect RECT
					procGetWindowRect.Call(hwnd, uintptr(unsafe.Pointer(&rect)))
					originalWindowStates[hwnd] = rect
				}
				mu.Unlock()

				// Python Logic: match ghost_server.py
				monLeft, monTop, monWidth, monHeight := getMonitorWorkArea(hwnd)
				isHorizontal := monWidth > monHeight

				var targetW, targetH, targetX, targetY int
				if isHorizontal {
					// Horizontal: 1/3 width, full height, right align
					targetW = monWidth / 3
					targetH = monHeight
					targetX = monLeft + monWidth - targetW
					targetY = monTop
				} else {
					// Vertical: width = height/3, height = width, bottom-right align
					targetW = monHeight / 3
					targetH = monWidth
					targetX = monLeft + monWidth - targetW
					targetY = monTop + monHeight - targetH
				}

				moveAndResizeWindow(hwnd, targetX, targetY, targetW, targetH)
				conn.WriteJSON(map[string]interface{}{
					"status":  "adapted",
					"size":    []int{targetW, targetH},
					"message": fmt.Sprintf("已适配 (%dx%d)", targetW, targetH),
				})
			} else {
				conn.WriteJSON(map[string]string{
					"status":  "error",
					"message": "未找到窗口",
				})
			}

		case "restore_window":
			hwnd := lockedWindowHwnd
			if hwnd == 0 {
				h, _, _ := procGetForegroundWindow.Call()
				hwnd = h
			}
			if hwnd != 0 {
				mu.Lock()
				rect, ok := originalWindowStates[hwnd]
				if ok {
					delete(originalWindowStates, hwnd)
				}
				mu.Unlock()

				if ok {
					w := int(rect.Right - rect.Left)
					h := int(rect.Bottom - rect.Top)
					moveAndResizeWindow(hwnd, int(rect.Left), int(rect.Top), w, h)
					conn.WriteJSON(map[string]interface{}{
						"status":  "restored",
						"message": fmt.Sprintf("已恢复 (%dx%d)", w, h),
					})
				} else {
					resizeWindow(hwnd, 1280, 720)
					conn.WriteJSON(map[string]string{
						"status":  "restored",
						"message": "已恢复默认 (1280x720)",
					})
				}
			} else {
				conn.WriteJSON(map[string]string{
					"status":  "error",
					"message": "未找到窗口",
				})
			}
		}
	}

	log.Println("📱 Client disconnected")
}

func handleMicStream(w http.ResponseWriter, r *http.Request) {
	ws, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	defer ws.Close()

	log.Println("🎤 Mic stream connected")

	// Simply read and discard for now
	for {
		_, _, err := ws.ReadMessage()
		if err != nil {
			break
		}
	}

	log.Println("🎤 Mic stream disconnected")
}

// Capture window using GetWindowDC with shadow trimming
func captureScreenWithHwnd(hwnd uintptr, visualRect RECT) (*image.RGBA, image.Point) {
	w := int(visualRect.Right - visualRect.Left)
	h := int(visualRect.Bottom - visualRect.Top)

	// Fallback
	if hwnd == 0 || w <= 0 || h <= 0 {
		return captureScreen(visualRect)
	}

	// Get full window rect (including shadows) to calculate offset
	var fullRect RECT
	procGetWindowRect.Call(hwnd, uintptr(unsafe.Pointer(&fullRect)))
	offsetX := int(visualRect.Left - fullRect.Left)
	offsetY := int(visualRect.Top - fullRect.Top)

	// Get window DC
	procGetWindowDC := user32.NewProc("GetWindowDC")
	hdcWindow, _, _ := procGetWindowDC.Call(hwnd)
	if hdcWindow == 0 {
		return captureScreen(visualRect)
	}
	defer procReleaseDC.Call(hwnd, hdcWindow)

	// Create compatible DC and bitmap
	hdcMem, _, _ := procCreateCompatibleDC.Call(hdcWindow)
	if hdcMem == 0 {
		return captureScreen(visualRect)
	}
	defer procDeleteDC.Call(hdcMem)

	hBitmap, _, _ := procCreateCompatibleBitmap.Call(hdcWindow, uintptr(w), uintptr(h))
	if hBitmap == 0 {
		return captureScreen(visualRect)
	}
	defer procDeleteObject.Call(hBitmap)

	// Select bitmap into DC
	oldBitmap, _, _ := procSelectObject.Call(hdcMem, hBitmap)
	defer procSelectObject.Call(hdcMem, oldBitmap)

	// BitBlt with offset
	ret, _, _ := procBitBlt.Call(hdcMem, 0, 0, uintptr(w), uintptr(h), hdcWindow, uintptr(offsetX), uintptr(offsetY), SRCCOPY)

	if ret == 0 {
		return captureScreen(visualRect)
	}

	// Get bits
	var bi BITMAPINFO
	bi.BmiHeader.BiSize = uint32(unsafe.Sizeof(bi.BmiHeader))
	bi.BmiHeader.BiWidth = int32(w)
	bi.BmiHeader.BiHeight = int32(-h) // Top-down
	bi.BmiHeader.BiPlanes = 1
	bi.BmiHeader.BiBitCount = 32
	bi.BmiHeader.BiCompression = BI_RGB

	img := image.NewRGBA(image.Rect(0, 0, w, h))
	procGetDIBits.Call(hdcMem, hBitmap, 0, uintptr(h), uintptr(unsafe.Pointer(&img.Pix[0])), uintptr(unsafe.Pointer(&bi)), 0) // DIB_RGB_COLORS

	// Convert BGRA to RGBA
	for i := 0; i < len(img.Pix); i += 4 {
		img.Pix[i], img.Pix[i+2] = img.Pix[i+2], img.Pix[i]
	}

	return img, image.Point{X: int(visualRect.Left), Y: int(visualRect.Top)}
}

// return image and TopLeft point for offset (fallback/fullscreen)
func captureScreen(target RECT) (*image.RGBA, image.Point) {
	// Get virtual screen info for multi-monitor support
	virtualLeft, _, _ := procGetSystemMetrics.Call(SM_XVIRTUALSCREEN)
	virtualTop, _, _ := procGetSystemMetrics.Call(SM_YVIRTUALSCREEN)
	virtualWidth, _, _ := procGetSystemMetrics.Call(SM_CXVIRTUALSCREEN)
	virtualHeight, _, _ := procGetSystemMetrics.Call(SM_CYVIRTUALSCREEN)

	// Fallback to primary screen if virtual screen not available
	if virtualWidth == 0 || virtualHeight == 0 {
		virtualLeft = 0
		virtualTop = 0
		virtualWidth, _, _ = procGetSystemMetrics.Call(SM_CXSCREEN)
		virtualHeight, _, _ = procGetSystemMetrics.Call(SM_CYSCREEN)
	}

	// Determine dimensions
	var x, y, w, h int32
	if target.Right > target.Left && target.Bottom > target.Top {
		x = target.Left
		y = target.Top
		w = target.Right - target.Left
		h = target.Bottom - target.Top
	} else {
		// Full screen default (virtual screen)
		x = int32(virtualLeft)
		y = int32(virtualTop)
		w = int32(virtualWidth)
		h = int32(virtualHeight)
	}

	// Boundary checks
	if w <= 0 || h <= 0 {
		return nil, image.Point{0, 0}
	}

	// Get screen DC (covers entire virtual screen)
	hdcScreen, _, _ := procGetDC.Call(0)
	defer procReleaseDC.Call(0, hdcScreen)

	// Create compatible DC and bitmap
	hdcMem, _, _ := procCreateCompatibleDC.Call(hdcScreen)
	defer procDeleteDC.Call(hdcMem)

	hBitmap, _, _ := procCreateCompatibleBitmap.Call(hdcScreen, uintptr(w), uintptr(h))
	defer procDeleteObject.Call(hBitmap)

	// Select bitmap into DC
	procSelectObject.Call(hdcMem, hBitmap)

	// BitBlt copy: Source X/Y are x,y
	procBitBlt.Call(hdcMem, 0, 0, uintptr(w), uintptr(h), hdcScreen, uintptr(x), uintptr(y), SRCCOPY)

	// Get bits
	// (Reuse struct definitions)
	bmi := BITMAPINFO{}
	bmi.BmiHeader.BiSize = uint32(unsafe.Sizeof(bmi.BmiHeader))
	bmi.BmiHeader.BiWidth = w
	bmi.BmiHeader.BiHeight = -h // Negative for top-down
	bmi.BmiHeader.BiPlanes = 1
	bmi.BmiHeader.BiBitCount = 32
	bmi.BmiHeader.BiCompression = BI_RGB

	pixelData := make([]byte, int(w)*int(h)*4)
	ret, _, _ := procGetDIBits.Call(
		hdcMem,
		hBitmap,
		0,
		uintptr(h),
		uintptr(unsafe.Pointer(&pixelData[0])),
		uintptr(unsafe.Pointer(&bmi)),
		0,
	)

	if ret == 0 {
		return nil, image.Point{0, 0}
	}

	// Convert BGRA to RGBA
	img := image.NewRGBA(image.Rect(0, 0, int(w), int(h)))
	for i := 0; i < len(pixelData); i += 4 {
		img.Pix[i+0] = pixelData[i+2] // R
		img.Pix[i+1] = pixelData[i+1] // G
		img.Pix[i+2] = pixelData[i+0] // B
		img.Pix[i+3] = 255            // A
	}

	return img, image.Point{X: int(x), Y: int(y)}
}

func encodeJPEG(img *image.RGBA) []byte {
	var buf = make([]byte, 0, 100*1024) // Pre-allocate 100KB
	w := &bufWriter{buf: buf}
	jpeg.Encode(w, img, &jpeg.Options{Quality: jpegQuality})
	return w.buf
}

type bufWriter struct {
	buf []byte
}

func (w *bufWriter) Write(p []byte) (n int, err error) {
	w.buf = append(w.buf, p...)
	return len(p), nil
}

func getWindowTitle(hwnd uintptr) string {
	buf := make([]uint16, 256)
	procGetWindowTextW.Call(hwnd, uintptr(unsafe.Pointer(&buf[0])), 256)
	return syscall.UTF16ToString(buf)
}

// framesAreSimilar checks if two images are roughly identical
// optimization: checks a subset of pixels to be fast.
func framesAreSimilar(a, b *image.RGBA) bool {
	if a.Rect != b.Rect {
		return false
	}
	if len(a.Pix) != len(b.Pix) {
		return false
	}

	// 1. Fast path: Exact match
	if bytes.Equal(a.Pix, b.Pix) {
		return true
	}

	// 2. Fuzzy path: Sample check
	// Check 1 pixel every 32 bytes (8 pixels)
	// If differences exceed 0.5% of samples, consider it changed.
	diffCount := 0
	samples := 0
	step := 32
	threshold := (len(a.Pix) / step) / 200 // 0.5% tolerance

	for i := 0; i < len(a.Pix); i += step {
		samples++
		if a.Pix[i] != b.Pix[i] { // Check R component of pixel
			diffCount++
			if diffCount > threshold {
				return false
			}
		}
	}
	return true
}
