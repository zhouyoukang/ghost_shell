package main

import (
	"encoding/binary"
	"log"
	"math"
	"sync"
	"syscall"
	"time"
	"unsafe"

	"os"

	"github.com/gorilla/websocket"
	"github.com/moutend/go-wca/pkg/wca"
)

var (
	audioBroadcast = make(chan []byte, 100)
	apiMutex       sync.Mutex

	ole32                = syscall.NewLazyDLL("ole32.dll")
	procCoTaskMemFree    = ole32.NewProc("CoTaskMemFree")
	procCoInitializeEx   = ole32.NewProc("CoInitializeEx")
	procCoUninitialize   = ole32.NewProc("CoUninitialize")
	procCoCreateInstance = ole32.NewProc("CoCreateInstance")
)

const (
	COINIT_APARTMENTTHREADED     = 0x2
	CLSCTX_ALL                   = 23
	eRender                      = 0
	eConsole                     = 0
	AUDCLNT_SHAREMODE_SHARED     = 0
	AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
)

func oleCoTaskMemFree(ptr unsafe.Pointer) {
	procCoTaskMemFree.Call(uintptr(ptr))
}

func oleCoInitializeEx() error {
	ret, _, _ := procCoInitializeEx.Call(0, uintptr(COINIT_APARTMENTTHREADED))
	if ret != 0 && ret != 1 { // S_OK or S_FALSE
		return syscall.Errno(ret)
	}
	return nil
}

func oleCoUninitialize() {
	procCoUninitialize.Call()
}

func oleCoCreateInstance(clsid *syscall.GUID, iid *syscall.GUID, ppv unsafe.Pointer) error {
	ret, _, _ := procCoCreateInstance.Call(
		uintptr(unsafe.Pointer(clsid)),
		0,
		uintptr(CLSCTX_ALL),
		uintptr(unsafe.Pointer(iid)),
		uintptr(ppv), // This should be **Interface, so uintptr(unsafe.Pointer(&iface))
	)
	if ret != 0 {
		return syscall.Errno(ret)
	}
	return nil
}

// Helper removed (using safe conversion inline)

func startAudioCapture() {
	log.Println("[Audio] Starting audio subsystem...")
	go func() {
		for {
			func() {
				// Recovery for panic protection
				defer func() {
					if r := recover(); r != nil {
						log.Printf("[Audio] PANIC recovered: %v", r)
					}
				}()

				log.Println("[Audio] Initializing capture loop...")
				if err := runAudioLoop(); err != nil {
					log.Printf("[Audio] Capture loop failed: %v", err)
				}
			}()

			// Retry delay
			log.Println("[Audio] Retrying in 5 seconds...")
			time.Sleep(5 * time.Second)
		}
	}()
}

func runAudioLoop() error {
	// Initialize COM
	if err := oleCoInitializeEx(); err != nil {
		return err
	}
	defer oleCoUninitialize()

	// wca constants/GUIDs
	// We assume wca package has CLSID_MMDeviceEnumerator etc.
	// If not, we fix it later.
	var enumerator *wca.IMMDeviceEnumerator
	if err := wca.CoCreateInstance(wca.CLSID_MMDeviceEnumerator, 0, wca.CLSCTX_ALL, wca.IID_IMMDeviceEnumerator, &enumerator); err != nil {
		return err
	}
	defer enumerator.Release()

	var device *wca.IMMDevice
	// Use local constants eRender, eConsole
	if err := enumerator.GetDefaultAudioEndpoint(eRender, eConsole, &device); err != nil {
		return err
	}
	defer device.Release()

	var audioClient *wca.IAudioClient
	if err := device.Activate(wca.IID_IAudioClient, wca.CLSCTX_ALL, nil, &audioClient); err != nil {
		return err
	}
	defer audioClient.Release()

	var waveFormat *wca.WAVEFORMATEX
	if err := audioClient.GetMixFormat(&waveFormat); err != nil {
		return err
	}
	defer oleCoTaskMemFree(unsafe.Pointer(waveFormat))

	// Print format info
	channels := int(waveFormat.NChannels)
	sampleRate := int(waveFormat.NSamplesPerSec)
	bitsPerSample := int(waveFormat.WBitsPerSample)
	if bitsPerSample == 0 {
		bitsPerSample = 32 // Default to Float32 if unreported
	}
	// Check Format Tag to decide Float vs Int
	// 1 = PCM, 3 = IEEE_FLOAT, 0xFFFE = EXTENSIBLE
	formatTag := waveFormat.WFormatTag

	// Check SubFormat if Extensible
	isFloat := false
	if formatTag == 3 {
		isFloat = true
	} else if formatTag == 0xFFFE {
		// Read SubFormat GUID (Data1) at offset 24
		// WAVEFORMATEXTENSIBLE: WAVEFORMATEX(18) + Samples(2) + ChannelMask(4) + SubFormat(16)
		// SubFormat starts at 18+2+4 = 24.
		ptr := unsafe.Pointer(waveFormat)
		// Safe only if cbSize is correct, but 0xFFFE implies Extensible
		data1Ptr := (*uint32)(unsafe.Pointer(uintptr(ptr) + 24))
		data1 := *data1Ptr

		if data1 == 1 {
			isFloat = false // KSDATAFORMAT_SUBTYPE_PCM
			log.Printf("[Audio] Extensible Format: PCM (Int)")
		} else if data1 == 3 {
			isFloat = true // KSDATAFORMAT_SUBTYPE_IEEE_FLOAT
			log.Printf("[Audio] Extensible Format: IEEE_FLOAT")
		} else {
			isFloat = true // Default
			log.Printf("[Audio] Extensible Format: Unknown GUID Data1=%d, using Float", data1)
		}
	}

	log.Printf("[Audio] System Format: Rate=%dHz, Ch=%d, Bits=%d, Tag=%d", sampleRate, channels, bitsPerSample, formatTag)

	// WASAPI Loopback requires Shared Mode
	// We must use the mix format provided by GetMixFormat
	// 50ms (500000) - Balanced for WAN: Small enough for fast recovery, large enough for stability.
	// 10ms caused noise, 100ms caused accumulation on slow networks.
	var period wca.REFERENCE_TIME = 500000

	// Safety: Recover from panics to avoid killing the whole server
	defer func() {
		if r := recover(); r != nil {
			log.Printf("!!! AUDIO PANIC RECOVERED !!!: %v", r)
			// Optional: restart loop? For now just log.
		}
	}()

	if err := audioClient.Initialize(AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_LOOPBACK, period, 0, waveFormat, nil); err != nil {
		log.Printf("[Audio] Initialize Failed: %v", err)
		return err
	}

	var captureClient *wca.IAudioCaptureClient
	if err := audioClient.GetService(wca.IID_IAudioCaptureClient, &captureClient); err != nil {
		return err
	}
	defer captureClient.Release()

	if err := audioClient.Start(); err != nil {
		return err
	}
	defer audioClient.Stop()

	log.Println("[Audio] Capture Started Successfully")

	var packetLength uint32
	// Target is 48000Hz, 2ch, 16bit (Client requirement)

	// Target is 48000Hz, 2ch, 16bit (Client requirement)
	packetCount := 0

	for {
		if err := captureClient.GetNextPacketSize(&packetLength); err != nil {
			log.Printf("GetNextPacketSize error: %v", err)
			break
		}

		if packetLength == 0 {
			time.Sleep(1 * time.Millisecond)
			continue
		}

		packetCount++
		if packetCount%200 == 0 {
			log.Printf("[Audio] Stream Active: Sent %d packets", packetCount)
		}

		var buffer *byte
		var framesAvailable uint32
		var flags uint32

		if err := captureClient.GetBuffer(&buffer, &framesAvailable, &flags, nil, nil); err != nil {
			log.Printf("GetBuffer error: %v", err)
			break
		}

		// Process audio if not silent
		if flags&wca.AUDCLNT_BUFFERFLAGS_SILENT == 0 {
			// Calculate buffer size in bytes
			bufferSize := int(framesAvailable) * int(waveFormat.NBlockAlign)
			// Use unsafe.Slice for safer pointer access (Go 1.17+)
			src := unsafe.Slice(buffer, bufferSize)

			raw := make([]byte, bufferSize)
			copy(raw, src)

			// --- Auto-Calibration (DISABLED due to stability issues in RDP) ---
			/*
				currentFrames := len(raw) / int(waveFormat.NBlockAlign)
				totalFrames += currentFrames

				// Detect every 500ms
				if time.Since(startTime) >= 500*time.Millisecond {
					elapsed := time.Since(startTime).Seconds()
					measuredRate := int(float64(totalFrames) / elapsed)

					// Snap to nearest standard rate...
					standards := []int{44100, 48000, 88200, 96000, 176400, 192000}
					bestRate := measuredRate
					minDiff := 1000000

					for _, r := range standards {
						diff := int(math.Abs(float64(r - measuredRate)))
						if diff < minDiff {
							minDiff = diff
							bestRate = r
						}
					}

					if packetCount%50 == 0 {
						log.Printf("[Audio Calib] Config=%d, Measured=%d, Best=%d", sampleRate, measuredRate, bestRate)
					}

					// DISABLE DYNAMIC SWITCHING
					// if math.Abs(float64(sampleRate-bestRate)) > 2000 {
					// 	log.Printf("[Audio] !!! RATE CORRECTION !!! Switch %d -> %d", sampleRate, bestRate)
					// 	sampleRate = bestRate
					// }

					startTime = time.Now()
					totalFrames = 0
				}
			*/
			// --------------------------------------------------

			// Process Audio: Convert anything to Stereo Int16 @ 48000Hz
			// 1. Convert to []float32 (SAFE METHOD)
			var floats []float32
			numSamples := len(raw) / (bitsPerSample / 8)
			floats = make([]float32, numSamples)

			if bitsPerSample == 32 {
				if isFloat {
					// Float32 (Standard)
					for i := 0; i < numSamples; i++ {
						bits := binary.LittleEndian.Uint32(raw[i*4:])
						floats[i] = math.Float32frombits(bits)
					}
				} else {
					// Int32 (PCM)
					for i := 0; i < numSamples; i++ {
						val := int32(binary.LittleEndian.Uint32(raw[i*4:]))
						// Scale Int32 to Float32 (-1.0 to 1.0)
						// Int32 Max ~2e9.
						floats[i] = float32(val) / 2147483648.0
					}
				}
			} else if bitsPerSample == 16 {
				// Int16
				for i := 0; i < numSamples; i++ {
					val := int16(binary.LittleEndian.Uint16(raw[i*2:]))
					floats[i] = float32(val) / 32768.0
				}
			} else if bitsPerSample == 24 {
				// Int24
				for i := 0; i < numSamples; i++ {
					pos := i * 3
					val := int32(raw[pos]) | (int32(raw[pos+1]) << 8) | (int32(raw[pos+2]) << 16)
					if val&0x800000 != 0 {
						val |= -0x1000000
					}
					floats[i] = float32(val) / 8388608.0
				}
			}

			// 2. Resample to 48000Hz (Linear Interpolation)
			// Python original logic: device_rate != self.sample_rate (48000)
			const targetRate = 48000
			if sampleRate != targetRate {
				ratio := float64(sampleRate) / float64(targetRate)
				// Input Frames = len(floats) / channels
				inputFrames := len(floats) / channels
				// Output Frames = Input Frames / ratio
				outputFrames := int(float64(inputFrames) / ratio)

				newFloats := make([]float32, outputFrames*channels)

				for i := 0; i < outputFrames; i++ {
					srcIdx := float64(i) * ratio
					idx0 := int(srcIdx)
					idx1 := idx0 + 1
					if idx1 >= inputFrames {
						idx1 = inputFrames - 1
					}
					frac := srcIdx - float64(idx0)

					// Resample each channel
					for ch := 0; ch < channels; ch++ {
						s0 := floats[idx0*channels+ch]
						s1 := floats[idx1*channels+ch]
						// Linear Interp
						newFloats[i*channels+ch] = s0 + float32(frac)*(s1-s0)
					}
				}
				floats = newFloats
			}

			// 3. Downmix to Stereo if needed
			if channels > 2 {
				frames := len(floats) / channels
				newFloats := make([]float32, frames*2)
				for i := 0; i < frames; i++ {
					newFloats[i*2] = floats[i*channels]     // L
					newFloats[i*2+1] = floats[i*channels+1] // R
				}
				floats = newFloats
			} else if channels == 1 {
				// Mono to Stereo
				frames := len(floats)
				newFloats := make([]float32, frames*2)
				for i := 0; i < frames; i++ {
					newFloats[i*2] = floats[i]
					newFloats[i*2+1] = floats[i]
				}
				floats = newFloats
			}

			// 4. Convert to Int16
			pcm := make([]int16, len(floats))
			for i, f := range floats {
				if f > 1.0 {
					f = 1.0
				}
				if f < -1.0 {
					f = -1.0
				}
				pcm[i] = int16(f * 32767)
			}

			// 5. Bytes
			outBytes := make([]byte, len(pcm)*2)
			for i, v := range pcm {
				outBytes[i*2] = byte(v)
				outBytes[i*2+1] = byte(v >> 8)
			}

			// Debug Wav (Disabled for performance)
			/*
				if debugWavFile != nil {
					saveAudioDebug(outBytes)
				}
			*/

			select {
			case audioBroadcast <- outBytes:
			default:
			}
		}

		// REMOVED unconditional sleep here.
		// We must process backlog as fast as possible.
		// Sleep is only for when there is NO data (handled at top of loop).

		// REMOVED runtime.Gosched() - it caused scheduling jitter and audio stutter

		if err := captureClient.ReleaseBuffer(framesAvailable); err != nil {
			log.Printf("ReleaseBuffer error: %v", err)
			break
		}
	}
	return nil
}

// Global audio connection manager
var audioConns = make(map[*websocket.Conn]bool)
var audioLock sync.Mutex

func broadcastAudio() {
	startAudioCapture()

	for packet := range audioBroadcast {
		audioLock.Lock()
		for conn := range audioConns {
			if err := conn.WriteMessage(websocket.BinaryMessage, packet); err != nil {
				conn.Close()
				delete(audioConns, conn)
			}
		}
		audioLock.Unlock()
	}
}

// Debugging
var debugWavFile *os.File
var debugWavSize int

func saveAudioDebug(data []byte) {
	if debugWavFile == nil {
		f, err := os.Create("audio_debug.wav")
		if err != nil {
			log.Printf("Failed to create wav: %v", err)
			return
		}
		debugWavFile = f
		// Write Header Placeholder (44 bytes)
		header := make([]byte, 44)
		debugWavFile.Write(header)
	}

	// Limit to ~5MB (approx 25s)
	if debugWavSize > 5*1024*1024 {
		if debugWavFile != nil {
			updateWavHeader()
			debugWavFile.Close()
			debugWavFile = nil
			log.Println("[Audio] Debug capture finished (limit reached).")
		}
		return
	}

	n, _ := debugWavFile.Write(data)
	debugWavSize += n
}

func updateWavHeader() {
	if debugWavFile == nil {
		return
	}
	// Seek to 0
	debugWavFile.Seek(0, 0)

	totalDataLen := uint32(debugWavSize)
	totalFileSize := uint32(36 + debugWavSize)

	// WAV Header (44 bytes)
	// RIFF + Size + WAVE
	header := make([]byte, 44)
	copy(header[0:], []byte("RIFF"))
	binary.LittleEndian.PutUint32(header[4:], totalFileSize)
	copy(header[8:], []byte("WAVE"))

	// fmt chunk
	copy(header[12:], []byte("fmt "))
	binary.LittleEndian.PutUint32(header[16:], 16)        // Subchunk1Size (16 for PCM)
	binary.LittleEndian.PutUint16(header[20:], 1)         // AudioFormat (1=PCM)
	binary.LittleEndian.PutUint16(header[22:], 2)         // NumChannels (Stereo)
	binary.LittleEndian.PutUint32(header[24:], 48000)     // SampleRate
	binary.LittleEndian.PutUint32(header[28:], 48000*2*2) // ByteRate
	binary.LittleEndian.PutUint16(header[32:], 4)         // BlockAlign
	binary.LittleEndian.PutUint16(header[34:], 16)        // BitsPerSample

	// data chunk
	copy(header[36:], []byte("data"))
	binary.LittleEndian.PutUint32(header[40:], totalDataLen)

	debugWavFile.Write(header)
}
