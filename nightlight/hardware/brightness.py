# SPDX-License-Identifier: GPL-3.0-only
"""Brightness backends: the layer that actually talks to monitor hardware.

Every backend exposes the same small interface so the UI never needs to know
whether it is driving real DDC/CI monitors or fake ones used for development.
All methods may be slow and must only be called from the monitor worker thread.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MonitorInfo:
    index: int
    name: str
    supported: bool  # False when the monitor doesn't respond to DDC/CI
    reason: str = ""  # why it isn't supported, when the backend knows better than "no DDC/CI"


class MonitorGoneError(Exception):
    """The monitor disappeared (unplugged, or the displays were reconfigured);
    the monitor list is out of date and should be detected again."""


class BrightnessBackend(Protocol):
    def list_monitors(self) -> list[MonitorInfo]: ...
    def get_brightness(self, index: int) -> int | None: ...
    def set_brightness(self, index: int, value: int) -> None: ...


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
            infos.append(MonitorInfo(i, name, supported))
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
        self._monitors = [
            MonitorInfo(0, "Fake Monitor 1 (DELL U2720Q)", True),
            MonitorInfo(1, "Fake Monitor 2 (LG 27GL850)", True),
            MonitorInfo(2, "Fake Monitor 3 (no DDC/CI)", False),
        ]
        self._brightness = {0: 70, 1: 40}

    def list_monitors(self) -> list[MonitorInfo]:
        time.sleep(self._latency * len(self._monitors))
        return list(self._monitors)

    def get_brightness(self, index: int) -> int | None:
        time.sleep(self._latency)
        return self._brightness.get(index)

    def set_brightness(self, index: int, value: int) -> None:
        time.sleep(self._latency)
        log.info("Fake monitor %d brightness -> %d", index, value)
        self._brightness[index] = value
