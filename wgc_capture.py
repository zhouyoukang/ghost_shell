# Ghost Shell WGC (Windows Graphics Capture) Module
# 使用 zbl 或 wincam 库实现基于 WGC 的窗口捕获
# 支持捕获被其他窗口遮挡的内容

from PIL import Image
import numpy as np
from typing import Optional, Tuple
import threading
import time

# Check for available WGC libraries
ZBL_AVAILABLE = False
WINCAM_AVAILABLE = False

try:
    from zbl import Capture as ZblCapture
    ZBL_AVAILABLE = True
    print("✅ zbl library available (WGC support)")
except ImportError:
    pass

try:
    from wincam import DXCamera
    WINCAM_AVAILABLE = True
    print("✅ wincam library available (WGC support)")
except ImportError:
    pass

WGC_AVAILABLE = ZBL_AVAILABLE or WINCAM_AVAILABLE
if not WGC_AVAILABLE:
    print("⚠️ No WGC library available. Install with: pip install zbl wincam")


class WGCCapture:
    """
    Windows Graphics Capture wrapper.
    Supports capturing windows even when covered by other windows.
    """
    
    def __init__(self):
        self._zbl_capture = None
        self._wincam_camera = None
        self._current_window_name = None
        self._current_hwnd = None
        self._lock = threading.Lock()
    
    def capture_window_by_name(self, window_name: str) -> Optional[Image.Image]:
        """
        Capture a window by its title (partial match).
        Uses zbl library which supports WGC.
        
        Args:
            window_name: Partial window title to match
            
        Returns:
            PIL Image or None if capture failed
        """
        if not ZBL_AVAILABLE:
            return None
        
        try:
            with self._lock:
                # Create capture context for this window
                with ZblCapture(window_name=window_name) as cap:
                    frame = cap.grab()
                    if frame is not None and frame.size > 0:
                        # Convert BGRA to RGB
                        if len(frame.shape) == 3 and frame.shape[2] == 4:
                            # BGRA -> RGB
                            frame = frame[:, :, [2, 1, 0]]
                        return Image.fromarray(frame)
            return None
        except Exception as e:
            # print(f"[WGC] zbl capture error: {e}")
            return None
    
    def capture_window_by_hwnd(self, hwnd: int) -> Optional[Image.Image]:
        """
        Capture a window by its HWND.
        Uses wincam library which supports WGC with HWND.
        
        Args:
            hwnd: Window handle
            
        Returns:
            PIL Image or None if capture failed
        """
        if not WINCAM_AVAILABLE:
            return None
        
        try:
            with self._lock:
                # Create or reuse camera
                if self._wincam_camera is None or self._current_hwnd != hwnd:
                    if self._wincam_camera is not None:
                        try:
                            self._wincam_camera.close()
                        except:
                            pass
                    self._wincam_camera = DXCamera(hwnd=hwnd)
                    self._current_hwnd = hwnd
                
                frame = self._wincam_camera.get_frame()
                if frame is not None and frame.size > 0:
                    return Image.fromarray(frame)
            return None
        except Exception as e:
            # print(f"[WGC] wincam capture error: {e}")
            # Reset camera on error
            self._wincam_camera = None
            self._current_hwnd = None
            return None
    
    def capture_window(self, hwnd: int = None, window_name: str = None) -> Optional[Image.Image]:
        """
        Capture a window using best available method.
        
        Args:
            hwnd: Window handle (preferred if available)
            window_name: Window title for partial match (fallback)
            
        Returns:
            PIL Image or None if capture failed
        """
        # Try HWND-based capture first (more reliable)
        if hwnd and WINCAM_AVAILABLE:
            result = self.capture_window_by_hwnd(hwnd)
            if result:
                return result
        
        # Fallback to name-based capture
        if window_name and ZBL_AVAILABLE:
            result = self.capture_window_by_name(window_name)
            if result:
                return result
        
        return None
    
    def cleanup(self):
        """Release resources."""
        with self._lock:
            if self._wincam_camera is not None:
                try:
                    self._wincam_camera.close()
                except:
                    pass
                self._wincam_camera = None
            self._current_hwnd = None
            self._current_window_name = None


# Global instance
_wgc_capture: Optional[WGCCapture] = None

def get_wgc_capture() -> WGCCapture:
    """Get or create the global WGC capture instance."""
    global _wgc_capture
    if _wgc_capture is None:
        _wgc_capture = WGCCapture()
    return _wgc_capture

def capture_window_wgc(hwnd: int = None, window_name: str = None) -> Optional[Image.Image]:
    """
    Convenience function to capture a window using WGC.
    
    Args:
        hwnd: Window handle
        window_name: Window title (partial match)
        
    Returns:
        PIL Image or None
    """
    if not WGC_AVAILABLE:
        return None
    return get_wgc_capture().capture_window(hwnd=hwnd, window_name=window_name)


if __name__ == "__main__":
    # Quick test
    print("Testing WGC capture...")
    
    if ZBL_AVAILABLE:
        print("\nTesting zbl (by window name)...")
        img = capture_window_wgc(window_name="Code")
        if img:
            img.save("wgc_zbl_test.png")
            print(f"✅ Captured {img.size}, saved to wgc_zbl_test.png")
        else:
            print("❌ zbl capture failed")
    
    if WINCAM_AVAILABLE:
        print("\nTesting wincam (by hwnd)...")
        import win32gui
        
        def find_window(title_part):
            result = []
            def callback(hwnd, _):
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    if title and title_part.lower() in title.lower():
                        result.append((hwnd, title))
                return True
            win32gui.EnumWindows(callback, None)
            return result
        
        windows = find_window("Code")
        if windows:
            hwnd, title = windows[0]
            print(f"Found: {title[:50]}...")
            img = capture_window_wgc(hwnd=hwnd)
            if img:
                img.save("wgc_wincam_test.png")
                print(f"✅ Captured {img.size}, saved to wgc_wincam_test.png")
            else:
                print("❌ wincam capture failed")
        else:
            print("No VS Code window found")
    
    print("\nDone!")
