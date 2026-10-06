# SPDX-License-Identifier: GPL-3.0-or-later
"""Radio core shared by the ESP32-C5 SDR front-ends (sdr_app.py / sdr_fluent.py).

Worker is the only code that touches the serial port. Front-ends share a
config dict with it (read under cfg["lock"]) and receive results on a queue:
  {"kind": "live" | "sweep" | "log" | "error" | "connected" | "disconnected", ...}
"""
import json
import os
import queue
import threading
import time

import numpy as np
import serial

import wifi_channels as wc
from espsdr import RATES, EspSdr, SdrError, find_device
from i18n import tr

HERE = os.path.dirname(os.path.abspath(__file__))
WF_ROWS = 200
SWEEP_RATE_IDX = 0   # 80 MS/s while sweeping
# Each sweep step keeps only 10-20 MHz either side of the LO: the passband is
# flat to about ±22 MHz, and on 5 GHz the LO leaks with a phase-noise skirt of
# about ±5 MHz. With LOs every 20 MHz every frequency gets one clean look.
SWEEP_STEP_MHZ = 20
SWEEP_WIN_MHZ = (10, 20)


def spectra(x, nfft, fs, dc_remove=True):
    """Return (mean_dBFS, max_dBFS) over FFT frames of one capture."""
    k = max(1, len(x) // nfft)
    win = np.hanning(nfft).astype(np.float32)
    frames = x[:k * nfft].reshape(k, nfft)
    if dc_remove:  # per-frame, window-weighted: zeroes the DC bin despite drift
        frames = frames - (frames * win).sum(1, keepdims=True) / win.sum()
    frames = frames * win
    p = np.abs(np.fft.fftshift(np.fft.fft(frames, axis=1), axes=1)) ** 2 / (win.sum() ** 2)
    p += 1e-14
    return 10 * np.log10(p.mean(0)), 10 * np.log10(p.max(0))


# --------------------------------------------------------------------------
# Radio worker thread: the only code that touches the serial port.
# --------------------------------------------------------------------------
class Worker(threading.Thread):
    def __init__(self, port, cfg, out_q):
        super().__init__(daemon=True)
        self.port = port
        self.cfg = cfg          # shared dict, read under cfg["lock"]
        self.q = out_q
        self.stop_evt = threading.Event()
        self.applied = {}

    def emit(self, item, drop_old=True):
        if drop_old and self.q.qsize() > 60:
            keep = []
            while True:
                try:
                    m = self.q.get_nowait()
                except queue.Empty:
                    break
                if m["kind"] not in ("live", "sweep") or (m["kind"] == "sweep" and m["done"]):
                    keep.append(m)
            for m in keep:
                self.q.put(m)
        self.q.put(item)

    def snapshot(self):
        with self.cfg["lock"]:
            return {k: v for k, v in self.cfg.items() if k != "lock"}

    def apply(self, dev, key, value, fn):
        if self.applied.get(key) != value:
            fn(value)
            self.applied[key] = value

    def run(self):
        try:
            port = self.port or find_device(log=lambda m: self.emit({"kind": "log", "msg": m}, False))
            if not port:
                raise SdrError(tr("ESP-SDR を実行中のデバイスが見つかりません"))
            dev = EspSdr(port)
        except (SdrError, serial.SerialException, OSError) as e:
            self.emit({"kind": "error", "msg": str(e), "fatal": True}, False)
            return
        self.emit({"kind": "connected", "port": port, "info": dev.info, "limits": dev.limits,
                   "max_samples": dev.max_samples}, False)
        errors = 0
        sweep = None
        try:
            while not self.stop_evt.is_set():
                c = self.snapshot()
                if c["paused"]:
                    time.sleep(0.1)
                    continue
                try:
                    if c["mode"] == "live":
                        self.apply(dev, "gain", c["gain"], dev.set_gain)
                        sweep = None
                        self.apply(dev, "bw", c["bw"], dev.set_bandwidth)
                        self.apply(dev, "freq", c["freq"] + c["lo_offset"], dev.set_freq)
                        self.live_step(dev, c)
                    else:
                        # AGC would pick a different gain at every step; use one fixed gain
                        self.apply(dev, "gain", c["manual_gain"], dev.set_gain)
                        self.apply(dev, "bw", 0, dev.set_bandwidth)
                        key = (c["band"], c["nfft"], c["nsamp"], c["sweep_gen"])
                        if sweep is None or sweep["key"] != key:
                            sweep = self.new_sweep(c, key)
                        self.sweep_step(dev, c, sweep)
                    errors = 0
                except SdrError as e:
                    errors += 1
                    self.emit({"kind": "error", "msg": str(e), "fatal": False}, False)
                    if errors > 5:
                        raise
                    time.sleep(0.2)
                    dev.resync()
                    self.applied.clear()
        except (SdrError, serial.SerialException, OSError) as e:
            self.emit({"kind": "error", "msg": tr("切断: {}").format(e), "fatal": True}, False)
        finally:
            dev.close()
            self.emit({"kind": "disconnected"}, False)

    # ---- live --------------------------------------------------------
    def live_step(self, dev, c):
        t0 = time.time()
        fs = RATES[c["rate"]]
        lo = c["freq"] + c["lo_offset"]
        x = dev.capture(c["nsamp"], c["rate"])
        mean_db, max_db = spectra(x, c["nfft"], fs, c["dc_remove"])
        f = lo + np.fft.fftshift(np.fft.fftfreq(c["nfft"], 1 / fs)) / 1e6
        blk = max(1, fs // 10_000_000)  # ~0.1 us resolution
        pw = np.abs(x[:len(x) // blk * blk]) ** 2
        env = 10 * np.log10(pw.reshape(-1, blk).mean(1) + 1e-12)
        t_us = np.arange(len(env)) * blk / fs * 1e6
        a = np.abs(np.concatenate([x.real, x.imag]))
        clip = float(np.mean(a >= 127 / 128)) * 100
        rms_db = 10 * np.log10(np.mean(np.abs(x) ** 2) + 1e-12)
        if c["record"]:
            self.record(x, c, fs)
        self.emit({"kind": "live", "f": f, "mean": mean_db, "max": max_db, "t_us": t_us, "env": env,
                   "clip": clip, "rms_db": rms_db, "dt": time.time() - t0, "freq": c["freq"], "lo": lo,
                   "fs": fs, "n": len(x)})

    def record(self, x, c, fs):
        with self.cfg["lock"]:
            self.cfg["record"] = False
        d = os.path.join(HERE, "recordings")
        os.makedirs(d, exist_ok=True)
        lo = c["freq"] + c["lo_offset"]
        stem = os.path.join(d, time.strftime("iq_%Y%m%d_%H%M%S") + f"_{lo}MHz_{fs // 1_000_000}Msps")
        np.save(stem + ".npy", x)
        x.astype(np.complex64).tofile(stem + ".cfile")  # GNU Radio / inspectrum compatible
        with open(stem + ".json", "w") as fp:
            json.dump({"center_mhz": lo, "sample_rate": fs, "samples": len(x),
                       "gain": "AGC" if c["gain"] is None else c["gain"], "bandwidth_mhz": c["bw"],
                       "format": "complex64, full scale 1.0", "time": time.strftime("%Y-%m-%d %H:%M:%S")},
                      fp, indent=1)
        self.emit({"kind": "log", "msg": tr("保存: {}.npy / .cfile").format(stem)}, False)

    # ---- sweep -------------------------------------------------------
    def new_sweep(self, c, key):
        fa, fb = wc.BANDS[c["band"]]
        fs = RATES[SWEEP_RATE_IDX]
        df = fs / c["nfft"] / 1e6
        los = list(range(fa - SWEEP_WIN_MHZ[0], fb + SWEEP_WIN_MHZ[0] + 1, SWEEP_STEP_MHZ))
        f_rel = np.fft.fftshift(np.fft.fftfreq(c["nfft"], 1 / fs)) / 1e6
        sel = (np.abs(f_rel) >= SWEEP_WIN_MHZ[0]) & (np.abs(f_rel) <= SWEEP_WIN_MHZ[1])
        grid = np.arange(fa, fb + df / 2, df)
        return {"key": key, "los": los, "i": 0, "f_rel": f_rel, "sel": sel, "f": grid, "df": df,
                "sum": np.zeros(len(grid)), "cnt": np.zeros(len(grid)), "max": np.full(len(grid), np.nan),
                "t0": time.time()}

    def sweep_step(self, dev, c, s):
        lo = s["los"][s["i"]]
        dev.set_freq(lo)
        self.applied["freq"] = lo
        x = dev.capture(c["nsamp"], SWEEP_RATE_IDX)
        mean_db, max_db = spectra(x, c["nfft"], RATES[SWEEP_RATE_IDX], True)
        idx = np.rint((lo + s["f_rel"][s["sel"]] - s["f"][0]) / s["df"]).astype(int)
        ok = (idx >= 0) & (idx < len(s["f"]))
        idx = idx[ok]
        np.add.at(s["sum"], idx, 10 ** (mean_db[s["sel"]][ok] / 10))
        np.add.at(s["cnt"], idx, 1)
        s["max"][idx] = np.fmax(s["max"][idx], max_db[s["sel"]][ok])
        s["i"] += 1
        done = s["i"] >= len(s["los"])
        with np.errstate(divide="ignore", invalid="ignore"):
            mean = np.where(s["cnt"] > 0, 10 * np.log10(s["sum"] / np.maximum(s["cnt"], 1)), np.nan)
        self.emit({"kind": "sweep", "f": s["f"], "mean": mean, "max": s["max"].copy(), "done": done,
                   "lo": lo, "band": c["band"], "step": s["i"], "steps": len(s["los"]),
                   "sweep_s": time.time() - s["t0"]}, drop_old=not done)
        if done:
            s.update(i=0, t0=time.time(), sum=np.zeros(len(s["f"])), cnt=np.zeros(len(s["f"])),
                     max=np.full(len(s["f"]), np.nan))


def default_cfg():
    return {"lock": threading.Lock(), "mode": "live", "band": "2.4 GHz", "freq": 2437,
            "rate": 0, "nsamp": 4096, "nfft": 512, "bw": 0, "gain": 60, "dc_remove": True,
            "paused": False, "record": False, "sweep_gen": 0, "lo_offset": 0, "manual_gain": 60}


def channel_activity(band, f, mean, mx):
    """Per 20 MHz channel: peak above the sweep's noise floor (dB)."""
    floor = np.nanpercentile(mean, 20)
    chans = wc.channels_20(band)
    act = []
    for _c, fc in chans:
        sel = np.abs(f - fc) <= 8
        act.append(np.nanmax(mx[sel]) - floor if sel.any() else 0.0)
    return chans, np.array(act)
