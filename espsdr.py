# SPDX-License-Identifier: GPL-3.0-or-later
"""ESP-SDR (ESPARGOS) burst protocol driver for ESP32-C5.

Protocol reference: https://github.com/ESPARGOS/esp-sdr (README, docs/rx-controls.md)
Newline-terminated ASCII commands, text replies; CAP16 replies carry a
binary payload of signed 8-bit I/Q (I first, then Q) verified by CRC32.
"""
import json
import time
import zlib

import numpy as np
import serial
import serial.tools.list_ports

# Rate index -> nominal sample rate (Hz). The C5 supports indices 0-5.
RATES = {0: 80_000_000, 1: 40_000_000, 2: 20_000_000, 3: 10_000_000, 4: 8_000_000, 5: 4_000_000}

# USB VIDs commonly used by ESP32 dev boards (Espressif native USB, WCH, Silabs, FTDI).
_LIKELY_VIDS = {0x303A, 0x1A86, 0x10C4, 0x0403}


class SdrError(Exception):
    pass


class EspSdr:
    def __init__(self, port, baud=2_000_000, timeout=2.0):
        self.ser = serial.Serial()
        self.ser.port = port
        self.ser.baudrate = baud
        self.ser.timeout = timeout
        # Keep RTS deasserted so opening the port does not reset the board.
        # Native USB Serial/JTAG only transmits once the host asserts DTR;
        # on a USB-UART bridge DTR stays low so the auto-reset circuit is idle.
        self.native_usb = any(p.device == port and p.vid == 0x303A
                              for p in serial.tools.list_ports.comports())
        self.ser.dtr = self.native_usb
        self.ser.rts = False
        self.ser.open()
        self._nonce = int(time.time()) & 0xFFFFFF
        self.resync()
        self.info = self.cmd("INFO")
        if "SDR" not in self.info:
            self.close()
            raise SdrError(f"{port}: ESP-SDR firmware not found (INFO -> {self.info!r})")
        self.caps = set(self.cmd("CAPS").split()[1:])
        lim = self.cmd("LIMITS?")
        self.limits = json.loads(lim.split(" ", 1)[1]) if lim.startswith("LIMITS") else {}
        parts = self.info.split()
        self.max_samples = int(parts[3]) if len(parts) >= 4 and parts[3].isdigit() else 16380
        self.freq_mhz = None

    # ---- low level -------------------------------------------------------
    def close(self):
        try:
            if self.ser.is_open:
                self.ser.write(b"RELEASE\n")
                self.ser.flush()
                time.sleep(0.05)
                self.ser.close()
        except serial.SerialException:
            pass

    def _readline(self):
        line = self.ser.readline()
        if not line.endswith(b"\n"):
            raise SdrError("timeout waiting for reply")
        return line.decode(errors="replace").strip()

    def resync(self):
        """Discard any partial transfer and realign on a SYNC echo."""
        # A previous client that died mid-transfer can leave the device blocked
        # on output for a few seconds, so keep draining and re-sending SYNC.
        old_timeout = self.ser.timeout
        self.ser.timeout = 0.2
        try:
            for _ in range(8):
                self.ser.reset_input_buffer()
                self._nonce += 1
                want = f"SYNC {self._nonce}".encode()
                self.ser.write(want + b"\n")
                deadline = time.time() + 1.0
                buf = b""
                while time.time() < deadline:
                    buf += self.ser.read(self.ser.in_waiting or 1)
                    if want + b"\n" in buf:
                        return
                    buf = buf[-64:]
        finally:
            self.ser.timeout = old_timeout
        raise SdrError("no SYNC reply (wrong port or firmware?)")

    def cmd(self, text):
        self.ser.write((text + "\n").encode())
        reply = self._readline()
        if reply.startswith("ERR"):
            raise SdrError(f"{text}: {reply}")
        return reply

    # ---- receiver controls ----------------------------------------------
    def set_freq(self, mhz):
        mhz = int(round(mhz))
        self.cmd(f"FREQ {mhz}")
        self.freq_mhz = mhz

    def set_bandwidth(self, mhz):
        """Analog bandwidth in MHz (C5: 11-48). 0 selects the widest setting."""
        self.cmd(f"BANDWIDTH {int(mhz)}")

    def set_gain(self, index=None):
        """None -> hardware AGC, otherwise manual PHY gain index (0-80 on C5)."""
        self.cmd("GAIN HARDWARE" if index is None else f"GAIN MANUAL {int(index)}")

    def capture(self, n, rate_idx=0):
        """Return n complex samples (complex64, full scale = 1.0)."""
        n = max(256, min(int(n), self.max_samples))
        self.ser.write(f"CAP16 {n} {rate_idx}\n".encode())
        header = self._readline()
        if not header.startswith("DATA"):
            raise SdrError(f"capture: {header}")
        _, count, crc, _elapsed = header.split()
        count = int(count)
        payload = self.ser.read(count * 2)
        if len(payload) != count * 2:
            raise SdrError("capture: short payload")
        if zlib.crc32(payload) != int(crc, 16):
            raise SdrError("capture: CRC mismatch")
        iq = np.frombuffer(payload, dtype=np.int8).astype(np.float32) / 128.0
        return (iq[0::2] + 1j * iq[1::2]).astype(np.complex64)


def find_device(log=print):
    """Probe likely serial ports and return the first one running ESP-SDR."""
    ports = sorted(serial.tools.list_ports.comports(), key=lambda p: (p.vid not in _LIKELY_VIDS, p.device))
    for p in ports:
        if p.vid is not None and p.vid not in _LIKELY_VIDS:
            continue
        try:
            dev = EspSdr(p.device, timeout=0.5)
            info = dev.info
            dev.close()
            log(f"{p.device}: {info}")
            return p.device
        except (SdrError, serial.SerialException, OSError, ValueError) as e:
            log(f"{p.device}: {e}")
    return None
