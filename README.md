# ESP32 WiFi System — Pre-Final Project Lab

WiFi-enabled ESP32 (MicroPython) with a state machine, local status on OLED, HTTP server, and failure detection/recovery.

---
## Part A — Requirements & Planning [5 pts]

### 1. Inputs and outputs

| Type | Item | Description |
|------|------|-------------|
| **Input** | WiFi credentials | SSID and password (in code or config) |
| **Input** | HTTP requests | GET to `/`, `/status`, `/oledOn`, `/oledOff`, `/scan` |
| **Output** | OLED display | Current state, IP, retry count, scan results |
| **Output** | HTTP responses | 200 OK with optional OLED updates |

### 2. Measurable success criteria

1. **Connection time**: System reaches OPERATIONAL (WiFi connected, server bound) within 20 seconds from power-on in at least 4 out of 5 trials under stable WiFi.
2. **Recovery**: After WiFi is lost (e.g., hotspot turned off), system enters ERROR_RECOVERY and returns to OPERATIONAL within 60 seconds after WiFi is restored, using backoff (no infinite loop).
3. **Network action**: HTTP server responds to GET requests on port 80; at least one endpoint (e.g. `/status`) returns 200 and updates or reflects system state.

### 3. Possible failure cases

1. **WiFi unavailable or wrong credentials**: Router off, wrong SSID/password, or out of range. System stays in CONNECTING until timeout, then goes to ERROR_RECOVERY and retries with backoff.
2. **WiFi drops during operation**: Hotspot turned off or link lost. Main loop detects `not wifi.isconnected()`, closes the server, transitions to ERROR_RECOVERY, then retries with backoff and re-enters CONNECTING.

---

## Part B — State Machine Design [7 pts]

### States

- **Boot** — Hardware init, OLED init, short delay.
- **Connecting** — Attempt WiFi connect with timeout; on success → Operational; on timeout → Error/Recovery.
- **Operational** — HTTP server running; periodic check of WiFi; on disconnect → Error/Recovery.
- **Error/Recovery** — Backoff delay, then disconnect and transition to Connecting.

### Labeled state diagram

```
                    +----------+
                    |   BOOT   |
                    +----+-----+
                         | (init done)
                         v
                    +------------+
              +---->| CONNECTING |<----+
              |     +-----+------+     |
              |           |            | (retry after backoff)
              | (timeout) | (connected)|
              |           v            |
              |     +-------------+    |
              |     | OPERATIONAL |    |
              |     +------+------+    |
              |            |           |
              | (WiFi lost)|           |
              |            v           |
              |     +----------------+ |
              +-----| ERROR_RECOVERY |-+
                    +----------------+
                         (backoff, then retry)
```

### Transition table

| Current state    | Event/Condition     | Next state     | Action |
|------------------|--------------------|----------------|--------|
| Boot             | Init complete      | Connecting     | Start WiFi connect |
| Connecting       | Connected          | Operational    | Reset retry count, start server |
| Connecting       | Timeout            | Error_Recovery | Set backoff |
| Operational      | WiFi disconnected  | Error_Recovery | Close server, set backoff |
| Error_Recovery   | Backoff elapsed    | Connecting     | Disconnect WiFi, then connect |

---

## Part C — Implementation [10 pts]

## Setup instructions

### Hardware

- ESP32 dev board.
- OLED (SSD1306, I2C): SDA → GPIO 22, SCL → GPIO 21, VCC → 3.3 V, GND → GND.

### Software

Edit `main.py`: set `WIFI_SSID` and `WIFI_PASSWORD` to your network.

### HTTP endpoints

- `http://<ESP32_IP>/` — 200 OK.
- `http://<ESP32_IP>/status` or `/oledOn` — show state and IP on OLED.
- `http://<ESP32_IP>/oledOff` — clear OLED.
- `http://<ESP32_IP>/scan` — show up to 5 scanned SSIDs on OLED.

---

## Part D — Testing & Evidence [5 pts]

### 1. Connection time (min. 5 trials)


| Trial | Time to OPERATIONAL (s) |
|-------|-------------------------|
| 1     |                        |
| 2     |                        |
| 3     |                        |
| 4     |                        |
| 5     |                        |

### 2. Simulate one failure (e.g. turn hotspot off)

- 

### 3. Record recovery behavior

- 
