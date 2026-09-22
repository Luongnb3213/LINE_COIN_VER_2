"""WebSocket transport cho Xiaowei local API.

Bản RFC 6455 tối thiểu tự viết trên thư viện chuẩn (không dùng thư viện
websocket ngoài) — chuyển thể từ LINE_COIN/core/transport.py.

Mỗi request dùng một kết nối riêng, có chủ đích: docs chỉ mô tả kiểu
request/response đơn giản, không nói gì về frame do server tự đẩy lên. Nếu
Xiaowei có bao giờ tự gửi một sự kiện không mời, một socket sống lâu có thể lặng
lẽ ghép nhầm nó vào response của lệnh kế tiếp.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import time
from typing import Any, Protocol
from urllib.parse import urlsplit

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OPCODE_CONTINUATION = 0x0
OPCODE_TEXT = 0x1
OPCODE_CLOSE = 0x8
OPCODE_PING = 0x9
OPCODE_PONG = 0xA


class TransportError(RuntimeError):
    """Base class cho mọi lỗi tầng transport."""


class ConnectionFailed(TransportError):
    """Không thiết lập được WebSocket."""


class RequestTimeout(TransportError):
    """Không có response trong thời gian chờ."""


class InvalidResponse(TransportError):
    """Phía kia trả về thứ không phải JSON object."""


class Transport(Protocol):
    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Gửi một JSON payload, trả về JSON reply đã decode."""

    def describe(self) -> str:
        """Mô tả ngắn để log."""

    def close(self) -> None:
        """Giải phóng tài nguyên (nếu có)."""


class WebSocketTransport:
    """Blocking WebSocket client, nói JSON với ``ws://127.0.0.1:22222/``."""

    def __init__(
        self,
        url: str = "ws://127.0.0.1:22222/",
        *,
        connect_timeout: float = 10.0,
        request_timeout: float = 20.0,
    ) -> None:
        self.url = url
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout

    def describe(self) -> str:
        return f"WebSocket {self.url}"

    def close(self) -> None:  # noqa: D401 - không giữ gì giữa các request
        """No-op: mỗi request tự sở hữu socket của nó."""

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        sock = self._connect()
        try:
            self._send_text(sock, json.dumps(payload, ensure_ascii=False))
            message = self._recv_text(sock)
        except socket.timeout as exc:
            raise RequestTimeout(
                f"Xiaowei không phản hồi trong {self.request_timeout:g}s."
            ) from exc
        except OSError as exc:
            raise TransportError(f"Lỗi socket khi gọi Xiaowei: {exc}") from exc
        finally:
            self._shutdown(sock)

        try:
            decoded = json.loads(message)
        except json.JSONDecodeError as exc:
            raise InvalidResponse(
                f"Xiaowei trả về JSON không hợp lệ: {message[:200]!r}"
            ) from exc
        if not isinstance(decoded, dict):
            raise InvalidResponse("Response của Xiaowei không phải JSON object.")
        return decoded

    # -- connection ----------------------------------------------------------

    def _connect(self) -> socket.socket | ssl.SSLSocket:
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"ws", "wss"}:
            raise ConnectionFailed(f"URL WebSocket không hợp lệ: {self.url}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        try:
            raw = socket.create_connection((host, port), timeout=self.connect_timeout)
        except OSError as exc:
            raise ConnectionFailed(
                f"Không kết nối được Xiaowei tại {host}:{port} ({exc}). "
                "Kiểm tra Xiaowei client đã chạy và API đang bật."
            ) from exc

        sock: socket.socket | ssl.SSLSocket = raw
        if parsed.scheme == "wss":
            try:
                sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
            except OSError as exc:
                raw.close()
                raise ConnectionFailed(f"TLS handshake thất bại: {exc}") from exc

        sock.settimeout(self.request_timeout)
        try:
            self._handshake(sock, host, port, path)
        except Exception:
            self._shutdown(sock)
            raise
        return sock

    def _handshake(
        self,
        sock: socket.socket | ssl.SSLSocket,
        host: str,
        port: int,
        path: str,
    ) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("ascii"))

        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        response = data.decode("iso-8859-1", errors="replace")
        if not response:
            raise ConnectionFailed("Xiaowei đóng kết nối trong lúc handshake.")

        status_line = response.split("\r\n", 1)[0]
        if " 101 " not in status_line:
            raise ConnectionFailed(f"Handshake WebSocket thất bại: {status_line}")

        expected = base64.b64encode(
            hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()
        ).decode("ascii")
        if f"sec-websocket-accept: {expected.lower()}" not in response.lower():
            raise ConnectionFailed("Handshake thiếu Sec-WebSocket-Accept hợp lệ.")

    @staticmethod
    def _shutdown(sock: socket.socket | ssl.SSLSocket | None) -> None:
        if sock is None:
            return
        try:
            sock.close()
        except OSError:
            pass

    # -- framing -------------------------------------------------------------

    def _send_text(self, sock: socket.socket | ssl.SSLSocket, text: str) -> None:
        self._send_frame(sock, OPCODE_TEXT, text.encode("utf-8"))

    def _send_frame(
        self,
        sock: socket.socket | ssl.SSLSocket,
        opcode: int,
        payload: bytes,
    ) -> None:
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length <= 0xFFFF:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        sock.sendall(bytes(header) + mask + masked)

    def _recv_text(self, sock: socket.socket | ssl.SSLSocket) -> str:
        deadline = time.monotonic() + self.request_timeout
        chunks: list[bytes] = []
        while True:
            if time.monotonic() > deadline:
                raise RequestTimeout(
                    f"Xiaowei không phản hồi trong {self.request_timeout:g}s."
                )
            fin, opcode, payload = self._recv_frame(sock)
            if opcode == OPCODE_CLOSE:
                raise TransportError("Xiaowei đóng WebSocket trước khi trả response.")
            if opcode == OPCODE_PING:
                self._send_frame(sock, OPCODE_PONG, payload)
                continue
            if opcode == OPCODE_PONG:
                continue
            if opcode in (OPCODE_TEXT, OPCODE_CONTINUATION):
                chunks.append(payload)
                if fin:
                    return b"".join(chunks).decode("utf-8", errors="replace")
                continue
            raise TransportError(f"WebSocket opcode không hỗ trợ: {opcode}")

    def _recv_frame(self, sock: socket.socket | ssl.SSLSocket) -> tuple[bool, int, bytes]:
        first = self._recv_exact(sock, 2)
        fin = bool(first[0] & 0x80)
        opcode = first[0] & 0x0F
        masked = bool(first[1] & 0x80)
        length = first[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(sock, 8))[0]
        mask = self._recv_exact(sock, 4) if masked else b""
        payload = self._recv_exact(sock, length) if length else b""
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return fin, opcode, payload

    @staticmethod
    def _recv_exact(sock: socket.socket | ssl.SSLSocket, length: int) -> bytes:
        chunks: list[bytes] = []
        remaining = length
        while remaining:
            chunk = sock.recv(remaining)
            if not chunk:
                raise TransportError("WebSocket đóng giữa chừng.")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)


__all__ = [
    "ConnectionFailed",
    "InvalidResponse",
    "RequestTimeout",
    "Transport",
    "TransportError",
    "WebSocketTransport",
]
