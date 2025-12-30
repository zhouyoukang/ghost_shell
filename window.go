package main

import (
	"syscall"
	"unsafe"
)

var (
	procEnumWindows         = user32.NewProc("EnumWindows")
	procIsWindowVisible     = user32.NewProc("IsWindowVisible")
	procSetForegroundWindow = user32.NewProc("SetForegroundWindow")
	procShowWindow          = user32.NewProc("ShowWindow")
	procGetWindowRect       = user32.NewProc("GetWindowRect")
	procPrintWindow         = user32.NewProc("PrintWindow")
	procSetWindowPos        = user32.NewProc("SetWindowPos")
	procMonitorFromWindow   = user32.NewProc("MonitorFromWindow")
	procGetMonitorInfo      = user32.NewProc("GetMonitorInfoW")

	dwmapi                    = syscall.NewLazyDLL("dwmapi.dll")
	procDwmGetWindowAttribute = dwmapi.NewProc("DwmGetWindowAttribute")
)

const (
	DWMWA_EXTENDED_FRAME_BOUNDS = 9
)

// SWP flags
const (
	SWP_NOSIZE       = 0x0001
	SWP_NOMOVE       = 0x0002
	SWP_NOZORDER     = 0x0004
	SWP_NOREDRAW     = 0x0008
	SWP_NOACTIVATE   = 0x0010
	SWP_FRAMECHANGED = 0x0020
	SWP_SHOWWINDOW   = 0x0040
)

const (
	MONITOR_DEFAULTTONEAREST = 0x00000002
)

type MONITORINFO struct {
	CbSize    uint32
	RcMonitor RECT
	RcWork    RECT
	DwFlags   uint32
}

func getMonitorWorkArea(hwnd uintptr) (int, int, int, int) {
	monitor, _, _ := procMonitorFromWindow.Call(hwnd, MONITOR_DEFAULTTONEAREST)
	if monitor != 0 {
		var mi MONITORINFO
		mi.CbSize = uint32(unsafe.Sizeof(mi))
		ret, _, _ := procGetMonitorInfo.Call(monitor, uintptr(unsafe.Pointer(&mi)))
		if ret != 0 {
			x := int(mi.RcWork.Left)
			y := int(mi.RcWork.Top)
			width := int(mi.RcWork.Right - mi.RcWork.Left)
			height := int(mi.RcWork.Bottom - mi.RcWork.Top)
			return x, y, width, height
		}
	}
	// Fallback to primary screen
	w, _, _ := procGetSystemMetrics.Call(0) // SM_CXSCREEN
	h, _, _ := procGetSystemMetrics.Call(1) // SM_CYSCREEN
	return 0, 0, int(w), int(h)
}

func resizeWindow(hwnd uintptr, w, h int) {
	// resizing only, keep position and z-order
	procSetWindowPos.Call(hwnd, 0, 0, 0, uintptr(w), uintptr(h), SWP_NOMOVE|SWP_NOZORDER|SWP_NOACTIVATE)
}

func moveAndResizeWindow(hwnd uintptr, x, y, w, h int) {
	// move and resize, keep z-order
	procSetWindowPos.Call(hwnd, 0, uintptr(x), uintptr(y), uintptr(w), uintptr(h), SWP_NOZORDER|SWP_NOACTIVATE)
}

type RECT struct {
	Left, Top, Right, Bottom int32
}

type WindowInfo struct {
	Title string  `json:"title"`
	Hwnd  uintptr `json:"hwnd"`
	Size  string  `json:"size"`
}

var currentWindowList []WindowInfo

func enumWindows() []WindowInfo {
	currentWindowList = make([]WindowInfo, 0)
	cb := syscall.NewCallback(func(hwnd uintptr, lparam uintptr) uintptr {
		if isVisible(hwnd) {
			title := getWindowTitle(hwnd)
			if title != "" && title != "Program Manager" {
				rect := getWindowRect(hwnd)
				width := rect.Right - rect.Left
				height := rect.Bottom - rect.Top

				// Filter small windows
				if width > 10 && height > 10 {
					currentWindowList = append(currentWindowList, WindowInfo{
						Title: title,
						Hwnd:  hwnd,
						// Format size string
						Size: string(itoa(int(width))) + "x" + string(itoa(int(height))),
					})
				}
			}
		}
		return 1 // Continue enumeration
	})
	procEnumWindows.Call(cb, 0)
	return currentWindowList
}

func isVisible(hwnd uintptr) bool {
	ret, _, _ := procIsWindowVisible.Call(hwnd)
	return ret != 0
}

func getWindowRect(hwnd uintptr) RECT {
	var rect RECT
	procGetWindowRect.Call(hwnd, uintptr(unsafe.Pointer(&rect)))
	return rect
}

func activateWindow(hwnd uintptr) {
	// SW_RESTORE = 9
	// Force Active with AttachThreadInput
	fgHwnd, _, _ := procGetForegroundWindow.Call()
	fgThreadId, _, _ := procGetWindowThreadProcessId.Call(fgHwnd, 0)
	targetThreadId, _, _ := procGetWindowThreadProcessId.Call(hwnd, 0)

	if fgThreadId != targetThreadId {
		procAttachThreadInput.Call(fgThreadId, targetThreadId, 1) // Attach
		procSetForegroundWindow.Call(hwnd)
		procShowWindow.Call(hwnd, 9)
		procAttachThreadInput.Call(fgThreadId, targetThreadId, 0) // Detach
	} else {
		procSetForegroundWindow.Call(hwnd)
		procShowWindow.Call(hwnd, 9)
	}
}

// Helper for integer to string since we use it in callback
func itoa(v int) []byte {
	if v == 0 {
		return []byte("0")
	}
	var buf [20]byte
	i := len(buf) - 1
	for v > 0 {
		buf[i] = byte('0' + v%10)
		v /= 10
		i--
	}
	return buf[i+1:]
}

func getWindowVisualBounds(hwnd uintptr) RECT {
	var rect RECT
	// Try DWM first (Windows Vista+)
	if procDwmGetWindowAttribute.Find() == nil {
		ret, _, _ := procDwmGetWindowAttribute.Call(
			hwnd,
			uintptr(DWMWA_EXTENDED_FRAME_BOUNDS),
			uintptr(unsafe.Pointer(&rect)),
			uintptr(unsafe.Sizeof(rect)),
		)
		if ret == 0 { // S_OK
			return rect
		}
	}
	// Fallback to GetWindowRect
	procGetWindowRect.Call(hwnd, uintptr(unsafe.Pointer(&rect)))
	return rect
}
