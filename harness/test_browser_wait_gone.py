"""`bin/browser wait-gone` against a fake Chrome (a stdlib CDP websocket server): no Chrome starts. Each pass of the
script is one `document.readyState` evaluation, and PASSES says what that pass sees."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import threading
import unittest

REPO = Path(__file__).resolve().parent.parent
BROWSER = REPO / '.claude/skills/verify/bin/browser'
GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'


def read_exact(conn: socket.socket, n: int) -> bytes:
    data = b''
    while len(data) < n:
        chunk = conn.recv(n - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return data


def read_frame(conn: socket.socket) -> dict | None:
    head = read_exact(conn, 2)
    size = head[1] & 0x7F
    if size == 126:
        size = struct.unpack('>H', read_exact(conn, 2))[0]
    elif size == 127:
        size = struct.unpack('>Q', read_exact(conn, 8))[0]
    mask = read_exact(conn, 4)
    payload = bytes(b ^ mask[i % 4] for i, b in enumerate(read_exact(conn, size)))
    if head[0] & 0x0F == 8:
        raise EOFError
    return json.loads(payload) if head[0] & 0x0F == 1 else None


def send_frame(conn: socket.socket, obj: dict) -> None:
    payload = json.dumps(obj).encode()
    head = b'\x81' + (bytes([len(payload)]) if len(payload) < 126 else b'\x7e' + struct.pack('>H', len(payload)))
    conn.sendall(head + payload)


class FakeChrome:
    """passes: one (readyState, target present) per pass; the last one repeats."""

    def __init__(self, passes: list[tuple[str, bool]]):
        self.passes, self.seen = passes, -1
        self.server = socket.socket()
        self.server.bind(('127.0.0.1', 0))
        self.server.listen(4)
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self) -> None:
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            request = conn.recv(4096).decode()
            if request.startswith('GET /json/list'):
                body = json.dumps([{'type': 'page', 'webSocketDebuggerUrl': f'ws://127.0.0.1:{self.port}/p'}]).encode()
                conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s'
                             % (len(body), body))
                conn.close()
                continue
            key = next(line.split(': ')[1] for line in request.split('\r\n') if line.lower().startswith('sec-websocket-key'))
            accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
            conn.sendall(f'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                         f'Sec-WebSocket-Accept: {accept}\r\n\r\n'.encode())
            threading.Thread(target=self.talk, args=(conn,), daemon=True).start()

    def current(self) -> tuple[str, bool]:
        return self.passes[min(self.seen, len(self.passes) - 1)]

    def talk(self, conn: socket.socket) -> None:
        try:
            while True:
                msg = read_frame(conn)
                if msg is None:
                    continue
                method, params = msg['method'], msg.get('params', {})
                result: dict = {}
                if method == 'Runtime.evaluate' and params['expression'] == 'document.readyState':
                    self.seen += 1
                    if self.current()[0] == 'throw':
                        send_frame(conn, {'id': msg['id'], 'error': {'message': 'Execution context was destroyed'}})
                        continue
                    result = {'result': {'value': self.current()[0]}}
                elif method == 'DOM.getDocument':
                    result = {'root': {'nodeId': 1}}
                elif method == 'Accessibility.queryAXTree':
                    result = {'nodes': [{'ignored': False, 'backendDOMNodeId': 2}] if self.current()[1] else []}
                elif method == 'DOM.resolveNode':
                    result = {'object': {'objectId': 'o'}}
                elif method == 'Runtime.callFunctionOn':
                    result = {'result': {'value': True}}
                send_frame(conn, {'id': msg['id'], 'result': result})
        except (EOFError, OSError):
            conn.close()


class WaitGone(unittest.TestCase):
    def wait_gone(self, passes: list[tuple[str, bool]]) -> int:
        """Runs wait-gone; returns how many passes it took."""
        chrome = FakeChrome(passes)
        self.addCleanup(chrome.server.close)
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / 't'
            state.mkdir()
            (state / 'browser.json').write_text(json.dumps({'pid': os.getpid(), 'port': chrome.port}))
            result = subprocess.run(['node', str(BROWSER), 'wait-gone', 'button', 'Ferma', '--timeout', '8000'],
                                    env={**os.environ, 'VERIFY_HOME': home, 'VERIFY_ID': 't'},
                                    capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('gone button "Ferma"', result.stdout)
        return chrome.seen + 1

    def test_a_document_still_loading_is_not_gone(self) -> None:
        loading, done, present = 'loading', 'complete', True
        passes = [(loading, not present), (loading, not present), (done, present), (done, not present), (done, not present)]
        self.assertEqual(self.wait_gone(passes), 5)

    def test_one_empty_pass_is_not_gone(self) -> None:
        done, present = 'complete', True
        passes = [(done, not present), (done, present), (done, not present), (done, not present)]
        self.assertEqual(self.wait_gone(passes), 4)

    def test_an_error_pass_between_two_empty_passes_resets_the_count(self) -> None:
        done, present = 'complete', True
        passes = [(done, not present), ('throw', not present), (done, not present), (done, not present)]
        self.assertEqual(self.wait_gone(passes), 4)

    def test_two_empty_passes_on_a_loaded_page_are_gone(self) -> None:
        self.assertEqual(self.wait_gone([('complete', True), ('complete', False), ('complete', False)]), 3)


if __name__ == '__main__':
    unittest.main()
