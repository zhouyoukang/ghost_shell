# AI PC - Ghost Shell 远程控制 v2.1

通过网页远程控制电脑窗口，支持多显示器和不同 DPI 缩放。

## 功能

- 🖥️ 窗口实时截屏串流 (WebSocket, 2 FPS)
- 🖱️ 远程点击/滚动
- ⌨️ 文本输入/快捷键
- 🎤 语音输入 (需 HTTPS)
- 📱 手机/电脑端访问
- 🔒 窗口锁定选择
- 🖥️ **多显示器支持** (v2.1)
- 📐 **自动 DPI 缩放适配** (v2.1)

## 文件说明

| 文件 | 说明 |
|:--|:--|
| `ghost_server.py` | FastAPI 服务器 |
| `ghost_client.html` | 网页控制界面 |
| `start_ghost_shell.ps1` | Windows 快捷启动脚本 |
| `config.py` | 配置文件 |
| `server.pem` | HTTPS 自签名证书 |

## 启动

```bash
# HTTP 模式 (端口 8000)
python ghost_server.py

# HTTPS 模式 (端口 8443，支持手机语音)
python ghost_server.py --https
```

## 访问

- HTTP: `http://电脑IP:8000`
- HTTPS: `https://电脑IP:8443`

## 多显示器兼容性

Ghost Shell v2.1 已优化多显示器支持：

- ✅ 主屏/副屏窗口截图
- ✅ 不同 DPI 缩放比例 (100%, 125%, 150%)
- ✅ 副屏在左侧（负坐标）
- ✅ 自动前台窗口检测

**技术实现**：
1. 文件开头设置 `SetProcessDpiAwareness(2)` 确保正确坐标
2. 使用 `ImageGrab.grab(bbox=rect, all_screens=True)` 支持多显示器
3. 多层回退机制：PrintWindow → ImageGrab → PyAutoGUI

## 依赖

```bash
pip install fastapi uvicorn pyautogui pygetwindow pillow pywin32 numpy
```

## 已知限制

- UWP 应用（设置、商店）需要窗口在前台
- 某些 GPU 渲染应用可能无法后台截图

