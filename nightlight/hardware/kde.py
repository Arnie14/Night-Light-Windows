# SPDX-License-Identifier: GPL-3.0-only
"""KDE Plasma backends, for developing on a Linux laptop.

Plasma already does what this app does on Windows, and exposes it on D-Bus:
PowerDevil's ScreenBrightness service (built-in panels and DDC/CI monitors)
and KWin's NightLight. Driving them makes the real UI and controller visibly
work on a KDE machine with no external monitor. Not used on Windows.

Uses jeepney rather than QtDBus because these methods take D-Bus `uint`
arguments, which PySide has no way to send.
"""

from __future__ import annotations

import logging

from jeepney import DBusAddress, DBusErrorResponse, Properties, new_method_call
from jeepney.io.blocking import DBusConnection, open_dbus_connection

from nightlight.hardware.brightness import MonitorInfo

log = logging.getLogger(__name__)

_BRIGHTNESS = DBusAddress("/org/kde/ScreenBrightness", bus_name="org.kde.ScreenBrightness", interface="org.kde.ScreenBrightness")
_NIGHT_LIGHT = DBusAddress("/org/kde/KWin/NightLight", bus_name="org.kde.KWin", interface="org.kde.KWin.NightLight")
_DISPLAY_IFACE = "org.kde.ScreenBrightness.Display"
_SUPPRESS_OSD = 0x1  # don't pop up Plasma's brightness indicator


class _Bus:
    """A blocking session-bus connection. Not thread-safe: one per backend."""

    def __init__(self) -> None:
        self._conn: DBusConnection = open_dbus_connection(bus="SESSION")

    def get(self, address: DBusAddress, name: str):
        reply = self._conn.send_and_get_reply(Properties(address).get(name))
        if isinstance(reply.body[0], str):  # error replies carry a message string
            raise DBusErrorResponse(reply)
        _signature, value = reply.body[0]
        return value

    def call(self, address: DBusAddress, method: str, signature: str | None = None, args: tuple = ()) -> None:
        reply = self._conn.send_and_get_reply(new_method_call(address, method, signature, args))
        if reply.header.message_type.name == "error":
            raise DBusErrorResponse(reply)


def is_available() -> bool:
    try:
        bus = _Bus()
        return bool(bus.get(_NIGHT_LIGHT, "available")) and bool(bus.get(_BRIGHTNESS, "DisplaysDBusNames"))
    except (OSError, KeyError, DBusErrorResponse):
        return False


class KdeBrightnessBackend:
    """Brightness through PowerDevil, which handles both laptop panels and DDC/CI.

    Created on the UI thread but used from the monitor worker thread, so the
    connection is opened lazily on first use.
    """

    def __init__(self) -> None:
        self._bus: _Bus | None = None
        self._displays: list[tuple[DBusAddress, int]] = []  # (address, max brightness)

    def list_monitors(self) -> list[MonitorInfo]:
        bus = self._get_bus()
        self._displays = []
        infos = []
        for i, name in enumerate(bus.get(_BRIGHTNESS, "DisplaysDBusNames")):
            address = DBusAddress(f"{_BRIGHTNESS.object_path}/{name}", bus_name=_BRIGHTNESS.bus_name, interface=_DISPLAY_IFACE)
            maximum = bus.get(address, "MaxBrightness")
            self._displays.append((address, maximum))
            infos.append(MonitorInfo(i, bus.get(address, "Label"), supported=maximum > 0))
        return infos

    def get_brightness(self, index: int) -> int | None:
        address, maximum = self._displays[index]
        try:
            return round(self._get_bus().get(address, "Brightness") * 100 / maximum)
        except DBusErrorResponse:
            log.warning("Failed to read brightness of %s", address.object_path, exc_info=True)
            return None

    def set_brightness(self, index: int, value: int) -> None:
        address, maximum = self._displays[index]
        # 0 can switch a laptop backlight fully off, leaving a black screen.
        raw = round(max(1, value) * maximum / 100)
        self._get_bus().call(address, "SetBrightness", "iu", (raw, _SUPPRESS_OSD))

    def _get_bus(self) -> _Bus:
        if self._bus is None:
            self._bus = _Bus()
        return self._bus


class KWinNightLightBackend:
    """Shows temperatures with KWin's preview, the call KDE's own settings slider uses.

    While previewing, KWin's own night light schedule is overridden; reset()
    hands control back to it, much as Windows' Night Light would resume.
    A preview lapses after 15 s, so it relies on the controller re-applying.
    KWin shows a "Color Temperature Preview" OSD on each call.
    """

    animates = True  # KWin animates every change itself (its "quick adjust")

    def __init__(self) -> None:
        self._bus = _Bus()

    def apply(self, kelvin: int) -> None:
        self._bus.call(_NIGHT_LIGHT, "preview", "u", (kelvin,))

    def reset(self) -> None:
        self._bus.call(_NIGHT_LIGHT, "stopPreview")

    def current_temperature(self) -> int:
        return self._bus.get(_NIGHT_LIGHT, "currentTemperature")
