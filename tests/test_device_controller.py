"""Test cho DeviceController: chờ boot qua Xiaowei, không qua ADB local."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from device.controller import DeviceController
from ldplayer.adapter import LDInstance
from xiaowei.models import Device


class FakeLDPlayer:
    def list_instances(self) -> list[LDInstance]:
        return [LDInstance(index=1, name="LineViet-g01", running=True)]

    def adb_serial(self, index: int) -> str:
        return f"emulator-{5554 + index * 2}"


class FakeXiaowei:
    def __init__(self) -> None:
        self.read_commands: list[tuple[str, str]] = []

    def list_devices(self) -> list[Device]:
        return [Device(serial="emulator-5556", only_serial="emulator-5556")]

    def adb_read(self, serial: str, shell_command: str) -> str:
        self.read_commands.append((serial, shell_command))
        return "1\n"


class WaitForBootTests(unittest.TestCase):
    def test_wait_for_boot_returns_bound_device_when_xiaowei_reports_booted(self) -> None:
        xiaowei = FakeXiaowei()
        controller = DeviceController(FakeLDPlayer(), xiaowei)  # type: ignore[arg-type]

        bound = controller.wait_for_boot_by_index(1, timeout=0.5, poll_seconds=0.01)

        self.assertEqual(bound.instance_name, "LineViet-g01")
        self.assertEqual(bound.adb_serial, "emulator-5556")
        self.assertEqual(bound.xiaowei_serial, "emulator-5556")
        self.assertEqual(xiaowei.read_commands, [("emulator-5556", "getprop sys.boot_completed")])


if __name__ == "__main__":
    unittest.main()
