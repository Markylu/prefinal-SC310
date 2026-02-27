import network
import time
import socket
import ssd1306
from machine import Pin, I2C

# --- Config ---
WIFI_SSID = "Phone"
WIFI_PASSWORD = "aaaaaaaa"
WIFI_CONNECT_TIMEOUT_MS = 15000
CONNECT_RETRY_MAX = 5
BACKOFF_BASE_SEC = 2
BACKOFF_MAX_SEC = 16
I2C_SCL = 21
I2C_SDA = 22

# --- States ---
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

    def transition_to(self, new_state):
        self.state = new_state

    def get_backoff_delay(self):
        # exponentially increasing delay
        delay = min(BACKOFF_BASE_SEC * (2 ** self.retry_count), BACKOFF_MAX_SEC)
        self.retry_count = min(self.retry_count + 1, 10)
        return delay

    def reset_retry(self):
        self.retry_count = 0
        self.backoff_sec = BACKOFF_BASE_SEC


def init_hardware():
    """Hardware setup"""
    i2c = I2C(scl=Pin(I2C_SCL), sda=Pin(I2C_SDA))
    oled = None
    try:
        addrs = i2c.scan()
        if addrs:
            oled = ssd1306.SSD1306_I2C(128, 64, i2c)
    except Exception:
        pass
    return oled


def update_oled(oled, sm, wifi):
    """Display current state and IP on OLED."""
    if oled is None:
        return
    oled.fill(0)
    oled.text("State: " + sm.state[:12], 0, 0)
    if wifi and wifi.isconnected():
        oled.text("IP: " + wifi.ifconfig()[0], 0, 16)
        oled.text("OK", 0, 32)
    elif sm.state == STATE_ERROR_RECOVERY:
        oled.text("Retry #" + str(sm.retry_count), 0, 16)
        oled.text("Recovering...", 0, 32)
    elif sm.state == STATE_CONNECTING:
        oled.text("Connecting...", 0, 16)
    oled.show()


def try_wifi_connect(wifi):
    """try to make a connection within the timeout time"""
    wifi.connect(WIFI_SSID, WIFI_PASSWORD)
    deadline = time.ticks_add(time.ticks_ms(), WIFI_CONNECT_TIMEOUT_MS)
    while not wifi.isconnected():
        if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
            return False
        time.sleep(0.5)
    return True


def run_http_server(wifi, oled, sm):
    """Run HTTP server. Returns after handling one request."""
    addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    server = socket.socket()
    # this fixes the "Address already in use" error
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(addr)
    server.listen(1)
    # this fixes the "TimeoutError: no connection" error
    server.settimeout(1.0)

    recovery_check_interval_ms = 3000
    last_wifi_check = time.ticks_ms()
    oled_freeze_until_ms = 0  # freeze display after /scan so results stay visible

    while True:
        # Periodic WiFi check
        now = time.ticks_ms()
        if time.ticks_diff(now, last_wifi_check) >= recovery_check_interval_ms:
            last_wifi_check = now
            if not wifi.isconnected():
                server.close()
                sm.transition_to(STATE_ERROR_RECOVERY)
                return

        if time.ticks_diff(now, oled_freeze_until_ms) >= 0:
            update_oled(oled, sm, wifi)

        try:
            cl, addr = server.accept()
        except OSError:
            continue

        try:
            request = cl.recv(1024)
            response = "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nOK\r\n"
            if oled:
                if b"/oledOn" in request or b"/status" in request:
                    oled_freeze_until_ms = 0
                    oled.fill(0)
                    oled.text("State: " + sm.state, 0, 0)
                    oled.text("IP: " + wifi.ifconfig()[0], 0, 16)
                    oled.text("OK", 0, 32)
                    oled.show()
                elif b"/oledOff" in request:
                    oled_freeze_until_ms = 0
                    oled.fill(0)
                    oled.show()
                elif b"/scan" in request:
                    networks = wifi.scan()
                    oled.fill(0)
                    oled.text(str(networks[0][0])[2:-1],0,0)
                    oled.text(str(networks[1][0])[2:-1],0,10)
                    oled.text(str(networks[2][0])[2:-1],0,20)
                    oled.text(str(networks[3][0])[2:-1],0,30)
                    oled.text(str(networks[4][0])[2:-1],0,40)
                    oled.show()
                    oled_freeze_until_ms = time.ticks_add(time.ticks_ms(), 10000)
            cl.send(response)
        finally:
            cl.close()


def main():
    sm = StateMachine()
    oled = init_hardware()

    wifi = network.WLAN(network.STA_IF)
    wifi.active(True)

    while True:
        if sm.state == STATE_BOOT:
            update_oled(oled, sm, wifi)
            if not sm.boot_done:
                time.sleep(2)
                sm.boot_done = True
            sm.transition_to(STATE_CONNECTING)

        elif sm.state == STATE_CONNECTING:
            update_oled(oled, sm, wifi)
            if try_wifi_connect(wifi):
                sm.reset_retry()
                sm.transition_to(STATE_OPERATIONAL)
            else:
                sm.transition_to(STATE_ERROR_RECOVERY)

        elif sm.state == STATE_OPERATIONAL:
            run_http_server(wifi, oled, sm)
            # should end here,but if we return we entered ERROR_RECOVERY

        elif sm.state == STATE_ERROR_RECOVERY:
            update_oled(oled, sm, wifi)
            delay = sm.get_backoff_delay()
            print("Recovery: retry in", delay, "s")
            time.sleep(delay)
            wifi.disconnect()
            time.sleep(0.5)
            sm.transition_to(STATE_CONNECTING)


if __name__ == "__main__":
    main()
