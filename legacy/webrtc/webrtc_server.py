"""
WebRTC Server Module for Ghost Shell
Uses aiortc for low-latency screen streaming

Integrated version - uses shared capture logic from ghost_server
"""

import asyncio
import fractions
import sys
from typing import Optional, Tuple
import numpy as np
import cv2  # Required for resizing

from av import VideoFrame
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack, RTCConfiguration, RTCIceServer


class ScreenCaptureTrack(VideoStreamTrack):
    """
    A video track that captures the screen using Ghost Shell's capture logic.
    Supports locked windows, background capture, WGC, etc.
    """
    
    kind = "video"
    
    def __init__(self, fps: int = 30, client_dims: Tuple[int, int] = None):
        super().__init__()
        self.fps = fps
        self._frame_count = 0
        self._get_current_frame = None  # Will be set externally
        self.client_dims = client_dims  # (width, height) or None
        print(f"[WebRTC-Track] Created: fps={fps}, client_dims={client_dims}", flush=True)
        
    def set_capture_function(self, func):
        """Set the capture function (from ghost_server)."""
        self._get_current_frame = func
        print(f"[WebRTC-Track] Capture function set", flush=True)
        
    async def recv(self):
        """
        Generate video frames using Ghost Shell's capture logic.
        """
        try:
            # Get timestamp
            pts, time_base = await self.next_timestamp()
            self._frame_count += 1
            
            # Log frames
            if self._frame_count % 120 == 1:
                print(f"[WebRTC-Track] Frame {self._frame_count}", flush=True)
            
            # Use Ghost Shell's capture logic
            if self._get_current_frame:
                screenshot, window_title = self._get_current_frame()
            else:
                # Fallback to mss if no capture function set
                import mss
                with mss.mss() as sct:
                    monitor = sct.monitors[1]
                    raw = sct.grab(monitor)
                    screenshot = None
                    # Convert to numpy
                    img = np.array(raw)
                    img = img[:, :, :3]
                    img = img[:, :, ::-1]
                    img = np.ascontiguousarray(img)
                    frame = VideoFrame.from_ndarray(img, format="rgb24")
                    frame.pts = pts
                    frame.time_base = time_base
                    return frame
            
            if screenshot is None:
                # Return a black frame if capture failed
                img = np.zeros((480, 640, 3), dtype=np.uint8)
                pixel_format = "bgr24"
            elif isinstance(screenshot, np.ndarray):
                # [OPTIMIZED] Fast path: DXcam returns BGR numpy array
                img = screenshot
                pixel_format = "bgr24"
                # Check dimensionality
                if len(img.shape) == 2:  # Grayscale
                    img = np.dstack((img, img, img))
                elif img.shape[2] == 4:  # BGRA
                    img = img[:, :, :3]
                
                # Ensure it's contiguous
                if not img.flags['C_CONTIGUOUS']:
                    img = np.ascontiguousarray(img)
            else:
                # Fallback: PIL Image (RGB)
                img = np.array(screenshot)
                pixel_format = "rgb24"
                
                # Handle different color formats for PIL
                if len(img.shape) == 2:
                    img = np.stack([img, img, img], axis=-1)
                elif img.shape[2] == 4:
                    img = img[:, :, :3]
                
                img = np.ascontiguousarray(img)

            # [REVERTED] No Resolution Cap
            # User choice: Send full raw resolution to avoid coordinate mapping issues.
            # Performance relies on hardware/capture speed.
            
            # Create VideoFrame
            frame = VideoFrame.from_ndarray(img, format=pixel_format)
            frame.pts = pts
            frame.time_base = time_base
            
            return frame
            
        except Exception as e:
            print(f"[WebRTC-Track] Error in recv(): {e}", flush=True)
            import traceback
            traceback.print_exc()
            # Return black frame on error
            img = np.zeros((480, 640, 3), dtype=np.uint8)
            frame = VideoFrame.from_ndarray(img, format="rgb24")
            frame.pts = pts if 'pts' in dir() else 0
            frame.time_base = time_base if 'time_base' in dir() else fractions.Fraction(1, 90000)
            return frame


class WebRTCManager:
    """
    Manages WebRTC peer connections for Ghost Shell.
    """
    
    def __init__(self):
        self.pcs: set[RTCPeerConnection] = set()
        self._capture_func = None
        
    def set_capture_function(self, func):
        """Set the capture function from ghost_server."""
        self._capture_func = func
        print(f"[WebRTC-Manager] Capture function set", flush=True)
        
    async def handle_offer(self, offer_sdp: str, offer_type: str = "offer", fps: int = 30, client_dims: Tuple[int, int] = None) -> Tuple[str, str]:
        """
        Handle an incoming WebRTC offer from a client.
        Returns (answer_sdp, answer_type).
        """
        # ICE servers for NAT traversal (same as client)
        ice_servers = [
            RTCIceServer(urls="stun:stun.l.google.com:19302"),
            RTCIceServer(urls="stun:stun1.l.google.com:19302"),
            RTCIceServer(
                urls="turn:openrelay.metered.ca:80",
                username="openrelayproject",
                credential="openrelayproject"
            ),
            RTCIceServer(
                urls="turn:openrelay.metered.ca:443",
                username="openrelayproject",
                credential="openrelayproject"
            ),
            RTCIceServer(
                urls="turn:openrelay.metered.ca:443?transport=tcp",
                username="openrelayproject",
                credential="openrelayproject"
            )
        ]
        
        config = RTCConfiguration(iceServers=ice_servers)
        pc = RTCPeerConnection(config)
        self.pcs.add(pc)
        
        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            print(f"[WebRTC] Connection state: {pc.connectionState}", flush=True)
            if pc.connectionState in ("failed", "closed"):
                await self.cleanup_pc(pc)
        
        # Add local tracks
        # Create video track with specific FPS and client dimensions
        track = ScreenCaptureTrack(fps=fps, client_dims=client_dims)
        if self._capture_func:
            track.set_capture_function(self._capture_func)
        
        # Add the screen capture track
        pc.addTrack(track)
        print(f"[WebRTC-Manager] Video track added to peer connection", flush=True)
        
        # Add audio track for system audio
        try:
            from audio_capture import get_audio_track, AUDIO_AVAILABLE
            if AUDIO_AVAILABLE:
                audio_track = get_audio_track()
                if audio_track:
                    pc.addTrack(audio_track)
                    print(f"[WebRTC-Manager] Audio track added", flush=True)
        except Exception as e:
            print(f"[WebRTC-Manager] Audio track not available: {e}", flush=True)
        
        # Set remote description (the offer)
        await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type=offer_type))
        print(f"[WebRTC-Manager] Remote description set", flush=True)
        
        # Create answer
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        
        print(f"[WebRTC-Manager] Answer created: {pc.localDescription.type}", flush=True)
        
        return pc.localDescription.sdp, pc.localDescription.type
    
    async def cleanup_pc(self, pc: RTCPeerConnection):
        """Clean up a peer connection."""
        print(f"[WebRTC-Manager] Cleaning up peer connection", flush=True)
        self.pcs.discard(pc)
        await pc.close()
        
    async def shutdown(self):
        """Close all peer connections."""
        coros = [pc.close() for pc in self.pcs]
        await asyncio.gather(*coros)
        self.pcs.clear()


# Global WebRTC manager instance
webrtc_manager = WebRTCManager()


# FastAPI integration functions
async def webrtc_offer_handler(offer: dict, fps: int = 30, region=None, client_dims=None) -> dict:
    """
    Handle WebRTC offer from client.
    Called by FastAPI route.
    """
    print(f"[WebRTC] webrtc_offer_handler: fps={fps}, dims={client_dims}", flush=True)
    
    answer_sdp, answer_type = await webrtc_manager.handle_offer(
        offer_sdp=offer.get("sdp", ""),
        offer_type=offer.get("type", "offer"),
        fps=fps,
        client_dims=client_dims
    )
    
    return {
        "sdp": answer_sdp,
        "type": answer_type
    }


def init_webrtc(capture_func):
    """
    Initialize WebRTC with Ghost Shell's capture function.
    Called from ghost_server.py on startup.
    """
    webrtc_manager.set_capture_function(capture_func)
    print(f"[WebRTC] Initialized with Ghost Shell capture", flush=True)
