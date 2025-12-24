"""
Audio Capture Module for Ghost Shell
Captures Windows system audio (WASAPI loopback) for WebRTC streaming.
Uses pyaudiowpatch for native WASAPI loopback support.
"""

import asyncio
import fractions
import numpy as np
from typing import Optional
import threading
import queue

# Audio dependencies
AUDIO_AVAILABLE = False
try:
    import pyaudiowpatch as pyaudio
    AUDIO_AVAILABLE = True
    print("✅ pyaudiowpatch available (WASAPI loopback capture)")
except ImportError:
    try:
        import pyaudio
        AUDIO_AVAILABLE = True
        print("⚠️ Using standard pyaudio (limited loopback support)")
    except ImportError:
        print("⚠️ pyaudio/pyaudiowpatch not installed. Audio disabled. Install: pip install pyaudiowpatch")

from av import AudioFrame
from aiortc import MediaStreamTrack


class SystemAudioTrack(MediaStreamTrack):
    """
    WebRTC Audio Track that captures Windows system audio via WASAPI loopback.
    """
    
    kind = "audio"
    
    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__()
        self.sample_rate = sample_rate
        self.channels = channels
        self._timestamp = 0
        self._samples_per_frame = 960  # 20ms at 48kHz (Opus standard)
        self._audio_queue = queue.Queue(maxsize=100)
        self._running = False
        self._stream = None
        self._p = None
        self._capture_thread = None
        
        # Start audio capture
        if AUDIO_AVAILABLE:
            self._start_capture()
    
    def _find_loopback_device(self, p):
        """Find WASAPI loopback device for system audio capture."""
        try:
            # Get default WASAPI output device
            wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
            default_speakers = p.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
            
            # Check if it's a loopback device
            if not default_speakers.get("isLoopbackDevice", False):
                # Find the loopback device for this output
                for i in range(p.get_device_count()):
                    dev = p.get_device_info_by_index(i)
                    if dev.get("isLoopbackDevice", False):
                        # Match with default output name
                        if default_speakers["name"] in dev["name"]:
                            return dev
                
                # If no exact match, just return first loopback device
                for i in range(p.get_device_count()):
                    dev = p.get_device_info_by_index(i)
                    if dev.get("isLoopbackDevice", False):
                        return dev
            
            return default_speakers
            
        except Exception as e:
            print(f"[Audio] Error finding loopback device: {e}")
            return None
    
    def _capture_loop(self):
        """Background thread for audio capture."""
        try:
            self._p = pyaudio.PyAudio()
            
            # Find loopback device
            loopback_device = self._find_loopback_device(self._p)
            
            if loopback_device is None:
                print("[Audio] No loopback device found!")
                self._running = False
                return
            
            device_index = loopback_device["index"]
            device_channels = int(loopback_device.get("maxInputChannels", 2))
            device_rate = int(loopback_device.get("defaultSampleRate", 48000))
            
            print(f"[Audio] Using device: {loopback_device['name']}")
            print(f"[Audio] Format: {device_rate}Hz, {device_channels}ch")
            
            def audio_callback(in_data, frame_count, time_info, status):
                if status:
                    print(f"[Audio] Status: {status}")
                
                # Convert bytes to numpy array
                audio_data = np.frombuffer(in_data, dtype=np.int16)
                
                # Reshape to (samples, channels)
                if device_channels > 1:
                    audio_data = audio_data.reshape(-1, device_channels)
                else:
                    audio_data = audio_data.reshape(-1, 1)
                
                # Resample if needed
                if device_rate != self.sample_rate:
                    # Simple resampling (not perfect but fast)
                    ratio = self.sample_rate / device_rate
                    new_len = int(len(audio_data) * ratio)
                    indices = np.linspace(0, len(audio_data) - 1, new_len).astype(int)
                    audio_data = audio_data[indices]
                
                # Channel adjustment
                if audio_data.shape[1] != self.channels:
                    if self.channels == 2 and audio_data.shape[1] == 1:
                        audio_data = np.column_stack([audio_data, audio_data])
                    elif self.channels == 1 and audio_data.shape[1] >= 2:
                        audio_data = audio_data[:, 0:1]
                
                try:
                    self._audio_queue.put_nowait(audio_data.copy())
                except queue.Full:
                    # Drop oldest
                    try:
                        self._audio_queue.get_nowait()
                        self._audio_queue.put_nowait(audio_data.copy())
                    except:
                        pass
                
                return (None, pyaudio.paContinue)
            
            self._stream = self._p.open(
                format=pyaudio.paInt16,
                channels=device_channels,
                rate=device_rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=self._samples_per_frame,
                stream_callback=audio_callback
            )
            
            self._stream.start_stream()
            print("[Audio] Capture started")
            
            # Keep thread alive while running
            # [FIX] Only check _running flag, not is_active() which can be unreliable
            while self._running:
                threading.Event().wait(0.1)
            
        except Exception as e:
            print(f"[Audio] Capture error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self._cleanup_stream()
    
    def _start_capture(self):
        """Start the audio capture in a background thread."""
        if self._running:
            return
        
        self._running = True
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._capture_thread.start()
    
    def _cleanup_stream(self):
        """Clean up audio resources."""
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except:
                pass
            self._stream = None
        
        if self._p:
            try:
                self._p.terminate()
            except:
                pass
            self._p = None
    
    async def recv(self) -> AudioFrame:
        """Receive an audio frame for WebRTC transmission."""
        if not AUDIO_AVAILABLE or not self._running:
            # Return silence if audio not available
            await asyncio.sleep(0.02)  # 20ms
            samples = np.zeros((self._samples_per_frame, self.channels), dtype=np.int16)
        else:
            try:
                # Get audio data from queue with small wait
                samples = None
                for _ in range(3):  # Try a few times
                    try:
                        samples = self._audio_queue.get_nowait()
                        break
                    except queue.Empty:
                        await asyncio.sleep(0.005)
                
                if samples is None:
                    samples = np.zeros((self._samples_per_frame, self.channels), dtype=np.int16)
                
            except Exception:
                samples = np.zeros((self._samples_per_frame, self.channels), dtype=np.int16)
        
        # Ensure correct shape
        if samples.ndim == 1:
            samples = samples.reshape(-1, 1)
        
        # Ensure correct length
        if len(samples) < self._samples_per_frame:
            pad = np.zeros((self._samples_per_frame - len(samples), samples.shape[1]), dtype=np.int16)
            samples = np.vstack([samples, pad])
        elif len(samples) > self._samples_per_frame:
            samples = samples[:self._samples_per_frame]
        
        # Create audio frame
        frame = AudioFrame(format='s16', layout='stereo' if self.channels == 2 else 'mono')
        frame.samples = self._samples_per_frame
        frame.sample_rate = self.sample_rate
        frame.pts = self._timestamp
        frame.time_base = fractions.Fraction(1, self.sample_rate)
        
        frame.planes[0].update(samples.tobytes())
        
        self._timestamp += self._samples_per_frame
        
        return frame
    
    def stop(self):
        """Stop audio capture."""
        self._running = False
        if self._capture_thread and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=1.0)
        self._cleanup_stream()
        print("[Audio] Capture stopped")


# [FIX] Each WebRTC connection needs its own audio track instance
# because aiortc calls track.stop() when connection closes,
# which would kill a shared global track.

def get_audio_track() -> Optional[SystemAudioTrack]:
    """Create a new system audio track for a WebRTC connection.
    
    Note: Returns a NEW instance each time because aiortc manages
    track lifecycle and calls stop() when connection closes.
    """
    if not AUDIO_AVAILABLE:
        return None
    
    return SystemAudioTrack()


def stop_audio_track():
    """Stop the global audio track."""
    global _audio_track
    if _audio_track:
        _audio_track.stop()
        _audio_track = None
