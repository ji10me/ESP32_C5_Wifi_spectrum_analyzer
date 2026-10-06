# SPDX-License-Identifier: GPL-3.0-or-later
"""ESP32-C5 Wi-Fi SDR - Fluent (WinUI 3 style) front-end.

PySide6 + PySide6-Fluent-Widgets for the Windows 11 look (navigation pane,
Mica, cards, toggle switches) and pyqtgraph for fast plotting. The radio side
(sdr_core.Worker / espsdr.py) is shared with the tkinter version (sdr_app.py).

Run:  python sdr_fluent.py [COMx] [--freq MHz] [--sweep 2.4|5] [--samples N] [--light] [--lang ja|en]
"""
import argparse
import os
import queue
import sys
import time

import numpy as np
import pyqtgraph as pg
import serial.tools.list_ports
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (BodyLabel, CaptionLabel, ComboBox, CompactSpinBox, FluentIcon, FluentWindow, InfoBar,
                            NavigationItemPosition, PlainTextEdit, PrimaryPushButton, PushButton,
                            SegmentedWidget, SimpleCardWidget, Slider, SmoothScrollArea, SpinBox,
                            StrongBodyLabel, SubtitleLabel, SwitchButton, Theme, TitleLabel,
                            ToolTipFilter, TransparentToolButton, isDarkTheme, qconfig,
                            setFontFamilies, setTheme, themeColor)

import i18n
import wifi_channels as wc
from espsdr import RATES
from i18n import bind, tr
from sdr_core import HERE, SWEEP_STEP_MHZ, WF_ROWS, Worker, channel_activity, default_cfg

pg.setConfigOptions(imageAxisOrder="row-major", antialias=False)

RATE_LABELS = [f"{v // 1_000_000} MS/s" for v in RATES.values()]
BW_CHOICES = ["最大", "11", "16", "20", "24", "30", "40", "48"]
LO_CHOICES = ["0", "+12", "-12", "+16", "-16"]
STAT_CAPTIONS = ("中心周波数", "取得レート", "RMS", "クリップ", "ゲイン")
CMAPS = ["turbo", "inferno", "viridis", "magma", "plasma"]


def tip(widget, text):
    bind(widget.setToolTip, text)
    widget.installEventFilter(ToolTipFilter(widget))
    return widget


class Section(SimpleCardWidget):
    """Settings card: a title and label/control rows."""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(14, 10, 14, 12)
        self.v.setSpacing(6)
        self.title = StrongBodyLabel()
        bind(self.title.setText, title)
        self.v.addWidget(self.title)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(6)
        self.grid.setColumnStretch(1, 1)
        self.v.addLayout(self.grid)
        self._row = 0

    def row(self, label, widget):
        if label:
            lb = BodyLabel()
            bind(lb.setText, label)
            self.grid.addWidget(lb, self._row, 0)
            self.grid.addWidget(widget, self._row, 1)
        else:
            self.grid.addWidget(widget, self._row, 0, 1, 2)
        self._row += 1
        return widget

    def pair(self, a, b):
        """Two captioned controls side by side on one row."""
        self.grid.addWidget(hbox(a, b, spacing=10), self._row, 0, 1, 2)
        self._row += 1


def hbox(*widgets, spacing=6, stretch=None):
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(spacing)
    for i, x in enumerate(widgets):
        if x is None:
            h.addStretch(1)
        else:
            h.addWidget(x, 1 if stretch is None or i in stretch else 0)
    return w


def captioned(caption, widget):
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(2)
    c = CaptionLabel()
    bind(c.setText, caption)
    v.addWidget(c)
    v.addWidget(widget)
    return w


def seg_item(seg, key, text, on_click=None):
    """SegmentedWidget item whose label follows the UI language."""
    seg.addItem(key, tr(text), on_click)
    bind(lambda t: seg.setItemText(key, t), text)


def switch(checked=True):
    sw = SwitchButton()
    bind(sw.setOnText, "オン")
    bind(sw.setOffText, "オフ")
    sw.setChecked(checked)
    return sw


class StatCard(SimpleCardWidget):
    def __init__(self, caption, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 7, 14, 8)
        v.setSpacing(0)
        self.cap = CaptionLabel(caption)
        self.val = SubtitleLabel("—")
        v.addWidget(self.cap)
        v.addWidget(self.val)

    def set(self, caption, value):
        self.cap.setText(caption)
        self.val.setText(value)


# --------------------------------------------------------------------------
# Spectrum page
# --------------------------------------------------------------------------
class SdrPage(QWidget):
    def __init__(self, device_page, parent=None):
        super().__init__(parent)
        self.setObjectName("sdrPage")
        self.device_page = device_page
        self.cfg = default_cfg()
        self.q = queue.Queue()
        self.worker = None
        self.avg = self.peak = self.wf = self.wf_key = None
        self.wf_levels = (-75, -40)
        self.occ_hits = None
        self.occ_sweeps = 0
        self.overlay_key = None
        self.overlay_items = []
        self.occ_items = []
        self.cap_t = []
        self.sel_width = 20
        self.auto_frames = 6
        self.auto_y = True
        self.last_auto = 0.0
        self.shrink_count = 0
        self._sync = False
        self.conn_state = "idle"
        self.last_m = self.last_stats_m = None
        self.cmap = pg.colormap.get("turbo", source="matplotlib")
        self._build_ui()
        self._build_plots()
        self.on_band("2.4 GHz", set_freq=False)
        self.set_mode("live")
        qconfig.themeChanged.connect(self.apply_theme)
        qconfig.themeColorChanged.connect(self.apply_theme)
        self.apply_theme()
        i18n.on_change(self.retranslate)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(15)

    # ---- config ---------------------------------------------------------
    def set_cfg(self, **kw):
        with self.cfg["lock"]:
            self.cfg.update(kw)

    def get_cfg(self, k):
        with self.cfg["lock"]:
            return self.cfg[k]

    # ---- layout -----------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 8, 24, 16)
        root.setSpacing(10)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        hdr.addWidget(TitleLabel("Wi-Fi Spectrum"))
        hdr.addSpacing(18)
        self.mode_seg = SegmentedWidget()
        seg_item(self.mode_seg, "live", "ライブ", lambda: self.set_mode("live"))
        seg_item(self.mode_seg, "sweep", "バンドスイープ", lambda: self.set_mode("sweep"))
        self.mode_seg.setCurrentItem("live")
        hdr.addWidget(self.mode_seg)
        hdr.addStretch(1)
        self.port_box = ComboBox()
        self.port_box.setMinimumWidth(150)
        self.refresh_ports()
        hdr.addWidget(self.port_box)
        hdr.addWidget(tip(self._tool(FluentIcon.SYNC, self.refresh_ports), "ポート一覧を更新"))
        self.conn_btn = PrimaryPushButton(FluentIcon.CONNECT, tr("接続"))
        self.conn_btn.setMinimumWidth(110)
        self.conn_btn.clicked.connect(self.toggle_connect)
        hdr.addWidget(self.conn_btn)
        self.pause_btn = tip(self._tool(FluentIcon.PAUSE, self.toggle_pause), "一時停止 / 再開")
        hdr.addWidget(self.pause_btn)
        hdr.addWidget(tip(self._tool(FluentIcon.SAVE, self.record), "次の取得を I/Q 保存 (.npy / .cfile)"))
        root.addLayout(hdr)

        stats = QHBoxLayout()
        stats.setSpacing(12)
        self.stat = [StatCard(tr(c)) for c in STAT_CAPTIONS]
        for s in self.stat:
            stats.addWidget(s)
        root.addLayout(stats)

        body = QHBoxLayout()
        body.setSpacing(12)
        self.plot_card = SimpleCardWidget()
        pv = QVBoxLayout(self.plot_card)
        pv.setContentsMargins(8, 10, 12, 8)
        self.plot_title = CaptionLabel("")
        pv.addWidget(self.plot_title, 0, Qt.AlignHCenter)
        self.glw = pg.GraphicsLayoutWidget()
        self.glw.setBackground(None)
        self.glw.setStyleSheet("background: transparent; border: none")  # let the card show through
        tip(self.plot_card, "クリック: その周波数のレベルを表示（マーカーはドラッグで移動）/ "
                            "ダブルクリック: 同調 / 右クリック: マーカー消去 / ホイール: 拡大")
        pv.addWidget(self.glw)
        body.addWidget(self.plot_card, 1)

        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(340)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none}")
        panel = QWidget()
        panel.setStyleSheet("background:transparent")
        pl = QVBoxLayout(panel)
        panel.setFixedWidth(326)
        pl.setContentsMargins(0, 0, 8, 0)
        pl.setSpacing(10)
        scroll.setWidget(panel)
        body.addWidget(scroll)
        root.addLayout(body, 1)

        # Frequency
        s = Section("周波数")
        self.band_seg = SegmentedWidget()
        for b in wc.BANDS:
            self.band_seg.addItem(b, b, lambda b=b: self.on_band(b))
        s.row("", self.band_seg)
        self.ch_box = s.row("", ComboBox())
        self.ch_box.currentIndexChanged.connect(self.on_channel)
        self.freq_spin = s.row("中心", SpinBox())
        self.freq_spin.setRange(100, 6000)
        self.freq_spin.setSuffix(" MHz")
        self.freq_spin.setKeyboardTracking(False)
        self.freq_spin.setValue(2437)
        self.freq_spin.valueChanged.connect(lambda _v: self.on_freq())
        pl.addWidget(s)

        # Receiver (incl. gain)
        s = Section("受信")
        self.rate_box, self.ns_box, self.fft_box, self.bw_box, self.lo_box = (ComboBox() for _ in range(5))
        self.rate_box.addItems(RATE_LABELS)
        self.ns_box.addItems(["2048", "4096", "8192", "16380"])
        self.ns_box.setCurrentText("4096")
        self.fft_box.addItems(["256", "512", "1024", "2048"])
        self.fft_box.setCurrentText("512")
        self.bw_box.addItems([tr(b) if b == "最大" else f"{b} MHz" for b in BW_CHOICES])
        self.lo_box.addItems([f"{x} MHz" for x in LO_CHOICES])
        for cb in (self.rate_box, self.ns_box, self.fft_box, self.bw_box, self.lo_box):
            cb.setMinimumWidth(0)
            cb.currentIndexChanged.connect(lambda _i: self.on_rx())
        tip(self.lo_box, "LO を見たいチャンネルからずらし、中心の DC / LO 漏れを避けます")
        self.agc_sw = switch(False)  # AGC hunts between captures; a fixed gain gives a stable display
        self.agc_sw.checkedChanged.connect(lambda _c: self.on_gain())
        s.pair(captioned("サンプルレート", self.rate_box), captioned("サンプル数", self.ns_box))
        s.pair(captioned("FFT サイズ", self.fft_box), captioned("アナログ帯域", self.bw_box))
        s.pair(captioned("LO オフセット", self.lo_box), captioned("自動ゲイン (AGC)", self.agc_sw))
        self.gain_sl = Slider(Qt.Horizontal)
        self.gain_sl.setRange(0, 83)
        self.gain_sl.setValue(60)
        self.gain_lbl = BodyLabel("60")
        self.gain_lbl.setFixedWidth(24)
        self.gain_sl.valueChanged.connect(lambda v: self.gain_lbl.setText(str(v)))
        self.gain_sl.sliderReleased.connect(self.on_gain)
        s.row("手動ゲイン", tip(hbox(self.gain_sl, self.gain_lbl, stretch={0}),
                               "AGC オフ時のゲイン。スイープ中は常にこの値を使います"))
        pl.addWidget(s)

        # Display
        s = Section("表示")
        self.avg_sl = s.row("平均化", Slider(Qt.Horizontal))
        self.avg_sl.setRange(0, 97)
        self.avg_sl.setValue(70)
        self.peak_sw = switch(True)
        rb = tip(self._tool(FluentIcon.BROOM, self.reset_display), "ピーク / 滝表示をリセット")
        s.row("ピークホールド", hbox(self.peak_sw, None, rb, stretch=set()))
        self.wf_seg = SegmentedWidget()
        seg_item(self.wf_seg, "max", "最大")
        seg_item(self.wf_seg, "mean", "平均")
        self.wf_seg.setCurrentItem("max")
        s.row("滝表示", self.wf_seg)
        self.ov_sw = switch(True)
        self.ov_sw.checkedChanged.connect(lambda _c: setattr(self, "overlay_key", None))
        self.dc_sw = switch(True)
        self.dc_sw.checkedChanged.connect(lambda c: self.set_cfg(dc_remove=c))
        s.pair(captioned("チャンネル表示", self.ov_sw), captioned("DC 除去", self.dc_sw))
        self.ymin_spin, self.ymax_spin = CompactSpinBox(), CompactSpinBox()
        for sp, v in ((self.ymin_spin, -75), (self.ymax_spin, -5)):
            sp.setRange(-160, 20)
            sp.setSingleStep(5)
            sp.setKeyboardTracking(False)
            sp.setValue(v)
            sp.valueChanged.connect(lambda _v: self.on_yrange_user())
        ab = tip(self._tool(FluentIcon.FIT_PAGE, self.autoscale_clicked), "自動スケール（データに追従）")
        s.row("dB 範囲", hbox(self.ymin_spin, self.ymax_spin, ab, stretch={0, 1}))
        pl.addWidget(s)
        pl.addStretch(1)

    def _tool(self, icon, slot):
        b = TransparentToolButton(icon)
        b.clicked.connect(slot)
        return b

    def _build_plots(self):
        g = self.glw
        self.p_sp = g.addPlot(row=0, col=0)
        self.p_wf = g.addPlot(row=1, col=0)
        self.p_bt = g.addPlot(row=2, col=0)
        lay = g.ci.layout
        for r, k in ((0, 5), (1, 5), (2, 3)):
            lay.setRowStretchFactor(r, k)
        self.p_wf.setXLink(self.p_sp)
        for p in (self.p_sp, self.p_wf, self.p_bt):
            p.setMenuEnabled(False)
            p.hideButtons()
            p.setMouseEnabled(x=True, y=False)
            p.enableAutoRange(False)
            p.showGrid(x=True, y=True, alpha=0.12)
            p.getAxis("left").setWidth(52)
        self.p_sp.setLabel("left", "dBFS")
        self.p_wf.getAxis("left").setTicks([[]])
        self.label_waterfall()
        self.p_wf.invertY(True)
        self.p_wf.showGrid(x=False, y=False)
        self.c_peak = self.p_sp.plot()
        self.c_avg = self.p_sp.plot()
        self.img = pg.ImageItem()
        self.apply_lut()
        self.p_wf.addItem(self.img)
        self.c_env = self.p_bt.plot()
        # Click marker: frequency / level readout that follows the live data
        self.marker_f = None
        self._mk_sync = False
        self.f_cur = None
        self.mk_line = pg.InfiniteLine(angle=90, movable=True)
        self.mk_line.sigPositionChanged.connect(self.on_marker_moved)
        self.mk_wf = pg.InfiniteLine(angle=90, movable=False)
        self.mk_dots = pg.ScatterPlotItem(size=9, pen=pg.mkPen(None))
        self.mk_text = pg.TextItem(anchor=(0, 0))
        for item, plot, z in ((self.mk_line, self.p_sp, 20), (self.mk_dots, self.p_sp, 21),
                              (self.mk_text, self.p_sp, 30), (self.mk_wf, self.p_wf, 20)):
            item.setZValue(z)
            item.hide()
            plot.addItem(item, ignoreBounds=True)
        self.on_yrange()
        self.p_sp.scene().sigMouseClicked.connect(self.on_click)

    def label_waterfall(self):
        self.p_wf.setLabel("left", tr("履歴"))
        self.p_wf.setLabel("bottom", tr("周波数 [MHz]"))

    def retranslate(self):
        """Re-label what bind() does not cover: state-dependent and computed texts."""
        self.port_box.setItemText(0, tr("自動検出"))
        self.bw_box.setItemText(0, tr("最大"))
        self.set_connected_ui(self.conn_state)
        self.label_waterfall()
        self.label_bottom()
        if self.last_m is not None:
            self.update_texts(self.last_m)
        if self.last_stats_m is not None:
            self.update_stats(self.last_stats_m)
        else:
            for card, c in zip(self.stat, STAT_CAPTIONS):
                card.cap.setText(tr(c))
        self.update_marker()

    def apply_theme(self, *_):
        dark = isDarkTheme()
        fg = QColor("#c8c8c8" if dark else "#505050")
        acc = themeColor()
        for p in (self.p_sp, self.p_wf, self.p_bt):
            for name in ("left", "bottom"):
                ax = p.getAxis(name)
                ax.setPen(pg.mkPen(fg))
                ax.setTextPen(pg.mkPen(fg))
        self.c_avg.setPen(pg.mkPen(acc, width=1.4))
        self.c_peak.setPen(pg.mkPen(QColor(255, 99, 132, 170) if dark else QColor(196, 43, 28, 150), width=1))
        self.c_env.setPen(pg.mkPen(acc, width=1))
        mk = QColor("#ffffff" if dark else "#202020")
        self.mk_line.setPen(pg.mkPen(mk, width=1, style=Qt.DashLine))
        self.mk_line.setHoverPen(pg.mkPen(acc, width=2))
        self.mk_wf.setPen(pg.mkPen(mk, width=1, style=Qt.DashLine))
        self.mk_text.fill = pg.mkBrush(QColor(32, 32, 32, 225) if dark else QColor(255, 255, 255, 235))
        self.mk_text.border = pg.mkPen(QColor(acc.red(), acc.green(), acc.blue(), 200))
        self.mk_text.setColor(mk)
        self.update_marker()
        self.overlay_key = None

    def set_colormap(self, name):
        self.cmap = pg.colormap.get(name, source="matplotlib")
        self.apply_lut()

    def apply_lut(self):
        # LUT entry 0 is transparent: rows not yet filled let the card show through
        lut = self.cmap.getLookupTable(nPts=256, alpha=True)
        lut[0, 3] = 0
        self.img.setLookupTable(lut)

    # ---- connection -------------------------------------------------------
    def refresh_ports(self):
        cur = self.port_box.currentText() if self.port_box.currentIndex() > 0 else None
        self.port_box.clear()
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_box.addItems([tr("自動検出")] + ports)
        self.port_box.setCurrentIndex(ports.index(cur) + 1 if cur in ports else 0)

    def toggle_connect(self):
        if self.worker and self.worker.is_alive():
            self.worker.stop_evt.set()
            return
        port = self.port_box.currentText() if self.port_box.currentIndex() > 0 else None
        self.reset_display()
        self.worker = Worker(port, self.cfg, self.q)
        self.worker.start()
        self.set_connected_ui("connecting")

    def set_connected_ui(self, state):
        """state: "idle", "connecting" or "on"."""
        self.conn_state = state
        self.conn_btn.setEnabled(state != "connecting")
        self.conn_btn.setText(tr({"idle": "接続", "connecting": "接続中…", "on": "切断"}[state]))
        self.conn_btn.setIcon(FluentIcon.CLOSE if state == "on" else FluentIcon.CONNECT)

    def toggle_pause(self):
        p = not self.get_cfg("paused")
        self.set_cfg(paused=p)
        self.pause_btn.setIcon(FluentIcon.PLAY if p else FluentIcon.PAUSE)

    def record(self):
        if self.get_cfg("mode") != "live":
            InfoBar.warning(tr("I/Q 保存"), tr("ライブモードで使えます"), duration=2500, parent=self.window())
            return
        self.set_cfg(record=True)

    def stop(self):
        if self.worker:
            self.worker.stop_evt.set()
            self.worker.join(timeout=2)

    # ---- controls -------------------------------------------------------
    def set_mode(self, mode):
        self.mode_seg.setCurrentItem(mode)
        self.auto_frames = 6 if mode == "live" else 2
        if mode == "sweep" and int(self.ns_box.currentText()) > 4096:
            self.ns_box.setCurrentText("4096")
        self.set_cfg(mode=mode, sweep_gen=self.get_cfg("sweep_gen") + 1)
        self.reset_display()
        self.setup_bottom()

    def on_band(self, band, set_freq=True):
        self.band_seg.setCurrentItem(band)
        self.presets = wc.channel_presets(band)
        self._sync = True
        self.ch_box.clear()
        self.ch_box.addItems([p[0] for p in self.presets])
        self.ch_box.setCurrentIndex(5 if band == "2.4 GHz" else 0)
        self._sync = False
        self.set_cfg(band=band, sweep_gen=self.get_cfg("sweep_gen") + 1)
        if set_freq:
            self.on_channel()
        self.reset_display()

    def on_channel(self, _i=None):
        if self._sync:
            return
        i = self.ch_box.currentIndex()
        if i < 0:
            return
        _, fc, width = self.presets[i]
        self.sel_width = width
        self._sync = True
        if width >= 40:
            self.rate_box.setCurrentText("80 MS/s")
            if width > 40:
                self.bw_box.setCurrentIndex(0)
        self.freq_spin.setValue(fc)
        self._sync = False
        self.on_rx()
        self.on_freq()

    def on_freq(self):
        if self._sync:
            return
        mhz = self.freq_spin.value()
        band = wc.band_of(mhz)
        if band != self.get_cfg("band"):
            self.band_seg.setCurrentItem(band)
            self.presets = wc.channel_presets(band)
            self._sync = True
            self.ch_box.clear()
            self.ch_box.addItems([p[0] for p in self.presets])
            self._sync = False
            self.set_cfg(band=band)
        self.set_cfg(freq=mhz)
        self.avg = None
        if self.get_cfg("mode") == "sweep":
            self.set_mode("live")

    def on_rx(self):
        if self._sync:
            return
        rate = RATE_LABELS.index(self.rate_box.currentText())
        bw = BW_CHOICES[max(0, self.bw_box.currentIndex())]
        self.set_cfg(rate=list(RATES)[rate], nsamp=int(self.ns_box.currentText()),
                     nfft=int(self.fft_box.currentText()), bw=0 if bw == "最大" else int(bw),
                     lo_offset=int(LO_CHOICES[max(0, self.lo_box.currentIndex())]))
        self.reset_display()

    def on_gain(self):
        mg = self.gain_sl.value()
        self.set_cfg(gain=None if self.agc_sw.isChecked() else mg, manual_gain=mg)
        self.reset_display()
        self.auto_frames = 6  # re-fit range and waterfall colours once the new gain settles

    def on_yrange(self):
        lo, hi = self.ymin_spin.value(), self.ymax_spin.value()
        if hi > lo:
            # extra headroom above the data holds the channel strip, so it never hides a trace
            self.p_sp.setYRange(lo, hi + (hi - lo) * 0.08, padding=0)
            self.overlay_key = None

    def on_yrange_user(self):
        if self._sync:
            return
        self.auto_y = False  # a manual range stays until 自動スケール is pressed
        self.on_yrange()

    def autoscale_clicked(self):
        self.auto_y = True
        self.autoscale(force=True)

    def autoscale(self, force=False):
        """Fit the dB range to the data. Without force it only grows, or shrinks
        when the data moved far away (band change, peak reset)."""
        if self.avg is None:
            return
        floor = float(np.nanpercentile(self.avg, 10))
        lo = int(np.floor((np.nanpercentile(self.avg, 2) - 8) / 5) * 5)
        hi = int(np.ceil((np.nanmax(self.peak if self.peak is not None else self.avg) + 4) / 5) * 5)
        cur_lo, cur_hi = self.ymin_spin.value(), self.ymax_spin.value()
        if not force:
            # grow at once; shrink only after the data stayed far away for 5 checks (~5 s)
            shrink = lo - cur_lo > 15 or cur_hi - hi > 15
            self.shrink_count = self.shrink_count + 1 if shrink else 0
            if self.shrink_count < 5:
                lo, hi = min(lo, cur_lo), max(hi, cur_hi)
            else:
                self.shrink_count = 0
        if (lo, hi) != (cur_lo, cur_hi):
            self._sync = True
            self.ymin_spin.setValue(lo)
            self.ymax_spin.setValue(hi)
            self._sync = False
            self.on_yrange()
        if force:
            # Waterfall colours are re-based only on explicit events: re-basing on every floor
            # change (AGC moves it by up to 16 dB) recolours the whole history at once.
            self.wf_levels = (floor - 3, floor + 30)

    def on_click(self, ev):
        pos = ev.scenePos()
        plot = next((p for p in (self.p_sp, self.p_wf) if p.sceneBoundingRect().contains(pos)), None)
        if plot is None:
            return
        x = plot.vb.mapSceneToView(pos).x()
        if ev.button() == Qt.RightButton:
            self.set_marker(None)
        elif ev.button() == Qt.LeftButton and ev.double():
            # double click tunes there (this resets averaging / peak hold)
            self.freq_spin.setValue(max(100, min(6000, int(round(x)))))
            self.on_freq()  # also when the value is unchanged (e.g. leaving sweep)
        elif ev.button() == Qt.LeftButton:
            self.set_marker(x)

    # ---- marker -----------------------------------------------------------
    def set_marker(self, x):
        self.marker_f = x
        on = x is not None
        for item in (self.mk_line, self.mk_wf):
            item.setVisible(on)
        if on:
            self._mk_sync = True
            self.mk_line.setValue(x)
            self.mk_wf.setValue(x)
            self._mk_sync = False
        self.update_marker()

    def on_marker_moved(self):
        if self._mk_sync:
            return
        self.marker_f = self.mk_line.value()
        self.mk_wf.setValue(self.marker_f)
        self.update_marker()

    def update_marker(self):
        f = self.f_cur
        mf = self.marker_f
        if mf is None or f is None or self.avg is None or not (f[0] <= mf <= f[-1]):
            self.mk_text.hide()
            self.mk_dots.hide()
            return
        i = int(np.argmin(np.abs(f - mf)))
        a, pk = float(self.avg[i]), float(self.peak[i])
        band = wc.band_of(mf)
        c, fc = min(wc.channels_20(band), key=lambda cf: abs(cf[1] - mf))
        ch = f"ch{c} ({band})" if abs(fc - mf) <= 10 else f"{tr('Wi-Fi チャンネル外')} ({band})"

        def db(v):
            return "—" if not np.isfinite(v) else f"{v:6.1f} dBFS"
        self.mk_text.setText(tr("{freq} MHz   {ch}\n平均レベル  {avg}\nピーク      {peak}").format(
            freq=f"{f[i]:.2f}", ch=ch, avg=db(a), peak=db(pk)))
        (xlo, xhi), (ylo, yhi) = self.p_sp.viewRange()
        right = mf > xlo + 0.7 * (xhi - xlo)
        self.mk_text.setAnchor((1, 0) if right else (0, 0))
        self.mk_text.setPos(mf + (xhi - xlo) * (-0.006 if right else 0.006), yhi - (yhi - ylo) * 0.09)
        pts = [(f[i], v) for v in (a, pk) if np.isfinite(v)]
        acc = themeColor()
        self.mk_dots.setData([p[0] for p in pts], [p[1] for p in pts],
                             brush=[pg.mkBrush(acc), pg.mkBrush(QColor(255, 99, 132))][:len(pts)])
        self.mk_text.show()
        self.mk_dots.show()

    def reset_display(self):
        self.avg = self.peak = self.wf = self.wf_key = None
        self.occ_hits = None
        self.occ_sweeps = 0

    # ---- plotting -------------------------------------------------------
    def setup_bottom(self):
        for it in self.occ_items:
            self.p_bt.removeItem(it)
        self.occ_items = []
        live = self.get_cfg("mode") == "live"
        self.c_env.setVisible(live)
        ax = self.p_bt.getAxis("bottom")
        if live:
            self.p_bt.setXLink(None)
            ax.setTicks(None)
            self.p_bt.setYRange(-70, 0, padding=0)
        else:
            # bars sit at each channel's centre frequency, sharing the spectrum's x axis
            self.p_bt.setXLink(self.p_sp)
        self.label_bottom()

    def label_bottom(self):
        if self.get_cfg("mode") == "live":
            self.p_bt.setLabel("bottom", tr("時間 [µs]　1 回の取得内の受信電力（Wi-Fi パケットのバースト）"))
            self.p_bt.setLabel("left", "dBFS")
        else:
            self.p_bt.setLabel("bottom", tr("チャンネル（中心周波数の位置）　棒: ピーク − ノイズフロア / 数字: 検出率 %"))
            self.p_bt.setLabel("left", tr("活動 [dB]"))

    def update_overlay(self, fmin, fmax, center=None):
        ylo, yhi = self.p_sp.viewRange()[1]
        key = (round(fmin, 1), round(fmax, 1), center, self.sel_width, self.ov_sw.isChecked(),
               round(ylo, 1), round(yhi, 1))
        if key == self.overlay_key:
            return
        self.overlay_key = key
        for it in self.overlay_items:
            self.p_sp.removeItem(it)
        self.overlay_items = []
        acc = themeColor()
        if center is not None:
            w = self.sel_width
            reg = pg.LinearRegionItem((center - w / 2, center + w / 2), movable=False,
                                      brush=QColor(acc.red(), acc.green(), acc.blue(), 28),
                                      pen=pg.mkPen(None))
            reg.setZValue(-10)
            self.p_sp.addItem(reg)
            self.overlay_items.append(reg)
        if not self.ov_sw.isChecked():
            return
        band = wc.band_of((fmin + fmax) / 2)
        chans = [(c, f) for c, f in wc.channels_20(band) if fmin - 10 < f < fmax + 10]
        if not chans:
            return
        h = (yhi - ylo) * 0.05
        dark = isDarkTheme()
        brushes = [QColor(acc.red(), acc.green(), acc.blue(), 150) if i % 2 else
                   QColor(150, 150, 150, 90 if dark else 70) for i in range(len(chans))]
        bars = pg.BarGraphItem(x=[f for _, f in chans], width=19.6, y0=yhi - h, height=h,
                               brushes=brushes, pen=pg.mkPen(None))
        self.p_sp.addItem(bars)
        self.overlay_items.append(bars)
        step = 1 if len(chans) <= 24 else 2
        for i, (c, f) in enumerate(chans):
            if i % step:
                continue
            t = pg.TextItem(str(c), color="#ffffff" if dark else "#202020", anchor=(0.5, 0.5))
            t.setPos(f, yhi - h / 2)
            self.p_sp.addItem(t)
            self.overlay_items.append(t)

    def draw_occupancy(self, m):
        chans, act = channel_activity(m["band"], m["f"], m["mean"], m["max"])
        if self.occ_hits is None or len(self.occ_hits) != len(chans):
            self.occ_hits = np.zeros(len(chans))
            self.occ_sweeps = 0
        self.occ_sweeps += 1
        self.occ_hits += act > 12
        pct = 100 * self.occ_hits / self.occ_sweeps
        for it in self.occ_items:
            self.p_bt.removeItem(it)
        x = np.array([fc for _, fc in chans], dtype=float)
        spacing = float(np.min(np.diff(x))) if len(x) > 1 else 20.0  # 5 MHz on 2.4 GHz, 20 MHz on 5 GHz
        cols = [QColor.fromRgbF(*self.cmap.mapToFloat(float(np.clip(a / 40, 0, 1)))) for a in act]
        bars = pg.BarGraphItem(x=x, height=np.nan_to_num(act), width=spacing * 0.75, brushes=cols,
                               pen=pg.mkPen(None))
        self.p_bt.addItem(bars)
        self.occ_items = [bars]
        fg = "#d0d0d0" if isDarkTheme() else "#404040"
        for xi, a, p in zip(x, act, pct):
            t = pg.TextItem(f"{p:.0f}", color=fg, anchor=(0.5, 1))
            t.setPos(xi, float(np.nan_to_num(a)) + 0.3)
            self.p_bt.addItem(t)
            self.occ_items.append(t)
        # label every channel if there is room (~30 px each), otherwise every other one
        span = max(1.0, float(np.ptp(m["f"])))
        px = self.p_bt.vb.width() * spacing / span
        step = 1 if px >= 30 else 2
        ticks = [(fc, str(c)) for c, fc in chans]
        self.p_bt.getAxis("bottom").setTicks([ticks[::step], ticks[1::2] if step == 2 else []])
        self.p_bt.setYRange(0, max(30.0, float(np.nanmax(act)) + 8), padding=0)

    # ---- data -------------------------------------------------------------
    def poll(self):
        live, sweep = [], None
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
                    self.device_page.log(m["msg"])
                elif k == "error":
                    self.device_page.log(tr("エラー: {}").format(m["msg"]))
                    if m["fatal"]:
                        InfoBar.error(tr("接続エラー"), m["msg"], duration=5000, parent=self.window())
                        self.set_connected_ui("idle")
                elif k == "connected":
                    self.set_connected_ui("on")
                    self.device_page.set_connected(m)
                    lim = m["limits"]
                    if lim.get("gain"):
                        self.gain_sl.setMaximum(lim["gain"][1])
                    InfoBar.success(tr("接続しました"), f"{m['port']}   {m['info']}", duration=2500,
                                    parent=self.window())
                elif k == "disconnected":
                    self.set_connected_ui("idle")
                    self.device_page.set_disconnected()
        except queue.Empty:
            pass
        # Fold every capture received since the last frame into the display.
        if live:
            self.draw(live)
        elif sweep:
            self.draw([sweep])

    def draw(self, msgs):
        m = msgs[-1]
        f = m["f"]
        key = (m["kind"], len(f), float(f[0]), float(f[-1]))
        alpha = self.avg_sl.value() / 100
        if key != self.wf_key or self.avg is None or self.avg.shape != m["mean"].shape:
            self.wf_key = key
            self.avg = m["mean"].copy()
            self.peak = m["max"].copy()
            rows = WF_ROWS if m["kind"] == "live" else 60
            self.wf = np.full((rows, len(f)), np.nan)
            self.p_sp.setXRange(f[0], f[-1], padding=0)
            self.p_wf.setYRange(0, rows, padding=0)
        rows = []
        for x in msgs:
            if x["mean"].shape != self.avg.shape:
                continue
            if x["kind"] == "live":
                self.avg = 10 * np.log10(alpha * 10 ** (self.avg / 10) + (1 - alpha) * 10 ** (x["mean"] / 10))
            else:
                self.avg = np.where(np.isnan(x["mean"]), self.avg, x["mean"])
            self.peak = np.fmax(self.peak, x["max"]) if self.peak_sw.isChecked() else x["max"]
            if x["kind"] == "live" or x["done"]:
                rows.append(x["max"] if self.wf_seg.currentRouteKey() == "max" else x["mean"])
        self.c_avg.setData(f, np.nan_to_num(self.avg, nan=-200), connect="finite")
        self.c_peak.setData(f, np.nan_to_num(self.peak, nan=-200), connect="finite")
        if rows:
            k = min(len(rows), len(self.wf))
            self.wf = np.roll(self.wf, k, axis=0)
            self.wf[:k] = np.array(rows[::-1][:k])
        lo, hi = self.wf_levels
        # rect is passed with every image: setRect before the first image has no size to scale
        shown = np.nan_to_num(np.clip(self.wf, lo + (hi - lo) / 200, None), nan=lo - 20)
        self.img.setImage(shown, autoLevels=False, levels=(lo, hi),
                          rect=QRectF(f[0], 0, f[-1] - f[0], len(self.wf)))

        now = time.time()
        self.cap_t = [t for t in self.cap_t if now - t < 2] + [now] * len(msgs)
        if m["kind"] == "live":
            self.update_overlay(f[0], f[-1], m["freq"])
            self.c_env.setData(m["t_us"], m["env"])
            if len(m["t_us"]):
                self.p_bt.setXRange(0, m["t_us"][-1], padding=0)
        else:
            self.update_overlay(f[0], f[-1], None)
            if m["done"]:
                self.draw_occupancy(m)
        self.update_texts(m)
        if m["kind"] == "live" or m["done"]:
            self.update_stats(m)
        if self.auto_frames and (m["kind"] == "live" or m["done"]):
            self.auto_frames = max(0, self.auto_frames - len(msgs))
            if self.auto_frames == 0:
                self.autoscale(force=True)
                self.last_auto = now
        elif self.auto_y and now - self.last_auto > 1.0:
            self.autoscale()
            self.last_auto = now
        self.f_cur = f
        self.update_marker()

    def update_texts(self, m):
        self.last_m = m
        if m["kind"] == "live":
            lo_txt = tr("　LO {} MHz").format(m["lo"]) if m["lo"] != m["freq"] else ""
            self.plot_title.setText(tr("ライブ　{rate} MS/s × {n} サンプル ({us} µs / 回){lo}").format(
                rate=f"{m['fs'] / 1e6:g}", n=m["n"], us=f"{m['n'] / m['fs'] * 1e6:.0f}", lo=lo_txt))
        else:
            f = m["f"]
            self.plot_title.setText(tr("バンドスイープ　{band}　{f0}–{f1} MHz　ステップ {step}/{steps}").format(
                band=m["band"], f0=f"{f[0]:.0f}", f1=f"{f[-1]:.0f}", step=m["step"], steps=m["steps"]))

    def update_stats(self, m):
        self.last_stats_m = m
        if m["kind"] == "live":
            gain = self.get_cfg("gain")
            self.stat[0].set(tr("中心周波数　{}").format(wc.band_of(m["freq"])), f"{m['freq']} MHz")
            self.stat[1].set(tr("取得レート"), tr("{:.1f} 回/秒").format(len(self.cap_t) / 2))
            self.stat[2].set("RMS", f"{m['rms_db']:.1f} dBFS")
            self.stat[3].set(tr("クリップ"), f"{m['clip']:.2f} %")
            self.stat[4].set(tr("ゲイン"), "AGC" if gain is None else tr("手動 {}").format(gain))
        else:
            self.stat[0].set(tr("バンド"), m["band"])
            self.stat[1].set(tr("1 スイープ"), tr("{:.2f} 秒").format(m["sweep_s"]))
            self.stat[2].set(tr("ステップ"), f"{m['steps']} × {SWEEP_STEP_MHZ} MHz")
            self.stat[3].set(tr("スイープ回数"), f"{self.occ_sweeps}")
            self.stat[4].set(tr("ゲイン"), tr("手動 {}").format(self.get_cfg("manual_gain")))


# --------------------------------------------------------------------------
# Device and settings pages
# --------------------------------------------------------------------------
class DevicePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("devicePage")
        v = QVBoxLayout(self)
        v.setContentsMargins(28, 14, 28, 20)
        v.setSpacing(12)
        title = TitleLabel()
        bind(title.setText, "デバイス")
        v.addWidget(title)
        s = Section("接続情報")
        self.lbl = {}
        for k in ("状態", "ポート", "ファームウェア", "最大サンプル数", "ゲイン範囲", "アナログ帯域", "サンプルレート"):
            self.lbl[k] = s.row(k, BodyLabel("—"))
        self.state = "未接続"
        i18n.on_change(lambda: self.lbl["状態"].setText(tr(self.state)))
        self.lbl["状態"].setText(tr(self.state))
        v.addWidget(s)
        s = Section("ログ")
        self.text = PlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMinimumHeight(220)
        s.row("", self.text)
        v.addWidget(s, 1)
        h = QHBoxLayout()
        b = PushButton(FluentIcon.FOLDER, "")
        bind(b.setText, "I/Q 保存フォルダを開く")
        b.clicked.connect(self.open_recordings)
        h.addWidget(b)
        h.addStretch(1)
        v.addLayout(h)

    def log(self, msg):
        self.text.appendPlainText(time.strftime("%H:%M:%S  ") + msg)

    def set_connected(self, m):
        lim = m["limits"]
        self.state = "接続中"
        self.lbl["状態"].setText(tr(self.state))
        self.lbl["ポート"].setText(m["port"])
        self.lbl["ファームウェア"].setText(m["info"])
        self.lbl["最大サンプル数"].setText(str(m["max_samples"]))
        if lim.get("gain"):
            self.lbl["ゲイン範囲"].setText(f"{lim['gain'][0]} – {lim['gain'][1]}")
        if lim.get("bandwidth"):
            self.lbl["アナログ帯域"].setText(f"{lim['bandwidth'][0]} – {lim['bandwidth'][1]} MHz")
        if lim.get("rates"):
            self.lbl["サンプルレート"].setText(" / ".join(f"{r // 1_000_000}" for r in lim["rates"]) + " MS/s")
        self.log(tr("接続: {port}  {info}").format(port=m["port"], info=m["info"]))

    def set_disconnected(self):
        self.state = "未接続"
        self.lbl["状態"].setText(tr(self.state))
        self.log(tr("切断しました"))

    def open_recordings(self):
        d = os.path.join(HERE, "recordings")
        os.makedirs(d, exist_ok=True)
        os.startfile(d)


class SettingsPage(QWidget):
    def __init__(self, sdr_page, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsPage")
        v = QVBoxLayout(self)
        v.setContentsMargins(28, 14, 28, 20)
        v.setSpacing(12)
        title = TitleLabel()
        bind(title.setText, "設定")
        v.addWidget(title)
        s = Section("外観")
        self.lang_seg = SegmentedWidget()
        for key, name in i18n.LANGS.items():
            self.lang_seg.addItem(key, name, lambda key=key: i18n.set_lang(key))
        self.lang_seg.setCurrentItem(i18n.lang())
        s.row("言語", self.lang_seg)
        self.theme_seg = SegmentedWidget()
        for key, text, th in (("light", "ライト", Theme.LIGHT), ("dark", "ダーク", Theme.DARK),
                              ("auto", "システム", Theme.AUTO)):
            seg_item(self.theme_seg, key, text, lambda th=th: setTheme(th))
        self.theme_seg.setCurrentItem("dark" if isDarkTheme() else "light")
        s.row("テーマ", self.theme_seg)
        cm = ComboBox()
        cm.addItems(CMAPS)
        cm.currentTextChanged.connect(sdr_page.set_colormap)
        s.row("滝表示の配色", cm)
        v.addWidget(s)
        s = Section("このアプリについて")
        about = BodyLabel()
        bind(about.setText,
             "ESP32-C5 の内蔵 Wi-Fi 6 デュアルバンド無線を SDR として使い、2.4 GHz / 5 GHz 帯を表示します。\n"
             "ファームウェア: ESPARGOS ESP-SDR (GPL-3.0)  https://github.com/ESPARGOS/esp-sdr\n"
             "dBFS は未校正の相対値です。受信のみで、送信は行いません。")
        about.setWordWrap(True)
        s.row("", about)
        v.addWidget(s)
        v.addStretch(1)


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ESP32-C5 Wi-Fi SDR")
        self.setWindowIcon(FluentIcon.WIFI.icon())
        self.resize(1560, 980)
        self.device = DevicePage(self)
        self.sdr = SdrPage(self.device, self)
        self.settings = SettingsPage(self.sdr, self)
        for page, icon, text, pos in ((self.sdr, FluentIcon.WIFI, "スペクトラム", NavigationItemPosition.TOP),
                                      (self.device, FluentIcon.IOT, "デバイス", NavigationItemPosition.TOP),
                                      (self.settings, FluentIcon.SETTING, "設定", NavigationItemPosition.BOTTOM)):
            item = self.addSubInterface(page, icon, tr(text), pos)
            bind(item.setText, text)
            bind(item.setToolTip, text)
        self.navigationInterface.setExpandWidth(200)

    def closeEvent(self, e):
        self.sdr.stop()
        super().closeEvent(e)


def main():
    ap = argparse.ArgumentParser(description="ESP32-C5 Wi-Fi SDR (Fluent UI)")
    ap.add_argument("port", nargs="?", help="serial port (default: auto-detect)")
    ap.add_argument("--freq", type=int, help="start center frequency [MHz]")
    ap.add_argument("--sweep", choices=["2.4", "5"], help="start in band-sweep mode")
    ap.add_argument("--samples", choices=["2048", "4096", "8192", "16380"], help="samples per capture")
    ap.add_argument("--light", action="store_true", help="light theme")
    ap.add_argument("--lang", choices=list(i18n.LANGS), help="UI language (default: last used, else the OS language)")
    args = ap.parse_args()
    i18n.set_lang(args.lang or i18n.load_lang(), save=bool(args.lang))

    app = QApplication(sys.argv)
    setFontFamilies(["Segoe UI", "Yu Gothic UI", "Meiryo UI"])
    setTheme(Theme.LIGHT if args.light else Theme.DARK)
    w = MainWindow()
    sdr = w.sdr
    if args.port:
        if sdr.port_box.findText(args.port) < 0:
            sdr.port_box.addItem(args.port)
        sdr.port_box.setCurrentText(args.port)
    if args.samples:
        sdr.ns_box.setCurrentText(args.samples)
    if args.freq:
        sdr.freq_spin.setValue(args.freq)
        sdr.on_freq()
    if args.sweep:
        sdr.on_band(args.sweep + " GHz", set_freq=False)
        sdr.set_mode("sweep")
    w.show()
    QTimer.singleShot(300, sdr.toggle_connect)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
