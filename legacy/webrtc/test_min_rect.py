import win32gui
import win32con
import time

def check_minimized():
    hwnd = win32gui.GetForegroundWindow()
    print(f"Active window: {win32gui.GetWindowText(hwnd)}")
    
    print("Please minimize this window (or the active one) in 3 seconds...")
    time.sleep(3)
    
    # Get the same hwnd again
    title = win32gui.GetWindowText(hwnd)
    print(f"checking: {title}")
    
    rect = win32gui.GetWindowRect(hwnd)
    print(f"Rect: {rect}")
    
    placement = win32gui.GetWindowPlacement(hwnd)
    print(f"Placement: {placement}")
    # placement[1] is showCmd. SW_SHOWMINIMIZED=2
    is_minimized = placement[1] == win32con.SW_SHOWMINIMIZED
    print(f"Is Minimized: {is_minimized}")

if __name__ == "__main__":
    check_minimized()
