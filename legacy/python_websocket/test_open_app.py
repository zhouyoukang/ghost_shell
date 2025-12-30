import requests
import time
import json

URL = "http://localhost:8000/interact"
APP_NAME = "notepad"

print(f"Testing open_app with '{APP_NAME}'...")

try:
    response = requests.post(URL, json={"action": "open_app", "text": APP_NAME, "x": 0, "y": 0})
    print("Status Code:", response.status_code)
    try:
        print("Response:", response.json())
    except:
        print("Raw Response:", response.text)
        
    print(f"Waiting 5 seconds to see if '{APP_NAME}' launches...")
    time.sleep(5)
    print("Test complete. Please check if Notepad opened.")
except Exception as e:
    print("Request failed:", e)
    print("Is the server (ghost_server.py) running?")
