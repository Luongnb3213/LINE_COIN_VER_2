"""Smoke test Phase 1: LDPlayer -> map Xiaowei -> UI dump -> click/swipe an toàn.

Chạy:
    python smoke_test.py --config config.json [--index 0 | --name "LDPlayer"]

Các bước (đúng yêu cầu Phase 1):
  1. List instance LDPlayer, log tất cả.
  2. Boot instance mục tiêu nếu chưa chạy, lấy serial ADB.
  3. List thiết bị Xiaowei, log tất cả.
  4. Map serial ADB -> thiết bị Xiaowei (chỉ khớp chính xác). Map thất bại thì
     dừng ngay, KHÔNG click lên bất kỳ đâu.
  5. Dump UI tree, log tóm tắt màn hình + package đang foreground.
  6. Chụp màn hình.
  7. Thực hiện một click an toàn (ưu tiên nút Home tìm được qua UI tree, nếu
     không có thì dùng phím tắt Home của Xiaowei — không bao giờ click bừa
     vào một node chưa xác định).
  8. Thực hiện một swipe an toàn (cuộn xuống theo kích thước màn hình thật).

`run_smoke_test_once()` là hàm lõi tách riêng để `gui.py` gọi trực tiếp trong
background thread (nút "Test kết nối"); `main()` chỉ là wrapper CLI mỏng gọi
lại đúng hàm này. Không bao giờ xoá instance.
"""

from __future__ import annotations

import argparse
import sys
import threading
from typing import Callable

from core.config import ConfigError, load_settings
from core.logging_utils import add_callback_handler, get_logger, remove_handler, setup_logging
from device.controller import DeviceController, DeviceControllerError
from device.ui_tree import Selector, UiTree
from ldplayer.adapter import LDPlayerAdapter, LDPlayerConfig, LDPlayerError
from xiaowei.client import XiaoweiClient
from xiaowei.transport import WebSocketTransport

_LOG = get_logger("smoke_test")

_HOME_HINTS = ("home", "trang chủ", "trang chu")


def _find_home_node(tree: UiTree):
    """Tìm một node có vẻ là nút Home, có tổ tiên/chính nó bấm được.

    Chỉ dùng cho cú click "an toàn" trong smoke test — không suy đoán gì hơn
    ngoài các nhãn phổ biến, và luôn yêu cầu resolve được node bấm được trước
    khi coi là ứng viên hợp lệ.
    """
    for node in tree.nodes:
        haystack = f"{node.text} {node.content_desc} {node.resource_id}".lower()
        if any(hint in haystack for hint in _HOME_HINTS):
            if node.clickable_self_or_ancestor() is not None:
                return node
    return None


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test LDPlayer + Xiaowei (Phase 1).")
    parser.add_argument("--config", default="config.json", help="Đường dẫn file config JSON.")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--index", type=int, help="Index instance LDPlayer.")
    target.add_argument("--name", type=str, help="Tên instance LDPlayer.")
    parser.add_argument(
        "--screenshot",
        default="smoke_screenshot.png",
        help="Đường dẫn lưu screenshot (savePath truyền cho Xiaowei).",
    )
    return parser.parse_args(argv)


def _stopped(stop_event: threading.Event | None) -> bool:
    return stop_event is not None and stop_event.is_set()


def run_smoke_test_once(
    config_path: str = "config.json",
    *,
    index: int | None = None,
    name: str | None = None,
    screenshot: str = "smoke_screenshot.png",
    stop_event: threading.Event | None = None,
    log_callback: Callable[[str], None] | None = None,
) -> int:
    """Chạy smoke test Phase 1 một lần — hàm lõi dùng chung cho CLI và GUI.

    `stop_event`/`log_callback` tuỳ chọn, cùng thiết kế với
    `phase2_test.run_phase2_once`: `stop_event` được kiểm tra ở các mốc trước
    khi đụng thiết bị (không làm gián đoạn boot instance đang chờ, đó là giới
    hạn đã biết); `log_callback` nhận mỗi dòng log đã format qua một
    `CallbackHandler` gắn tạm vào logger gốc. Không bao giờ xoá instance.
    """
    setup_logging()
    callback_handler = add_callback_handler(log_callback)
    try:
        return _run_smoke_test(
            config_path, index=index, name=name, screenshot=screenshot, stop_event=stop_event
        )
    finally:
        remove_handler(callback_handler)


def _run_smoke_test(
    config_path: str,
    *,
    index: int | None,
    name: str | None,
    screenshot: str,
    stop_event: threading.Event | None,
) -> int:
    try:
        settings = load_settings(config_path)
    except ConfigError as exc:
        _LOG.error("Lỗi cấu hình: %s", exc)
        return 1

    ldplayer = LDPlayerAdapter(
        LDPlayerConfig(
            ld_console=settings.ldplayer.ld_console,
            adb_path=settings.ldplayer.adb_path,
            boot_timeout=settings.ldplayer.boot_timeout,
        )
    )

    # 1. List instance LDPlayer.
    try:
        instances = ldplayer.list_instances()
    except LDPlayerError as exc:
        _LOG.error("Không list được instance LDPlayer: %s", exc)
        return 1
    if not instances:
        _LOG.error("Không có instance LDPlayer nào. Tạo ít nhất một instance trước.")
        return 1
    for instance in instances:
        _LOG.info(
            "LDPlayer[%s] `%s` running=%s",
            instance.index,
            instance.name,
            instance.running,
        )

    if name:
        matches = [inst for inst in instances if inst.name == name]
        if not matches:
            _LOG.error("Không tìm thấy instance tên `%s`.", name)
            return 1
        resolved_index = matches[0].index
    elif index is not None:
        resolved_index = index
    else:
        resolved_index = instances[0].index
        _LOG.info("Không chỉ định index/name, dùng instance đầu tiên: index=%s", resolved_index)

    if _stopped(stop_event):
        _LOG.info("Dừng theo yêu cầu trước khi khởi động thiết bị.")
        return 1

    # 2. Boot nếu cần, lấy serial ADB.
    try:
        if not ldplayer.is_running(resolved_index):
            _LOG.info("Instance %s chưa chạy, đang khởi động...", resolved_index)
            ldplayer.start_instance(resolved_index)
        adb_serial = ldplayer.adb_serial(resolved_index)
    except LDPlayerError as exc:
        _LOG.error("Không khởi động được instance %s: %s", resolved_index, exc)
        return 1
    _LOG.info("Instance %s có serial ADB (suy ra theo công thức): %s", resolved_index, adb_serial)

    if _stopped(stop_event):
        _LOG.info("Dừng theo yêu cầu trước khi map thiết bị Xiaowei.")
        return 1

    # 3. List thiết bị Xiaowei.
    transport = WebSocketTransport(
        settings.xiaowei.ws_url,
        connect_timeout=settings.xiaowei.connect_timeout,
        request_timeout=settings.xiaowei.request_timeout,
    )
    xiaowei = XiaoweiClient(
        transport,
        max_retries=settings.xiaowei.max_retries,
        retry_backoff=settings.xiaowei.retry_backoff,
        screenshot_dir=settings.xiaowei.screenshot_dir,
    )
    try:
        devices = xiaowei.list_devices()
    except Exception as exc:  # noqa: BLE001 - log rồi dừng, không đoán tiếp
        _LOG.error("Không list được thiết bị Xiaowei: %s", exc)
        return 1
    if not devices:
        _LOG.error("Xiaowei không báo cáo thiết bị nào. Kiểm tra Xiaowei đã kết nối máy ảo chưa.")
        return 1
    for device in devices:
        _LOG.info(
            "Xiaowei device serial=%s onlySerial=%s status=%s model=%s",
            device.serial,
            device.only_serial or "-",
            device.status or "-",
            device.model or "-",
        )

    # 4. Map an toàn: dừng ngay nếu không khớp chính xác.
    controller = DeviceController(ldplayer, xiaowei)
    try:
        bound = controller.bind_by_index(resolved_index)
    except DeviceControllerError as exc:
        _LOG.error("DỪNG AN TOÀN — không map được thiết bị: %s", exc)
        return 1

    if _stopped(stop_event):
        _LOG.info("[%s] Dừng theo yêu cầu ngay sau khi bind — bỏ qua thao tác UI.", bound.instance_name)
        return 1

    # 5. UI dump + foreground package.
    tree = controller.ui_tree(bound)
    if tree.empty:
        _LOG.error("UI dump rỗng — không tiếp tục thao tác trên màn hình chưa xác định.")
        return 1
    _LOG.info("Tóm tắt màn hình: %s", tree.summary())
    foreground = controller.foreground_package(bound)
    _LOG.info("Package foreground: %s", foreground or "(không đọc được)")

    # 6. Screenshot.
    controller.screenshot(bound, screenshot)
    _LOG.info("Đã yêu cầu chụp màn hình -> %s", screenshot)

    # 7. Click an toàn.
    home_node = _find_home_node(tree)
    if home_node is not None:
        _LOG.info("Tìm thấy node giống nút Home: %s", home_node.describe())
        controller.tap_node(bound, home_node)
    else:
        _LOG.info("Không tìm thấy node Home trong UI tree, dùng phím tắt Home của Xiaowei.")
        controller.press_home(bound)

    # 8. Swipe an toàn.
    controller.scroll_down(bound, tree)

    _LOG.info(
        "SMOKE TEST OK — ldplayer_index=%s instance=%s adb_serial=%s "
        "xiaowei_serial=%s xiaowei_only_serial=%s foreground=%s",
        bound.ldplayer_index,
        bound.instance_name,
        bound.adb_serial,
        bound.xiaowei_serial,
        bound.xiaowei_device.only_serial or "-",
        foreground or "-",
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return run_smoke_test_once(args.config, index=args.index, name=args.name, screenshot=args.screenshot)


if __name__ == "__main__":
    sys.exit(main())
