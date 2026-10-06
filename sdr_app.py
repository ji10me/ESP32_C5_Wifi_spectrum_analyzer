# SPDX-License-Identifier: GPL-3.0-or-later
"""ESP32-C5 Wi-Fi SDR viewer (2.4 GHz / 5 GHz).

Uses the ESPARGOS ESP-SDR firmware (https://github.com/ESPARGOS/esp-sdr) to
capture raw I/Q bursts from the ESP32-C5 radio and shows:
  * Live mode   : spectrum, waterfall and time-domain power (packet bursts)
  * Sweep mode  : stitched panorama of the whole 2.4 GHz or 5 GHz Wi-Fi band
                  with per-channel activity
Run:  python sdr_app.py [COMx] [--freq MHz] [--sweep 2.4|5] [--lang ja|en]
"""
import argparse
import queue
import time
import tkinter as tk
from tkinter import messagebox, ttk

import matplotlib
import numpy as np
import serial.tools.list_ports

matplotlib.use("TkAgg")
from matplotlib import font_manager  # noqa: E402
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

import i18n  # noqa: E402
import wifi_channels as wc  # noqa: E402
from espsdr import RATES  # noqa: E402
from i18n import bind, tr  # noqa: E402
from sdr_core import SWEEP_STEP_MHZ, WF_ROWS, Worker, channel_activity, default_cfg  # noqa: E402

for _f in ("Yu Gothic", "Meiryo", "MS Gothic", "Noto Sans CJK JP"):
    if any(f.name == _f for f in font_manager.fontManager.ttflist):
        matplotlib.rcParams["font.family"] = _f
        break
matplotlib.rcParams["axes.unicode_minus"] = False


def labelled(widget, text):
    """Give a Tk widget a text that follows the UI language."""
    bind(lambda t: widget.config(text=t), text)
    return widget


def is_text(value, key):
    """True if value is key shown in any UI language (combobox values hold display text)."""
    return value in (key, i18n.EN.get(key, key))

# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------
class App:
    def __init__(self, root, port=None):
        self.root = root
        root.title("ESP32-C5 Wi-Fi SDR  (2.4 / 5 GHz)")
        root.geometry("1400x900")
        self.q = queue.Queue()
        self.worker = None
        self.cfg = default_cfg()
        self.avg = None
        self.peak = None
        self.wf = None
        self.wf_key = None
        self.occ_hits = None
        self.occ_sweeps = 0
        self.overlay_key = None
        self.overlay_artists = []
        self.fps_t = []
        self.cap_t = []
        self.bg = None
        self.need_full = True
        self.sel_width = 20
        self.auto_frames = 6  # autoscale after a few frames
        self.conn_state = "idle"
        self.status_key = "未接続"
        self.last_m = self.last_info_m = None
        self._build_controls(port)
        self._build_plots()
        i18n.on_change(self.retranslate)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(30, self.poll)

    # ---- config helpers ------------------------------------------------
    def set_cfg(self, **kw):
        with self.cfg["lock"]:
            self.cfg.update(kw)

    def get_cfg(self, k):
        with self.cfg["lock"]:
            return self.cfg[k]

    # ---- controls -------------------------------------------------------
    def _build_controls(self, port):
        p = ttk.Frame(self.root, padding=6)
        p.pack(side=tk.LEFT, fill=tk.Y)
        self.panel = p

        def section(title):
            lf = labelled(ttk.LabelFrame(p, padding=4), title)
            lf.pack(fill=tk.X, pady=3)
            return lf

        s = section("接続")
        self.ports = [x.device for x in serial.tools.list_ports.comports()]
        self.port_var = tk.StringVar(value=port or tr("自動検出"))
        self.port_box = ttk.Combobox(s, textvariable=self.port_var, values=[tr("自動検出")] + self.ports, width=14)
        self.port_box.grid(row=0, column=0)
        self.conn_btn = ttk.Button(s, text=tr("接続"), command=self.toggle_connect)
        self.conn_btn.grid(row=0, column=1, padx=3)
        self.status = ttk.Label(s, text=tr("未接続"), wraplength=230, foreground="gray")
        self.status.grid(row=1, column=0, columnspan=2, sticky="w")

        s = section("モード")
        self.mode_var = tk.StringVar(value="live")
        labelled(ttk.Radiobutton(s, value="live", variable=self.mode_var, command=self.on_mode),
                 "ライブ (中心周波数固定)").pack(anchor="w")
        labelled(ttk.Radiobutton(s, value="sweep", variable=self.mode_var, command=self.on_mode),
                 "バンドスイープ (帯域全体)").pack(anchor="w")

        s = section("バンド / Wi-Fi チャンネル")
        self.band_var = tk.StringVar(value="2.4 GHz")
        f = ttk.Frame(s)
        f.pack(fill=tk.X)
        for b in wc.BANDS:
            ttk.Radiobutton(f, text=b, value=b, variable=self.band_var, command=self.on_band).pack(side=tk.LEFT)
        self.ch_var = tk.StringVar()
        self.ch_box = ttk.Combobox(s, textvariable=self.ch_var, width=34, state="readonly")
        self.ch_box.pack(fill=tk.X, pady=2)
        self.ch_box.bind("<<ComboboxSelected>>", self.on_channel)
        f = ttk.Frame(s)
        f.pack(fill=tk.X)
        labelled(ttk.Label(f), "中心 [MHz]").pack(side=tk.LEFT)
        self.freq_var = tk.StringVar(value="2437")
        e = ttk.Entry(f, textvariable=self.freq_var, width=7)
        e.pack(side=tk.LEFT, padx=2)
        e.bind("<Return>", lambda _e: self.on_freq())
        for d in (-5, -1, 1, 5):
            ttk.Button(f, text=f"{d:+d}", width=3, command=lambda d=d: self.nudge(d)).pack(side=tk.LEFT)

        s = section("受信設定")
        g = ttk.Frame(s)
        g.pack(fill=tk.X)
        self.rate_var = tk.StringVar(value="80 MS/s")
        self.nsamp_var = tk.StringVar(value="4096")
        self.nfft_var = tk.StringVar(value="512")
        self.bw_var = tk.StringVar(value=tr("最大"))
        self.lo_var = tk.StringVar(value="0")
        rows = [("サンプルレート", self.rate_var, [f"{v // 1_000_000} MS/s" for v in RATES.values()]),
                ("サンプル数/取得", self.nsamp_var, ["2048", "4096", "8192", "16380"]),
                ("FFT サイズ", self.nfft_var, ["256", "512", "1024", "2048"]),
                ("アナログ帯域 [MHz]", self.bw_var, [tr("最大"), "11", "16", "20", "24", "30", "40", "48"]),
                ("LO オフセット [MHz]", self.lo_var, ["0", "+12", "-12", "+16", "-16"])]
        for i, (lab, var, vals) in enumerate(rows):
            labelled(ttk.Label(g), lab).grid(row=i, column=0, sticky="w")
            cb = ttk.Combobox(g, textvariable=var, values=vals, width=9, state="readonly")
            if var is self.bw_var:
                self.bw_box = cb
            cb.grid(row=i, column=1, sticky="w", pady=1)
            cb.bind("<<ComboboxSelected>>", lambda _e: self.on_rx())
        self.agc_var = tk.BooleanVar(value=False)
        labelled(ttk.Checkbutton(s, variable=self.agc_var, command=self.on_gain), "自動ゲイン (AGC)").pack(anchor="w")
        self.gain_lbl = ttk.Label(s)
        self.gain_lbl.pack(anchor="w")
        self.gain_scale = ttk.Scale(s, from_=0, to=80, orient=tk.HORIZONTAL, command=lambda _v: self.label_gain())
        self.gain_scale.set(60)
        bind(lambda _t: self.label_gain(), "手動ゲイン: {}  (スイープは常に手動)")
        self.gain_scale.bind("<ButtonRelease-1>", lambda _e: self.on_gain())
        self.gain_scale.pack(fill=tk.X)
        self.dc_var = tk.BooleanVar(value=True)
        labelled(ttk.Checkbutton(s, variable=self.dc_var, command=lambda: self.set_cfg(dc_remove=self.dc_var.get())),
                 "DC オフセット除去").pack(anchor="w")

        s = section("表示")
        self.avg_var = tk.DoubleVar(value=0.7)
        labelled(ttk.Label(s), "スペクトラム平均化").pack(anchor="w")
        ttk.Scale(s, from_=0, to=0.97, variable=self.avg_var, orient=tk.HORIZONTAL).pack(fill=tk.X)
        self.peak_var = tk.BooleanVar(value=True)
        f = ttk.Frame(s)
        f.pack(fill=tk.X)
        labelled(ttk.Checkbutton(f, variable=self.peak_var), "ピークホールド").pack(side=tk.LEFT)
        labelled(ttk.Button(f, command=self.reset_display), "リセット").pack(side=tk.LEFT, padx=4)
        self.wfmode_var = tk.StringVar(value="max")
        f = ttk.Frame(s)
        f.pack(fill=tk.X)
        labelled(ttk.Label(f), "ウォーターフォール:").pack(side=tk.LEFT)
        for v, text in (("max", "最大"), ("mean", "平均")):
            labelled(ttk.Radiobutton(f, value=v, variable=self.wfmode_var), text).pack(side=tk.LEFT)
        self.chov_var = tk.BooleanVar(value=True)
        labelled(ttk.Checkbutton(s, variable=self.chov_var, command=lambda: setattr(self, "overlay_key", None)),
                 "Wi-Fi チャンネル表示").pack(anchor="w")
        self.ymin_var = tk.DoubleVar(value=-75)
        self.ymax_var = tk.DoubleVar(value=-5)
        f = ttk.Frame(s)
        f.pack(fill=tk.X)
        labelled(ttk.Label(f), "dB 範囲").pack(side=tk.LEFT)
        ttk.Spinbox(f, from_=-160, to=0, increment=5, textvariable=self.ymin_var, width=5,
                    command=self.on_yrange).pack(side=tk.LEFT)
        ttk.Spinbox(f, from_=-160, to=20, increment=5, textvariable=self.ymax_var, width=5,
                    command=self.on_yrange).pack(side=tk.LEFT)
        labelled(ttk.Button(f, width=4, command=self.autoscale), "自動").pack(side=tk.LEFT, padx=2)

        s = section("操作")
        f = ttk.Frame(s)
        f.pack(fill=tk.X)
        self.pause_btn = ttk.Button(f, text=tr("一時停止"), command=self.toggle_pause)
        self.pause_btn.pack(side=tk.LEFT)
        labelled(ttk.Button(f, command=lambda: self.set_cfg(record=True)), "IQ 保存").pack(side=tk.LEFT, padx=4)
        labelled(ttk.Label(s, foreground="gray"), "スペクトラムをクリック → その周波数へ同調").pack(anchor="w")
        f = ttk.Frame(s)
        f.pack(fill=tk.X, pady=(3, 0))
        labelled(ttk.Label(f), "言語").pack(side=tk.LEFT)
        self.lang_var = tk.StringVar(value=i18n.LANGS[i18n.lang()])
        cb = ttk.Combobox(f, textvariable=self.lang_var, values=list(i18n.LANGS.values()), width=9, state="readonly")
        cb.pack(side=tk.LEFT, padx=4)
        cb.bind("<<ComboboxSelected>>", lambda _e: i18n.set_lang(
            next(k for k, v in i18n.LANGS.items() if v == self.lang_var.get())))

        self.info_lbl = ttk.Label(p, text="", justify=tk.LEFT, font=("Consolas", 9))
        self.info_lbl.pack(fill=tk.X, pady=4)
        self.log = tk.Text(p, height=6, width=34, font=("Consolas", 8))
        self.log.pack(fill=tk.BOTH, expand=True)
        self.on_band(set_freq=False)

    # ---- plots -------------------------------------------------------
    def _build_plots(self):
        self.fig = Figure(figsize=(10, 8), dpi=100, facecolor="#111")
        gs = self.fig.add_gridspec(3, 1, height_ratios=[2.2, 2.2, 1.2], hspace=0.28,
                                   left=0.07, right=0.98, top=0.96, bottom=0.06)
        self.ax_sp = self.fig.add_subplot(gs[0])
        self.ax_wf = self.fig.add_subplot(gs[1], sharex=self.ax_sp)
        self.ax_bt = self.fig.add_subplot(gs[2])
        for ax in (self.ax_sp, self.ax_wf, self.ax_bt):
            ax.set_facecolor("#000")
            ax.tick_params(colors="#ccc", labelsize=8)
            for sp in ax.spines.values():
                sp.set_color("#555")
            ax.grid(True, color="#333", lw=0.5)
        self.ax_sp.set_ylabel("dBFS", color="#ccc")
        self.ln_avg, = self.ax_sp.plot([], [], color="#ffd23f", lw=0.9)
        self.ln_peak, = self.ax_sp.plot([], [], color="#ff4d6d", lw=0.6, alpha=0.8)
        self.ax_sp.set_ylim(self.ymin_var.get(), self.ymax_var.get())
        self.title = self.ax_sp.set_title("", color="#eee", fontsize=10)
        self.img = self.ax_wf.imshow(np.zeros((WF_ROWS, 2)), aspect="auto", cmap="turbo", origin="upper",
                                     vmin=self.ymin_var.get(), vmax=self.ymax_var.get(),
                                     extent=(0, 1, WF_ROWS, 0), interpolation="nearest")
        self.ax_wf.set_yticks([])
        self.label_plots()
        self.ln_env, = self.ax_bt.plot([], [], color="#4cc9f0", lw=0.6)
        self.bars = None
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.root)
        self.canvas.get_tk_widget().pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        self.canvas.mpl_connect("button_press_event", self.on_click)
        self.canvas.mpl_connect("draw_event", self.on_draw_event)
        self.setup_bottom()

    def label_plots(self):
        self.ln_avg.set_label(tr("平均"))
        self.ln_peak.set_label(tr("ピーク"))
        self.ax_sp.legend(loc="upper right", fontsize=8, facecolor="#222", labelcolor="#ddd", edgecolor="#444")
        self.ax_wf.set_ylabel(tr("履歴 (新→旧)"), color="#ccc")
        self.ax_wf.set_xlabel(tr("周波数 [MHz]"), color="#ccc")

    def label_bottom(self):
        if self.mode_var.get() == "live":
            self.ax_bt.set_xlabel(tr("時間 [µs]  (1 回の取得内の受信電力 — Wi-Fi パケットのバースト)"), color="#ccc")
            self.ax_bt.set_ylabel("dBFS", color="#ccc")
        else:
            self.ax_bt.set_ylabel(tr("活動 [dB]"), color="#ccc")
            self.ax_bt.set_xlabel(tr("20 MHz チャンネル  (棒: ピーク - ノイズフロア / 数字: 検出率 %)"), color="#ccc")

    def label_gain(self):
        self.gain_lbl.config(text=tr("手動ゲイン: {}  (スイープは常に手動)").format(int(float(self.gain_scale.get()))))

    def set_conn(self, state):
        """state: "idle" or "on"."""
        self.conn_state = state
        self.conn_btn.config(text=tr("切断" if state == "on" else "接続"))

    def set_status(self, key, color, text=None):
        """key: translatable status, or None with a literal text (port info, error message)."""
        self.status_key = key
        self.status.config(text=tr(key) if key else text, foreground=color)

    def retranslate(self):
        """Re-label what bind() does not cover: state-dependent and computed texts."""
        auto = is_text(self.port_var.get(), "自動検出")
        self.port_box.config(values=[tr("自動検出")] + self.ports)
        if auto:
            self.port_var.set(tr("自動検出"))
        mx = is_text(self.bw_var.get(), "最大")
        self.bw_box.config(values=[tr("最大")] + list(self.bw_box.cget("values"))[1:])
        if mx:
            self.bw_var.set(tr("最大"))
        self.set_conn(self.conn_state)
        if self.status_key:
            self.status.config(text=tr(self.status_key))
        self.pause_btn.config(text=tr("再開" if self.get_cfg("paused") else "一時停止"))
        self.label_plots()
        self.label_bottom()
        if self.last_m is not None:
            self.update_texts(self.last_m)
        if self.last_info_m is not None:
            self.update_info(self.last_info_m)
        self.need_full = True
        self.render()

    def setup_bottom(self):
        ax = self.ax_bt
        ax.cla()
        ax.set_facecolor("#000")
        ax.grid(True, color="#333", lw=0.5)
        ax.tick_params(colors="#ccc", labelsize=8)
        self.bars = None
        self.need_full = True
        if self.mode_var.get() == "live":
            self.ln_env, = ax.plot([], [], color="#4cc9f0", lw=0.6)
            ax.set_ylim(-70, 0)
        self.label_bottom()

    # ---- events --------------------------------------------------------
    def logmsg(self, m):
        self.log.insert(tk.END, m + "\n")
        self.log.see(tk.END)

    def toggle_connect(self):
        if self.worker and self.worker.is_alive():
            self.worker.stop_evt.set()
            self.set_conn("idle")
            return
        port = self.port_var.get()
        port = None if is_text(port, "自動検出") else port
        self.set_status("接続中…", "orange")
        self.reset_display()
        self.worker = Worker(port, self.cfg, self.q)
        self.worker.start()
        self.set_conn("on")

    def toggle_pause(self):
        p = not self.get_cfg("paused")
        self.set_cfg(paused=p)
        self.pause_btn.config(text=tr("再開" if p else "一時停止"))

    def on_mode(self):
        mode = self.mode_var.get()
        self.auto_frames = 6 if mode == "live" else 2
        if mode == "sweep" and int(self.nsamp_var.get()) > 4096:
            self.nsamp_var.set("4096")  # keeps a 5 GHz sweep at ~2.5 s
            self.on_rx()
        self.set_cfg(mode=mode, sweep_gen=self.get_cfg("sweep_gen") + 1)
        self.reset_display()
        self.setup_bottom()

    def on_band(self, set_freq=True):
        band = self.band_var.get()
        self.presets = wc.channel_presets(band)
        self.ch_box.config(values=[p[0] for p in self.presets])
        idx = 5 if band == "2.4 GHz" else 0  # ch6 / ch36
        self.ch_box.current(idx)
        self.set_cfg(band=band, sweep_gen=self.get_cfg("sweep_gen") + 1)
        if set_freq:
            self.on_channel()
        self.reset_display()

    def on_channel(self, _e=None):
        i = self.ch_box.current()
        if i < 0:
            return
        _, fc, width = self.presets[i]
        self.sel_width = width
        self.freq_var.set(str(fc))
        if width > 40:
            self.rate_var.set("80 MS/s")
            self.bw_var.set(tr("最大"))
            self.logmsg(tr("{width} MHz 幅: 受信帯域は最大 ~48 MHz のため中心部のみ表示").format(width=width))
        elif width == 40 and self.rate_var.get() not in ("80 MS/s", "40 MS/s"):
            self.rate_var.set("80 MS/s")
        self.on_rx()
        self.on_freq()

    def on_freq(self):
        try:
            mhz = int(round(float(self.freq_var.get())))
        except ValueError:
            return
        mhz = max(100, min(6000, mhz))
        self.freq_var.set(str(mhz))
        band = wc.band_of(mhz)
        if band != self.band_var.get():
            self.band_var.set(band)
            self.presets = wc.channel_presets(band)
            self.ch_box.config(values=[p[0] for p in self.presets])
            self.set_cfg(band=band)
        self.set_cfg(freq=mhz)
        self.avg = None
        if self.mode_var.get() == "sweep":
            self.mode_var.set("live")
            self.on_mode()

    def nudge(self, d):
        try:
            self.freq_var.set(str(int(self.freq_var.get()) + d))
        except ValueError:
            return
        self.on_freq()

    def on_rx(self):
        rate = [k for k, v in RATES.items() if f"{v // 1_000_000} MS/s" == self.rate_var.get()][0]
        nsamp = int(self.nsamp_var.get())
        nfft = int(self.nfft_var.get())
        bw = 0 if is_text(self.bw_var.get(), "最大") else int(self.bw_var.get())
        lo_offset = int(self.lo_var.get())
        self.set_cfg(rate=rate, nsamp=nsamp, nfft=nfft, bw=bw, lo_offset=lo_offset)
        self.reset_display()

    def on_gain(self):
        mg = int(float(self.gain_scale.get()))
        self.set_cfg(gain=None if self.agc_var.get() else mg, manual_gain=mg)
        self.reset_display()

    def on_yrange(self):
        try:
            lo, hi = float(self.ymin_var.get()), float(self.ymax_var.get())
        except (tk.TclError, ValueError):
            return
        if hi > lo:
            self.ax_sp.set_ylim(lo, hi)
            self.overlay_key = None
            self.need_full = True

    def autoscale(self):
        if self.avg is None:
            return
        floor = np.nanpercentile(self.avg, 10)
        lo = np.nanpercentile(self.avg, 2) - 10
        hi = np.nanmax(self.peak if self.peak is not None else self.avg) + 10
        self.ymin_var.set(np.floor(lo / 5) * 5)
        self.ymax_var.set(np.ceil(hi / 5) * 5)
        self.on_yrange()
        # Waterfall: emphasise what rises above the noise floor
        self.img.set_clim(floor - 3, floor + 30)
        self.need_full = True

    def on_click(self, ev):
        if ev.inaxes not in (self.ax_sp, self.ax_wf) or ev.xdata is None or ev.button != 1:
            return
        self.freq_var.set(str(int(round(ev.xdata))))
        self.on_freq()

    def reset_display(self):
        self.avg = None
        self.peak = None
        self.wf = None
        self.wf_key = None
        self.occ_hits = None
        self.occ_sweeps = 0

    def on_close(self):
        if self.worker:
            self.worker.stop_evt.set()
            self.worker.join(timeout=2)
        self.root.destroy()

    # ---- drawing -------------------------------------------------------
    def poll(self):
        live = []
        sweep = None
        try:
            while True:
                m = self.q.get_nowait()
                k = m["kind"]
                if k == "live":
                    live.append(m)
                elif k == "sweep":
                    if m["done"]:
                        self.draw([m])
                        sweep = None
                    else:
                        sweep = m
                elif k == "log":
                    self.logmsg(m["msg"])
                elif k == "error":
                    self.logmsg(tr("エラー: {}").format(m["msg"]))
                    if m["fatal"]:
                        self.set_status(None, "red", m["msg"])
                        self.set_conn("idle")
                elif k == "connected":
                    lim = m["limits"]
                    self.set_status(None, "green", f"{m['port']}  {m['info']}")
                    self.logmsg(tr("接続: {port}  {info}").format(port=m["port"], info=m["info"]))
                    self.logmsg(tr("ゲイン {gain}  帯域 {bw}").format(gain=lim.get("gain"), bw=lim.get("bandwidth")))
                    if lim.get("gain"):
                        self.gain_scale.config(to=lim["gain"][1])
                elif k == "disconnected":
                    self.set_conn("idle")
                    if "red" not in str(self.status.cget("foreground")):
                        self.set_status("未接続", "gray")
        except queue.Empty:
            pass
        # Every capture received since the last frame is folded into the
        # display, so faster acquisition is never thrown away by slow drawing.
        if live:
            self.draw(live)
        elif sweep:
            self.draw([sweep])
        self.root.after(15, self.poll)

    # Blitting: axes, ticks, overlays and labels are rendered once into a cached
    # background; per frame only the traces and the waterfall image are redrawn.
    def animated_artists(self):
        arts = [self.ln_avg, self.ln_peak, self.img]
        if self.mode_var.get() == "live" and self.ln_env.axes is not None:
            arts.append(self.ln_env)
        return arts

    def on_draw_event(self, _ev):
        self.bg = self.canvas.copy_from_bbox(self.fig.bbox)
        for a in self.animated_artists():
            a.set_animated(True)
            a.axes.draw_artist(a)

    def render(self):
        if self.need_full or self.bg is None:
            self.need_full = False
            for a in self.animated_artists():
                a.set_animated(True)
            self.canvas.draw()  # fires on_draw_event, which caches the background
            return
        self.canvas.restore_region(self.bg)
        for a in self.animated_artists():
            a.axes.draw_artist(a)
        self.canvas.blit(self.fig.bbox)

    def update_overlay(self, fmin, fmax, center=None):
        key = (round(fmin, 1), round(fmax, 1), center, self.sel_width, self.chov_var.get(),
               self.ax_sp.get_ylim())
        if key == self.overlay_key:
            return
        self.overlay_key = key
        self.need_full = True
        for a in self.overlay_artists:
            a.remove()
        self.overlay_artists = []
        if not self.chov_var.get():
            return
        y1 = self.ax_sp.get_ylim()[1]
        band = wc.band_of((fmin + fmax) / 2)
        chans = [(c, f) for c, f in wc.channels_20(band) if fmin - 10 < f < fmax + 10]
        step = 1 if len(chans) <= 24 else 2
        for i, (c, f) in enumerate(chans):
            a = self.ax_sp.axvspan(f - 10, f + 10, ymin=0.965, ymax=1.0,
                                   color="#3a86ff" if i % 2 else "#8338ec", alpha=0.6, lw=0)
            self.overlay_artists.append(a)
            if i % step == 0:
                t = self.ax_sp.text(f, y1, str(c), color="#ddd", fontsize=7, ha="center", va="bottom")
                self.overlay_artists.append(t)
        if center is not None and self.mode_var.get() == "live":
            w = self.sel_width
            a = self.ax_sp.axvspan(center - w / 2, center + w / 2, color="#06d6a0", alpha=0.08, lw=0)
            self.overlay_artists.append(a)

    def draw(self, msgs):
        m = msgs[-1]
        f = m["f"]
        key = (m["kind"], len(f), float(f[0]), float(f[-1]))
        alpha = float(self.avg_var.get())
        if key != self.wf_key or self.avg is None or self.avg.shape != m["mean"].shape:
            self.wf_key = key
            self.avg = m["mean"].copy()
            self.peak = m["max"].copy()
            rows = WF_ROWS if m["kind"] == "live" else 60
            self.wf = np.full((rows, len(f)), np.nan)
            self.img.set_extent((f[0], f[-1], rows, 0))
            self.ax_sp.set_xlim(f[0], f[-1])
            self.ln_avg.set_xdata(f)
            self.ln_peak.set_xdata(f)
            self.need_full = True
        rows = []
        for x in msgs:
            if x["mean"].shape != self.avg.shape:
                continue
            if x["kind"] == "live":
                self.avg = 10 * np.log10(alpha * 10 ** (self.avg / 10) + (1 - alpha) * 10 ** (x["mean"] / 10))
            else:
                self.avg = np.where(np.isnan(x["mean"]), self.avg, x["mean"])
            self.peak = np.fmax(self.peak, x["max"]) if self.peak_var.get() else x["max"]
            if x["kind"] == "live" or x["done"]:
                rows.append(x["max"] if self.wfmode_var.get() == "max" else x["mean"])
        self.ln_avg.set_ydata(self.avg)
        self.ln_peak.set_ydata(self.peak)
        if rows:
            k = min(len(rows), len(self.wf))
            self.wf = np.roll(self.wf, k, axis=0)
            self.wf[:k] = np.array(rows[::-1][:k])
            self.img.set_data(self.wf)

        now = time.time()
        self.cap_t = [t for t in self.cap_t if now - t < 2] + [now] * len(msgs)
        self.fps_t = [t for t in self.fps_t if now - t < 2] + [now]
        if m["kind"] == "live":
            self.update_overlay(f[0], f[-1], m["freq"])
            self.ln_env.set_data(m["t_us"], m["env"])
            xmax = m["t_us"][-1] if len(m["t_us"]) else 1
            if self.ax_bt.get_xlim()[1] != xmax:
                self.ax_bt.set_xlim(0, xmax)
                self.need_full = True
        else:
            self.update_overlay(f[0], f[-1], None)
            if m["done"]:
                self.draw_occupancy(m)
                self.need_full = True
        self.update_texts(m)
        if m["kind"] == "live" or m["done"]:
            self.update_info(m)
        if self.auto_frames and (m["kind"] == "live" or m["done"]):
            self.auto_frames = max(0, self.auto_frames - len(msgs))
            if self.auto_frames == 0:
                self.autoscale()
        self.render()

    def update_texts(self, m):
        self.last_m = m
        f = m["f"]
        if m["kind"] == "live":
            lo_txt = f"  LO {m['lo']} MHz" if m["lo"] != m["freq"] else ""
            title = tr("ライブ  中心 {freq} MHz{lo}  ({band})   {rate} MS/s × {n} サンプル ({us} µs)").format(
                freq=m["freq"], lo=lo_txt, band=wc.band_of(m["freq"]), rate=f"{m['fs'] / 1e6:g}", n=m["n"],
                us=f"{m['n'] / m['fs'] * 1e6:.0f}")
        else:
            title = tr("バンドスイープ  {band}  ({f0}–{f1} MHz)").format(
                band=m["band"], f0=f"{f[0]:.0f}", f1=f"{f[-1]:.0f}")
        if self.title.get_text() != title:
            self.title.set_text(title)
            self.need_full = True

    def update_info(self, m):
        self.last_info_m = m
        if m["kind"] == "live":
            gain = self.get_cfg("gain")
            text = tr("取得   {cap:5.1f} 回/秒\n描画   {fps:5.1f} 回/秒\nRMS   {rms:6.1f} dBFS\n"
                      "クリップ {clip:5.2f} %\nゲイン  {gain}").format(
                cap=len(self.cap_t) / 2, fps=len(self.fps_t) / 2, rms=m["rms_db"], clip=m["clip"],
                gain="AGC" if gain is None else gain)
        else:
            text = tr("1 スイープ {s:.2f} 秒\n{steps} ステップ × {step} MHz\nゲイン {gain} (手動)\n"
                      "スイープ回数 {n}").format(s=m["sweep_s"], steps=m["steps"], step=SWEEP_STEP_MHZ,
                                                gain=self.get_cfg("manual_gain"), n=self.occ_sweeps)
        self.info_lbl.config(text=text)

    def draw_occupancy(self, m):
        chans, act = channel_activity(m["band"], m["f"], m["mean"], m["max"])
        if self.occ_hits is None or len(self.occ_hits) != len(chans):
            self.occ_hits = np.zeros(len(chans))
            self.occ_sweeps = 0
        self.occ_sweeps += 1
        self.occ_hits += act > 12
        pct = 100 * self.occ_hits / self.occ_sweeps
        ax = self.ax_bt
        ax.cla()
        self.setup_bottom()
        x = np.arange(len(chans))
        colors = matplotlib.colormaps["turbo"](np.clip(act / 40, 0, 1))
        ax.bar(x, act, color=colors, width=0.8)
        ax.set_xticks(x)
        ax.set_xticklabels([str(c) for c, _ in chans], fontsize=7, color="#ccc")
        ax.set_xlim(-0.6, len(chans) - 0.4)
        ax.set_ylim(0, max(30, float(np.nanmax(act)) + 8))
        for xi, (a, p) in enumerate(zip(act, pct)):
            ax.text(xi, a + 0.5, f"{p:.0f}", color="#ddd", fontsize=6, ha="center", va="bottom")


def main():
    ap = argparse.ArgumentParser(description="ESP32-C5 Wi-Fi SDR viewer")
    ap.add_argument("port", nargs="?", help="serial port (default: auto-detect)")
    ap.add_argument("--freq", type=int, help="start center frequency [MHz]")
    ap.add_argument("--sweep", choices=["2.4", "5"], help="start in band-sweep mode")
    ap.add_argument("--samples", choices=["2048", "4096", "8192", "16380"], help="samples per capture")
    ap.add_argument("--lang", choices=list(i18n.LANGS), help="UI language (default: last used, else the OS language)")
    args = ap.parse_args()
    i18n.set_lang(args.lang or i18n.load_lang(), save=bool(args.lang))
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    app = App(root, args.port)
    if args.samples:
        app.nsamp_var.set(args.samples)
        app.on_rx()
    if args.freq:
        app.freq_var.set(str(args.freq))
        app.on_freq()
    if args.sweep:
        app.band_var.set(args.sweep + " GHz")
        app.on_band(set_freq=False)
        app.mode_var.set("sweep")
        app.on_mode()
    root.after(200, app.toggle_connect)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # show unexpected startup errors in a dialog
        messagebox.showerror("ESP32-C5 SDR", str(e))
        raise
