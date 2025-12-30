"""
Windows Graphics Capture (WGC) Test Script
测试 WGC 能否捕获被其他窗口遮挡的窗口内容

使用方法:
1. 打开一个目标窗口 (如 Chrome, VS Code)
2. 运行本脚本
3. 输入目标窗口的部分标题
4. 用其他窗口遮挡目标窗口
5. 观察截图是否仍然能捕获到被遮挡的内容
"""

import sys
import time
import ctypes
from ctypes import wintypes

# Windows API for finding windows
user32 = ctypes.windll.user32

def get_all_windows():
    """Get all visible windows with their titles and handles."""
    windows = []
    
    def enum_callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                title = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, title, length + 1)
                if title.value:
                    windows.append((hwnd, title.value))
        return True
    
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
    return windows

def find_window_by_title(partial_title):
    """Find a window by partial title match."""
    windows = get_all_windows()
    matches = [(hwnd, title) for hwnd, title in windows if partial_title.lower() in title.lower()]
    return matches

def test_wgc_capture(hwnd, title, save_path="wgc_test.png"):
    """Test WGC capture for a specific window."""
    try:
        from windows_capture import WindowsCapture, Frame, InternalCaptureControl
        
        print(f"\n🎯 正在测试捕获窗口: {title}")
        print(f"   HWND: {hwnd}")
        print(f"\n⏳ 请在 5 秒内用其他窗口**完全遮挡**目标窗口...")
        time.sleep(5)
        
        frames_captured = []
        
        # Event handlers
        def on_frame_arrived(frame: Frame, capture_control: InternalCaptureControl):
            print(f"✅ 收到帧: {frame.width}x{frame.height}")
            frames_captured.append(frame)
            # Stop after first frame
            capture_control.stop()
        
        def on_closed():
            print("📷 捕获会话已关闭")
        
        # Create capture for specific window
        capture = WindowsCapture(
            cursor_capture=False,
            draw_border=False,  # 不显示黄色边框
            monitor_index=None,
            window_name=None,
            window_handle=hwnd,  # 直接指定窗口句柄
        )
        
        capture.event(on_frame_arrived)
        capture.event(on_closed)
        
        print("🚀 开始捕获...")
        capture.start()
        
        # Wait for capture
        time.sleep(2)
        
        if frames_captured:
            frame = frames_captured[0]
            # Save the frame
            frame.save_as_image(save_path)
            print(f"\n🎉 成功! 截图已保存到: {save_path}")
            print(f"   分辨率: {frame.width}x{frame.height}")
            print("\n👀 请检查截图文件，看看是否捕获到了**被遮挡的窗口内容**!")
            return True
        else:
            print("\n❌ 未能捕获到任何帧")
            return False
            
    except ImportError as e:
        print(f"❌ windows-capture 库未正确安装: {e}")
        return False
    except Exception as e:
        print(f"❌ 捕获失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    print("=" * 60)
    print("  Windows Graphics Capture (WGC) 测试工具")
    print("  测试目标: 验证能否捕获被遮挡的窗口")
    print("=" * 60)
    
    # List all windows
    windows = get_all_windows()
    print(f"\n找到 {len(windows)} 个窗口")
    
    # Get user input
    print("\n请输入要测试的窗口标题关键字 (如: Chrome, Code, 记事本):")
    keyword = input("> ").strip()
    
    if not keyword:
        print("❌ 未输入关键字")
        return
    
    # Find matching windows
    matches = find_window_by_title(keyword)
    
    if not matches:
        print(f"❌ 未找到包含 '{keyword}' 的窗口")
        return
    
    if len(matches) > 1:
        print(f"\n找到 {len(matches)} 个匹配的窗口:")
        for i, (hwnd, title) in enumerate(matches):
            print(f"  [{i}] {title[:60]}...")
        
        print("\n请选择窗口序号:")
        try:
            idx = int(input("> "))
            hwnd, title = matches[idx]
        except:
            print("❌ 无效选择")
            return
    else:
        hwnd, title = matches[0]
    
    # Run test
    save_path = "f:/github/AIOT/ghost_shell/wgc_test.png"
    test_wgc_capture(hwnd, title, save_path)

if __name__ == "__main__":
    main()
