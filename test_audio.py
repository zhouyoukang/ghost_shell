#!/usr/bin/env python
"""Test audio capture availability and list loopback devices."""

import pyaudiowpatch as pyaudio

p = pyaudio.PyAudio()

print("=== WASAPI Audio Devices ===\n")

try:
    wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    print(f"WASAPI Host API Index: {wasapi_info['index']}")
    print(f"Default Output Device Index: {wasapi_info['defaultOutputDevice']}")
    print(f"Default Input Device Index: {wasapi_info['defaultInputDevice']}")
    print()
    
    default_out = p.get_device_info_by_index(wasapi_info['defaultOutputDevice'])
    print(f"Default Output: {default_out['name']}")
    print(f"  - Sample Rate: {default_out['defaultSampleRate']}")
    print(f"  - Channels: {default_out['maxOutputChannels']}")
    print(f"  - Is Loopback: {default_out.get('isLoopbackDevice', False)}")
    print()
    
    print("=== All Loopback Devices ===\n")
    found_loopback = False
    for i in range(p.get_device_count()):
        dev = p.get_device_info_by_index(i)
        if dev.get('isLoopbackDevice', False):
            found_loopback = True
            print(f"[{i}] {dev['name']}")
            print(f"      Channels: {dev['maxInputChannels']}, Rate: {dev['defaultSampleRate']}")
    
    if not found_loopback:
        print("No loopback devices found! This may indicate pyaudiowpatch is not working properly.")
        print("\nAll WASAPI devices:")
        for i in range(p.get_device_count()):
            dev = p.get_device_info_by_index(i)
            if dev.get('hostApi') == wasapi_info['index']:
                print(f"[{i}] {dev['name']} (in={dev['maxInputChannels']}, out={dev['maxOutputChannels']})")
                
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

finally:
    p.terminate()
