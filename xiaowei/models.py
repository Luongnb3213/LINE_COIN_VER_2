"""Model dữ liệu ánh xạ đúng theo response đã tài liệu hoá của Xiaowei.

Tên field và ý nghĩa lấy từ tài liệu Xiaowei local API, không suy đoán từ
traffic thực tế. Chuyển thể từ LINE_COIN/core/models.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Response code tài liệu hoá cho mọi action.
CODE_SUCCESS = 10000
CODE_FAILURE = 10001

#: Giá trị ``mode`` mà action ``list`` trả về.
CONNECTION_MODES: dict[int, str] = {
    0: "USB",
    1: "Wi-Fi",
    2: "OTG",
    3: "Accessibility (không USB debugging)",
    10: "Cloud real device",
    11: "Cloud phone",
    12: "Cloud phone",
}


@dataclass(frozen=True)
class ApiResponse:
    """Bọc chung ``{code, message, data}``."""

    code: int
    message: str
    data: Any = None

    @property
    def ok(self) -> bool:
        return self.code == CODE_SUCCESS

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ApiResponse":
        return cls(
            code=int(payload.get("code", 0) or 0),
            message=str(payload.get("message") or payload.get("msg") or ""),
            data=payload.get("data"),
        )


@dataclass(frozen=True)
class Device:
    """Một điện thoại theo action ``list``."""

    serial: str
    only_serial: str = ""
    name: str = ""
    model: str = ""
    status: str = ""
    mode: int | None = None
    intranet_ip: str = ""
    sort: int | None = None
    width: int | None = None
    height: int | None = None
    source_width: int | None = None
    source_height: int | None = None
    hide: bool | None = None
    connect_time: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def online(self) -> bool:
        status = self.status.strip().lower()
        if status in {"", "unknown"}:
            return True
        return status in {"online", "device"}

    @property
    def mode_label(self) -> str:
        if self.mode is None:
            return "unknown"
        return CONNECTION_MODES.get(self.mode, f"mode {self.mode}")

    @property
    def stable_id(self) -> str:
        """Ưu tiên ``onlySerial``: giữ nguyên qua thay đổi kiểu kết nối."""
        return self.only_serial or self.serial

    @property
    def label(self) -> str:
        parts = [self.name or self.model or self.serial]
        if self.model and self.model != parts[0]:
            parts.append(self.model)
        parts.append(self.mode_label)
        return " · ".join(part for part in parts if part)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "Device | None":
        serial = str(payload.get("serial") or payload.get("onlySerial") or "").strip()
        if not serial:
            return None

        def as_int(key: str) -> int | None:
            value = payload.get(key)
            if value is None or isinstance(value, bool):
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        hide = payload.get("hide")
        return cls(
            serial=serial,
            only_serial=str(payload.get("onlySerial") or "").strip(),
            name=str(payload.get("name") or "").strip(),
            model=str(payload.get("model") or "").strip(),
            status=str(payload.get("status") or "").strip(),
            mode=as_int("mode"),
            intranet_ip=str(payload.get("intranetIp") or "").strip(),
            sort=as_int("sort"),
            width=as_int("width"),
            height=as_int("height"),
            source_width=as_int("sourceWidth"),
            source_height=as_int("sourceHeight"),
            hide=hide if isinstance(hide, bool) else None,
            connect_time=as_int("connectTime"),
            raw=dict(payload),
        )


@dataclass(frozen=True)
class ApkInfo:
    """Một app bên thứ ba từ action ``apkList``."""

    package: str
    label: str = ""

    @classmethod
    def from_payload(cls, payload: Any) -> "ApkInfo | None":
        if isinstance(payload, str):
            package = payload.strip()
            return cls(package=package) if package else None
        if not isinstance(payload, dict):
            return None
        package = str(payload.get("package") or "").strip()
        if not package:
            return None
        return cls(package=package, label=str(payload.get("apk") or "").strip())


__all__ = ["ApiResponse", "ApkInfo", "CODE_FAILURE", "CODE_SUCCESS", "CONNECTION_MODES", "Device"]
