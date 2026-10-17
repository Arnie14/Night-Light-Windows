# SPDX-License-Identifier: GPL-3.0-only
"""Keeps one copy of the app running per user.

A second copy would add a second tray icon and fight the first over the gamma
ramps, so, as Monitorian and Twinkle Tray do, a second launch asks the running
copy to show its panel and then exits. Uses a local socket (a named pipe on
Windows), named per user so separate sign-ins don't see each other.
"""

from __future__ import annotations

import getpass
import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_MS = 500


def default_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # no user name in the environment
        user = "default"
    return f"night-light-{user}"


class SingleInstance(QObject):
    activated = Signal()  # another launch asked this copy to show itself
    quitRequested = Signal()  # `--quit`, e.g. from the installer, asked this copy to exit

    def __init__(self, name: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._name = name or default_name()
        self._server: QLocalServer | None = None

    def notify_running(self) -> bool:
        """If another copy is running, ask it to show itself and return True.
        Otherwise start listening for later launches and return False."""
        if self.send(b"show"):
            return True
        return self._listen()

    def send(self, command: bytes) -> bool:
        """Send `command` ("show" or "quit") to a running copy; False if none runs."""
        socket = QLocalSocket()
        socket.connectToServer(self._name)
        if socket.waitForConnected(CONNECT_TIMEOUT_MS):
            socket.write(command + b"\n")
            socket.waitForBytesWritten(CONNECT_TIMEOUT_MS)
            socket.disconnectFromServer()
            return True
        return False

    def _listen(self) -> bool:
        # Nobody answered. On Linux a crashed copy can leave its socket file behind.
        QLocalServer.removeServer(self._name)
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not self._server.listen(self._name):
            log.warning("Could not listen for other launches: %s", self._server.errorString())
            return False
        self._server.newConnection.connect(self._on_connection)
        return False

    def _on_connection(self) -> None:
        while self._server is not None and (socket := self._server.nextPendingConnection()) is not None:
            socket.disconnected.connect(socket.deleteLater)
            socket.readyRead.connect(lambda socket=socket: self._on_command(bytes(socket.readAll().data())))

    def _on_command(self, data: bytes) -> None:
        if b"quit" in data:
            log.info("Asked to quit by another launch")
            self.quitRequested.emit()
        else:
            log.info("Another launch asked to show the panel")
            self.activated.emit()
