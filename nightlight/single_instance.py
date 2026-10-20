# SPDX-License-Identifier: GPL-3.0-only
"""Keeps one copy of the app running per user.

A second copy would add a second tray icon and fight the first over the gamma
ramps, so, as Monitorian and Twinkle Tray do, a second launch asks the running
copy to show its panel and then exits. Uses a local socket (a named pipe on
Windows), named per user so separate sign-ins don't see each other.

The second launch sends one line, "show" or "quit" (`--quit`, used by the
installer). On Windows, Qt makes the pipe unbuffered so nothing is lost if
the sender hangs up early, which means a write only completes once the
running copy has read it. The sender therefore waits for the write to finish
before hanging up. Both ends must be separate processes (as they always are
in use, and in the tests): in one thread, the sender's wait would stop the
receiver from ever reading.
"""

from __future__ import annotations

import getpass
import json
import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_MS = 500
REPLY_TIMEOUT_MS = 10_000
# The running copy reads the command from its event loop, which may be busy
# for a moment (detecting monitors, starting up), so allow it some time.
WRITE_TIMEOUT_MS = 3000
# A request is a short JSON line; anything far longer is not one.
MAX_REQUEST_BYTES = 64 * 1024


def default_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # no user name in the environment
        user = "default"
    return f"night-light-{user}"


class SingleInstance(QObject):
    activated = Signal()  # another launch asked this copy to show itself
    quitRequested = Signal()  # `--quit`, e.g. from the installer, asked this copy to exit
    # The command line asked something (a dict); answer with reply(dict).
    requestReceived = Signal(object, object)  # request, reply

    def __init__(self, name: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._name = name or default_name()
        self._server: QLocalServer | None = None
        self._buffers: dict[int, bytes] = {}  # id(socket) -> received but not yet a whole line

    def notify_running(self) -> bool:
        """If another copy is running, ask it to show itself and return True.
        Otherwise start listening for later launches and return False."""
        if self.send(b"show"):
            return True
        return self._listen()

    def send(self, command: bytes) -> bool:
        """Send `command` ("show" or "quit") to a running copy. True when one
        is running (even if it didn't take the command), False if none is."""
        socket = QLocalSocket()
        socket.connectToServer(self._name)
        if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
            return False
        socket.write(command + b"\n")
        # Don't hang up before the running copy has read it: on Windows the
        # unbuffered pipe drops unread data when the sender disconnects.
        if not socket.waitForBytesWritten(WRITE_TIMEOUT_MS):
            log.warning("The running copy didn't take the %r command: %s", command.decode(), socket.errorString())
        socket.disconnectFromServer()
        if socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
            socket.waitForDisconnected(CONNECT_TIMEOUT_MS)
        return True

    def request(self, payload: dict, timeout_ms: int = REPLY_TIMEOUT_MS) -> dict | None:
        """Send a command-line request to the running copy and wait for its
        answer. None if no copy is running."""
        socket = QLocalSocket()
        socket.connectToServer(self._name)
        if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
            return None
        socket.write(json.dumps(payload).encode() + b"\n")
        socket.waitForBytesWritten(WRITE_TIMEOUT_MS)
        reply = b""
        while not reply.endswith(b"\n") and socket.waitForReadyRead(timeout_ms):
            reply += bytes(socket.readAll().data())
        socket.disconnectFromServer()
        try:
            return json.loads(reply)
        except ValueError:
            return {"ok": False, "output": "Winlight didn't answer in time."}

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
            # Read whatever is already waiting, then whatever arrives. The
            # sender waits until its command has been read before hanging up,
            # so nothing needs reading at hang-up, when Qt is tearing the
            # socket down (touching it then crashed on Windows).
            socket.readyRead.connect(lambda socket=socket: self._read(socket))
            socket.disconnected.connect(lambda key=id(socket): self._buffers.pop(key, None))
            socket.disconnected.connect(socket.deleteLater)
            self._read(socket)

    def close(self) -> None:
        """Stop listening (at exit, and between tests): closing explicitly
        lets Qt finish its pending pipe operations instead of the server
        being destroyed under them."""
        if self._server is not None:
            self._server.close()
            self._server.deleteLater()
            self._server = None

    def _read(self, socket: QLocalSocket) -> None:
        if socket.bytesAvailable() <= 0:
            return
        # A pipe may deliver one message in pieces: act on whole lines only.
        data = self._buffers.pop(id(socket), b"") + bytes(socket.readAll().data())
        *lines, rest = data.split(b"\n")
        if rest:
            if len(rest) > MAX_REQUEST_BYTES:
                log.warning("Ignoring an oversized message from another launch")
            else:
                self._buffers[id(socket)] = rest
        for line in lines:
            if not line.strip():
                continue
            if line.lstrip().startswith(b"{"):
                self._on_request(line, socket)
            else:
                self._on_command(line)

    def _on_request(self, data: bytes, socket: QLocalSocket) -> None:
        try:
            payload = json.loads(data)
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        def reply(answer: dict) -> None:
            try:
                if socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
                    socket.write(json.dumps(answer).encode() + b"\n")
                    socket.flush()
            except RuntimeError:
                log.debug("The command line hung up before the answer")  # Qt already deleted the socket

        # The command only: its arguments can hold the user's own display names.
        log.info("Command line: %s", payload.get("command") or "?")
        self.requestReceived.emit(payload, reply)

    def _on_command(self, data: bytes) -> None:
        if b"quit" in data:
            log.info("Asked to quit by another launch")
            self.quitRequested.emit()
        else:
            log.info("Another launch asked to show the panel")
            self.activated.emit()
