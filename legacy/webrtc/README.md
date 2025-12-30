# AI PC - Ghost Shell 远程控制 v2.1 (WebRTC 版)

> ⚠️ **注意**: 这是 WebRTC 分支。如果需要更稳定的 WebSocket 版本，请切换到 `main` 分支。

通过网页远程控制电脑窗口，支持多显示器和不同 DPI 缩放。

## 🔄 版本对比

| 特性 | WebSocket 版 (main) | WebRTC 版 (webrtc) |
|:--|:--|:--|
| **延迟** | 中等 (~100-200ms) | 极低 (~30-50ms) |
| **帧率** | 30 FPS | 60 FPS |
| **公网穿透** | ✅ 简单 (FRP直接转发) | ❌ 复杂 (需TURN服务器) |
| **稳定性** | ✅ 高 | ⚠️ 中等 |
| **配置难度** | ✅ 简单 | ⚠️ 复杂 |
| **适用场景** | 公网/FRP/局域网 | 仅局域网 |

### WebRTC 优点
- 🚀 **极低延迟**: P2P 直连，延迟可低至 30ms
- 📹 **高帧率**: 支持 60 FPS 流畅画面
- 🎵 **音频支持**: 可传输系统音频

### WebRTC 缺点
- ❌ **公网配置复杂**: 需要 STUN/TURN 服务器穿透 NAT
- ❌ **FRP 兼容性差**: FRP 只能转发 TCP，WebRTC 的 ICE 协商会失败
- ❌ **依赖更多**: 需要 aiortc 等额外库
- ❌ **调试困难**: ICE 连接失败时难以诊断

### 推荐选择
- **局域网使用**: 选择 WebRTC 版 (本分支)
- **公网/FRP 使用**: 选择 WebSocket 版 (`main` 分支)

---

## 功能

- 🖥️ 窗口实时截屏串流 (WebRTC, 60 FPS)
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
| `ghost_server.py` | FastAPI 主服务器 |
| `webrtc_server.py` | WebRTC 信令服务器 |
| `ghost_client.html` | 网页控制界面 |
| `wgc_capture.py` | Windows Graphics Capture |
| `config.py` | 配置文件 |

## 启动

```bash
# HTTP 模式 (端口 8000)
python ghost_server.py

# HTTPS 模式 (端口 8444，支持手机语音)
python ghost_server.py --https
```

## 访问

- HTTP: `http://电脑IP:8000`
- HTTPS: `https://电脑IP:8444`
- 语音识别: `https://电脑IP:8444/speech/`

## 依赖

```bash
pip install fastapi uvicorn pyautogui pygetwindow pillow pywin32 numpy aiortc
```

## 已知限制

- UWP 应用（设置、商店）需要窗口在前台
- 某些 GPU 渲染应用可能无法后台截图
- **公网环境需要配置 TURN 服务器**
