# Ghost Shell Server v2.1 - Background Window Capture
# Supports capturing windows even when not in foreground

# CRITICAL: Set DPI awareness BEFORE any other imports
# This must be the first thing that runs to fix multi-monitor coordinate issues
import ctypes
import asyncio
try:
    # Try Per-Monitor DPI Aware V2 (Windows 10 1703+)
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except:
    try:
        # Fallback to System DPI Aware
        ctypes.windll.user32.SetProcessDPIAware()
    except:
        pass

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pyautogui
import pygetwindow as gw
import io
import asyncio
import base64
import time
from typing import Optional
import subprocess
from PIL import Image

# Try to import win32 for background capture
try:
    import win32gui
    import win32ui
    import win32con
    BACKGROUND_CAPTURE_AVAILABLE = True
except ImportError:
    BACKGROUND_CAPTURE_AVAILABLE = False
    print("WARNING: pywin32 not installed. Background capture disabled.")

app = FastAPI(title="Ghost Shell Server v2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Target window keywords (order of priority - Antigravity Agent Manager first!)
TARGET_KEYWORDS = ["Agent Manager", "Antigravity", "Kiro", "Code", "Cursor"]

# Capture mode: 'full' = entire window, 'agent_manager' = left sidebar only
CAPTURE_MODE = "full"  # Changed to full as user requested
AGENT_MANAGER_WIDTH = 220

# CRITICAL: Set to False to prevent UI lockup!
ACTIVATE_WINDOW = False  # Don't steal focus during capture


# Locked window title (None = auto-detect by keywords)
LOCKED_WINDOW_TITLE = None
# 当前正在显示的窗口标题（用于点击时定位）
CURRENT_DISPLAY_WINDOW = None
# Flag: activate window once on next capture (set True when user switches window)
PENDING_ACTIVATION = False
# Frame rate control (adjustable via API)
FRAME_DELAY = 0.2  # Default 5 FPS (1/5 = 0.2s)
# Original window state for restore
ORIGINAL_WINDOW_STATE = None

class InteractionRequest(BaseModel):
    action: str  # click, type, key
    x: int = 0
    y: int = 0
    text: str = ""
    key: str = ""

class LockRequest(BaseModel):
    title: str

def get_all_windows():
    """Get all visible windows."""
    all_windows = gw.getAllWindows()
    return [w for w in all_windows if w.title and w.visible and w.width > 100]

def get_foreground_window():
    """Get the currently active foreground window."""
    if not BACKGROUND_CAPTURE_AVAILABLE:
        return None
    try:
        hwnd = win32gui.GetForegroundWindow()
        if hwnd:
            title = win32gui.GetWindowText(hwnd)
            if title:
                # Find matching pygetwindow object
                windows = gw.getWindowsWithTitle(title)
                if windows:
                    return windows[0]
    except:
        pass
    return None

def get_foreground_hwnd_and_rect():
    """Get foreground window hwnd and rect directly (v2_simplified approach)."""
    if not BACKGROUND_CAPTURE_AVAILABLE:
        return None, None
    try:
        hwnd = win32gui.GetForegroundWindow()
        if hwnd and win32gui.IsWindow(hwnd):
            rect = win32gui.GetWindowRect(hwnd)
            if rect[2] > rect[0] and rect[3] > rect[1]:  # Valid rect
                return hwnd, rect
    except:
        pass
    return None, None

def activate_window(win):
    """Activate a window to bring it to foreground."""
    if not win:
        return False
    try:
        # Method 1: Use pygetwindow
        if hasattr(win, 'activate'):
            win.activate()
            time.sleep(0.1)  # Give time to activate
            return True
        
        # Method 2: Use win32gui if available
        if BACKGROUND_CAPTURE_AVAILABLE:
            try:
                hwnd = win32gui.FindWindow(None, win.title)
                if hwnd:
                    win32gui.SetForegroundWindow(hwnd)
                    time.sleep(0.1)
                    return True
            except:
                pass
        
        return False
    except Exception as e:
        print(f"[ERROR] Failed to activate window: {e}")
        return False

def simple_capture(hwnd=None, rect=None):
    """Simple capture using ImageGrab - works for all monitors (v2_simplified approach)."""
    from PIL import ImageGrab
    try:
        if rect is None and hwnd:
            rect = win32gui.GetWindowRect(hwnd)
        if rect and rect[2] > rect[0] and rect[3] > rect[1]:
            return ImageGrab.grab(bbox=rect, all_screens=True)
    except Exception as e:
        print(f"[CAPTURE] simple_capture error: {e}")
    return None

def get_target_window():
    """Find target window - locked or foreground (auto-follow).
    
    锁定模式: 只返回锁定的窗口
    自动模式: 跟随当前前台窗口（不限于预设关键词）
    """
    global LOCKED_WINDOW_TITLE
    
    # If locked to a specific window, find it first (exact match)
    if LOCKED_WINDOW_TITLE:
        all_windows = get_all_windows()
        for win in all_windows:
            if win.title == LOCKED_WINDOW_TITLE:
                return win
        # Also try partial match if exact fails
        for win in all_windows:
            if LOCKED_WINDOW_TITLE in win.title:
                return win
        # Locked window not found
        return None
    
    # Auto-detect mode: follow the foreground window (any window!)
    foreground = get_foreground_window()
    if foreground and foreground.title:
        # Only skip Ghost Shell itself and system windows
        skip_titles = ["Ghost Shell", "任务栏", "Program Manager"]
        if foreground.title and not any(skip == foreground.title or skip in foreground.title for skip in skip_titles):
            return foreground
    
    # Fallback: No foreground window or it's a system window
    # Try keyword search as last resort
    for keyword in TARGET_KEYWORDS:
        windows = gw.getWindowsWithTitle(keyword)
        if windows:
            return windows[0]
    return None

def activate_window(win):
    """Activate and restore window if minimized (only if ACTIVATE_WINDOW is True)."""
    if not ACTIVATE_WINDOW:
        return True  # Skip activation to prevent UI lockup
    try:
        if win.isMinimized:
            win.restore()
        win.activate()
        import time
        time.sleep(0.15)
        return True
    except Exception as e:
        print(f"Activation warning: {e}")
        return False

def capture_window_background(hwnd, width, height):
    """
    Capture window content even when covered by other windows.
    Uses multiple fallback methods for maximum compatibility.
    """
    if not BACKGROUND_CAPTURE_AVAILABLE:
        return None
    
    try:
        # Get actual window rect
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        actual_width = right - left
        actual_height = bottom - top
        
        if actual_width <= 0 or actual_height <= 0:
            return None
        
        # Create device contexts
        hwndDC = win32gui.GetWindowDC(hwnd)
        mfcDC = win32ui.CreateDCFromHandle(hwndDC)
        saveDC = mfcDC.CreateCompatibleDC()
        
        # Create bitmap
        saveBitMap = win32ui.CreateBitmap()
        saveBitMap.CreateCompatibleBitmap(mfcDC, actual_width, actual_height)
        saveDC.SelectObject(saveBitMap)
        
        # Try Method 1: PrintWindow with PW_RENDERFULLCONTENT (best for modern apps)
        result = False
        try:
            result = win32gui.PrintWindow(hwnd, saveDC.GetSafeHdc(), 2)  # PW_RENDERFULLCONTENT = 2
        except:
            pass
        
        # Method 2: If failed, try WM_PRINT message
        if not result:
            try:
                WM_PRINT = 0x0317
                PRF_CLIENT = 0x04
                PRF_NONCLIENT = 0x02
                PRF_CHILDREN = 0x10
                PRF_OWNED = 0x20
                flags = PRF_CLIENT | PRF_NONCLIENT | PRF_CHILDREN | PRF_OWNED
                win32gui.SendMessage(hwnd, WM_PRINT, saveDC.GetSafeHdc(), flags)
                result = True
            except:
                pass
        
        # Method 3: If still failed, try regular PrintWindow without flag
        if not result:
            try:
                result = win32gui.PrintWindow(hwnd, saveDC.GetSafeHdc(), 0)
            except:
                pass
        
        # Method 4: BitBlt from screen DC (works better for multi-monitor)
        if not result:
            try:
                # Get screen DC for the window's monitor
                screenDC = win32gui.GetDC(0)  # 0 = entire virtual screen
                screenMfcDC = win32ui.CreateDCFromHandle(screenDC)
                # BitBlt from screen coordinates
                saveDC.BitBlt((0, 0), (actual_width, actual_height), screenMfcDC, (left, top), win32con.SRCCOPY)
                screenMfcDC.DeleteDC()
                win32gui.ReleaseDC(0, screenDC)
                result = True
            except Exception as e:
                print(f"BitBlt capture error: {e}")
        
        # Convert to PIL Image
        bmpinfo = saveBitMap.GetInfo()
        bmpstr = saveBitMap.GetBitmapBits(True)
        img = Image.frombuffer('RGB', (bmpinfo['bmWidth'], bmpinfo['bmHeight']), bmpstr, 'raw', 'BGRX', 0, 1)
        
        # Check if image is mostly black (capture failed) - use higher threshold
        import numpy as np
        arr = np.array(img)
        mean_brightness = np.mean(arr)
        if mean_brightness < 10:  # Nearly black = capture failed
            # 静默处理，不刷屏日志
            img = None
        
        # Cleanup
        win32gui.DeleteObject(saveBitMap.GetHandle())
        saveDC.DeleteDC()
        mfcDC.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwndDC)
        
        return img
    except Exception as e:
        print(f"Background capture error: {e}")
        return None

# Path to HTML client
import os
CLIENT_HTML_PATH = os.path.join(os.path.dirname(__file__), "ghost_client.html")

from fastapi.responses import Response

@app.get("/", response_class=HTMLResponse)
def root():
    """Serve the HTML client - access from any device via http://IP:8000"""
    try:
        with open(CLIENT_HTML_PATH, "r", encoding="utf-8") as f:
            content = f.read()
            # Return with no-cache headers to always get latest version
            return Response(
                content=content,
                media_type="text/html",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0"
                }
            )
    except:
        return """<html><body><h1>Ghost Shell</h1><p>Client HTML not found. 
        Place ghost_client.html in the same directory as ghost_server.py</p></body></html>"""

@app.get("/api")
def api_info():
    return {"status": "Ghost Shell 服务器运行中", "version": "2.1", "endpoints": ["/capture", "/stream", "/interact", "/status", "/windows", "/lock"]}

@app.get("/windows")
def list_windows():
    """列出所有可用窗口 / List all available windows."""
    windows = get_all_windows()
    current_win = get_target_window()
    return {
        "windows": [{"title": w.title, "size": f"{w.width}x{w.height}"} for w in windows],
        "locked": LOCKED_WINDOW_TITLE,
        "current": current_win.title if current_win else None,
        "auto_follow": LOCKED_WINDOW_TITLE is None  # True = 自动跟随模式
    }

@app.post("/lock")
def lock_window(req: LockRequest):
    """锁定到指定窗口 / Lock to a specific window."""
    global LOCKED_WINDOW_TITLE, PENDING_ACTIVATION
    if req.title == "":
        LOCKED_WINDOW_TITLE = None
        PENDING_ACTIVATION = False
        print(f"[LOCK] Unlocked, switching to auto-follow")
        return {"status": "unlocked", "message": "已解锁，恢复自动跟随", "auto_follow": True}
    else:
        LOCKED_WINDOW_TITLE = req.title
        PENDING_ACTIVATION = True
        print(f"[LOCK] Locked to: '{req.title}'")
        win = get_target_window()
        return {
            "status": "locked", 
            "title": req.title, 
            "message": f"已锁定: {req.title[:30]}",
            "auto_follow": False
        }

@app.post("/lock_current")
def lock_current_window():
    """一键锁定当前正在自动跟随显示的窗口 / Lock the currently auto-followed window."""
    global LOCKED_WINDOW_TITLE, PENDING_ACTIVATION
    
    # 如果已经锁定，返回当前锁定状态
    if LOCKED_WINDOW_TITLE:
        return {
            "status": "already_locked",
            "title": LOCKED_WINDOW_TITLE,
            "message": f"已锁定: {LOCKED_WINDOW_TITLE[:30]}",
            "auto_follow": False
        }
    
    # 获取当前正在自动跟随显示的窗口（不是前台窗口，而是 Ghost Shell 正在显示的）
    current_win = get_target_window()
    if current_win and current_win.title:
        title = current_win.title
        LOCKED_WINDOW_TITLE = title
        PENDING_ACTIVATION = False
        print(f"[LOCK_CURRENT] Locked to current target: '{title}'")
        return {
            "status": "locked",
            "title": title,
            "message": f"已锁定: {title[:30]}",
            "auto_follow": False
        }
    
    return {"status": "error", "message": "没有找到可锁定的窗口"}

class FpsRequest(BaseModel):
    fps: int

@app.post("/set_fps")
def set_fps(req: FpsRequest):
    """Set frame rate for WebSocket stream."""
    global FRAME_DELAY
    fps = max(1, min(60, req.fps))  # Allow up to 60 FPS
    FRAME_DELAY = 1.0 / fps
    print(f"[FPS] Set to {fps} FPS (delay: {FRAME_DELAY:.3f}s)")
    return {"fps": fps, "delay": FRAME_DELAY}

@app.get("/capture")
def capture():
    """Capture screenshot - works even when window is in background."""
    win = get_target_window()
    if not win:
        raise HTTPException(status_code=404, detail="未找到目标窗口")
    
    try:
        # Try background capture first (works even when window is not in foreground)
        if BACKGROUND_CAPTURE_AVAILABLE:
            hwnd = win32gui.FindWindow(None, win.title)
            if hwnd:
                screenshot = capture_window_background(hwnd, win.width, win.height)
                if screenshot:
                    img_byte_arr = io.BytesIO()
                    screenshot.save(img_byte_arr, format='JPEG', quality=85)
                    img_byte_arr.seek(0)
                    return StreamingResponse(img_byte_arr, media_type="image/jpeg")
        
        # Fallback to pyautogui (requires window to be visible)
        activate_window(win)
        if CAPTURE_MODE == "agent_manager":
            region = (win.left, win.top, AGENT_MANAGER_WIDTH, win.height)
        else:
            region = (win.left, win.top, win.width, win.height)
        
        screenshot = pyautogui.screenshot(region=region)
        img_byte_arr = io.BytesIO()
        screenshot.save(img_byte_arr, format='JPEG', quality=85)
        img_byte_arr.seek(0)
        return StreamingResponse(img_byte_arr, media_type="image/jpeg")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/stream")
async def stream(websocket: WebSocket):
    """WebSocket stream - uses v2_simplified approach for reliable multi-monitor capture."""
    import time
    await websocket.accept()
    try:
        while True:
            frame_start = time.perf_counter()  # Track frame timing
            screenshot = None
            width, height = 800, 600
            window_title = "未知"
            
            # Check if we have a locked window
            if LOCKED_WINDOW_TITLE:
                # Locked mode: use the locked window
                win = get_target_window()
                if win:
                    window_title = win.title
                    hwnd = getattr(win, '_hWnd', None)
                    if not hwnd and BACKGROUND_CAPTURE_AVAILABLE:
                        hwnd = win32gui.FindWindow(None, win.title)
                    
                    # Handle pending activation (only once after lock)
                    global PENDING_ACTIVATION
                    if PENDING_ACTIVATION:
                        print(f"[STREAM] Activating locked window once: '{win.title}'")
                        try:
                            if win.isMinimized:
                                win.restore()
                            win.activate()
                            import time
                            time.sleep(0.15)
                        except:
                            pass
                        PENDING_ACTIVATION = False
                    
                    # Try background capture first
                    if hwnd and BACKGROUND_CAPTURE_AVAILABLE:
                        screenshot = capture_window_background(hwnd, win.width, win.height)
                    
                    # Fallback to simple_capture
                    if screenshot is None and hwnd:
                        screenshot = simple_capture(hwnd=hwnd)
                    
                    if screenshot:
                        width, height = screenshot.size
            else:
                # Auto-detect mode: use v2_simplified direct approach
                hwnd, rect = get_foreground_hwnd_and_rect()
                if hwnd and rect:
                    window_title = win32gui.GetWindowText(hwnd)
                    # Skip Ghost Shell itself and system windows
                    skip_titles = ["Ghost Shell", "任务栏", "Program Manager"]
                    if window_title and not any(skip in window_title for skip in skip_titles):
                        # Optimize: Try fast BitBlt capture first if available (much faster than ImageGrab)
                        if BACKGROUND_CAPTURE_AVAILABLE:
                            try:
                                # Calculate width/height from rect
                                w = rect[2] - rect[0]
                                h = rect[3] - rect[1]
                                screenshot = capture_window_background(hwnd, w, h)
                            except:
                                screenshot = None
                        
                        # Fallback to simple_capture (ImageGrab) if BitBlt fails or not available
                        if screenshot is None:
                            screenshot = simple_capture(hwnd=hwnd, rect=rect)

                        if screenshot:
                            width, height = screenshot.size
                
                # 不再回退到关键词搜索，只跟随实际的前台窗口
                # 如果没有截图（比如前台是 Ghost Shell 浏览器本身），保持上一帧
            
            if screenshot:
                global CURRENT_DISPLAY_WINDOW
                width, height = screenshot.size
                
                # 记住当前正在显示的窗口（用于点击时定位）
                CURRENT_DISPLAY_WINDOW = window_title
                
                # 直接发送原始图像，不缩放
                img_byte_arr = io.BytesIO()
                screenshot.save(img_byte_arr, format='JPEG', quality=85)
                img_byte_arr.seek(0)
                img_base64 = base64.b64encode(img_byte_arr.read()).decode()
                await websocket.send_json({
                    "type": "frame",
                    "data": img_base64,
                    "width": width,
                    "height": height,
                    "window": window_title[:50] if window_title else "未知"
                })
            else:
                await websocket.send_json({"type": "error", "message": "未找到窗口或截图失败"})
            
            # Smart delay: subtract actual processing time from target delay
            elapsed = time.perf_counter() - frame_start
            sleep_time = max(0, FRAME_DELAY - elapsed)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
    except Exception as e:
        print(f"WebSocket closed: {e}")

@app.post("/interact")
async def interact(req: InteractionRequest):
    """Send interaction to target window."""
    global ORIGINAL_WINDOW_STATE, CURRENT_DISPLAY_WINDOW
    
    # 使用当前正在显示的窗口，而不是当前前台窗口
    win = None
    if CURRENT_DISPLAY_WINDOW:
        # 根据标题找到窗口
        windows = gw.getWindowsWithTitle(CURRENT_DISPLAY_WINDOW)
        if windows:
            win = windows[0]
    
    # 备用：如果没有记录的窗口，使用 get_target_window
    if not win:
        win = get_target_window()
    
    if not win:
        raise HTTPException(status_code=404, detail="Target window not found")

    # 调试：打印窗口位置和点击坐标
    print(f"[CLICK] Window: '{win.title[:30]}' at ({win.left}, {win.top}) size {win.width}x{win.height}")
    print(f"[CLICK] Request coords: ({req.x}, {req.y})")
    
    abs_x = win.left + req.x
    abs_y = win.top + req.y
    print(f"[CLICK] Absolute: ({abs_x}, {abs_y})")


    try:
        if req.action == "click":
            pyautogui.click(abs_x, abs_y)
            return {"status": "clicked", "pos": (abs_x, abs_y), "window": win.title[:30]}
        elif req.action == "type":
            import pyperclip
            # If x=0 and y=0, don't click - just type to currently focused field
            if req.x == 0 and req.y == 0:
                # Use clipboard paste for reliable text input (supports Unicode/Chinese)
                pyperclip.copy(req.text)
                pyautogui.hotkey('ctrl', 'v')
                print(f"[TYPE] Pasted to active field: '{req.text[:20]}...'")
            else:
                # Click to position first, then type
                pyautogui.click(abs_x, abs_y)
                import time
                time.sleep(0.1)  # Wait for focus
                pyperclip.copy(req.text)
                pyautogui.hotkey('ctrl', 'v')
            return {"status": "typed", "text": req.text}
        elif req.action == "key":
            pyautogui.press(req.key)
            return {"status": "key_pressed", "key": req.key}
        elif req.action == "hotkey":
            keys = req.key.split("+")
            pyautogui.hotkey(*keys)
            return {"status": "hotkey_pressed", "keys": keys}
        elif req.action == "scroll":
            # Scroll at position, amount in req.text (positive=up, negative=down)
            pyautogui.moveTo(abs_x, abs_y)
            amount = int(req.text) if req.text else 3
            pyautogui.scroll(amount)
            return {"status": "scrolled", "amount": amount, "pos": (abs_x, abs_y)}
        elif req.action == "scroll_up":
            pyautogui.moveTo(abs_x, abs_y)
            pyautogui.scroll(3)
            return {"status": "scrolled_up", "pos": (abs_x, abs_y)}
        elif req.action == "scroll_down":
            pyautogui.moveTo(abs_x, abs_y)
            pyautogui.scroll(-3)
            return {"status": "scrolled_down", "pos": (abs_x, abs_y)}
        elif req.action == "open_app":
            # press Win+S, type app name, press Enter
            pyautogui.hotkey('win', 's')
            import time
            import pyperclip
            time.sleep(1.0)  # Wait longer for search bar
            
            # Use clipboard for reliable input (supports Unicode/faster)
            pyperclip.copy(req.text)
            pyautogui.hotkey('ctrl', 'v')
            
            time.sleep(0.5)  # Wait for search results
            pyautogui.press('enter')
            return {"status": "opening_app", "app": req.text}
        elif req.action == "drag":
            # Drag from current position by delta specified in text as "dx,dy"
            parts = req.text.split(",")
            if len(parts) == 2:
                dx, dy = int(parts[0]), int(parts[1])
                pyautogui.moveTo(abs_x, abs_y)
                pyautogui.drag(dx, dy, duration=0.3)
                return {"status": "dragged", "from": (abs_x, abs_y), "delta": (dx, dy)}
            else:
                return {"status": "error", "message": "drag requires text='dx,dy'"}
        elif req.action == "right_click":
            # Right click (triggered by long press on mobile)
            pyautogui.click(abs_x, abs_y, button='right')
            return {"status": "right_clicked", "pos": (abs_x, abs_y)}
        elif req.action == "double_click":
            # Double click
            pyautogui.doubleClick(abs_x, abs_y)
            return {"status": "double_clicked", "pos": (abs_x, abs_y)}
        elif req.action == "resize_window":
            # Resize the locked window to specified dimensions
            if not LOCKED_WINDOW_TITLE:
                return {"status": "error", "message": "请先锁定一个窗口"}
            
            win = get_target_window()
            if not win:
                return {"status": "error", "message": "未找到锁定的窗口"}
            
            try:
                parts = req.text.split(",")
                width, height = int(parts[0]), int(parts[1])
                win.resizeTo(width, height)
                print(f"[RESIZE] Window resized to {width}x{height}")
                return {"status": "resized", "size": (width, height)}
            except Exception as e:
                return {"status": "error", "message": str(e)}
        elif req.action == "adapt_phone":
            # Resize window based on its current monitor's orientation
            # Horizontal: width = 1/3, height = full
            # Vertical: width = full, height = 1/3
            
            win = get_target_window()
            if not win:
                return {"status": "error", "message": "未找到可操作的窗口"}
            
            try:
                # Save original state for restore
                ORIGINAL_WINDOW_STATE = {
                    'left': win.left,
                    'top': win.top,
                    'width': win.width,
                    'height': win.height
                }
                
                # Get the monitor info for the window's current position
                # Use window's center point to determine which monitor it's on
                win_center_x = win.left + win.width // 2
                win_center_y = win.top + win.height // 2
                
                # Get monitor info using win32api if available
                try:
                    from win32api import GetMonitorInfo, MonitorFromPoint
                    from win32con import MONITOR_DEFAULTTONEAREST
                    
                    # Get monitor for window's center point
                    hmonitor = MonitorFromPoint((win_center_x, win_center_y), MONITOR_DEFAULTTONEAREST)
                    monitor_info = GetMonitorInfo(hmonitor)
                    work_area = monitor_info['Work']  # (left, top, right, bottom) - excludes taskbar
                    
                    mon_left, mon_top, mon_right, mon_bottom = work_area
                    mon_width = mon_right - mon_left
                    mon_height = mon_bottom - mon_top
                    
                except ImportError:
                    # Fallback: use pyautogui (only works for primary monitor)
                    mon_left, mon_top = 0, 0
                    mon_width, mon_height = pyautogui.size()
                    mon_height -= 40  # Rough taskbar offset
                
                # Determine orientation: horizontal if width > height
                is_horizontal = mon_width > mon_height
                
                if is_horizontal:
                    # Horizontal screen: 1/3 width, full height
                    width = mon_width // 3
                    height = mon_height
                    # Position on right side of this monitor
                    new_left = mon_left + mon_width - width
                    new_top = mon_top
                else:
                    # Vertical screen: width = height/3, height = width
                    # This creates a phone-like aspect ratio on vertical monitor
                    width = mon_height // 3
                    height = mon_width
                    # Position at bottom-right of this monitor
                    new_left = mon_left + mon_width - width
                    new_top = mon_top + mon_height - height
                
                win.moveTo(new_left, new_top)
                win.resizeTo(width, height)
                
                orientation = "横屏" if is_horizontal else "竖屏"
                print(f"[ADAPT] {orientation} adapted: {width}x{height} at ({new_left},{new_top})")
                return {"status": "adapted", "size": (width, height), "orientation": orientation}
            except Exception as e:
                return {"status": "error", "message": str(e)}
        elif req.action == "restore_window":
            # Restore window to original size
            
            if not ORIGINAL_WINDOW_STATE:
                return {"status": "error", "message": "没有保存的窗口状态"}
            
            win = get_target_window()
            if not win:
                return {"status": "error", "message": "未找到可操作的窗口"}
            
            try:
                orig = ORIGINAL_WINDOW_STATE
                win.moveTo(orig['left'], orig['top'])
                win.resizeTo(orig['width'], orig['height'])
                print(f"[RESTORE] Window restored to: {orig['width']}x{orig['height']}")
                ORIGINAL_WINDOW_STATE = None
                return {"status": "restored", "size": (orig['width'], orig['height'])}
            except Exception as e:
                return {"status": "error", "message": str(e)}
        elif req.action == "close_window":
            # Close the target window using Alt+F4
            win = get_target_window()
            if not win:
                return {"status": "error", "message": "未找到可关闭的窗口，请先锁定一个窗口"}
            
            win_title = win.title
            print(f"[CLOSE] Attempting to close window: '{win_title}'")
            
            try:
                # CRITICAL: Activate window first to ensure Alt+F4 targets it
                print(f"[CLOSE] Activating window...")
                success = activate_window(win)
                if not success:
                    print(f"[CLOSE] Warning: Failed to activate window, trying anyway")
                
                # Give extra time for activation
                await asyncio.sleep(0.2)
                
                # SAFETY CHECK: Ensure target is actually foreground
                try:
                    active_win = gw.getActiveWindow()
                    if active_win and active_win.title != win_title:
                        # Try verifying with handle if titles change slightly
                        # But mostly just abort if dangerous
                        print(f"[CLOSE ABORTED] Active window is '{active_win.title}', expected '{win_title}'")
                        return {"status": "error", "message": f"安全拦截：无法激活目标窗口，当前焦点在 '{active_win.title}'"}
                except:
                    pass

                # Send Alt+F4 to close the window
                print(f"[CLOSE] Sending Alt+F4...")
                pyautogui.hotkey('alt', 'f4')
                
                # Wait a bit to let window close
                await asyncio.sleep(0.3)
                
                print(f"[CLOSE] Successfully sent close command to '{win_title}'")
                return {"status": "success", "message": f"已关闭窗口: {win_title}", "title": win_title}
            except Exception as e:
                error_msg = f"关闭窗口失败: {str(e)}"
                print(f"[CLOSE ERROR] {error_msg}")
                return {"status": "error", "message": error_msg}
        else:
            raise HTTPException(status_code=400, detail="Unknown action")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/status")
def status():
    """Get server and window status."""
    win = get_target_window()
    sessions = []
    try:
        result = subprocess.run(
            ["C:\\Windows\\System32\\query.exe", "session"],
            capture_output=True, text=True, timeout=5
        )
        sessions = result.stdout.strip().split("\n") if result.returncode == 0 else []
    except:
        pass
    
    return {
        "server": "running",
        "version": "2.0",
        "window_found": bool(win),
        "window_title": win.title if win else None,
        "window_box": {"left": win.left, "top": win.top, "width": win.width, "height": win.height} if win else None,
        "sessions": sessions
    }

if __name__ == "__main__":
    import uvicorn
    import sys
    
    # Check for SSL certificate
    cert_dir = os.path.dirname(__file__)
    cert_file = os.path.join(cert_dir, "server.pem")
    
    if os.path.exists(cert_file) and "--https" in sys.argv:
        print("Ghost Shell Server v2.1 starting on HTTPS port 8443...")
        print("Access via: https://192.168.31.141:8443")
        uvicorn.run(app, host="0.0.0.0", port=8443, ssl_certfile=cert_file, ssl_keyfile=cert_file)
    else:
        print("Ghost Shell Server v2.1 starting on HTTP port 8000...")
        print("Access via: http://192.168.31.141:8000")
        print("For HTTPS (mobile voice): python ghost_server.py --https")
        uvicorn.run(app, host="0.0.0.0", port=8000)
