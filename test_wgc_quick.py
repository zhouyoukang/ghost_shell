"""
Quick test of zbl library for WGC window capture
"""
import time

def test_zbl():
    """Test zbl library"""
    print("Testing zbl library...")
    try:
        from zbl import Capture
        
        # Test capturing VS Code
        with Capture(window_name='Code') as cap:
            print("Capture opened for VS Code")
            time.sleep(1)  # Give it a moment
            frame = cap.grab()
            if frame is not None:
                print(f"✅ zbl: Captured frame shape: {frame.shape}")
                # Save to file
                from PIL import Image
                import numpy as np
                img = Image.fromarray(frame)
                img.save("zbl_test.png")
                print("✅ Saved to zbl_test.png")
                return True
            else:
                print("❌ zbl: Frame is None")
                return False
    except Exception as e:
        print(f"❌ zbl error: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_wincam():
    """Test wincam library"""
    print("\nTesting wincam library...")
    try:
        from wincam import DXCamera
        import win32gui
        
        # Find a window
        hwnd = win32gui.FindWindow(None, None)  # Get any window
        hwnds = []
        def enum_callback(h, _):
            if win32gui.IsWindowVisible(h):
                title = win32gui.GetWindowText(h)
                if title and 'Code' in title:
                    hwnds.append((h, title))
            return True
        win32gui.EnumWindows(enum_callback, None)
        
        if hwnds:
            hwnd, title = hwnds[0]
            print(f"Found window: {title[:50]}... (hwnd={hwnd})")
            
            camera = DXCamera(hwnd=hwnd)
            frame = camera.get_frame()
            if frame is not None:
                print(f"✅ wincam: Captured frame shape: {frame.shape}")
                from PIL import Image
                img = Image.fromarray(frame)
                img.save("wincam_test.png")
                print("✅ Saved to wincam_test.png")
                camera.close()
                return True
            else:
                print("❌ wincam: Frame is None")
                camera.close()
                return False
        else:
            print("❌ No VS Code window found")
            return False
    except Exception as e:
        print(f"❌ wincam error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("=" * 50)
    print("WGC Library Quick Test")
    print("=" * 50)
    
    zbl_ok = test_zbl()
    wincam_ok = test_wincam()
    
    print("\n" + "=" * 50)
    print("Results:")
    print(f"  zbl: {'✅ OK' if zbl_ok else '❌ FAILED'}")
    print(f"  wincam: {'✅ OK' if wincam_ok else '❌ FAILED'}")
    print("=" * 50)
