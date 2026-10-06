# SPDX-License-Identifier: GPL-3.0-or-later
"""Wi-Fi channel plans for the bands the ESP32-C5 receives (2.4 GHz and 5 GHz).

Wi-Fi 6 (802.11ax) uses 20/40/80/160 MHz channels on the same 2.4/5 GHz
channel grid as earlier standards. 6 GHz (Wi-Fi 6E) is outside the C5's range.
"""

BANDS = {
    "2.4 GHz": (2400, 2500),
    "5 GHz": (5150, 5895),
}


def chan_freq(ch):
    """Center frequency (MHz) of a channel number."""
    if ch == 14:
        return 2484
    if ch <= 13:
        return 2407 + 5 * ch
    return 5000 + 5 * ch


# 20 MHz primary channels
CH24_20 = list(range(1, 15))
CH5_20 = ([36, 40, 44, 48, 52, 56, 60, 64]
          + list(range(100, 145, 4))
          + list(range(149, 178, 4)))  # 169-177: U-NII-4

# Bonded channel centers (channel numbers name the center frequency)
CH5_40 = [38, 46, 54, 62, 102, 110, 118, 126, 134, 142, 151, 159, 167, 175]
CH5_80 = [42, 58, 106, 122, 138, 155, 171]
CH5_160 = [50, 114, 163]
# 2.4 GHz 40 MHz: primary ch N with secondary above (N+4); center = N+2
CH24_40 = list(range(3, 12))

_U_NII = [(36, 48, "U-NII-1"), (52, 64, "U-NII-2A"), (100, 144, "U-NII-2C"),
          (149, 165, "U-NII-3"), (169, 177, "U-NII-4")]


def _subband(ch):
    for a, b, name in _U_NII:
        if a <= ch <= b:
            return name
    return ""


def channel_presets(band):
    """List of (label, center_MHz, width_MHz) for the channel selector."""
    out = []
    if band == "2.4 GHz":
        for ch in CH24_20:
            out.append((f"ch{ch}  20MHz  ({chan_freq(ch)} MHz)", chan_freq(ch), 20))
        for c in CH24_40:
            out.append((f"ch{c - 2}+{c + 2}  40MHz  ({chan_freq(c)} MHz)", chan_freq(c), 40))
    else:
        for ch in CH5_20:
            out.append((f"ch{ch}  20MHz  ({chan_freq(ch)} MHz) {_subband(ch)}", chan_freq(ch), 20))
        for ch in CH5_40:
            out.append((f"ch{ch}  40MHz  ({chan_freq(ch)} MHz)", chan_freq(ch), 40))
        for ch in CH5_80:
            out.append((f"ch{ch}  80MHz  ({chan_freq(ch)} MHz)", chan_freq(ch), 80))
        for ch in CH5_160:
            out.append((f"ch{ch}  160MHz ({chan_freq(ch)} MHz)", chan_freq(ch), 160))
    return out


def channels_20(band):
    """(channel, center_MHz) for every 20 MHz channel in the band."""
    chs = CH24_20 if band == "2.4 GHz" else CH5_20
    return [(c, chan_freq(c)) for c in chs]


def band_of(mhz):
    return "2.4 GHz" if mhz < 3000 else "5 GHz"
