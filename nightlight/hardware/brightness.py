# SPDX-License-Identifier: GPL-3.0-only
"""Brightness backends: the layer that actually talks to monitor hardware.

Every backend exposes the same small interface so the UI never needs to know
whether it is driving real DDC/CI monitors or fake ones used for development.
All methods may be slow and must only be called from the monitor worker thread.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from typing import Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DisplayDetails:
    """What is known about a display beyond its name, for the list of
    compatible displays and the compatibility report. All read without
    talking to the display (EDID from the registry, Windows' display
    configuration)."""

    manufacturer: str = ""  # PnP id, e.g. "DEL"
    model: str = ""
    serial: str = ""
    connector: str = ""  # "HDMI", "DisplayPort", "Internal", ...
    hdr: bool = False
    year: int = 0
    diagonal_inches: float = 0.0
    source: str = ""  # Windows' GDI name, e.g. "\\.\DISPLAY1"


# How brightness reaches a display (MonitorInfo.channel), as shown to users.
CHANNEL_LABELS = {
    "ddc": "DDC/CI",
    "ddc-high-level": "DDC/CI (high-level API)",
    "wmi": "WMI (laptop panel)",
    "sdr": "Windows SDR brightness (HDR)",
    "software": "Software dimming (gamma ramp)",
    "kde": "KDE Plasma",
    "linux-ddc": "DDC/CI (i2c-dev)",
    "fake": "Simulated",
    "": "None",
}


@dataclass(frozen=True)
class MonitorInfo:
    index: int
    name: str
    supported: bool  # False when the monitor doesn't respond to DDC/CI
    # Why it isn't supported, in a few words for under its slider; `detail`
    # says more (what to try), for the tooltip and the compatibility report.
    reason: str = ""
    # Identifies the monitor across reconnects, for remembering its settings.
    # Not the Windows device instance id: that changes with the port or cable
    # (Monitorian's README), so the EDID's manufacturer and product code are
    # used where known. Identical monitors are told apart by order ("#2").
    key: str = ""
    internal: bool = False  # a laptop's built-in panel
    # How brightness is changed: "hardware" (DDC/CI, or WMI for a laptop
    # panel), "sdr" (Windows' SDR content brightness, for HDR displays) or
    # "software" (dimming the gamma ramp, for displays without either).
    control: str = "hardware"
    display: str = ""  # Windows' GDI name ("\\.\DISPLAY2"), for its gamma ramp; "" if unknown
    hdr: bool = False  # HDR on: gamma ramps (night light, software dimming) don't apply
    # The lowest level the slider offers: 1 for laptop panels, which can go
    # fully dark at 0 (KDE keeps them above zero too), and software dimming's
    # floor, below which Windows rejects the ramp.
    minimum: int = 0
    detail: str = ""
    # The exact way in, finer than `control`: a key of CHANNEL_LABELS.
    channel: str = ""
    # Why an unsupported display can't be controlled right now: "unsupported"
    # (no way to control it), "not responding" (a call to it hung) or
    # "retrying" (it answered before; likely still waking up).
    status: str = "ok"
    details: DisplayDetails | None = None

    @property
    def channel_label(self) -> str:
        return CHANNEL_LABELS.get(self.channel if self.supported else "", self.channel)


def legacy_key(key: str) -> str:
    """The key older versions used for this display: its hardware id without
    the serial ("DEL41A2:7XKJ9F3" -> "DEL41A2"), for migrating remembered settings."""
    return key.split(":", 1)[0]


def with_unique_keys(monitors: list[MonitorInfo]) -> list[MonitorInfo]:
    """Fill in missing keys from the name, and number repeated ones."""
    seen: dict[str, int] = {}
    result = []
    for monitor in monitors:
        key = monitor.key or f"name:{monitor.name}"
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            key = f"{key}#{seen[key]}"
        result.append(replace(monitor, key=key))
    return result


class MonitorGoneError(Exception):
    """The monitor disappeared (unplugged, or the displays were reconfigured);
    the monitor list is out of date and should be detected again."""


class BrightnessBackend(Protocol):
    def list_monitors(self) -> list[MonitorInfo]: ...
    def get_brightness(self, index: int) -> int | None: ...
    def set_brightness(self, index: int, value: int) -> None: ...

    # Optional: is_powered_on(index) -> bool | None, whether the monitor is
    # awake (None: it can't tell). Checked before restoring brightness.


def to_percent(raw: int, minimum: int, maximum: int) -> int:
    """A monitor's raw brightness as 0-100. Monitors report their own range:
    as Monitorian notes, the minimum is not always 0 nor the maximum 100."""
    return round((raw - minimum) * 100 / (maximum - minimum))


def to_raw(percent: int, minimum: int, maximum: int) -> int:
    return round(minimum + percent * (maximum - minimum) / 100)


def valid_range(minimum: int, current: int, maximum: int) -> bool:
    return minimum < maximum and minimum <= current <= maximum


def nearest_level(levels: list[int], value: int) -> int:
    """The supported level closest to `value`; laptop panels often offer only a
    few (WMI reports them), and other values are rejected or rounded oddly."""
    return min(levels, key=lambda level: (abs(level - value), level)) if levels else value


class DdcBackend:
    """External monitors over DDC/CI on Linux (i2c-dev), using the `monitorcontrol` library.

    Windows uses nightlight.hardware.windows instead, which also handles laptop panels.
    """

    def __init__(self) -> None:
        self._monitors: list = []

    def list_monitors(self) -> list[MonitorInfo]:
        from monitorcontrol import get_monitors

        self._monitors = get_monitors()
        infos = []
        for i, monitor in enumerate(self._monitors):
            name = f"Monitor {i + 1}"
            supported = True
            try:
                with monitor:
                    monitor.get_luminance()
                    # Capability strings are often malformed, so the model
                    # name is a nice-to-have, never a requirement.
                    try:
                        model = monitor.get_vcp_capabilities().get("model")
                        if model:
                            name = f"{name} ({model})"
                    except Exception:
                        log.debug("Could not read capabilities for %s", name, exc_info=True)
            except Exception:
                log.info("%s does not support DDC/CI brightness", name, exc_info=True)
                supported = False
            reason = "" if supported else "DDC/CI didn't answer"
            infos.append(MonitorInfo(i, name, supported, reason, channel="linux-ddc" if supported else "", status="ok" if supported else "unsupported"))
        return infos

    def get_brightness(self, index: int) -> int | None:
        try:
            with self._monitors[index] as monitor:
                return monitor.get_luminance()
        except Exception:
            log.warning("Failed to read brightness of monitor %d", index, exc_info=True)
            return None

    def set_brightness(self, index: int, value: int) -> None:
        with self._monitors[index] as monitor:
            monitor.set_luminance(value)


class FakeBrightnessBackend:
    """In-memory monitors for working without DDC/CI hardware.

    Calls sleep for `latency` seconds to mimic how slow real DDC/CI is, so
    UI responsiveness problems show up during development too.
    """

    def __init__(self, latency: float = 0.15) -> None:
        self._latency = latency
        # One of each kind of control, and one display without any.
        self._monitors = [
            MonitorInfo(0, "DELL U2720Q", True, key="DEL41A2:7XKJ9F3", channel="ddc", display=r"\\.\DISPLAY1",
                        details=DisplayDetails("DEL", "DELL U2720Q", "7XKJ9F3", "DisplayPort", False, 2021, 27.0, r"\\.\DISPLAY1")),
            MonitorInfo(1, "LG 27GP950 (HDR)", True, key="GSM5BBF:204NTAB1", control="sdr", channel="sdr", display=r"\\.\DISPLAY2", hdr=True,
                        details=DisplayDetails("GSM", "LG 27GP950", "204NTAB1", "HDMI", True, 2022, 27.0, r"\\.\DISPLAY2")),
            MonitorInfo(2, "Living room TV", True, key="SAM7136", control="software", minimum=20, channel="software", display=r"\\.\DISPLAY3",
                        details=DisplayDetails("SAM", "SAMSUNG", "", "HDMI", False, 2019, 54.6, r"\\.\DISPLAY3")),
            MonitorInfo(3, "Old projector", False, key="EPS0000", reason="DDC/CI didn't answer", status="unsupported", display=r"\\.\DISPLAY4",
                        detail="Didn't answer over DDC/CI. Turn DDC/CI on in the display's own menu",
                        details=DisplayDetails("EPS", "EPSON PJ", "", "VGA", False, 2012, 0.0, r"\\.\DISPLAY4")),
        ]
        self._brightness = {0: 70, 1: 40, 2: 100}
        self.software_dimming = True

    def set_software_dimming(self, enabled: bool) -> None:
        self.software_dimming = enabled

    def list_monitors(self) -> list[MonitorInfo]:
        time.sleep(self._latency * len(self._monitors))
        if self.software_dimming:
            return list(self._monitors)
        return [
            replace(m, supported=False, control="hardware", channel="", reason="No brightness control", status="unsupported",
                    detail="Turn on Settings > Brightness > Dim displays without brightness control")
            if m.control == "software" else m
            for m in self._monitors
        ]

    def get_brightness(self, index: int) -> int | None:
        time.sleep(self._latency)
        return self._brightness.get(index)

    def set_brightness(self, index: int, value: int) -> None:
        time.sleep(self._latency)
        log.info("Fake monitor %d brightness -> %d", index, value)
        self._brightness[index] = value
