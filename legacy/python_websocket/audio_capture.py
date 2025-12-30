"""
🔊 PC Audio Capture Module (WASAPI Loopback)
Uses PyAudioWPatch for proper Windows system audio capture
"""

import asyncio
import threading
from typing import Set
import struct

# Check if pyaudiowpatch is available
try:
    import pyaudiowpatch as pyaudio
    AUDIO_AVAILABLE = True
    print("✅ PyAudioWPatch loaded for WASAPI loopback")
except ImportError:
    try:
        import pyaudio
        AUDIO_AVAILABLE = True
        print("⚠️ Using standard PyAudio (no loopback support)")
    except ImportError:
        pyaudio = None
        AUDIO_AVAILABLE = False
        print("⚠️ PyAudio not installed. Install with: pip install PyAudioWPatch")


class AudioCapture:
    """Captures system audio using WASAPI loopback and broadcasts to clients"""
    
    def __init__(self, sample_rate=48000, channels=2):
        self.sample_rate = sample_rate
        self.channels = channels
        self.is_running = False
        self._listeners: Set[asyncio.Queue] = set()
        self._lock = threading.Lock()
        self._stream = None
        self._audio = None
        self._thread = None
        self._device_info = None
        
    def add_listener(self, queue: asyncio.Queue):
        """Add a listener queue for audio data"""
        with self._lock:
            self._listeners.add(queue)
            print(f"[Audio] Added listener. Total: {len(self._listeners)}")
    
    def remove_listener(self, queue: asyncio.Queue):
        """Remove a listener queue"""
        with self._lock:
            self._listeners.discard(queue)
            print(f"[Audio] Removed listener. Total: {len(self._listeners)}")
    
    def _find_loopback_device(self):
        """Find the WASAPI loopback device for system audio"""
        if not self._audio:
            return None
            
        try:
            # Get default speakers info
            wasapi_info = self._audio.get_host_api_info_by_type(pyaudio.paWASAPI)
            default_speakers = self._audio.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
            
            # Check if we can get loopback device (PyAudioWPatch feature)
            if hasattr(default_speakers, 'get') and default_speakers.get("isLoopbackDevice"):
                return default_speakers
                
            # Search for loopback device matching default speakers
            for i in range(self._audio.get_device_count()):
                device = self._audio.get_device_info_by_index(i)
                
                # PyAudioWPatch marks loopback devices
                if device.get("isLoopbackDevice", False):
                    # Match with default speakers
                    if default_speakers["name"] in device["name"]:
                        print(f"[Audio] Found loopback: {device['name']}")
                        return device
            
            # Fallback: find any loopback device
            for i in range(self._audio.get_device_count()):
                device = self._audio.get_device_info_by_index(i)
                if device.get("isLoopbackDevice", False):
                    print(f"[Audio] Using loopback: {device['name']}")
                    return device
                    
            # Last resort: try to use speakers as output (may not work)
            print(f"[Audio] No loopback found, using speakers: {default_speakers['name']}")
            return default_speakers
            
        except Exception as e:
            print(f"[Audio] Error finding loopback device: {e}")
            return None
    
    def _audio_thread(self):
        """Background thread for audio capture"""
        print("[Audio] Capture thread started")
        
        try:
            CHUNK = 4096
            
            while self.is_running and self._stream:
                try:
                    data = self._stream.read(CHUNK, exception_on_overflow=False)
                    
                    # Broadcast to all listeners
                    with self._lock:
                        for queue in list(self._listeners):
                            try:
                                queue.put_nowait(data)
                            except asyncio.QueueFull:
                                pass  # Drop if queue is full
                                
                except Exception as e:
                    if self.is_running:
                        print(f"[Audio] Read error: {e}")
                    break
                    
        except Exception as e:
            print(f"[Audio] Thread error: {e}")
        finally:
            print("[Audio] Capture thread stopped")
    
    def start(self, loop=None):
        """Start capturing audio"""
        if not AUDIO_AVAILABLE or not pyaudio:
            print("[Audio] PyAudio not available")
            return False
        
        if self.is_running:
            return True
        
        try:
            self._audio = pyaudio.PyAudio()
            
            # Find loopback device
            device = self._find_loopback_device()
            if not device:
                print("[Audio] No suitable audio device found")
                self._audio.terminate()
                self._audio = None
                return False
            
            self._device_info = device
            
            # Use device's native sample rate if different
            device_rate = int(device.get("defaultSampleRate", self.sample_rate))
            if device_rate != self.sample_rate:
                print(f"[Audio] Using device sample rate: {device_rate}Hz")
                self.sample_rate = device_rate
            
            # Open stream
            self._stream = self._audio.open(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                input_device_index=device["index"],
                frames_per_buffer=4096
            )
            
            self.is_running = True
            
            # Start capture thread
            self._thread = threading.Thread(target=self._audio_thread, daemon=True)
            self._thread.start()
            
            print(f"[Audio] Started capturing: {device['name']}")
            print(f"[Audio] Rate: {self.sample_rate}Hz, Channels: {self.channels}")
            return True
            
        except Exception as e:
            print(f"[Audio] Failed to start: {e}")
            import traceback
            traceback.print_exc()
            if self._audio:
                self._audio.terminate()
                self._audio = None
            return False
    
    def stop(self):
        """Stop capturing audio"""
        self.is_running = False
        
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
            
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except:
                pass
            self._stream = None
            
        if self._audio:
            try:
                self._audio.terminate()
            except:
                pass
            self._audio = None
            
        print("[Audio] Stopped")


# Global instance
audio_capture = AudioCapture() if AUDIO_AVAILABLE else None
