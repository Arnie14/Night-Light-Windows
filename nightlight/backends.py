"""Display backends: the layer that actually talks to monitor hardware.

Every backend exposes the same small interface so the UI never needs to know
whether it is driving real DDC/CI monitors or fake ones used for development.
All methods may be slow and must only be called from the worker thread.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MonitorInfo:
    index: int
    name: str
    supported: bool  # False when the monitor doesn't respond to DDC/CI


class DisplayBackend(Protocol):
    def list_monitors(self) -> list[MonitorInfo]: ...
    def get_brightness(self, index: int) -> int | None: ...
    def set_brightness(self, index: int, value: int) -> None: ...


class DdcBackend:
    """External monitors over DDC/CI, using the `monitorcontrol` library."""

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


class FakeBackend:
    """In-memory monitors for working on the UI without DDC/CI hardware."""

    def __init__(self) -> None:
        self._monitors = [
            MonitorInfo(0, "Fake Monitor 1 (DELL U2720Q)", True),
            MonitorInfo(1, "Fake Monitor 2 (LG 27GL850)", True),
            MonitorInfo(2, "Fake Monitor 3 (no DDC/CI)", False),
        ]
        self._brightness = {0: 70, 1: 40}

    def list_monitors(self) -> list[MonitorInfo]:
        return list(self._monitors)

    def get_brightness(self, index: int) -> int | None:
        return self._brightness.get(index)

    def set_brightness(self, index: int, value: int) -> None:
        log.info("Fake monitor %d brightness -> %d", index, value)
        self._brightness[index] = value
