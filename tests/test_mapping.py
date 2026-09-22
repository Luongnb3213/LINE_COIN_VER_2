"""Test cho device/mapping.py: chỉ khớp chính xác, không bao giờ suy đoán."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from device.mapping import DeviceMappingError, find_xiaowei_device, require_xiaowei_device
from xiaowei.models import Device


def _device(serial: str, only_serial: str = "") -> Device:
    return Device(serial=serial, only_serial=only_serial)


class FindXiaoweiDeviceTests(unittest.TestCase):
    def test_matches_by_serial(self) -> None:
        devices = [_device("emulator-5554"), _device("emulator-5556")]
        found = find_xiaowei_device("emulator-5556", devices)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.serial, "emulator-5556")

    def test_matches_by_only_serial(self) -> None:
        devices = [_device("127.0.0.1:5555", only_serial="ABC123")]
        found = find_xiaowei_device("ABC123", devices)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.only_serial, "ABC123")

    def test_no_positional_fallback(self) -> None:
        """Serial mục tiêu khớp phần tử thứ hai, không phải thứ nhất — không
        được có chuyện code âm thầm trả về phần tử đầu danh sách."""
        devices = [
            _device("emulator-5554", only_serial="FIRST"),
            _device("emulator-5556", only_serial="SECOND"),
        ]
        found = find_xiaowei_device("emulator-5556", devices)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.only_serial, "SECOND")

    def test_no_match_returns_none(self) -> None:
        devices = [_device("emulator-5554")]
        self.assertIsNone(find_xiaowei_device("emulator-9999", devices))

    def test_empty_serial_returns_none(self) -> None:
        devices = [_device("emulator-5554")]
        self.assertIsNone(find_xiaowei_device("", devices))
        self.assertIsNone(find_xiaowei_device("   ", devices))

    def test_empty_device_list_returns_none(self) -> None:
        self.assertIsNone(find_xiaowei_device("emulator-5554", []))


class RequireXiaoweiDeviceTests(unittest.TestCase):
    def test_raises_when_not_found(self) -> None:
        with self.assertRaises(DeviceMappingError):
            require_xiaowei_device("emulator-5554", [])

    def test_returns_device_when_found(self) -> None:
        devices = [_device("emulator-5554")]
        found = require_xiaowei_device("emulator-5554", devices)
        self.assertEqual(found.serial, "emulator-5554")


if __name__ == "__main__":
    unittest.main()
