"""Ánh xạ serial ADB (LDPlayer) sang thiết bị Xiaowei — chỉ khớp chính xác.

LDPlayer và Xiaowei là hai hệ định danh thiết bị khác nhau. Không bao giờ suy
đoán ánh xạ theo thứ tự danh sách — nếu không khớp chính xác được, phải dừng
lại thay vì đoán, vì đoán sai đồng nghĩa với thao tác nhầm lên một máy ảo khác.
"""

from __future__ import annotations

from typing import Sequence

from xiaowei.models import Device


class DeviceMappingError(RuntimeError):
    """Không tìm được thiết bị Xiaowei khớp chính xác với serial ADB."""


def find_xiaowei_device(adb_serial: str, devices: Sequence[Device]) -> Device | None:
    """Tìm thiết bị Xiaowei có `serial` hoặc `onlySerial` khớp chính xác.

    Không có fallback theo vị trí trong danh sách — trả về ``None`` nếu không
    khớp chính xác, để người gọi tự quyết định dừng an toàn.
    """
    target = str(adb_serial or "").strip()
    if not target:
        return None
    for device in devices:
        if device.serial == target or device.only_serial == target:
            return device
    return None


def require_xiaowei_device(adb_serial: str, devices: Sequence[Device]) -> Device:
    """Như :func:`find_xiaowei_device`, nhưng raise nếu không tìm thấy."""
    device = find_xiaowei_device(adb_serial, devices)
    if device is not None:
        return device
    known = ", ".join(f"{d.serial}/{d.only_serial or '-'}" for d in devices) or "(rỗng)"
    raise DeviceMappingError(
        f"Không tìm thấy thiết bị Xiaowei khớp với ADB serial `{adb_serial}`. "
        f"Danh sách Xiaowei hiện có (serial/onlySerial): {known}."
    )


__all__ = ["DeviceMappingError", "find_xiaowei_device", "require_xiaowei_device"]
