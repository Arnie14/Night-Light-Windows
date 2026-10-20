# SPDX-License-Identifier: GPL-3.0-only
"""A second launch reaching the running copy.

The second launch runs as its own process, as it does in use. Sending from
the same thread can't work on Windows: the pipe is unbuffered, so the
sender's write waits for the receiver to read, and the receiver can't read
while that same thread waits (see single_instance.py).
"""

import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from PySide6.QtTest import QTest

from nightlight.system.single_instance import SingleInstance

ROOT = Path(__file__).resolve().parent.parent

# A second launch: exit code 0 if it found the running copy, 3 if not.
SECOND_LAUNCH = """
import sys
from PySide6.QtCore import QCoreApplication
app = QCoreApplication([])
from nightlight.system.single_instance import SingleInstance
name, command = sys.argv[1], sys.argv[2]
instance = SingleInstance(name)
found = instance.notify_running() if command == "launch" else instance.send(command.encode())
sys.exit(0 if found else 3)
"""


def second_launch(name: str, command: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", SECOND_LAUNCH, name, command], cwd=ROOT)


def wait_for(condition, process: subprocess.Popen, timeout_ms: int = 15000) -> int:
    """Run the event loop until `condition()` and the process has exited; its exit code."""
    for _ in range(timeout_ms // 10):
        if condition() and process.poll() is not None:
            break
        QTest.qWait(10)
    try:
        return process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        pytest.fail("the second launch didn't finish")


@pytest.fixture
def first():
    instance = SingleInstance(f"night-light-test-{uuid.uuid4().hex}")
    assert instance.notify_running() is False  # nothing running yet: it listens
    received = {"show": 0, "quit": 0}
    instance.activated.connect(lambda: received.__setitem__("show", received["show"] + 1))
    instance.quitRequested.connect(lambda: received.__setitem__("quit", received["quit"] + 1))
    instance.received = received
    yield instance
    instance.close()
    QTest.qWait(50)  # let Qt finish closing the pipe before the next test


def test_second_launch_shows_the_first(first):
    exit_code = wait_for(lambda: first.received["show"], second_launch(first._name, "launch"))
    assert exit_code == 0  # the second launch knew to exit
    assert first.received == {"show": 1, "quit": 0}


def test_quit_reaches_the_first(first):
    exit_code = wait_for(lambda: first.received["quit"], second_launch(first._name, "quit"))
    assert exit_code == 0
    assert first.received == {"show": 0, "quit": 1}


def test_several_launches_each_get_through(first):
    for n in range(1, 4):
        # Waiting runs the event loop, so the first copy keeps reading.
        assert wait_for(lambda n=n: first.received["show"] == n, second_launch(first._name, "launch")) == 0
    assert first.received == {"show": 3, "quit": 0}


def test_with_nothing_running():
    assert SingleInstance(f"night-light-test-{uuid.uuid4().hex}").send(b"quit") is False


@pytest.mark.parametrize(("data", "signal"), [(b"show\n", "show"), (b"quit\n", "quit"), (b"", "show")])
def test_commands(first, data, signal):
    first._on_command(data)
    assert first.received[signal] == 1


# --- From the 0.2.0 audit -------------------------------------------------------


class PieceSocket:
    """Delivers what it was given in the pieces it was given."""

    def __init__(self, *pieces):
        self.pieces = list(pieces)
        self.written = b""

    def bytesAvailable(self):
        return len(self.pieces[0]) if self.pieces else 0

    def readAll(self):
        from PySide6.QtCore import QByteArray

        return QByteArray(self.pieces.pop(0))

    def state(self):
        from PySide6.QtNetwork import QLocalSocket

        return QLocalSocket.LocalSocketState.ConnectedState

    def write(self, data):
        self.written += data

    def flush(self):
        pass


def test_a_request_split_across_reads_is_put_back_together(first):
    requests = []
    first.requestReceived.connect(lambda payload, reply: requests.append(payload))
    socket = PieceSocket(b'{"command": "li', b'st", "json": true}\n')
    first._read(socket)
    assert requests == []  # half a request: wait for the rest
    first._read(socket)
    assert requests == [{"command": "list", "json": True}]


def test_a_broken_request_gets_an_empty_one_and_other_lines_are_commands(first):
    requests = []
    first.requestReceived.connect(lambda payload, reply: requests.append(payload))
    first._read(PieceSocket(b"{not json" + bytes([10])))
    assert requests == [{}]  # answered with an error, not a crash
    first._read(PieceSocket(b"[1, 2, 3]" + bytes([10])))
    assert first.received["show"] == 1  # not a request: the panel is shown, as for any launch


def test_answering_after_the_command_line_hung_up_is_harmless(first):
    replies = []
    first.requestReceived.connect(lambda payload, reply: replies.append(reply))
    first._read(PieceSocket(b'{"command": "list"}\n'))

    class Gone(PieceSocket):
        def state(self):
            raise RuntimeError("Internal C++ object (PySide6.QtNetwork.QLocalSocket) already deleted.")

    gone = Gone(b'{"command": "list"}\n')
    first._read(gone)
    replies[-1]({"ok": True})  # no exception
    assert gone.written == b""



@pytest.mark.skipif(sys.platform != "win32", reason="named pipe ownership is a Windows question")
def test_our_own_pipe_is_recognised_as_this_users(first):
    from PySide6.QtNetwork import QLocalSocket

    from nightlight.hardware import winsystem

    socket = QLocalSocket()
    socket.connectToServer(first._name)
    assert socket.waitForConnected(2000)
    assert winsystem.pipe_server_is_same_user(int(socket.socketDescriptor())) is True
    socket.abort()


def test_nothing_is_sent_to_a_pipe_another_user_holds(first, monkeypatch):
    """The name is predictable; another user's program could create it first."""
    from nightlight.hardware import winsystem

    monkeypatch.setattr(winsystem, "pipe_server_is_same_user", lambda handle: False)
    received = []
    first.requestReceived.connect(lambda payload, reply: received.append(payload))
    sender = SingleInstance(first._name)
    assert sender.send(b"show") is True  # taken: don't start a second copy
    answer = sender.request({"command": "list"}, timeout_ms=500)
    assert answer["ok"] is False and "Another user's program" in answer["output"]
    QTest.qWait(100)
    assert received == [] and first.received["show"] == 0
