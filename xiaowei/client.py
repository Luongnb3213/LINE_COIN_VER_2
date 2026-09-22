"""Client Xiaowei: gọi action đã tài liệu hoá qua một `Transport`.

Đọc (list/apkList/adb đọc) được tự động retry theo `max_retries`; hành động
làm thay đổi trạng thái máy (tap/swipe/shell ghi/mở app...) không bao giờ tự
retry — gửi lại một tap sau khi timeout có thể vừa chạm hai lần lên máy thật.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from core.logging_utils import get_logger
from xiaowei.actions import ActionParameterError, UnsupportedActionError, build_request
from xiaowei.models import ApiResponse, ApkInfo, Device
from xiaowei.transport import RequestTimeout, Transport, TransportError

_LOG = get_logger("xiaowei")


class XiaoweiError(RuntimeError):
    """Base class cho lỗi tầng client Xiaowei."""


class ApiError(XiaoweiError):
    """Xiaowei trả `code != 10000`."""

    def __init__(self, response: ApiResponse, action: str) -> None:
        super().__init__(f"Xiaowei action `{action}` thất bại: {response.message} (code={response.code})")
        self.response = response
        self.action = action


class OperationCancelled(XiaoweiError):
    """Bị huỷ qua `cancel` event trong lúc chờ retry."""


class XiaoweiClient:
    """Gọi action Xiaowei đã tài liệu hoá, có retry cho action đọc."""

    def __init__(
        self,
        transport: Transport,
        *,
        max_retries: int = 2,
        retry_backoff: float = 0.5,
        screenshot_dir: str = "",
        cancel: threading.Event | None = None,
    ) -> None:
        self._transport = transport
        self._max_retries = max(max_retries, 0)
        self._retry_backoff = max(retry_backoff, 0.0)
        self._screenshot_dir = screenshot_dir
        self._cancel = cancel

    def describe(self) -> str:
        return self._transport.describe()

    def close(self) -> None:
        self._transport.close()

    # -- core call -------------------------------------------------------

    def call(
        self,
        action: str,
        *,
        devices: str | None = None,
        data: dict[str, Any] | None = None,
        read_only: bool | None = None,
    ) -> ApiResponse:
        payload = build_request(action, devices=devices, data=data)

        from xiaowei.actions import get_spec

        spec = get_spec(action)
        retryable = (not spec.mutating) if read_only is None else read_only

        attempt = 0
        while True:
            attempt += 1
            self._check_cancelled()
            try:
                raw = self._transport.request(payload)
                return ApiResponse.from_payload(raw)
            except TransportError as exc:
                if not retryable or attempt > self._max_retries:
                    raise
                _LOG.warning(
                    "Action `%s` lỗi transport (lần %s/%s): %s — thử lại.",
                    action,
                    attempt,
                    self._max_retries,
                    exc,
                )
                self._sleep(self._retry_backoff * attempt)

    def call_ok(
        self,
        action: str,
        *,
        devices: str | None = None,
        data: dict[str, Any] | None = None,
        read_only: bool | None = None,
    ) -> ApiResponse:
        response = self.call(action, devices=devices, data=data, read_only=read_only)
        if not response.ok:
            raise ApiError(response, action)
        return response

    def _sleep(self, seconds: float) -> None:
        if seconds <= 0:
            self._check_cancelled()
            return
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._check_cancelled()
            time.sleep(min(0.2, deadline - time.monotonic()))

    def _check_cancelled(self) -> None:
        if self._cancel is not None and self._cancel.is_set():
            raise OperationCancelled("Bị huỷ trong lúc gọi Xiaowei.")

    # -- read-only ---------------------------------------------------------

    def list_devices(self) -> list[Device]:
        response = self.call_ok("list")
        payload = response.data if isinstance(response.data, list) else []
        devices: list[Device] = []
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            device = Device.from_payload(entry)
            if device is not None:
                devices.append(device)
        return devices

    def find_device(self, serial: str) -> Device | None:
        target = str(serial or "").strip()
        if not target:
            return None
        for device in self.list_devices():
            if device.serial == target or device.only_serial == target:
                return device
        return None

    def apk_list(self, serial: str) -> list[ApkInfo]:
        response = self.call_ok("apkList", devices=serial, read_only=True)
        entry = self._device_entry(response.data, serial)
        items = entry if isinstance(entry, list) else []
        apps: list[ApkInfo] = []
        for item in items:
            info = ApkInfo.from_payload(item)
            if info is not None:
                apps.append(info)
        return apps

    def adb_read(self, serial: str, shell_command: str) -> str:
        command = shell_command.strip()
        if not command.startswith("shell "):
            command = f"shell {command}"
        response = self.call_ok(
            "adb",
            devices=serial,
            data={"command": command},
            read_only=True,
        )
        entry = self._device_entry(response.data, serial)
        return str(entry or "")

    # -- mutating ------------------------------------------------------------

    def adb_shell(self, serial: str, shell_command: str) -> None:
        self.call_ok("adb_shell", devices=serial, data={"command": shell_command})

    def start_apk(self, serial: str, package: str) -> None:
        self.call_ok("startApk", devices=serial, data={"apk": package})

    def stop_apk(self, serial: str, package: str) -> None:
        self.call_ok("stopApk", devices=serial, data={"apk": package})

    def push_event(self, serial: str, event_type: int) -> None:
        self.call_ok("pushEvent", devices=serial, data={"type": str(event_type)})

    def pointer_event(
        self,
        serial: str,
        event_type: int,
        x: float | None = None,
        y: float | None = None,
    ) -> None:
        for label, value in (("x", x), ("y", y)):
            if value is not None and not (0 <= value <= 100):
                raise ActionParameterError(f"pointerEvent `{label}` phải trong [0,100], nhận {value!r}.")
        data: dict[str, Any] = {"type": str(event_type)}
        if x is not None:
            data["x"] = x
        if y is not None:
            data["y"] = y
        self.call_ok("pointerEvent", devices=serial, data=data)

    def tap_pixels(self, serial: str, x: int, y: int) -> None:
        """Tap theo pixel thật qua `adb shell input tap` — không tự retry."""
        self.adb_shell(serial, f"input tap {int(x)} {int(y)}")

    def swipe_pixels(
        self,
        serial: str,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 300,
    ) -> None:
        """Vuốt theo pixel thật qua `adb shell input swipe` — không tự retry."""
        self.adb_shell(
            serial,
            f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration_ms)}",
        )

    def screenshot(self, serial: str, save_path: str | None = None) -> ApiResponse:
        target = save_path or self._screenshot_dir
        data = {"savePath": target} if target else None
        return self.call_ok("screen", devices=serial, data=data)

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _device_entry(data: Any, serial: str) -> Any:
        """Nhiều action trả `data` dạng `{serial: value}` — bóc theo serial."""
        if isinstance(data, dict):
            if serial in data:
                return data[serial]
            if len(data) == 1:
                return next(iter(data.values()))
        return data


__all__ = ["ApiError", "OperationCancelled", "XiaoweiClient", "XiaoweiError"]
