"""Direct ADB controller used only as a fallback for Google preparation.

Phase 1/Phase 2 should keep using :mod:`device.controller` so Xiaowei remains
the source of truth for UI clicks. Google preparation is different: it happens
on a freshly cloned LDPlayer instance before gift-code work, and Xiaowei may
not list that emulator yet. In that narrow case, direct ADB is safer than
blocking the whole preparation step forever.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable

from core.logging_utils import get_logger
from device.ui_tree import Selector, UiNode, UiTree, parse_ui_dump
from ldplayer.adapter import LDPlayerAdapter

_LOG = get_logger("adb_controller")

_RESUMED_RE = re.compile(r"\bu0\s+([\w.]+)/([\w.$]+)")
_CLEAR_KEY_COUNT = 40


def _shell_quote(text: str) -> str:
    return "'" + str(text).replace("'", "'\\''") + "'"


class AdbControllerError(RuntimeError):
    """Direct ADB fallback failed."""


@dataclass(frozen=True)
class AdbBoundDevice:
    ldplayer_index: int
    instance_name: str
    adb_serial: str

    @property
    def xiaowei_serial(self) -> str:
        return self.adb_serial


class AdbDeviceController:
    """Small subset of DeviceController backed by `adb.exe`."""

    def __init__(self, ldplayer: LDPlayerAdapter) -> None:
        self._ldplayer = ldplayer

    def wait_for_boot_by_index(
        self,
        index: int,
        *,
        timeout: float = 180.0,
        poll_seconds: float = 3.0,
        stop_event=None,
        log: Callable[[str], None] | None = None,
    ) -> AdbBoundDevice:
        instances = {inst.index: inst for inst in self._ldplayer.list_instances()}
        instance = instances.get(index)
        if instance is None:
            raise AdbControllerError(f"Không tìm thấy instance LDPlayer index {index}.")
        serial = self._ldplayer.adb_serial(index)
        bound = AdbBoundDevice(ldplayer_index=index, instance_name=instance.name, adb_serial=serial)
        deadline = time.monotonic() + timeout
        last_error = ""
        last_log_at = 0.0
        while time.monotonic() < deadline:
            if stop_event is not None and stop_event.is_set():
                raise AdbControllerError("STOPPED")
            try:
                boot_completed = self.shell_read(bound, "getprop sys.boot_completed").strip()
                if boot_completed == "1":
                    return bound
                last_error = f"ADB thấy {serial}, boot_completed={boot_completed or '<empty>'}"
            except Exception as exc:  # noqa: BLE001 - emulator may not expose adb yet
                last_error = str(exc)
            now = time.monotonic()
            if log is not None and now - last_log_at >= 9.0:
                log(f"Chờ ADB boot instance index {index}: {last_error}")
                last_log_at = now
            time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))
        raise AdbControllerError(
            f"ADB_BOOT_TIMEOUT: instance index {index} ({serial}) chưa sẵn sàng sau {timeout:g}s: {last_error}"
        )

    def ui_dump(self, bound: AdbBoundDevice) -> str:
        remote_path = "/sdcard/line_coin_ver2_dump.xml"
        self.shell_exec(bound, f"rm -f {remote_path}; uiautomator dump {remote_path}")
        for _ in range(10):
            output = self.shell_read(bound, f"cat {remote_path} 2>/dev/null")
            if "<hierarchy" in output:
                return output
            time.sleep(0.5)
        return self.shell_read(bound, f"cat {remote_path} 2>/dev/null")

    def ui_tree(self, bound: AdbBoundDevice) -> UiTree:
        return parse_ui_dump(self.ui_dump(bound))

    def find_node(self, bound: AdbBoundDevice, selector: Selector, tree: UiTree | None = None) -> UiNode | None:
        active_tree = tree or self.ui_tree(bound)
        return active_tree.find(selector)

    def tap_node(self, bound: AdbBoundDevice, node: UiNode) -> None:
        target = node.clickable_self_or_ancestor()
        if target is None:
            raise AdbControllerError(f"Node `{node.describe()}` không bấm được và không có tổ tiên bấm được.")
        x, y = self._tap_point(node, target)
        _LOG.info("ADB tap node %s tại (%s, %s)", target.describe(), x, y)
        self.tap_bounds(bound, x, y)

    def tap_bounds(self, bound: AdbBoundDevice, x: int, y: int) -> None:
        self.shell_exec(bound, f"input tap {int(x)} {int(y)}")

    @staticmethod
    def _tap_point(matched: UiNode, target: UiNode) -> tuple[int, int]:
        mx, my = matched.bounds.center
        if matched is target or not target.bounds.visible:
            return mx, my
        return target.bounds.clamp_point(mx, my)

    def swipe(self, bound: AdbBoundDevice, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self.shell_exec(bound, f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration_ms)}")

    def scroll_down(self, bound: AdbBoundDevice, tree: UiTree | None = None) -> None:
        screen = (tree or self.ui_tree(bound)).screen
        if screen.visible:
            x = (screen.left + screen.right) // 2
            top = screen.top + int(screen.height * 0.75)
            bottom = screen.top + int(screen.height * 0.25)
        else:
            x, top, bottom = 500, 1500, 500
        self.swipe(bound, x, top, x, bottom)

    def start_app(self, bound: AdbBoundDevice, package: str) -> None:
        self.shell_exec(bound, f"monkey -p {package} -c android.intent.category.LAUNCHER 1")

    def stop_app(self, bound: AdbBoundDevice, package: str) -> None:
        self.shell_exec(bound, f"am force-stop {package}")

    def _read_resumed(self, bound: AdbBoundDevice) -> re.Match[str] | None:
        output = self.shell_read(
            bound,
            "dumpsys activity activities | grep -e mResumedActivity -e topResumedActivity",
        )
        return _RESUMED_RE.search(output)

    def foreground_package(self, bound: AdbBoundDevice) -> str:
        match = self._read_resumed(bound)
        return match.group(1) if match else ""

    def foreground_activity(self, bound: AdbBoundDevice) -> str:
        match = self._read_resumed(bound)
        return match.group(2) if match else ""

    def shell_read(self, bound: AdbBoundDevice, command: str) -> str:
        return self._ldplayer.adb_shell_read(bound.adb_serial, command)

    def shell_exec(self, bound: AdbBoundDevice, command: str) -> None:
        self._ldplayer.adb_shell_exec(bound.adb_serial, command)

    def clear_text(self, bound: AdbBoundDevice, max_chars: int = _CLEAR_KEY_COUNT) -> None:
        self.shell_exec(bound, "input keyevent KEYCODE_MOVE_END")
        keys = " ".join(["KEYCODE_DEL"] * max_chars)
        self.shell_exec(bound, f"input keyevent {keys}")

    def type_text(self, bound: AdbBoundDevice, text: str) -> None:
        self.shell_exec(bound, f"input text {_shell_quote(text)}")

    def set_text(self, bound: AdbBoundDevice, node: UiNode, text: str) -> None:
        self.tap_node(bound, node)
        time.sleep(0.3)
        self.clear_text(bound)
        self.type_text(bound, text)

    def is_installed(self, bound: AdbBoundDevice, package: str) -> bool:
        return package in self.shell_read(bound, f"pm list packages {package}")

    def clear_app_data(self, bound: AdbBoundDevice, package: str) -> None:
        self.shell_exec(bound, f"pm clear {package}")


__all__ = ["AdbBoundDevice", "AdbControllerError", "AdbDeviceController"]
