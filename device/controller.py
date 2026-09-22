"""`DeviceController`: điểm truy cập duy nhất mà flow nên dùng.

Flow không bao giờ import trực tiếp `ldplayer` hay `xiaowei` — mọi thao tác đi
qua đây, để việc map serial an toàn (fail-closed) luôn được áp dụng trước khi
bất kỳ lệnh nào chạm vào máy ảo.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable

from core.logging_utils import get_logger
from device.mapping import require_xiaowei_device
from device.ui_tree import Rect, Selector, UiNode, UiTree, parse_ui_dump
from ldplayer.adapter import LDPlayerAdapter
from xiaowei.client import XiaoweiClient
from xiaowei.models import Device

_LOG = get_logger("device")

#: Android cũ: `mResumedActivity: ActivityRecord{... u0 com.pkg/.MainActivity t123}`
#: Android mới: `topResumedActivity=ActivityRecord{... u0 com.pkg/.MainActivity t123}`
#: Nhóm 1 = package, nhóm 2 = activity (tương đối hoặc đầy đủ) — dùng chung
#: cho cả `foreground_package` lẫn `foreground_activity`.
_RESUMED_RE = re.compile(r"\bu0\s+([\w.]+)/([\w.$]+)")

#: pushEvent: 1 recent, 2 home, 3 back (xem xiaowei/actions.py).
_PUSH_RECENT = 1
_PUSH_HOME = 2
_PUSH_BACK = 3

#: Số lần bấm KEYCODE_DEL để xoá field trước khi gõ text mới — không có API
#: "select-all" đáng tin cậy qua ADB `input` thuần (không IME/uiautomator2)
#: trên mọi bản Android của LDPlayer, nên lùi cursor về cuối rồi xoá lùi một
#: số lượng đủ lớn cho tên hiển thị/mật khẩu; DEL trên field đã rỗng là no-op.
_CLEAR_KEY_COUNT = 40


def _shell_quote(text: str) -> str:
    """Escape text để chèn an toàn trong dấu nháy đơn của lệnh shell từ xa.

    Theo đúng quy ước đã dùng ở `flows/phase2.py::_send_chrome_intent` — nháy
    đơn quanh chuỗi vì `adb_shell` của Xiaowei xử lý sai nháy kép lồng nhau;
    ký tự nháy đơn bên trong text được thoát theo kiểu POSIX chuẩn.
    """
    return "'" + str(text).replace("'", "'\\''") + "'"


class DeviceControllerError(RuntimeError):
    """Lỗi ở tầng điều phối chung, bao gồm cả lỗi map serial."""


@dataclass(frozen=True)
class BoundDevice:
    """Một cặp instance LDPlayer đã được xác nhận khớp với thiết bị Xiaowei."""

    ldplayer_index: int
    instance_name: str
    adb_serial: str
    xiaowei_device: Device

    @property
    def xiaowei_serial(self) -> str:
        return self.xiaowei_device.serial


class DeviceController:
    """Lớp chung cho flow: bind an toàn rồi điều khiển UI qua Xiaowei."""

    def __init__(self, ldplayer: LDPlayerAdapter, xiaowei: XiaoweiClient) -> None:
        self._ldplayer = ldplayer
        self._xiaowei = xiaowei

    # -- binding -------------------------------------------------------------

    def bind_by_index(self, index: int) -> BoundDevice:
        """Map một instance LDPlayer sang thiết bị Xiaowei, chỉ khớp chính xác.

        Đây là hàm an toàn cốt lõi: nếu không map được, raise
        `DeviceControllerError` thay vì đoán — người gọi phải dừng lại, không
        được click lên nhầm thiết bị.
        """
        instances = {inst.index: inst for inst in self._ldplayer.list_instances()}
        instance = instances.get(index)
        if instance is None:
            raise DeviceControllerError(f"Không tìm thấy instance LDPlayer index {index}.")

        adb_serial = self._ldplayer.adb_serial(index)
        devices = self._xiaowei.list_devices()
        try:
            xiaowei_device = require_xiaowei_device(adb_serial, devices)
        except Exception as exc:
            raise DeviceControllerError(str(exc)) from exc

        bound = BoundDevice(
            ldplayer_index=index,
            instance_name=instance.name,
            adb_serial=adb_serial,
            xiaowei_device=xiaowei_device,
        )
        _LOG.info(
            "Đã map: LDPlayer[%s] `%s` -> adb=%s -> xiaowei serial=%s onlySerial=%s model=%s",
            bound.ldplayer_index,
            bound.instance_name,
            bound.adb_serial,
            xiaowei_device.serial,
            xiaowei_device.only_serial or "-",
            xiaowei_device.model or "-",
        )
        return bound

    def wait_for_boot_by_index(
        self,
        index: int,
        *,
        timeout: float = 180.0,
        poll_seconds: float = 3.0,
        stop_event=None,
        log: Callable[[str], None] | None = None,
    ) -> BoundDevice:
        """Chờ Xiaowei thấy đúng instance và Android báo boot xong.

        Không gọi `adb.exe` trực tiếp ở đây; toàn bộ readiness đi qua Xiaowei để
        tránh tranh ADB server với chính Xiaowei.
        """
        deadline = time.monotonic() + timeout
        last_error = ""
        last_log_at = 0.0
        adb_serial = self._ldplayer.adb_serial(index)

        while time.monotonic() < deadline:
            if stop_event is not None and stop_event.is_set():
                raise DeviceControllerError("STOPPED")
            try:
                bound = self.bind_by_index(index)
                boot_completed = self.shell_read(bound, "getprop sys.boot_completed").strip()
                if boot_completed == "1":
                    return bound
                last_error = f"Xiaowei đã thấy {adb_serial}, boot_completed={boot_completed or '<empty>'}"
            except Exception as exc:  # noqa: BLE001 - Xiaowei/Android có thể chưa sẵn sàng lúc boot
                last_error = str(exc)

            now = time.monotonic()
            if log is not None and now - last_log_at >= 9.0:
                log(f"Chờ Xiaowei nhận/boot instance index {index}: {last_error}")
                last_log_at = now
            time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))

        raise DeviceControllerError(
            f"XIAOWEI_BOOT_TIMEOUT: instance index {index} ({adb_serial}) chưa sẵn sàng "
            f"sau {timeout:g}s: {last_error}"
        )

    # -- UI tree ---------------------------------------------------------

    def ui_dump(self, bound: BoundDevice) -> str:
        remote_path = "/sdcard/line_coin_ver2_dump.xml"
        self._xiaowei.adb_shell(
            bound.xiaowei_serial,
            f"rm -f {remote_path}; uiautomator dump {remote_path}",
        )
        for _ in range(10):
            output = self._xiaowei.adb_read(bound.xiaowei_serial, f"cat {remote_path} 2>/dev/null")
            if "<hierarchy" in output:
                return output
            time.sleep(0.5)
        return self._xiaowei.adb_read(bound.xiaowei_serial, f"cat {remote_path} 2>/dev/null")

    def ui_tree(self, bound: BoundDevice) -> UiTree:
        return parse_ui_dump(self.ui_dump(bound))

    def find_node(self, bound: BoundDevice, selector: Selector, tree: UiTree | None = None) -> UiNode | None:
        active_tree = tree or self.ui_tree(bound)
        return active_tree.find(selector)

    # -- interaction -------------------------------------------------------

    def tap_node(self, bound: BoundDevice, node: UiNode) -> None:
        target = node.clickable_self_or_ancestor()
        if target is None:
            raise DeviceControllerError(f"Node `{node.describe()}` không bấm được và không có tổ tiên bấm được.")
        x, y = self._tap_point(node, target)
        _LOG.info("Tap node %s tại (%s, %s)", target.describe(), x, y)
        self._xiaowei.tap_pixels(bound.xiaowei_serial, x, y)

    def tap_bounds(self, bound: BoundDevice, x: int, y: int) -> None:
        _LOG.info("Tap toạ độ cố định (%s, %s) — không dùng UI tree.", x, y)
        self._xiaowei.tap_pixels(bound.xiaowei_serial, x, y)

    @staticmethod
    def _tap_point(matched: UiNode, target: UiNode) -> tuple[int, int]:
        """Điểm tap: tâm của node đã khớp, kẹp vào trong bounds của node bấm được.

        Khi node bấm được là một container lớn (vd. cả thanh nav) còn node
        khớp chữ nhỏ và lệch tâm, tap vào tâm container có thể trật mục tiêu —
        nên ưu tiên tâm của node đã khớp, chỉ kẹp toạ độ vào trong bounds của
        node thực sự nhận sự kiện chạm.
        """
        mx, my = matched.bounds.center
        if matched is target or not target.bounds.visible:
            return mx, my
        return target.bounds.clamp_point(mx, my)

    def press_home(self, bound: BoundDevice) -> None:
        self._xiaowei.push_event(bound.xiaowei_serial, _PUSH_HOME)

    def press_back(self, bound: BoundDevice) -> None:
        self._xiaowei.push_event(bound.xiaowei_serial, _PUSH_BACK)

    def swipe(self, bound: BoundDevice, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        _LOG.info("Swipe (%s,%s) -> (%s,%s) trong %sms", x1, y1, x2, y2, duration_ms)
        self._xiaowei.swipe_pixels(bound.xiaowei_serial, x1, y1, x2, y2, duration_ms)

    def scroll_down(self, bound: BoundDevice, tree: UiTree | None = None) -> None:
        """Vuốt lên (cuộn nội dung xuống) dựa theo kích thước màn hình thật.

        Toạ độ lấy từ `tree.screen` của chính bản dump hiện tại, không hard
        code độ phân giải — nếu dump rỗng thì lùi về một cử chỉ an toàn ở giữa
        màn hình theo tỉ lệ chung.
        """
        screen = (tree or self.ui_tree(bound)).screen
        if screen.visible:
            x = (screen.left + screen.right) // 2
            top = screen.top + int(screen.height * 0.75)
            bottom = screen.top + int(screen.height * 0.25)
        else:
            _LOG.warning("Không đọc được kích thước màn hình, dùng cử chỉ scroll mặc định.")
            x, top, bottom = 500, 1500, 500
        self.swipe(bound, x, top, x, bottom)

    # -- apps / shell ------------------------------------------------------

    def start_app(self, bound: BoundDevice, package: str) -> None:
        self._xiaowei.start_apk(bound.xiaowei_serial, package)

    def stop_app(self, bound: BoundDevice, package: str) -> None:
        self._xiaowei.stop_apk(bound.xiaowei_serial, package)

    def _read_resumed(self, bound: BoundDevice) -> re.Match[str] | None:
        output = self._xiaowei.adb_read(
            bound.xiaowei_serial,
            "dumpsys activity activities | grep -e mResumedActivity -e topResumedActivity",
        )
        return _RESUMED_RE.search(output)

    def foreground_package(self, bound: BoundDevice) -> str:
        match = self._read_resumed(bound)
        return match.group(1) if match else ""

    def foreground_activity(self, bound: BoundDevice) -> str:
        """Tên activity đang resume (không kèm package), vd. `.MainActivity`
        hoặc `com.linecorp.line.registration.ui.RegistrationActivity`.

        Trả chuỗi rỗng nếu không đọc được — người gọi tự quyết định coi đó là
        "chưa rõ màn hình" thay vì đoán bừa.
        """
        match = self._read_resumed(bound)
        return match.group(2) if match else ""

    def shell_read(self, bound: BoundDevice, command: str) -> str:
        return self._xiaowei.adb_read(bound.xiaowei_serial, command)

    def shell_exec(self, bound: BoundDevice, command: str) -> None:
        """Chạy shell command làm thay đổi trạng thái máy — không tự retry."""
        self._xiaowei.adb_shell(bound.xiaowei_serial, command)

    # -- text input ----------------------------------------------------------

    def clear_text(self, bound: BoundDevice, max_chars: int = _CLEAR_KEY_COUNT) -> None:
        """Xoá nội dung field đang focus bằng cách lùi cuối rồi xoá lùi.

        Không có API "select-all" đáng tin cậy qua ADB `input` thuần (không
        IME/uiautomator2), nên dùng `KEYCODE_MOVE_END` rồi bấm `KEYCODE_DEL`
        lặp lại — bấm DEL trên field đã rỗng là no-op nên xoá dư không sao.
        """
        self.shell_exec(bound, "input keyevent KEYCODE_MOVE_END")
        keys = " ".join(["KEYCODE_DEL"] * max_chars)
        self.shell_exec(bound, f"input keyevent {keys}")

    def type_text(self, bound: BoundDevice, text: str) -> None:
        self.shell_exec(bound, f"input text {_shell_quote(text)}")

    def set_text(self, bound: BoundDevice, node: UiNode, text: str) -> None:
        """Bấm vào field, xoá nội dung cũ rồi gõ `text` mới.

        Lưu ý: `input text` hỗ trợ Unicode yếu hơn IME của uiautomator2 —
        đủ dùng cho tên/mật khẩu ASCII từ Excel, cần kiểm chứng thêm trên
        máy thật nếu tên hiển thị có ký tự ngoài ASCII.
        """
        self.tap_node(bound, node)
        time.sleep(0.3)
        self.clear_text(bound)
        self.type_text(bound, text)

    def is_installed(self, bound: BoundDevice, package: str) -> bool:
        output = self._xiaowei.adb_read(bound.xiaowei_serial, f"pm list packages {package}")
        return package in output

    def clear_app_data(self, bound: BoundDevice, package: str) -> None:
        self.shell_exec(bound, f"pm clear {package}")

    def screenshot(self, bound: BoundDevice, save_path: str | None = None) -> None:
        self._xiaowei.screenshot(bound.xiaowei_serial, save_path)


__all__ = ["BoundDevice", "DeviceController", "DeviceControllerError"]
