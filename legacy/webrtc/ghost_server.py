# Ghost Shell Server v2.2 - Multi-mode Screen Capture (Restored)
# Supports: DXcam (fastest), mss (cross-platform), legacy (pywin32)

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

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pyautogui
# Optimize input speed for real-time control (default is 0.1s which is too slow)
pyautogui.PAUSE = 0.005
pyautogui.MINIMUM_DURATION = 0
import pygetwindow as gw
import io
import asyncio
import base64
import time
from typing import Optional
import subprocess
from PIL import Image
# [OPTIMIZATION] Pre-import ImageGrab at module level to avoid per-frame import overhead
from PIL import ImageGrab
import os

# Optional: numpy and cv2 for DXcam mode
try:
    import numpy as np
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    np = None
    cv2 = None

# ==================== Capture Library Detection ====================
# Try to import DXcam (fastest, Windows-only, GPU-accelerated)
DXCAM_AVAILABLE = False
dxcam_camera = None
try:
    import dxcam
    DXCAM_AVAILABLE = True
    print("✅ DXcam available (GPU-accelerated capture)")
except ImportError:
    print("⚠️ DXcam not installed. Install with: pip install dxcam")

# Try to import mss (fast, cross-platform)
MSS_AVAILABLE = False
mss_sct = None
try:
    import mss
    MSS_AVAILABLE = True
    mss_sct = mss.mss()
    print("✅ mss available (cross-platform capture)")
except ImportError:
    print("⚠️ mss not installed. Install with: pip install mss")

# Try to import win32 for background capture (legacy)
try:
    import win32gui
    import win32ui
    import win32con
    import win32api  # Needed for MAKELONG
    BACKGROUND_CAPTURE_AVAILABLE = True
except ImportError:
    BACKGROUND_CAPTURE_AVAILABLE = False
    print("WARNING: pywin32 not installed. Background capture disabled.")

# Try to import WGC (Windows Graphics Capture) - can capture covered GPU-accelerated windows
WGC_CAPTURE_AVAILABLE = False
try:
    from wgc_capture import capture_window_wgc, WGC_AVAILABLE
    if WGC_AVAILABLE:
        WGC_CAPTURE_AVAILABLE = True
        print("✅ WGC capture available (supports covered GPU windows)")
except ImportError:
    pass

# ==================== Capture Mode Configuration ====================
# Available modes: 'dxcam' (fastest), 'mss' (cross-platform), 'legacy' (pywin32)
# ==================== Capture Mode Configuration ====================
# Available modes: 'dxcam' (fastest), 'mss' (cross-platform), 'legacy' (pywin32)
# [PERFORMANCE] Use DXcam when available, fallback to mss
CAPTURE_ENGINE = "auto"  # 'auto' = use best available (DXcam > mss > legacy)

import logging
logging.basicConfig(filename='ghost_crash.log', level=logging.ERROR, 
                    format='%(asctime)s %(levelname)s: %(message)s')

# ==================== DXcam Lifecycle Management ====================
def start_dxcam():
    global dxcam_camera
    if DXCAM_AVAILABLE and CV2_AVAILABLE:
        try:
            if dxcam_camera is None:
                dxcam_camera = dxcam.create(output_idx=0, output_color="BGR")
            
            if not dxcam_camera.is_capturing:
                # Start background capture thread (120 FPS target for WebRTC)
                dxcam_camera.start(target_fps=120, video_mode=True)
                print("✅ DXcam streaming started at 120 FPS (Background Thread)")
        except Exception as e:
            print(f"⚠️ Failed to start DXcam streaming: {e}")

def stop_dxcam():
    global dxcam_camera
    if dxcam_camera and dxcam_camera.is_capturing:
        try:
            dxcam_camera.stop()
            print("⏹️ DXcam streaming stopped")
        except Exception as e:
            print(f"⚠️ Failed to stop DXcam: {e}")

app = FastAPI(title="Ghost Shell Server v2.2")

@app.on_event("startup")
async def startup_event():
    # Auto-start DXcam if available
    start_dxcam()

@app.on_event("shutdown")
async def shutdown_event():
    stop_dxcam()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# [OPTIMIZATION] GZip compression for slow networks (frp/public)
# Reduces HTML transfer from 107KB to ~25KB
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=500)

# Mount speech modules
from fastapi.staticfiles import StaticFiles
speech_dir = os.path.join(os.path.dirname(__file__), "speech")
if os.path.exists(speech_dir):
    try:
        app.mount("/speech", StaticFiles(directory=speech_dir, html=True), name="speech")
        print(f"✅ Mounted Speech Modules from: {speech_dir}")
    except Exception as e:
        print(f"⚠️ Failed to mount speech modules: {e}")
else:
    print(f"⚠️ Speech modules dir not found at: {speech_dir}")

# Target window keywords (order of priority - Antigravity Agent Manager first!)
TARGET_KEYWORDS = ["Agent Manager", "Antigravity", "Kiro", "Code", "Cursor"]

# Capture mode: 'full' = entire window, 'agent_manager' = left sidebar only
CAPTURE_MODE = "full"  # Changed to full as user requested
AGENT_MANAGER_WIDTH = 220

# CRITICAL: Set to False to prevent UI lockup!
ACTIVATE_WINDOW = False  # Don't steal focus during capture


# Locked window title (None = auto-detect by keywords)
LOCKED_WINDOW_TITLE = None
MANUAL_LOCK_ACTIVE = False  # True = Hard Lock (User selected dropdown/button), False = Soft Lock (Auto-click)
# 当前正在显示的窗口标题（用于点击时定位）
CURRENT_DISPLAY_WINDOW = None
# 上一个有效窗口（用于同机测试时防止窗口切换）
LAST_VALID_WINDOW = None
# 窗口切换时间戳（用于检测快速切换）
WINDOW_CHANGE_TIME = 0
# Flag: activate window once on next capture (set True when user switches window)
PENDING_ACTIVATION = False
# Frame rate control - set high, let hardware be the limit
FRAME_DELAY = 0.008  # Target 120+ FPS, actual FPS depends on hardware
# Original window state for restore
ORIGINAL_WINDOW_STATE = None
# 🔧 [FIX] Last click position - used to focus correct input field when typing
LAST_CLICK_POS = None  # (abs_x, abs_y, window_title)

class InteractionRequest(BaseModel):
    action: str  # click, type, key
    x: int = 0
    y: int = 0
    text: str = ""
    key: str = ""
    window_title: str = None  # Client-specified target window for robust locking

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
    """Activate a window to bring it to foreground. Uses multiple methods for reliability."""
    if not win:
        return False
    
    try:
        hwnd = None
        
        # Get window handle
        if BACKGROUND_CAPTURE_AVAILABLE:
            try:
                hwnd = win32gui.FindWindow(None, win.title)
            except:
                pass
        
        # Early exit: if window is already foreground, skip activation
        if hwnd and BACKGROUND_CAPTURE_AVAILABLE:
            try:
                if win32gui.GetForegroundWindow() == hwnd:
                    return True  # Already foreground, no action needed
            except:
                pass
        
        if hwnd and BACKGROUND_CAPTURE_AVAILABLE:
            try:
                # Method 1: AttachThreadInput workaround (bypasses SetForegroundWindow restrictions)
                # This is the most reliable method for background processes
                import win32process
                import win32con
                
                foreground_hwnd = win32gui.GetForegroundWindow()
                foreground_thread = win32process.GetWindowThreadProcessId(foreground_hwnd)[0]
                target_thread = win32process.GetWindowThreadProcessId(hwnd)[0]
                current_thread = win32api.GetCurrentThreadId()
                
                # Attach input queues to allow SetForegroundWindow
                if foreground_thread != current_thread:
                    ctypes.windll.user32.AttachThreadInput(current_thread, foreground_thread, True)
                if target_thread != current_thread:
                    ctypes.windll.user32.AttachThreadInput(current_thread, target_thread, True)
                
                try:
                    # Restore if minimized
                    if win32gui.IsIconic(hwnd):
                        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    
                    # Bring to top and activate
                    win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, 
                                         win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
                    win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, 
                                         win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
                    win32gui.SetForegroundWindow(hwnd)
                    win32gui.BringWindowToTop(hwnd)
                finally:
                    # Detach threads
                    if foreground_thread != current_thread:
                        ctypes.windll.user32.AttachThreadInput(current_thread, foreground_thread, False)
                    if target_thread != current_thread:
                        ctypes.windll.user32.AttachThreadInput(current_thread, target_thread, False)
                
                return True
            except Exception as e:
                print(f"[ACTIVATE] AttachThreadInput method failed: {e}")
        
        # Method 2: Fallback to pygetwindow
        if hasattr(win, 'activate'):
            try:
                win.activate()
                return True
            except:
                pass
        
        # Method 3: Simple SetForegroundWindow fallback
        if hwnd:
            try:
                win32gui.SetForegroundWindow(hwnd)
                return True
            except:
                pass
        
        return False
    except Exception as e:
        print(f"[ERROR] Failed to activate window: {e}")
        return False

def get_current_frame():
    """
    Shared capture function for WebRTC.
    Returns (PIL.Image, window_title) or (None, None) if capture fails.
    Uses the same capture logic as WebSocket stream.
    """
    global LOCKED_WINDOW_TITLE, CURRENT_DISPLAY_WINDOW, LAST_VALID_WINDOW
    
    screenshot = None
    window_title = None
    hwnd = None
    rect = None
    
    try:
        # Check if we have a locked window
        if LOCKED_WINDOW_TITLE:
            win = get_target_window()
            if win:
                window_title = win.title
                hwnd = getattr(win, '_hWnd', None)
                if not hwnd and BACKGROUND_CAPTURE_AVAILABLE:
                    hwnd = win32gui.FindWindow(None, win.title)
                
                # [OPTIMIZED] Hybrid Locked Capture
                # 1. If window is in foreground, use DXcam (Fastest, 60+ FPS)
                if BACKGROUND_CAPTURE_AVAILABLE:
                    fg_hwnd = win32gui.GetForegroundWindow()
                    if fg_hwnd == hwnd:
                         try:
                             rect = win32gui.GetWindowRect(hwnd)
                             # Use fast_mode=True for raw BGR array
                             screenshot = simple_capture(hwnd=hwnd, rect=rect, fast_mode=True)
                             if screenshot is not None:
                                 return screenshot, window_title
                         except:
                             pass

                # 2. If background/covered, use WGC or PrintWindow (Slower but correct)
                width, height = win.width, win.height
                
                # Try WGC first (Best for background GPU apps)
                if WGC_CAPTURE_AVAILABLE and screenshot is None:
                    screenshot = capture_window_wgc(hwnd=hwnd, window_name=win.title)
                
                # Fallback to PrintWindow (Most compatible background capture)
                if screenshot is None and hwnd and BACKGROUND_CAPTURE_AVAILABLE:
                    screenshot = capture_window_background(hwnd, width, height, skip_black_check=True)
                
                # DO NOT fallback to simple_capture(DXcam) if background capture failed
                # because DXcam would capture the covering window, which is wrong behavior for "Locked" mode.

        else:
            # Auto-detect mode: use foreground window
            hwnd, rect = get_foreground_hwnd_and_rect()
            if hwnd and rect:
                window_title = win32gui.GetWindowText(hwnd)
                width = rect[2] - rect[0]
                height = rect[3] - rect[1]
                
                # Skip Ghost Shell
                if "Ghost Shell" in window_title:
                    if CURRENT_DISPLAY_WINDOW and "Ghost Shell" not in CURRENT_DISPLAY_WINDOW:
                        window_title = CURRENT_DISPLAY_WINDOW
                    elif LAST_VALID_WINDOW and "Ghost Shell" not in LAST_VALID_WINDOW:
                        window_title = LAST_VALID_WINDOW
                
                # DXcam priority capture
                # Use fast_mode=True to get raw BGR numpy array instead of PIL Image
                # This avoids expensive BGR->RGB->PIL->RGB->YUV conversions
                screenshot = simple_capture(hwnd=hwnd, rect=rect, fast_mode=True)
                
                # BitBlt fallback
                if screenshot is None and BACKGROUND_CAPTURE_AVAILABLE:
                    try:
                        screenshot = capture_window_background(hwnd, width, height)
                    except:
                        screenshot = None
        
        return screenshot, window_title
        
    except Exception as e:
        print(f"[get_current_frame] Error: {e}")
        return None, None

def simple_capture(hwnd=None, rect=None, fast_mode=False):
    """
    Multi-mode capture with fallback chain.
    Modes: 'dxcam' (fastest), 'mss' (cross-platform), 'legacy' (PIL.ImageGrab)
    fast_mode: If True, returns raw numpy array (BGR) instead of PIL Image when possible.
    """
    global CAPTURE_ENGINE, dxcam_camera
    
    # Determine which engine to use
    engine = CAPTURE_ENGINE
    
    # [OPTIMIZATION] If fast_mode is requested, we MUST use DXcam (if available)
    # because only DXcam supports returning raw numpy arrays efficiently.
    if fast_mode and DXCAM_AVAILABLE:
        engine = "dxcam"
    
    if engine == "auto":
        if DXCAM_AVAILABLE:
            engine = "dxcam"
        elif MSS_AVAILABLE:
            engine = "mss"
        else:
            engine = "legacy"
    
    try:
        # Get rect if not provided
        if rect is None and hwnd and BACKGROUND_CAPTURE_AVAILABLE:
            rect = win32gui.GetWindowRect(hwnd)
        
        if not rect or rect[2] <= rect[0] or rect[3] <= rect[1]:
            return None
        
        left, top, right, bottom = rect
        width = right - left
        height = bottom - top
        
        # ==================== DXcam Mode (Fastest) ====================
        if engine == "dxcam" and DXCAM_AVAILABLE and CV2_AVAILABLE:
            try:
                # Initialize camera if needed
                if dxcam_camera is None:
                    dxcam_camera = dxcam.create(output_idx=0, output_color="BGR")
                
                # Check bounds (DXcam only captures one monitor)
                cam_w, cam_h = dxcam_camera.width, dxcam_camera.height
                if (left < 0 or top < 0 or right > cam_w or bottom > cam_h):
                    # Window is outside primary monitor or cross-monitor
                    raise ValueError("Window out of bounds for DXcam")

                # DXcam captures full screen, we need to crop
                # [STREAMING MODE] Use get_latest_frame if started (Zero latency)
                if dxcam_camera.is_capturing:
                    frame = dxcam_camera.get_latest_frame()
                else:
                    # [POLLING MODE] Fallback to grab (One-shot)
                    frame = dxcam_camera.grab()
                
                if frame is None:
                    raise ValueError("DXcam frame is None (static or failed)")
                    
                # Crop to window region
                cropped = frame[top:bottom, left:right]
                
                if cropped.size == 0:
                    raise ValueError(f"Empty crop result: {cropped.shape}")

                # [OPTIMIZED] Fast Mode: Return raw BGR numpy array
                if fast_mode:
                    return cropped

                # Default: Convert to PIL Image (RGB)
                return Image.fromarray(cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB))
            except Exception as e:
                # Only print non-bounds errors to avoid log spam
                if "bounds" not in str(e):
                    print(f"[CAPTURE] DXcam error: {e} | Rect: {rect}, falling back to mss")
                engine = "mss"  # Fallback
        
        # ==================== mss Mode (Fast, Cross-platform) ====================
        if engine == "mss" and MSS_AVAILABLE:
            try:
                monitor = {
                    "left": left,
                    "top": top,
                    "width": width,
                    "height": height
                }
                sct_img = mss_sct.grab(monitor)
                # Convert to PIL Image
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                return img
            except Exception as e:
                print(f"[CAPTURE] mss error: {e} | Rect: {rect}, falling back to legacy")
                engine = "legacy"  # Fallback
        
        # ==================== Legacy Mode (PIL.ImageGrab) ====================
        from PIL import ImageGrab
        return ImageGrab.grab(bbox=rect, all_screens=True)
        
    except Exception as e:
        print(f"[CAPTURE] simple_capture error: {e}")
    return None

def get_current_capture_engine():
    """Get the currently active capture engine."""
    if CAPTURE_ENGINE == "auto":
        if DXCAM_AVAILABLE and CV2_AVAILABLE:
            return "dxcam"
        elif MSS_AVAILABLE:
            return "mss"
        else:
            return "legacy"
    return CAPTURE_ENGINE

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



def capture_window_background(hwnd, width, height, skip_black_check=False):
    """
    Capture window content even when covered by other windows.
    Uses multiple fallback methods for maximum compatibility.
    
    Args:
        skip_black_check: If True, skip the "black screen" detection. Use for locked windows
                          where user explicitly wants the content even if dark-themed.
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
        
        # Check if image is mostly black (capture failed)
        # When skip_black_check is True (locked mode), use very low threshold (2) to only detect
        # truly failed captures (all-black from PrintWindow failure), while allowing dark themes through.
        # When False, use higher threshold (10) to be more aggressive about fallback.
        import numpy as np
        arr = np.array(img)
        mean_brightness = np.mean(arr)
        threshold = 2 if skip_black_check else 10
        if mean_brightness < threshold:
            # Capture failed - image is completely/nearly black
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
    return {"status": "Ghost Shell 服务器运行中", "version": "2.2", "endpoints": ["/capture", "/stream", "/interact", "/status", "/windows", "/lock", "/webrtc/offer"]}

# ==================== WebRTC Signaling ====================
# Low-latency streaming via WebRTC (aiortc)

WEBRTC_AVAILABLE = False
try:
    from webrtc_server import webrtc_offer_handler, webrtc_manager, init_webrtc
    # Initialize WebRTC with Ghost Shell's capture function
    init_webrtc(get_current_frame)
    WEBRTC_AVAILABLE = True
    print("✅ WebRTC available (low-latency streaming)")
except ImportError as e:
    print(f"⚠️ WebRTC not available: {e}")

class WebRTCOffer(BaseModel):
    sdp: str
    type: str = "offer"
    client_width: Optional[int] = None
    client_height: Optional[int] = None

@app.post("/webrtc/offer")
async def webrtc_offer(offer: WebRTCOffer):
    """
    Handle WebRTC SDP offer from client.
    Returns SDP answer for peer connection establishment.
    """
    print(f"[WebRTC] /webrtc/offer called from client: {offer.client_width}x{offer.client_height}", flush=True)
    
    if not WEBRTC_AVAILABLE:
        print("[WebRTC] Not available!", flush=True)
        return {"error": "WebRTC not available on server"}
    
    try:
        # Get current capture region if locked
        region = None
        if LOCKED_WINDOW_TITLE:
            windows = gw.getWindowsWithTitle(LOCKED_WINDOW_TITLE)
            if windows:
                win = windows[0]
                region = (win.left, win.top, win.width, win.height)
        
        # Calculate target FPS from FRAME_DELAY
        target_fps = int(1 / FRAME_DELAY) if FRAME_DELAY > 0 else 30
        print(f"[WebRTC] Calling handler: fps={target_fps}, region={region}", flush=True)
        
        answer = await webrtc_offer_handler(
            offer={"sdp": offer.sdp, "type": offer.type},
            fps=target_fps,
            region=region,
            client_dims=(offer.client_width, offer.client_height)
        )
        print(f"[WebRTC] Handler returned, answer type: {answer.get('type', 'unknown')}", flush=True)
        return answer
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[WebRTC] Error: {e}", flush=True)
        return {"error": str(e)}

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
    global LOCKED_WINDOW_TITLE, PENDING_ACTIVATION, MANUAL_LOCK_ACTIVE
    if req.title == "":
        LOCKED_WINDOW_TITLE = None
        MANUAL_LOCK_ACTIVE = False  # Reset to auto mode
        PENDING_ACTIVATION = False
        print(f"[LOCK] Unlocked, switching to auto-follow")
        return {"status": "unlocked", "message": "已解锁，恢复自动跟随", "auto_follow": True}
    else:
        LOCKED_WINDOW_TITLE = req.title
        MANUAL_LOCK_ACTIVE = True  # Hard Lock - won't auto-unlock
        PENDING_ACTIVATION = True
        print(f"[LOCK] Manually locked to: '{req.title}'")
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
    global LOCKED_WINDOW_TITLE, PENDING_ACTIVATION, MANUAL_LOCK_ACTIVE
    
    # 如果已经锁定，返回当前锁定状态
    if LOCKED_WINDOW_TITLE:
        return {
            "status": "already_locked",
            "title": LOCKED_WINDOW_TITLE,
            "message": f"已锁定: {LOCKED_WINDOW_TITLE[:30]}",
            "auto_follow": False
        }
    
    # 获取当前正在自动跟随显示的窗口（使用保存的状态，而非前台窗口）
    # 这是因为用户点击"锁定"按钮时，前台窗口已经变成 Ghost Shell 了
    title = CURRENT_DISPLAY_WINDOW
    
    # 如果当前显示窗口无效，尝试使用上一个有效窗口
    if not title or "Ghost Shell" in title:
        title = LAST_VALID_WINDOW
    
    # 再次检查：只排除 Ghost Shell 自身
    # 注意：不要过滤 "Antigravity"，因为用户的项目文件名可能包含这个词
    if title and "Ghost Shell" not in title:
        LOCKED_WINDOW_TITLE = title
        MANUAL_LOCK_ACTIVE = True  # Hard Lock - user clicked lock button
        PENDING_ACTIVATION = False
        print(f"[LOCK_CURRENT] Manually locked to current display: '{title}'")
        return {
            "status": "locked",
            "title": title,
            "message": f"已锁定: {title[:30]}",
            "auto_follow": False
        }
    
    return {"status": "error", "message": "没有找到可锁定的窗口（请先切换到目标窗口）"}

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
async def stream(websocket: WebSocket, client_id: int = 0):
    """WebSocket stream - control commands only (video via WebRTC)."""
    global CURRENT_DISPLAY_WINDOW, LAST_VALID_WINDOW, WINDOW_CHANGE_TIME, LOCKED_WINDOW_TITLE, PENDING_ACTIVATION, MANUAL_LOCK_ACTIVE, LAST_CLICK_POS
    import time
    import json
    await websocket.accept()
    print("[WS] Client connected (commands only, video via WebRTC)")
    
    async def process_command(cmd):
        """Process a single control command and return response."""
        global LOCKED_WINDOW_TITLE, MANUAL_LOCK_ACTIVE, CURRENT_DISPLAY_WINDOW, LAST_VALID_WINDOW, LAST_CLICK_POS, PENDING_ACTIVATION
        
        cmd_type = cmd.get('type', cmd.get('action', ''))
        
        # Handle lock_current command
        if cmd_type == 'lock_current':
            title = CURRENT_DISPLAY_WINDOW
            if not title or "Ghost Shell" in title:
                title = LAST_VALID_WINDOW
            if title and "Ghost Shell" not in title:
                LOCKED_WINDOW_TITLE = title
                MANUAL_LOCK_ACTIVE = True
                PENDING_ACTIVATION = False
                return {"type": "lock_result", "status": "locked", "title": title}
            return {"type": "lock_result", "status": "error", "message": "没有可锁定的窗口"}
        
        # Handle unlock command
        if cmd_type == 'unlock':
            LOCKED_WINDOW_TITLE = None
            MANUAL_LOCK_ACTIVE = False
            return {"type": "unlock_result", "status": "unlocked"}
        
        # Handle interaction commands (click, type, key, scroll, etc.)
        action = cmd.get('action', cmd_type)
        x = cmd.get('x', 0)
        y = cmd.get('y', 0)
        text = cmd.get('text', '')
        key = cmd.get('key', '')
        
        # Find target window
        target_title = LOCKED_WINDOW_TITLE or CURRENT_DISPLAY_WINDOW or LAST_VALID_WINDOW
        win = None
        if target_title:
            windows = gw.getWindowsWithTitle(target_title)
            if windows:
                win = windows[0]
        if not win:
            win = get_target_window()
        if not win:
            return {"type": "error", "message": "未找到目标窗口"}
        
        # Calculate absolute coordinates
        abs_x = win.left + x
        abs_y = win.top + y
        
        # Activate window
        try:
            activate_window(win)
        except:
            pass
        
        # Execute action
        try:
            if action == 'click':
                pyautogui.click(abs_x, abs_y)
                LAST_CLICK_POS = (abs_x, abs_y, win.title)
                return {"type": "result", "status": "clicked", "pos": [abs_x, abs_y]}
            elif action == 'double_click':
                pyautogui.doubleClick(abs_x, abs_y)
                return {"type": "result", "status": "double_clicked"}
            elif action == 'right_click':
                pyautogui.click(abs_x, abs_y, button='right')
                return {"type": "result", "status": "right_clicked"}
            elif action == 'type':
                import pyperclip
                import win32api
                import win32con
                if x != 0 or y != 0:
                    pyautogui.click(abs_x, abs_y)
                elif LAST_CLICK_POS and LAST_CLICK_POS[2] == win.title:
                    pyautogui.click(LAST_CLICK_POS[0], LAST_CLICK_POS[1])
                safe_text = text.replace('\x00', '').strip()
                if safe_text:
                    print(f"[WS-TYPE] Typing: '{safe_text}' to '{win.title[:30]}'")
                    pyperclip.copy(safe_text)
                    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
                    win32api.keybd_event(0x56, 0, 0, 0)
                    win32api.keybd_event(0x56, 0, win32con.KEYEVENTF_KEYUP, 0)
                    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
                return {"type": "result", "status": "typed", "text": safe_text}
            elif action == 'key':
                if len(key) == 1:
                    pyautogui.typewrite(key, interval=0)
                else:
                    pyautogui.press(key)
                return {"type": "result", "status": "key_pressed", "key": key}
            elif action == 'hotkey':
                keys = key.split('+')
                pyautogui.hotkey(*keys)
                return {"type": "result", "status": "hotkey_pressed", "keys": keys}
            elif action in ['scroll', 'scroll_up', 'scroll_down']:
                pyautogui.moveTo(abs_x, abs_y)
                amount = int(text) if text else (3 if action == 'scroll_up' else -3 if action == 'scroll_down' else 3)
                pyautogui.scroll(amount)
                return {"type": "result", "status": "scrolled", "amount": amount}
            elif action == 'mousedown':
                pyautogui.mouseDown(abs_x, abs_y)
                return {"type": "result", "status": "mousedown"}
            elif action == 'mouseup':
                pyautogui.mouseUp(abs_x, abs_y)
                return {"type": "result", "status": "mouseup"}
            elif action == 'mousemove':
                pyautogui.moveTo(abs_x, abs_y)
                return {"type": "result", "status": "mousemove"}
            elif action == 'open_app':
                pyautogui.hotkey('win', 's')
                import pyperclip
                await asyncio.sleep(1.0)
                pyperclip.copy(text)
                pyautogui.hotkey('ctrl', 'v')
                await asyncio.sleep(0.5)
                pyautogui.press('enter')
                return {"type": "result", "status": "opening_app", "app": text}
            else:
                return {"type": "error", "message": f"Unknown action: {action}"}
        except Exception as e:
            return {"type": "error", "message": str(e)}
    
    # [WEBSOCKET RESTORATION]
    # Background task to receive commands without blocking video loop
    command_queue = asyncio.Queue()
    
    async def receive_commands():
        try:
            while True:
                data = await websocket.receive_text()
                try:
                    cmd = json.loads(data)
                    await command_queue.put(cmd)
                except Exception:
                    pass
        except Exception:
            pass

    receiver_task = asyncio.create_task(receive_commands())

    try:
        # Import dependencies here if not global
        try:
            import cv2
            import numpy as np
        except ImportError:
            pass
            
        from encoders import get_encoder_manager
        encoder = get_encoder_manager()

        while True:
            frame_start = time.perf_counter()
            
            # 1. Capture Frame (Optimized)
            screenshot, window_title = get_current_frame()
            
            if screenshot is not None:
                # 2. Encode
                encoded_data = None
                width = 0
                height = 0
                format_type = "jpeg"
                enc_name = encoder.name
                
                is_numpy = isinstance(screenshot, np.ndarray)
                
                if is_numpy:
                    # DXcam / Fast Mode (BGR)
                    height, width = screenshot.shape[:2]
                    # Direct CV2 encoding
                    if cv2:
                        # Ensure BGR (DXcam default)
                        # cv2.imencode expects BGR. Perfect.
                        _, enc = cv2.imencode('.jpg', screenshot, [cv2.IMWRITE_JPEG_QUALITY, 85])
                        encoded_data = enc.tobytes()
                        enc_name = "CV2-Fast"
                    else:
                        from PIL import Image
                        rgb = screenshot[..., ::-1] # BGR to RGB
                        pil_img = Image.fromarray(rgb)
                        encoded_data, format_type = encoder.encode(pil_img)
                else:
                    # PIL Stream (PrintWindow)
                    width, height = screenshot.size
                    encoded_data, format_type = encoder.encode(screenshot)

                # 3. Send Meta (with foreground status)
                is_fg = False
                if LOCKED_WINDOW_TITLE and BACKGROUND_CAPTURE_AVAILABLE:
                    try:
                        fg_hwnd = win32gui.GetForegroundWindow()
                        fg_title = win32gui.GetWindowText(fg_hwnd)
                        is_fg = (fg_title == LOCKED_WINDOW_TITLE)
                    except:
                        pass
                
                await websocket.send_json({
                    "type": "meta",
                    "width": width,
                    "height": height,
                    "window": window_title[:50] if window_title else "未知",
                    "locked_title": LOCKED_WINDOW_TITLE if LOCKED_WINDOW_TITLE else None,
                    "manual_lock": MANUAL_LOCK_ACTIVE,
                    "is_foreground": is_fg,  # True = 锁定窗口在前台, False = 后台
                    "format": format_type,
                    "encoder": enc_name
                })
                
                # 4. Send Video Data
                await websocket.send_bytes(encoded_data)
            
            # 5. Process Commands (Poll Queue)
            while not command_queue.empty():
                try:
                    cmd = command_queue.get_nowait()
                    # print(f"[WS-CMD] Processing: {cmd.get('action')}")
                    result = await process_command(cmd)
                    await websocket.send_json(result)
                except Exception as e:
                    print(f"[WS-CMD] Error: {e}")
            
            # 6. FPS Wait
            elapsed = time.perf_counter() - frame_start
            wait_time = max(0.001, FRAME_DELAY - elapsed)
            await asyncio.sleep(wait_time)

    except WebSocketDisconnect:
        print("[WS] WebSocket disconnected")
    except Exception as e:
        print(f"[WS] Fatal Error: {e}")
        # traceback.print_exc()
    finally:
        receiver_task.cancel()
        try:
            await receiver_task
        except:
            pass

def background_click(hwnd, x, y, button='left', action='click'):
    """
    Send background click messages directly to window HWND.
    x, y are SCREEN coordinates.
    """
    if not BACKGROUND_CAPTURE_AVAILABLE:
        return False
        
    try:
        # Convert screen coords to client coords
        # ScreenToClient expects a POINT structure or tuple
        client_point = win32gui.ScreenToClient(hwnd, (x, y))
        
        # MAKELONG creates LPARAM: low-order word is x, high-order is y
        l_param = win32api.MAKELONG(client_point[0], client_point[1])
        
        msg_down = win32con.WM_LBUTTONDOWN
        msg_up = win32con.WM_LBUTTONUP
        w_param = win32con.MK_LBUTTON
        
        if button == 'right':
            msg_down = win32con.WM_RBUTTONDOWN
            msg_up = win32con.WM_RBUTTONUP
            w_param = win32con.MK_RBUTTON
            
        # print(f"[BG] Sending {button} {action} to client coords {client_point}")

        if action == 'mousedown' or action == 'click' or action == 'right_click':
            win32gui.PostMessage(hwnd, msg_down, w_param, l_param)
            
        if action == 'mouseup' or action == 'click' or action == 'right_click':
            win32gui.PostMessage(hwnd, msg_up, 0, l_param)
            
        return True
    except Exception as e:
        print(f"[BG-CLICK] Error: {e}")
        return False

def background_key(hwnd, key, action='press'):
    """
    Send keyboard messages directly to window HWND without focus switching.
    Supports: single keys, special keys (enter, backspace, etc.)
    """
    if not BACKGROUND_CAPTURE_AVAILABLE:
        return False
        
    try:
        # Virtual key codes for common keys
        VK_CODES = {
            'enter': 0x0D, 'return': 0x0D,
            'backspace': 0x08, 'back': 0x08,
            'tab': 0x09,
            'escape': 0x1B, 'esc': 0x1B,
            'space': 0x20,
            'left': 0x25, 'up': 0x26, 'right': 0x27, 'down': 0x28,
            'delete': 0x2E, 'del': 0x2E,
            'home': 0x24, 'end': 0x23,
            'pageup': 0x21, 'pagedown': 0x22,
            'f1': 0x70, 'f2': 0x71, 'f3': 0x72, 'f4': 0x73,
            'f5': 0x74, 'f6': 0x75, 'f7': 0x76, 'f8': 0x77,
            'f9': 0x78, 'f10': 0x79, 'f11': 0x7A, 'f12': 0x7B,
            'ctrl': 0x11, 'alt': 0x12, 'shift': 0x10,
            'win': 0x5B, 'windows': 0x5B,
        }
        
        WM_KEYDOWN = 0x0100
        WM_KEYUP = 0x0101
        WM_CHAR = 0x0102
        
        key_lower = key.lower()
        
        if key_lower in VK_CODES:
            # Special key - use WM_KEYDOWN/WM_KEYUP
            vk = VK_CODES[key_lower]
            if action in ['press', 'down']:
                win32gui.PostMessage(hwnd, WM_KEYDOWN, vk, 0)
            if action in ['press', 'up']:
                win32gui.PostMessage(hwnd, WM_KEYUP, vk, 0)
        elif len(key) == 1:
            # Single character - use WM_CHAR
            win32gui.PostMessage(hwnd, WM_CHAR, ord(key), 0)
        else:
            # Unknown key, try as character sequence
            for char in key:
                win32gui.PostMessage(hwnd, WM_CHAR, ord(char), 0)
        
        return True
    except Exception as e:
        print(f"[BG-KEY] Error: {e}")
        return False

def background_type(hwnd, text):
    """Send text to window using WM_CHAR messages."""
    if not BACKGROUND_CAPTURE_AVAILABLE or not text:
        return False
    try:
        WM_CHAR = 0x0102
        for char in text:
            win32gui.PostMessage(hwnd, WM_CHAR, ord(char), 0)
        return True
    except Exception as e:
        print(f"[BG-TYPE] Error: {e}")
        return False

@app.post("/interact")
async def interact(req: InteractionRequest):
    """Send interaction to target window."""
    global ORIGINAL_WINDOW_STATE, CURRENT_DISPLAY_WINDOW, LOCKED_WINDOW_TITLE, LAST_VALID_WINDOW, MANUAL_LOCK_ACTIVE, LAST_CLICK_POS
    
    # ... (Target selection logic unchanged)
    # 优先使用客户端指定的窗口（最准确）
    target_title = None
    if req.window_title and req.window_title != "未找到" and req.window_title != "-":
        target_title = req.window_title
    else:
        target_title = CURRENT_DISPLAY_WINDOW

    # [同机测试修复]
    if not target_title and LAST_VALID_WINDOW and WINDOW_CHANGE_TIME:
        time_since_change = time.time() - WINDOW_CHANGE_TIME
        if time_since_change < 0.5:
            target_title = LAST_VALID_WINDOW
            
    # [AUTO-LOCK] (Fix: Now updates Global state)
    if target_title and not LOCKED_WINDOW_TITLE:
        if "Ghost Shell" not in target_title:
             print(f"[AUTO-LOCK] Interaction triggered Soft Lock on: {target_title}")
             LOCKED_WINDOW_TITLE = target_title
             MANUAL_LOCK_ACTIVE = False # Soft Lock
             CURRENT_DISPLAY_WINDOW = target_title
             LAST_VALID_WINDOW = target_title
             # PENDING_ACTIVATION = False # (Optional, if defined global)
    
    # Find window
    win = None
    if target_title:
        windows = gw.getWindowsWithTitle(target_title)
        if windows:
            win = windows[0]
    
    if not win:
        win = get_target_window()
    
    if not win:
        raise HTTPException(status_code=404, detail="Target window not found")

    # [FIXED] Always calculate coordinates relative to TARGET window
    # The stream shows target window content, so user clicks should map to target window position
    abs_x = win.left + req.x
    abs_y = win.top + req.y
    print(f"[INPUT] Target: '{win.title[:30]}' at ({win.left}, {win.top}), click at ({abs_x}, {abs_y})")

    # [RELIABLE INPUT] Activate target window then send input via pyautogui (uses SendInput API)
    # This is the standard approach used by all remote desktop tools
    try:
        activate_window(win)
    except:
        pass  # Continue even if activation fails
    
    try:
        if req.action == "click":
            pyautogui.click(abs_x, abs_y)
            # 🔧 [FIX] Save click position for future text input
            LAST_CLICK_POS = (abs_x, abs_y, win.title)
            print(f"[CLICK] Saved position for text input: ({abs_x}, {abs_y})")
            return {"status": "clicked", "pos": (abs_x, abs_y), "window": win.title[:30]}
        elif req.action == "type":
            import pyperclip
            import win32api
            import win32con
            
            # 点击确保焦点
            try:
                if req.x != 0 or req.y != 0:
                    pyautogui.click(abs_x, abs_y)
                    LAST_CLICK_POS = (abs_x, abs_y, win.title)
                elif LAST_CLICK_POS and LAST_CLICK_POS[2] == win.title:
                    pyautogui.click(LAST_CLICK_POS[0], LAST_CLICK_POS[1])
                else:
                    activate_window(win)
            except: pass

            safe_text = req.text.replace('\x00', '').strip()
            if not safe_text:
                return {"status": "empty"}

            # 直接复制粘贴
            pyperclip.copy(safe_text)
            win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
            win32api.keybd_event(0x56, 0, 0, 0)
            win32api.keybd_event(0x56, 0, win32con.KEYEVENTF_KEYUP, 0)
            win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
            
            print(f"[TYPE] '{safe_text}'")
            return {"status": "typed", "text": safe_text}
        elif req.action == "key":
            # Handle keys: single characters use typewrite, special keys use press
            key = req.key
            if len(key) == 1:
                # Single character - use typewrite for proper IME support
                # This simulates typing the character, allowing target PC's IME to process it
                pyautogui.typewrite(key, interval=0)
            else:
                # Special key (enter, backspace, etc.) - use press
                pyautogui.press(key)
            print(f"[KEY] Sent '{key}' to '{win.title[:20]}'")
            return {"status": "key_pressed", "key": req.key, "window": win.title[:30]}
        elif req.action == "hotkey":
            keys = req.key.split("+")
            pyautogui.hotkey(*keys)
            print(f"[HOTKEY] Sent '{req.key}' to '{win.title[:20]}'")
            return {"status": "hotkey_pressed", "keys": keys, "window": win.title[:30]}


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
        elif req.action == "middle_click":
            # Middle mouse button click
            pyautogui.click(abs_x, abs_y, button='middle')
            return {"status": "middle_clicked", "pos": (abs_x, abs_y)}
        elif req.action == "keydown":
            # Hold a key down (for modifier+click combinations)
            pyautogui.keyDown(req.key)
            return {"status": "key_down", "key": req.key}
        elif req.action == "keyup":
            # Release a held key
            pyautogui.keyUp(req.key)
            return {"status": "key_up", "key": req.key}
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
        elif req.action == "mousedown":
            # Mouse button down (left)
            pyautogui.mouseDown(abs_x, abs_y)
            return {"status": "mousedown", "pos": (abs_x, abs_y)}
        elif req.action == "mouseup":
            # Mouse button up (left)
            pyautogui.mouseUp(abs_x, abs_y)
            return {"status": "mouseup", "pos": (abs_x, abs_y)}
        elif req.action == "mousemove":
            # Mouse move only
            pyautogui.moveTo(abs_x, abs_y)
            return {"status": "moved", "pos": (abs_x, abs_y)}
        else:
            raise HTTPException(status_code=400, detail="Unknown action")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/get_clipboard")
def get_clipboard():
    """Get current clipboard content from PC."""
    import pyperclip
    try:
        content = pyperclip.paste()
        return {"status": "success", "content": content}
    except Exception as e:
        return {"status": "error", "message": str(e)}

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

# Helper functions for multiprocessing
def start_http():
    import uvicorn
    print(f"✅ HTTP Server started on port 8000")
    # Need to pass import string or app object. App object works if defined globally.
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="error")

def start_https(cert_file, key_file):
    import uvicorn
    # Re-verify path in process
    if os.path.exists(cert_file):
        print(f"✅ HTTPS Server started on port 8444")
        uvicorn.run(app, host="0.0.0.0", port=8444, ssl_certfile=cert_file, ssl_keyfile=key_file, log_level="error")
    else:
        print("❌ HTTPS certificate not found in child process.")

if __name__ == "__main__":
    import uvicorn
    import sys
    import multiprocessing
    import time
    import socket
    
    # Get local IP
    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
    except:
        local_ip = "192.168.x.x"
    
    # Check for SSL certificate
    cert_dir = os.path.dirname(__file__)
    cert_file = os.path.join(cert_dir, "cert.pem")
    key_file = os.path.join(cert_dir, "key.pem")
    has_cert = os.path.exists(cert_file) and os.path.exists(key_file)

    # If --https-only flag is passed (legacy/debug), run only HTTPS
    if "--https-only" in sys.argv:
        start_https(cert_file, key_file)
    elif "--http-only" in sys.argv:
        start_http()
    else:
        # Default: Start HTTP + HTTPS servers
        processes = []
        
        p_http = multiprocessing.Process(target=start_http)
        p_http.start()
        processes.append(p_http)
        
        if has_cert:
            p_https = multiprocessing.Process(target=start_https, args=(cert_file, key_file))
            p_https.start()
            processes.append(p_https)
            
            print("\n" + "="*50)
            print("🚀 Ghost Shell Server Active")
            print("   - PC (HTTP):      http://localhost:8000")
            print("   - Mobile (HTTPS): https://localhost:8444")
            print("   - Speech (HTTP):  http://localhost:8000/speech/")
            print("   - Speech (HTTPS): https://localhost:8444/speech/")
            print("="*50 + "\n")
            
            try:
                for p in processes:
                    p.join()
            except KeyboardInterrupt:
                print("Stopping servers...")
                for p in processes:
                    p.terminate()
        else:
            print("⚠️ SSL cert.pem/key.pem not found. Running in HTTP-only mode.")
            print("   Speech available at: http://localhost:8000/speech/")
            start_http()

