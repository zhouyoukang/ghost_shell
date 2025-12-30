package main

import (
	"fmt"
	"syscall"
	"time"
	"unicode/utf16"
	"unsafe"
)

// Windows API for Input
var (
	user32DLL        = syscall.NewLazyDLL("user32.dll")
	procSendInput    = user32DLL.NewProc("SendInput")
	procSetCursorPos = user32DLL.NewProc("SetCursorPos")
)

const (
	INPUT_MOUSE    = 0
	INPUT_KEYBOARD = 1
	INPUT_HARDWARE = 2

	// Mouse flags
	MOUSEEVENTF_MOVE       = 0x0001
	MOUSEEVENTF_LEFTDOWN   = 0x0002
	MOUSEEVENTF_LEFTUP     = 0x0004
	MOUSEEVENTF_RIGHTDOWN  = 0x0008
	MOUSEEVENTF_RIGHTUP    = 0x0010
	MOUSEEVENTF_MIDDLEDOWN = 0x0020
	MOUSEEVENTF_MIDDLEUP   = 0x0040
	MOUSEEVENTF_ABSOLUTE   = 0x8000
	MOUSEEVENTF_WHEEL      = 0x0800

	// Keyboard flags
	KEYEVENTF_EXTENDEDKEY = 0x0001
	KEYEVENTF_KEYUP       = 0x0002
	KEYEVENTF_UNICODE     = 0x0004
	KEYEVENTF_SCANCODE    = 0x0008
)

type INPUT struct {
	Type uint32
	Mi   MOUSEINPUT
}

type MOUSEINPUT struct {
	Dx          int32
	Dy          int32
	MouseData   uint32
	DwFlags     uint32
	Time        uint32
	DwExtraInfo uintptr
}

type KEYBDINPUT struct {
	WVk         uint16
	WScan       uint16
	DwFlags     uint32
	Time        uint32
	DwExtraInfo uintptr
}

// Helper to create keyboard input struct
func createKeyInput(vk uint16, flags uint32) INPUT {
	var input INPUT
	input.Type = INPUT_KEYBOARD
	ki := (*KEYBDINPUT)(unsafe.Pointer(&input.Mi))
	ki.WVk = vk
	ki.DwFlags = flags
	return input
}

func createUnicodeInput(r uint16, flags uint32) INPUT {
	var input INPUT
	input.Type = INPUT_KEYBOARD
	ki := (*KEYBDINPUT)(unsafe.Pointer(&input.Mi))
	ki.WVk = 0
	ki.WScan = r
	ki.DwFlags = flags | KEYEVENTF_UNICODE
	return input
}

func sendInput(inputs []INPUT) {
	if len(inputs) == 0 {
		return
	}
	procSendInput.Call(
		uintptr(len(inputs)),
		uintptr(unsafe.Pointer(&inputs[0])),
		uintptr(unsafe.Sizeof(INPUT{})),
	)
}

func moveMouse(x, y int) {
	procSetCursorPos.Call(uintptr(x), uintptr(y))
}

func clickMouse(x, y int, button string) {
	// First move to position
	moveMouse(x, y)

	var inputs []INPUT
	var down, up uint32

	switch button {
	case "left":
		down = MOUSEEVENTF_LEFTDOWN
		up = MOUSEEVENTF_LEFTUP
	case "right":
		down = MOUSEEVENTF_RIGHTDOWN
		up = MOUSEEVENTF_RIGHTUP
	case "middle":
		down = MOUSEEVENTF_MIDDLEDOWN
		up = MOUSEEVENTF_MIDDLEUP
	default:
		return
	}

	inputs = append(inputs, INPUT{
		Type: INPUT_MOUSE,
		Mi: MOUSEINPUT{
			DwFlags: down,
		},
	})
	inputs = append(inputs, INPUT{
		Type: INPUT_MOUSE,
		Mi: MOUSEINPUT{
			DwFlags: up,
		},
	})

	sendInput(inputs)
}

func scrollMouse(delta int) {
	var inputs []INPUT
	inputs = append(inputs, INPUT{
		Type: INPUT_MOUSE,
		Mi: MOUSEINPUT{
			DwFlags:   MOUSEEVENTF_WHEEL,
			MouseData: uint32(delta),
		},
	})
	sendInput(inputs)
}

// simulateText sends unicode characters
func simulateText(text string) {
	// Convert string to UTF-16 to handle Emoji and complex chars correctly
	u16 := utf16.Encode([]rune(text))

	var inputs []INPUT
	for _, c := range u16 {
		// Down
		inputs = append(inputs, createUnicodeInput(c, 0))
		// Up
		inputs = append(inputs, createUnicodeInput(c, KEYEVENTF_KEYUP))
	}
	sendInput(inputs)
}

func simulateKey(key string) {
	vk := mapKeyToVk(key)
	if vk == 0 {
		return
	}

	inputs := []INPUT{
		createKeyInput(vk, 0),               // Down
		createKeyInput(vk, KEYEVENTF_KEYUP), // Up
	}
	sendInput(inputs)
}

// simulateHotkey presses modifier+key combination (e.g., Win+S, Ctrl+V)
func simulateHotkey(modifier, key uint16) {
	inputs := []INPUT{
		createKeyInput(modifier, 0),               // Modifier Down
		createKeyInput(key, 0),                    // Key Down
		createKeyInput(key, KEYEVENTF_KEYUP),      // Key Up
		createKeyInput(modifier, KEYEVENTF_KEYUP), // Modifier Up
	}
	sendInput(inputs)
}

// simulateKeyVk presses a key by virtual key code
func simulateKeyVk(vk uint16) {
	inputs := []INPUT{
		createKeyInput(vk, 0),               // Down
		createKeyInput(vk, KEYEVENTF_KEYUP), // Up
	}
	sendInput(inputs)
}

// Virtual Key Codes
const (
	VK_BACK    = 0x08
	VK_TAB     = 0x09
	VK_RETURN  = 0x0D
	VK_SHIFT   = 0x10
	VK_CONTROL = 0x11
	VK_MENU    = 0x12 // Alt
	VK_ESCAPE  = 0x1B
	VK_SPACE   = 0x20
	VK_LEFT    = 0x25
	VK_UP      = 0x26
	VK_RIGHT   = 0x27
	VK_DOWN    = 0x28
	VK_DELETE  = 0x2E
	VK_LWIN    = 0x5B // Left Windows key
	VK_S       = 0x53 // S key
	VK_V       = 0x56 // V key
)

func mapKeyToVk(key string) uint16 {
	// Basic mapping (handle both cases since client may send lowercase)
	switch key {
	case "Enter", "enter":
		return VK_RETURN
	case "Backspace", "backspace":
		return VK_BACK
	case "Space", "space", " ":
		return VK_SPACE
	case "Escape", "escape", "esc":
		return VK_ESCAPE
	case "Tab", "tab":
		return VK_TAB
	case "Delete", "delete":
		return VK_DELETE
	case "ArrowUp", "up", "Up":
		return VK_UP
	case "ArrowDown", "down", "Down":
		return VK_DOWN
	case "ArrowLeft", "left", "Left":
		return VK_LEFT
	case "ArrowRight", "right", "Right":
		return VK_RIGHT
	}

	// Single characters
	if len(key) == 1 {
		b := key[0]
		if b >= 'a' && b <= 'z' {
			return uint16(b - 32) // To Upper
		}
		if b >= 'A' && b <= 'Z' {
			return uint16(b)
		}
		if b >= '0' && b <= '9' {
			return uint16(b)
		}
	}
	return 0
}

// Safer setClipboard with local DLL loading to avoid init order issues
func setClipboard(text string) error {
	user32 := syscall.NewLazyDLL("user32.dll")
	kernel32 := syscall.NewLazyDLL("kernel32.dll")

	procOpenClipboard := user32.NewProc("OpenClipboard")
	procCloseClipboard := user32.NewProc("CloseClipboard")
	procEmptyClipboard := user32.NewProc("EmptyClipboard")
	procSetClipboardData := user32.NewProc("SetClipboardData")
	procGlobalAlloc := kernel32.NewProc("GlobalAlloc")
	procGlobalLock := kernel32.NewProc("GlobalLock")
	procGlobalUnlock := kernel32.NewProc("GlobalUnlock")

	const CF_UNICODETEXT = 13
	const GMEM_MOVEABLE = 0x0002

	// Convert to UTF-16
	u16 := utf16.Encode([]rune(text + "\x00"))
	size := len(u16) * 2

	// GlobalAlloc
	hmem, _, err := procGlobalAlloc.Call(GMEM_MOVEABLE, uintptr(size))
	if hmem == 0 {
		return fmt.Errorf("GlobalAlloc failed: %v", err)
	}

	// GlobalLock
	ptr, _, err := procGlobalLock.Call(hmem)
	if ptr == 0 {
		return fmt.Errorf("GlobalLock failed: %v", err)
	}

	// Copy data
	dst := unsafe.Slice((*uint16)(unsafe.Pointer(ptr)), len(u16))
	copy(dst, u16)

	// GlobalUnlock
	procGlobalUnlock.Call(hmem)

	// OpenClipboard
	// Retries for OpenClipboard as it might be locked by another app
	var openRet uintptr
	for i := 0; i < 5; i++ {
		openRet, _, _ = procOpenClipboard.Call(0)
		if openRet != 0 {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if openRet == 0 {
		return fmt.Errorf("OpenClipboard failed")
	}
	defer procCloseClipboard.Call()

	// EmptyClipboard
	procEmptyClipboard.Call()

	// SetClipboardData
	procSetClipboardData.Call(CF_UNICODETEXT, hmem)

	return nil
}
