from machine import Pin, I2C
import network
import ssd1306
import time
import socket
try:
    import ujson as json
except ImportError:
    import json

WIFI_SSID = "Phone"
WIFI_PASSWORD = "aaaaaaaa"
WIFI_CONNECT_TIMEOUT_MS = 15000
CONNECT_RETRY_MAX = 5
BACKOFF_BASE_SEC = 2
BACKOFF_MAX_SEC = 16
I2C_SCL = 21
I2C_SDA = 22
BAZAAR_API_URL = "https://api.hypixel.net/skyblock/bazaar"
BAZAAR_FREEZE_MS = 10000
HTTP_PORT = 80

STATE_BOOT = "BOOT"
STATE_CONNECTING = "CONNECTING"
STATE_OPERATIONAL = "OPERATIONAL"
STATE_ERROR_RECOVERY = "ERROR_RECOVERY"


class StateMachine:
    def __init__(self):
        self.state = STATE_BOOT
        self.retry_count = 0
        self.backoff_sec = BACKOFF_BASE_SEC
        self.boot_done = False
        self.connect_start_ms = None
        self.connect_time_logged = False

    def transition_to(self, new_state):
        self.state = new_state

    def get_backoff_delay(self):
        delay = min(BACKOFF_BASE_SEC * (2 ** self.retry_count), BACKOFF_MAX_SEC)
        self.retry_count = min(self.retry_count + 1, 10)
        return delay

    def reset_retry(self):
        self.retry_count = 0
        self.backoff_sec = BACKOFF_BASE_SEC


class OledManager:
    def __init__(self, oled):
        self.oled = oled
        self._lines = []

    def _draw(self):
        if self.oled is None:
            return
        self.oled.fill(0)
        for i, line in enumerate(self._lines):
            if i * 8 < 64:
                self.oled.text(line[:21], 0, i * 8)
        self.oled.show()

    def show_state(self, sm, wifi):
        self._lines = ["State: " + sm.state[:12]]
        if wifi and getattr(wifi, "isconnected", lambda: False)():
            self._lines.append("IP: " + (wifi.ifconfig()[0] if hasattr(wifi, "ifconfig") else "?"))
            self._lines.append("OK")
        elif sm.state == STATE_ERROR_RECOVERY:
            self._lines.append("Retry #" + str(sm.retry_count))
            self._lines.append("Recovering...")
        elif sm.state == STATE_CONNECTING:
            self._lines.append("Connecting...")
        self._draw()

    def show_bazaar(self, item_id, buy_price, sell_price, sell_volume, buy_volume):
        self._lines = [
            item_id[:20] if len(item_id) > 20 else item_id,
            "Buy: " + str(round(buy_price, 2)),
            "Sell: " + str(round(sell_price, 2)),
            "Vol: " + str(sell_volume) + "/" + str(buy_volume),
        ]
        self._draw()

    def show_bazaar_error(self, msg):
        self._lines = [msg[:20], ""]
        self._draw()


def init_hardware():
    i2c = I2C(scl=Pin(I2C_SCL), sda=Pin(I2C_SDA))
    oled = None
    try:
        addrs = i2c.scan()
        if addrs:
            oled = ssd1306.SSD1306_I2C(128, 64, i2c)
    except Exception:
        pass
    return oled


def try_wifi_connect(wifi):
    wifi.connect(WIFI_SSID, WIFI_PASSWORD)
    deadline = time.ticks_add(time.ticks_ms(), WIFI_CONNECT_TIMEOUT_MS)
    while not wifi.isconnected():
        if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
            return False
        time.sleep(0.5)
    return True


def fetch_bazaar_item(item_id):
    item_id = item_id.replace(" ", "_").upper().strip()
    if not item_id:
        return None

    try:
        import ssl
        ai = socket.getaddrinfo("api.hypixel.net", 443, socket.AF_INET)
        addr = ai[0][-1]
        s = socket.socket()
        s.settimeout(20)
        s.connect(addr)
        s = ssl.wrap_socket(s)
        req = b"GET /skyblock/bazaar HTTP/1.1\r\nHost: api.hypixel.net\r\nConnection: close\r\n\r\n"
        s.write(req)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = s.read(256)
            if not chunk:
                s.close()
                return None
            buf += chunk
            if len(buf) > 8192:
                s.close()
                return None
        head, body_start = buf.split(b"\r\n\r\n", 1)
        needle = ('"' + item_id + '":').encode("ascii")
        collected = bytearray(body_start)
        while True:
            chunk = s.read(512)
            if not chunk:
                break
            collected.extend(chunk)
            if len(collected) > 64 * 1024:
                break
        s.close()
        raw = bytes(collected)
        idx = raw.find(needle)
        if idx < 0:
            return None
        start = raw.find(b"{", idx)
        if start < 0:
            return None
        depth = 1
        i = start + 1
        while i < len(raw) and depth > 0:
            if raw[i:i + 1] == b"{":
                depth += 1
            elif raw[i:i + 1] == b"}":
                depth -= 1
            i += 1
        if depth != 0:
            return None
        obj_str = raw[start:i].decode("utf-8", "replace")
        prod = json.loads(obj_str)
        qs = prod.get("quick_status") or {}
        return {
            "product_id": item_id,
            "buyPrice": qs.get("buyPrice", 0),
            "sellPrice": qs.get("sellPrice", 0),
            "sellVolume": qs.get("sellVolume", qs.get("sellMovingWeek", 0)),
            "buyVolume": qs.get("buyVolume", qs.get("buyMovingWeek", 0)),
        }
    except Exception:
        return None


def parse_request_path(request):
    if not request:
        return None
    if isinstance(request, bytes):
        lines = request.split(b"\r\n") if b"\r\n" in request else request.split(b"\n")
    else:
        lines = request.split("\r\n") if "\r\n" in request else request.split("\n")
    if not lines:
        return None
    first = lines[0] if isinstance(lines[0], str) else lines[0].decode("utf-8", "replace")
    parts = first.split(None, 2)
    if len(parts) < 2:
        return None
    return parts[1].split("?")[0].strip()


def run_http_server(wifi, oled_manager, sm):
    addr = socket.getaddrinfo("0.0.0.0", HTTP_PORT)[0][-1]
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(addr)
    server.listen(1)
    server.settimeout(1.0)

    recovery_check_interval_ms = 3000
    last_wifi_check = time.ticks_ms()
    oled_freeze_until_ms = 0

    while True:
        now = time.ticks_ms()
        if time.ticks_diff(now, last_wifi_check) >= recovery_check_interval_ms:
            last_wifi_check = now
            if not wifi.isconnected():
                server.close()
                sm.transition_to(STATE_ERROR_RECOVERY)
                return

        if time.ticks_diff(now, oled_freeze_until_ms) >= 0:
            oled_manager.show_state(sm, wifi)

        try:
            cl, _ = server.accept()
        except OSError:
            continue

        try:
            request = cl.recv(1024)
            path = parse_request_path(request)
            response = "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nOK\r\n"

            if path and path.startswith("/item/"):
                item_id = path[6:].strip().replace(" ", "_").upper()
                if item_id:
                    oled_manager.show_state(sm, wifi)
                    data = fetch_bazaar_item(item_id)
                    if data:
                        oled_manager.show_bazaar(
                            data["product_id"],
                            data["buyPrice"],
                            data["sellPrice"],
                            data["sellVolume"],
                            data["buyVolume"],
                        )
                        oled_freeze_until_ms = time.ticks_add(time.ticks_ms(), BAZAAR_FREEZE_MS)
                        response = "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nBuy: %s Sell: %s\r\n" % (
                            data["sellPrice"], data["buyPrice"]
                        )
                    else:
                        oled_manager.show_bazaar_error("Item not found")
                        oled_freeze_until_ms = time.ticks_add(time.ticks_ms(), 3000)
                        response = "HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\n\r\nItem not found\r\n"
                else:
                    response = "HTTP/1.1 400 Bad Request\r\nContent-Type: text/plain\r\n\r\nBad item id\r\n"

            elif oled_manager.oled is not None and (b"/status" in request):
                oled_freeze_until_ms = 0
                oled_manager.show_state(sm, wifi)

            cl.send(response)
        finally:
            cl.close()


def main():
    sm = StateMachine()
    oled = init_hardware()
    oled_manager = OledManager(oled)
    wifi = network.WLAN(network.STA_IF)
    wifi.active(True)

    sm.connect_start_ms = time.ticks_ms()

    while True:
        if sm.state == STATE_BOOT:
            oled_manager.show_state(sm, wifi)
            if not sm.boot_done:
                time.sleep(2)
                sm.boot_done = True
            sm.transition_to(STATE_CONNECTING)

        elif sm.state == STATE_CONNECTING:
            oled_manager.show_state(sm, wifi)
            if try_wifi_connect(wifi):
                if not sm.connect_time_logged and sm.connect_start_ms is not None:
                    elapsed_ms = time.ticks_diff(time.ticks_ms(), sm.connect_start_ms)
                    print("Connected in", elapsed_ms / 1000, "seconds")
                    sm.connect_time_logged = True
                sm.reset_retry()
                sm.transition_to(STATE_OPERATIONAL)
            else:
                sm.transition_to(STATE_ERROR_RECOVERY)

        elif sm.state == STATE_OPERATIONAL:
            run_http_server(wifi, oled_manager, sm)

        elif sm.state == STATE_ERROR_RECOVERY:
            oled_manager.show_state(sm, wifi)
            delay = sm.get_backoff_delay()
            print("Recovery: retry in", delay, "s")
            time.sleep(delay)
            wifi.disconnect()
            time.sleep(0.5)
            sm.transition_to(STATE_CONNECTING)


if __name__ == "__main__":
    main()