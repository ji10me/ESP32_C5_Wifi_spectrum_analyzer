# SPDX-License-Identifier: GPL-3.0-or-later
"""UI language (Japanese / English / Simplified Chinese) shared by both front-ends.

Japanese text is the key; tr() returns it as is, its ZH entry, or its English
entry. The language can change at run time: widgets register their text with
bind() and are re-labelled by set_lang(); on_change() callbacks redo anything
computed. The choice is saved in settings.json next to the app.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(HERE, "settings.json")
LANGS = {"ja": "日本語", "en": "English", "zh": "简体中文"}

_lang = "en"
_bindings = []
_listeners = []


def tr(text):
    if _lang == "ja":
        return text
    if _lang == "zh":
        # Fall back to English, never to Japanese, so a missing entry cannot
        # leave a Chinese UI with stray Japanese words.
        return ZH.get(text) or EN.get(text, text)
    return EN.get(text, text)


def lang():
    return _lang


def bind(setter, text):
    """Call setter(tr(text)) now and again whenever the language changes."""
    setter(tr(text))
    _bindings.append((setter, text))


def on_change(callback):
    _listeners.append(callback)


def set_lang(new, save=True):
    global _lang
    if new not in LANGS:
        return
    _lang = new
    if save:
        _save({"lang": new})
    for setter, text in list(_bindings):
        try:
            setter(tr(text))
        except Exception:  # the Qt / Tk widget behind the setter was destroyed
            _bindings.remove((setter, text))
    for cb in _listeners:
        cb()


def load_lang():
    """Saved choice, else English (the default UI language)."""
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            saved = json.load(f).get("lang")
        if saved in LANGS:
            return saved
    except (OSError, ValueError):
        pass
    return "en"


def _save(update):
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data.update(update)
    try:
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


EN = {
    # common
    "オン": "On",
    "オフ": "Off",
    "接続": "Connect",
    "切断": "Disconnect",
    "接続中…": "Connecting…",
    "未接続": "Not connected",
    "自動検出": "Auto-detect",
    "最大": "Max",
    "平均": "Mean",
    "ピーク": "Peak",
    "ライブ": "Live",
    "バンドスイープ": "Band sweep",
    "周波数": "Frequency",
    "中心": "Center",
    "サンプルレート": "Sample rate",
    "FFT サイズ": "FFT size",
    "自動ゲイン (AGC)": "Auto gain (AGC)",
    "表示": "Display",
    "ピークホールド": "Peak hold",
    "dB 範囲": "dB range",
    "周波数 [MHz]": "Frequency [MHz]",
    "活動 [dB]": "Activity [dB]",
    "エラー: {}": "Error: {}",
    "接続: {port}  {info}": "Connected: {port}  {info}",
    "言語": "Language",
    # sdr_core
    "ESP-SDR を実行中のデバイスが見つかりません": "No device running the ESP-SDR firmware was found",
    "切断: {}": "Disconnected: {}",
    "保存: {}.npy / .cfile": "Saved: {}.npy / .cfile",
    # sdr_fluent: spectrum page
    "ポート一覧を更新": "Refresh port list",
    "一時停止 / 再開": "Pause / resume",
    "次の取得を I/Q 保存 (.npy / .cfile)": "Save the next capture as I/Q (.npy / .cfile)",
    "中心周波数": "Center frequency",
    "取得レート": "Capture rate",
    "クリップ": "Clipping",
    "ゲイン": "Gain",
    "クリック: その周波数のレベルを表示（マーカーはドラッグで移動）/ "
    "ダブルクリック: 同調 / 右クリック: マーカー消去 / ホイール: 拡大":
        "Click: show the level at that frequency (drag the marker to move it) / "
        "Double-click: tune there / Right-click: remove the marker / Wheel: zoom",
    "受信": "Receiver",
    "サンプル数": "Samples",
    "アナログ帯域": "Analog bandwidth",
    "LO オフセット": "LO offset",
    "LO を見たいチャンネルからずらし、中心の DC / LO 漏れを避けます":
        "Moves the LO away from the channel you are watching to avoid the DC / LO leakage at the center",
    "手動ゲイン": "Manual gain",
    "AGC オフ時のゲイン。スイープ中は常にこの値を使います":
        "Gain used while AGC is off. Band sweeps always use this value",
    "平均化": "Averaging",
    "ピーク / 滝表示をリセット": "Reset peak hold / waterfall",
    "滝表示": "Waterfall",
    "チャンネル表示": "Channels",
    "DC 除去": "DC removal",
    "自動スケール（データに追従）": "Autoscale (follow the data)",
    "履歴": "History",
    "I/Q 保存": "Save I/Q",
    "ライブモードで使えます": "Available in live mode",
    "Wi-Fi チャンネル外": "outside Wi-Fi channels",
    "{freq} MHz   {ch}\n平均レベル  {avg}\nピーク      {peak}":
        "{freq} MHz   {ch}\nMean   {avg}\nPeak   {peak}",
    "時間 [µs]　1 回の取得内の受信電力（Wi-Fi パケットのバースト）":
        "Time [µs]   received power within one capture (Wi-Fi packet bursts)",
    "チャンネル（中心周波数の位置）　棒: ピーク − ノイズフロア / 数字: 検出率 %":
        "Channel (at its center frequency)   bar: peak − noise floor / number: detection rate %",
    "接続エラー": "Connection error",
    "接続しました": "Connected",
    "　LO {} MHz": "   LO {} MHz",
    "ライブ　{rate} MS/s × {n} サンプル ({us} µs / 回){lo}":
        "Live   {rate} MS/s × {n} samples ({us} µs / capture){lo}",
    "中心周波数　{}": "Center frequency   {}",
    "{:.1f} 回/秒": "{:.1f} /s",
    "手動 {}": "Manual {}",
    "バンドスイープ　{band}　{f0}–{f1} MHz　ステップ {step}/{steps}":
        "Band sweep   {band}   {f0}–{f1} MHz   step {step}/{steps}",
    "バンド": "Band",
    "1 スイープ": "Sweep time",
    "{:.2f} 秒": "{:.2f} s",
    "ステップ": "Steps",
    "スイープ回数": "Sweeps",
    # sdr_fluent: device / settings pages, navigation
    "スペクトラム": "Spectrum",
    "デバイス": "Device",
    "設定": "Settings",
    "接続情報": "Connection",
    "状態": "Status",
    "ポート": "Port",
    "ファームウェア": "Firmware",
    "最大サンプル数": "Max samples",
    "ゲイン範囲": "Gain range",
    "接続中": "Connected",
    "ログ": "Log",
    "I/Q 保存フォルダを開く": "Open the I/Q recordings folder",
    "切断しました": "Disconnected",
    "外観": "Appearance",
    "テーマ": "Theme",
    "ライト": "Light",
    "ダーク": "Dark",
    "システム": "System",
    "滝表示の配色": "Waterfall colors",
    "このアプリについて": "About",
    "ESP32-C5 の内蔵 Wi-Fi 6 デュアルバンド無線を SDR として使い、2.4 GHz / 5 GHz 帯を表示します。\n"
    "ファームウェア: ESPARGOS ESP-SDR (GPL-3.0)  https://github.com/ESPARGOS/esp-sdr\n"
    "dBFS は未校正の相対値です。受信のみで、送信は行いません。":
        "Uses the built-in Wi-Fi 6 dual-band radio of the ESP32-C5 as an SDR to show the 2.4 GHz / 5 GHz bands.\n"
        "Firmware: ESPARGOS ESP-SDR (GPL-3.0)  https://github.com/ESPARGOS/esp-sdr\n"
        "dBFS values are uncalibrated relative levels. Receive only; nothing is transmitted.",
    # sdr_app (tkinter)
    "モード": "Mode",
    "ライブ (中心周波数固定)": "Live (fixed center frequency)",
    "バンドスイープ (帯域全体)": "Band sweep (whole band)",
    "バンド / Wi-Fi チャンネル": "Band / Wi-Fi channel",
    "中心 [MHz]": "Center [MHz]",
    "受信設定": "Receiver",
    "サンプル数/取得": "Samples/capture",
    "アナログ帯域 [MHz]": "Analog BW [MHz]",
    "LO オフセット [MHz]": "LO offset [MHz]",
    "手動ゲイン: {}  (スイープは常に手動)": "Manual gain: {}  (sweeps always use it)",
    "DC オフセット除去": "DC offset removal",
    "スペクトラム平均化": "Spectrum averaging",
    "リセット": "Reset",
    "ウォーターフォール:": "Waterfall:",
    "Wi-Fi チャンネル表示": "Wi-Fi channel overlay",
    "自動": "Auto",
    "操作": "Control",
    "一時停止": "Pause",
    "再開": "Resume",
    "IQ 保存": "Save IQ",
    "スペクトラムをクリック → その周波数へ同調": "Click the spectrum to tune to that frequency",
    "履歴 (新→旧)": "History (new → old)",
    "時間 [µs]  (1 回の取得内の受信電力 — Wi-Fi パケットのバースト)":
        "Time [µs]  (received power within one capture — Wi-Fi packet bursts)",
    "20 MHz チャンネル  (棒: ピーク - ノイズフロア / 数字: 検出率 %)":
        "20 MHz channel  (bar: peak - noise floor / number: detection rate %)",
    "{width} MHz 幅: 受信帯域は最大 ~48 MHz のため中心部のみ表示":
        "{width} MHz wide: only the center is shown (receive bandwidth is at most ~48 MHz)",
    "ゲイン {gain}  帯域 {bw}": "Gain {gain}  bandwidth {bw}",
    "ライブ  中心 {freq} MHz{lo}  ({band})   {rate} MS/s × {n} サンプル ({us} µs)":
        "Live  center {freq} MHz{lo}  ({band})   {rate} MS/s × {n} samples ({us} µs)",
    "取得   {cap:5.1f} 回/秒\n描画   {fps:5.1f} 回/秒\nRMS   {rms:6.1f} dBFS\nクリップ {clip:5.2f} %\nゲイン  {gain}":
        "Capture {cap:5.1f} /s\nDraw    {fps:5.1f} /s\nRMS   {rms:6.1f} dBFS\nClip  {clip:5.2f} %\nGain  {gain}",
    "バンドスイープ  {band}  ({f0}–{f1} MHz)": "Band sweep  {band}  ({f0}–{f1} MHz)",
    "1 スイープ {s:.2f} 秒\n{steps} ステップ × {step} MHz\nゲイン {gain} (手動)\nスイープ回数 {n}":
        "Sweep {s:.2f} s\n{steps} steps × {step} MHz\nGain {gain} (manual)\nSweeps {n}",
}

# Simplified Chinese. Terminology follows common SDR / spectrum-analyser usage;
# the Japanese key and its Chinese rendering are given with the English meaning
# so the mapping stays readable for maintainers who do not read Chinese:
#   スペクトラム → 频谱    (spectrum)          滝表示       → 瀑布图  (waterfall)
#   ノイズフロア → 噪声底  (noise floor)       同調         → 调谐    (tune)
#   チャンネル   → 信道    (channel)           クリップ     → 削顶    (clipping)
#   検出率       → 检出率  (detection rate)    サンプル     → 采样点  (samples)
#   バンド       → 频段    (band)              スイープ     → 扫描    (sweep)
#   ゲイン       → 增益    (gain)              平均化       → 平均次数 (averaging)
ZH = {
    # common
    "オン": "开",
    "オフ": "关",
    "接続": "连接",
    "切断": "断开",
    "接続中…": "正在连接…",
    "未接続": "未连接",
    "自動検出": "自动检测",
    "最大": "最大",
    "平均": "平均",
    "ピーク": "峰值",
    "ライブ": "实时",
    "バンドスイープ": "频段扫描",
    "周波数": "频率",
    "中心": "中心",
    "サンプルレート": "采样率",
    "FFT サイズ": "FFT 点数",
    "自動ゲイン (AGC)": "自动增益 (AGC)",
    "表示": "显示",
    "ピークホールド": "峰值保持",
    "dB 範囲": "dB 范围",
    "周波数 [MHz]": "频率 [MHz]",
    "活動 [dB]": "活动度 [dB]",
    "エラー: {}": "错误：{}",
    "接続: {port}  {info}": "已连接：{port}  {info}",
    "言語": "语言",
    # sdr_core
    "ESP-SDR を実行中のデバイスが見つかりません": "未找到正在运行 ESP-SDR 固件的设备",
    "切断: {}": "已断开：{}",
    "保存: {}.npy / .cfile": "已保存：{}.npy / .cfile",
    # sdr_fluent: spectrum page
    "Wi-Fi Spectrum": "Wi-Fi 频谱",
    "ポート一覧を更新": "刷新端口列表",
    "一時停止 / 再開": "暂停 / 继续",
    "次の取得を I/Q 保存 (.npy / .cfile)": "把下一次采集保存为 I/Q 文件 (.npy / .cfile)",
    "中心周波数": "中心频率",
    "取得レート": "采集速率",
    "クリップ": "削顶",
    "ゲイン": "增益",
    "クリック: その周波数のレベルを表示（マーカーはドラッグで移動）/ "
    "ダブルクリック: 同調 / 右クリック: マーカー消去 / ホイール: 拡大":
        "单击：显示该频率处的电平（可拖动标记）/ "
        "双击：调谐到该频率 / 右键：清除标记 / 滚轮：缩放",
    "受信": "接收",
    "サンプル数": "采样点数",
    "アナログ帯域": "模拟带宽",
    "LO オフセット": "LO 偏移",
    "LO を見たいチャンネルからずらし、中心の DC / LO 漏れを避けます":
        "把 LO 从要观察的信道移开，避开中心处的 DC / LO 泄漏",
    "手動ゲイン": "手动增益",
    "AGC オフ時のゲイン。スイープ中は常にこの値を使います":
        "AGC 关闭时使用的增益；频段扫描时始终使用该值",
    "平均化": "平均次数",
    "ピーク / 滝表示をリセット": "复位峰值 / 瀑布图",
    "滝表示": "瀑布图",
    "チャンネル表示": "信道显示",
    "DC 除去": "去除 DC",
    "自動スケール（データに追従）": "自动缩放（跟随数据）",
    "履歴": "历史",
    "I/Q 保存": "保存 I/Q",
    "ライブモードで使えます": "仅实时模式可用",
    "Wi-Fi チャンネル外": "在 Wi-Fi 信道之外",
    "{freq} MHz   {ch}\n平均レベル  {avg}\nピーク      {peak}":
        "{freq} MHz   {ch}\n平均电平  {avg}\n峰值      {peak}",
    "時間 [µs]　1 回の取得内の受信電力（Wi-Fi パケットのバースト）":
        "时间 [µs]　单次采集内的接收功率（Wi-Fi 数据包突发）",
    "チャンネル（中心周波数の位置）　棒: ピーク − ノイズフロア / 数字: 検出率 %":
        "信道（位于其中心频率）　柱：峰值 − 噪声底 / 数字：检出率 %",
    "接続エラー": "连接错误",
    "接続しました": "已连接",
    "　LO {} MHz": "　LO {} MHz",
    "ライブ　{rate} MS/s × {n} サンプル ({us} µs / 回){lo}":
        "实时　{rate} MS/s × {n} 采样点 ({us} µs / 次){lo}",
    "中心周波数　{}": "中心频率　{}",
    "{:.1f} 回/秒": "{:.1f} 次/秒",
    "手動 {}": "手动 {}",
    "バンドスイープ　{band}　{f0}–{f1} MHz　ステップ {step}/{steps}":
        "频段扫描　{band}　{f0}–{f1} MHz　步进 {step}/{steps}",
    "バンド": "频段",
    "1 スイープ": "单次扫描",
    "{:.2f} 秒": "{:.2f} 秒",
    "ステップ": "步数",
    "スイープ回数": "扫描次数",
    # sdr_fluent: device / settings pages, navigation
    "スペクトラム": "频谱",
    "デバイス": "设备",
    "設定": "设置",
    "接続情報": "连接信息",
    "状態": "状态",
    "ポート": "端口",
    "ファームウェア": "固件",
    "最大サンプル数": "最大采样点数",
    "ゲイン範囲": "增益范围",
    "接続中": "已连接",
    "ログ": "日志",
    "I/Q 保存フォルダを開く": "打开 I/Q 记录文件夹",
    "切断しました": "已断开",
    "外観": "外观",
    "テーマ": "主题",
    "ライト": "浅色",
    "ダーク": "深色",
    "システム": "跟随系统",
    "滝表示の配色": "瀑布图配色",
    "このアプリについて": "关于本应用",
    "ESP32-C5 の内蔵 Wi-Fi 6 デュアルバンド無線を SDR として使い、2.4 GHz / 5 GHz 帯を表示します。\n"
    "ファームウェア: ESPARGOS ESP-SDR (GPL-3.0)  https://github.com/ESPARGOS/esp-sdr\n"
    "dBFS は未校正の相対値です。受信のみで、送信は行いません。":
        "使用 ESP32-C5 内置的 Wi-Fi 6 双频无线电作为 SDR，显示 2.4 GHz / 5 GHz 频段。\n"
        "固件：ESPARGOS ESP-SDR (GPL-3.0)  https://github.com/ESPARGOS/esp-sdr\n"
        "dBFS 为未校准的相对值。仅接收，不发射。",
    # sdr_app (tkinter)
    "モード": "模式",
    "ライブ (中心周波数固定)": "实时（中心频率固定）",
    "バンドスイープ (帯域全体)": "频段扫描（整个频段）",
    "バンド / Wi-Fi チャンネル": "频段 / Wi-Fi 信道",
    "中心 [MHz]": "中心 [MHz]",
    "受信設定": "接收设置",
    "サンプル数/取得": "每帧采样点数",
    "アナログ帯域 [MHz]": "模拟带宽 [MHz]",
    "LO オフセット [MHz]": "LO 偏移 [MHz]",
    "手動ゲイン: {}  (スイープは常に手動)": "手动增益：{}（扫描时始终使用）",
    "DC オフセット除去": "去除 DC 偏置",
    "スペクトラム平均化": "频谱平均",
    "リセット": "复位",
    "ウォーターフォール:": "瀑布图：",
    "Wi-Fi チャンネル表示": "Wi-Fi 信道显示",
    "自動": "自动",
    "操作": "操作",
    "一時停止": "暂停",
    "再開": "继续",
    "IQ 保存": "保存 IQ",
    "スペクトラムをクリック → その周波数へ同調": "点击频谱 → 调谐到该频率",
    "履歴 (新→旧)": "历史（新→旧）",
    "時間 [µs]  (1 回の取得内の受信電力 — Wi-Fi パケットのバースト)":
        "时间 [µs]  （单次采集内的接收功率 — Wi-Fi 数据包突发）",
    "20 MHz チャンネル  (棒: ピーク - ノイズフロア / 数字: 検出率 %)":
        "20 MHz 信道  （柱：峰值 - 噪声底 / 数字：检出率 %）",
    "{width} MHz 幅: 受信帯域は最大 ~48 MHz のため中心部のみ表示":
        "{width} MHz 带宽：接收带宽最大约 48 MHz，因此只显示中心部分",
    "ゲイン {gain}  帯域 {bw}": "增益 {gain}  带宽 {bw}",
    "ライブ  中心 {freq} MHz{lo}  ({band})   {rate} MS/s × {n} サンプル ({us} µs)":
        "实时  中心 {freq} MHz{lo}  （{band}）   {rate} MS/s × {n} 采样点（{us} µs）",
    "取得   {cap:5.1f} 回/秒\n描画   {fps:5.1f} 回/秒\nRMS   {rms:6.1f} dBFS\nクリップ {clip:5.2f} %\nゲイン  {gain}":
        "采集   {cap:5.1f} 次/秒\n绘制   {fps:5.1f} 次/秒\nRMS   {rms:6.1f} dBFS\n削顶 {clip:5.2f} %\n增益  {gain}",
    "バンドスイープ  {band}  ({f0}–{f1} MHz)": "频段扫描  {band}  ({f0}–{f1} MHz)",
    "1 スイープ {s:.2f} 秒\n{steps} ステップ × {step} MHz\nゲイン {gain} (手動)\nスイープ回数 {n}":
        "单次扫描 {s:.2f} 秒\n{steps} 步 × {step} MHz\n增益 {gain}（手动）\n扫描次数 {n}",
}
