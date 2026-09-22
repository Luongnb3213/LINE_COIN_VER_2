"""Phase 1 test entrypoint: đăng ký/đăng nhập LINE bằng Google có sẵn cho một email.

Chạy:
    py phase1_test.py --config config.json --index 0 --email jisogabe@gmail.com

Luôn dùng config local của project này (`config.json`), bind thiết bị an toàn
qua `DeviceController` (map serial ADB <-> Xiaowei chỉ khớp chính xác, dừng
ngay nếu không khớp), chạy `Phase1LineFlow` rồi ghi kết quả vào Excel
(`xlsx_path`/`active_sheet` đọc từ config). Mỗi lần chạy tạo một file log
riêng dưới `logs/`.

`display_name`/`account_password` đọc từ dòng Excel của email này
(`get_account`); nếu Excel thiếu `account_password` thì lấy `default_password`
trong config.json làm mặc định — thiếu cả hai, hoặc thiếu `display_name`, thì
dừng TRƯỚC KHI đụng thiết bị.

Instance LDPlayer chỉ bị xoá khi truyền `--remove-instance` VÀ mọi điều kiện
an toàn đều đạt: ghi Excel thành công, Phase 1 `SUCCESS`, và nếu có chạy Phase
2 kèm theo (`--run-phase2-after-success`) thì Phase 2 cũng phải thành công.
Mặc định KHÔNG xoá gì, kể cả khi thành công — instance được giữ lại để
debug/chạy lại. Dọn dẹp mặc định CHỈ force-stop app LINE; `pm clear` (xoá app
data) chỉ chạy khi truyền `--clear-app-data` — không bao giờ tự động.

`run_phase1_once()` là hàm lõi tách riêng để `gui.py` gọi trực tiếp trong
background thread (không qua subprocess); `main()` chỉ là wrapper CLI mỏng
gọi lại đúng hàm này. `stop_event`/`log_callback` là tuỳ chọn: CLI không
truyền, GUI truyền để nhận log realtime và có đường dừng sớm tối thiểu — cùng
thiết kế với `phase2_test.run_phase2_once`. Khi `stop_event` đã set trước lúc
Phase 1 kết thúc (`status == "STOPPED"`), KHÔNG ghi `line_status=FAILED` vào
Excel — để nguyên dòng, không xoá instance dù `--remove-instance` có bật.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from core.config import ConfigError, load_settings
from core.logging_utils import add_callback_handler, add_file_handler, get_logger, remove_handler, setup_logging
from device.controller import DeviceController, DeviceControllerError
from flows.phase1_line import LINE_PACKAGE, Phase1LineFlow
from flows.xlsx_store import XlsxStore, XlsxStoreError
from ldplayer.adapter import LDPlayerAdapter, LDPlayerConfig, LDPlayerError
from phase2_test import run_phase2_once
from xiaowei.client import XiaoweiClient
from xiaowei.transport import WebSocketTransport

_LOG = get_logger("phase1_test")

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Chạy Phase 1 (đăng ký/đăng nhập LINE bằng Google có sẵn) cho một email, ghi kết quả vào Excel."
    )
    parser.add_argument("--config", default="config.json", help="Đường dẫn file config JSON.")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--index", type=int, help="Index instance LDPlayer.")
    target.add_argument("--name", type=str, help="Tên instance LDPlayer.")
    parser.add_argument("--email", required=True, help="Email (dòng trong Excel) cần chạy Phase 1.")
    parser.add_argument(
        "--remove-instance",
        action="store_true",
        help=(
            "Cho phép xoá instance LDPlayer SAU KHI Phase 1 (và Phase 2 nếu có chạy kèm) thành công "
            "và ghi Excel thành công. Mặc định KHÔNG xoá."
        ),
    )
    parser.add_argument(
        "--run-phase2-after-success",
        action="store_true",
        help="Nếu Phase 1 thành công, chạy tiếp Phase 2 (nhận gift code) trên cùng instance.",
    )
    parser.add_argument(
        "--source",
        choices=("both", "airwallet", "ministop"),
        default="both",
        help="Source cho Phase 2 khi dùng --run-phase2-after-success (mặc định cả hai).",
    )
    parser.add_argument(
        "--clear-app-data",
        action="store_true",
        help="Cho phép `pm clear` app LINE lúc dọn dẹp cuối cùng. Mặc định KHÔNG xoá app data.",
    )
    parser.add_argument("--log-dir", default="logs", help="Thư mục chứa log riêng cho mỗi lần chạy.")
    return parser.parse_args(argv)


def _stopped(stop_event: threading.Event | None) -> bool:
    return stop_event is not None and stop_event.is_set()


def _bind_stop_event(stop_event: threading.Event | None, controller: DeviceController, bound) -> None:
    """Gắn `controller`/`bound` vào `stop_event` sau khi bind thành công — xem
    ghi chú y hệt ở `phase2_test._bind_stop_event`."""
    if stop_event is None:
        return
    try:
        stop_event.controller = controller  # type: ignore[attr-defined]
        stop_event.bound = bound  # type: ignore[attr-defined]
    except AttributeError:
        pass


def _read_default_password(config_path: str) -> str:
    """Đọc `default_password` thẳng từ JSON gốc — `AppSettings` không có
    trường này (nó chỉ là fallback mật khẩu cho Phase 1, không phải cấu hình
    LDPlayer/Xiaowei) nên không đáng để mở rộng `core/config.py` cho một giá
    trị tuỳ chọn. Lỗi đọc file ở đây không quan trọng — `load_settings` đã
    được gọi trước và sẽ báo lỗi rõ hơn nếu file thật sự hỏng."""
    try:
        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("default_password") or "").strip()


def run_phase1_once(
    config_path: str = "config.json",
    *,
    index: int | None = None,
    name: str | None = None,
    email: str = "",
    remove_instance: bool = False,
    run_phase2_after_success: bool = False,
    source: str = "both",
    clear_app_data: bool = False,
    log_dir: str = "logs",
    stop_event: threading.Event | None = None,
    log_callback: Callable[[str], None] | None = None,
) -> int:
    """Chạy Phase 1 một lần cho một email — hàm lõi dùng chung cho CLI và GUI.

    Trả về exit code: 0 nếu Phase 1 `SUCCESS`, ghi Excel thành công, và (nếu
    `run_phase2_after_success`) Phase 2 cũng thành công; khác 0 nếu chưa đủ
    điều kiện/lỗi/đã dừng. Xem docstring module để biết chi tiết an toàn.
    """
    setup_logging()
    callback_handler = add_callback_handler(log_callback)
    file_handler = None
    try:
        safe_email = (email or "unknown").replace("@", "_at_").replace("/", "_")
        log_path = Path(log_dir) / f"phase1_{safe_email}_{datetime.now():%Y%m%d_%H%M%S}.log"
        file_handler = add_file_handler(log_path)
        _LOG.info("Log file riêng cho lần chạy này: %s", log_path)

        return _run_phase1(
            config_path,
            index=index,
            name=name,
            email=email,
            remove_instance=remove_instance,
            run_phase2_after_success=run_phase2_after_success,
            source=source,
            clear_app_data=clear_app_data,
            log_dir=log_dir,
            stop_event=stop_event,
        )
    finally:
        remove_handler(file_handler)
        remove_handler(callback_handler)


def _run_phase1(
    config_path: str,
    *,
    index: int | None,
    name: str | None,
    email: str,
    remove_instance: bool,
    run_phase2_after_success: bool,
    source: str,
    clear_app_data: bool,
    log_dir: str,
    stop_event: threading.Event | None,
) -> int:
    try:
        settings = load_settings(config_path)
    except ConfigError as exc:
        _LOG.error("Lỗi cấu hình: %s", exc)
        return 1

    if not settings.xlsx_path:
        _LOG.error("Config thiếu `xlsx_path` — cần để ghi kết quả Phase 1.")
        return 1

    try:
        xlsx_store = XlsxStore(settings.xlsx_path, settings.active_sheet)
    except XlsxStoreError as exc:
        _LOG.error("Lỗi Excel: %s", exc)
        return 1

    #: Preflight — đọc dòng Excel của email này TRƯỚC KHI đụng tới
    #: LDPlayer/thiết bị thật, để không phí một lượt chạy thật (Google có thể
    #: khoá tài khoản nếu đăng ký lặp lại bất thường) rồi mới phát hiện thiếu
    #: dữ liệu. Không dùng `get_pending_accounts` vì nó bỏ qua các dòng đã
    #: `line_status=SUCCESS` — CLI chạy cho một email cụ thể vẫn cần đọc được
    #: dòng đó (rerun thủ công).
    try:
        account = xlsx_store.get_account(email)
    except XlsxStoreError as exc:
        _LOG.error("Lỗi Excel khi preflight email: %s", exc)
        return 1
    if account is None:
        _LOG.error(
            "[%s] Không tìm thấy email trong Excel (`%s`, sheet `%s`) — dừng trước khi đụng thiết bị.",
            email, settings.xlsx_path, settings.active_sheet,
        )
        return 1

    display_name = account.display_name
    if not display_name:
        _LOG.error(
            "[%s] Dòng Excel thiếu `display_name`/`name` — không có tên để điền, dừng trước khi đụng thiết bị.",
            email,
        )
        return 1

    password = account.account_password or _read_default_password(config_path)
    if not password:
        _LOG.error(
            "[%s] Dòng Excel thiếu `account_password` và config thiếu `default_password` — "
            "không có mật khẩu để đặt, dừng trước khi đụng thiết bị.",
            email,
        )
        return 1
    _LOG.info(
        "[%s] display_name=%r account_password=%s",
        email, display_name, "(từ Excel)" if account.account_password else "(default_password trong config)",
    )

    if _stopped(stop_event):
        _LOG.info("[%s] Dừng theo yêu cầu trước khi khởi động thiết bị.", email)
        return 1

    ldplayer = LDPlayerAdapter(
        LDPlayerConfig(
            ld_console=settings.ldplayer.ld_console,
            adb_path=settings.ldplayer.adb_path,
            boot_timeout=settings.ldplayer.boot_timeout,
        )
    )

    try:
        instances = ldplayer.list_instances()
    except LDPlayerError as exc:
        _LOG.error("Không list được instance LDPlayer: %s", exc)
        return 1
    if not instances:
        _LOG.error("Không có instance LDPlayer nào. Tạo ít nhất một instance trước.")
        return 1

    if name:
        matches = [inst for inst in instances if inst.name == name]
        if not matches:
            _LOG.error("Không tìm thấy instance tên `%s`.", name)
            return 1
        resolved_index = matches[0].index
    elif index is not None:
        resolved_index = index
    elif account.google_instance:
        matches = [inst for inst in instances if inst.name == account.google_instance]
        if not matches:
            _LOG.error(
                "[%s] Excel đang map Google instance `%s` nhưng LDPlayer không có instance này — "
                "dừng để tránh chạy nhầm máy.",
                email, account.google_instance,
            )
            return 1
        resolved_index = matches[0].index
        _LOG.info(
            "[%s] Không chỉ định index/name, dùng Google instance trong Excel: %s (index=%s)",
            email, account.google_instance, resolved_index,
        )
    else:
        resolved_index = instances[0].index
        _LOG.info("Không chỉ định index/name, dùng instance đầu tiên: index=%s", resolved_index)

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

    controller = DeviceController(ldplayer, xiaowei)
    try:
        if not ldplayer.is_running(resolved_index):
            _LOG.info("Instance %s chưa chạy, đang khởi động...", resolved_index)
            ldplayer.start_instance(resolved_index)
        bound = controller.wait_for_boot_by_index(
            resolved_index,
            timeout=settings.ldplayer.boot_timeout,
            stop_event=stop_event,
            log=lambda message: _LOG.info("[%s] %s", email, message),
        )
    except LDPlayerError as exc:
        _LOG.error("Không khởi động được instance %s: %s", resolved_index, exc)
        return 1
    except DeviceControllerError as exc:
        _LOG.error("DỪNG AN TOÀN — không map được thiết bị: %s", exc)
        return 1

    _bind_stop_event(stop_event, controller, bound)

    if _stopped(stop_event):
        _LOG.info("[%s] Dừng theo yêu cầu ngay sau khi bind — bỏ qua chạy flow.", email)
        return 1

    flow = Phase1LineFlow(controller, bound)
    result = flow.run(email, display_name, password, email_password=account.email_password, stop_event=stop_event)

    excel_ok = True
    if result.status == "SUCCESS":
        try:
            xlsx_store.update_line_success(email)
        except XlsxStoreError as exc:
            excel_ok = False
            _LOG.error("[%s] Ghi Excel thất bại: %s", email, exc)
    elif result.status == "FAILED":
        try:
            xlsx_store.update_line_failed(email, result.message)
        except XlsxStoreError as exc:
            excel_ok = False
            _LOG.error("[%s] Ghi Excel thất bại: %s", email, exc)
    else:
        #: STOPPED — không ghi gì vào Excel, để nguyên dòng cho lần chạy sau.
        _LOG.info("[%s] Đã dừng theo yêu cầu — không ghi Excel.", email)

    #: Dọn dẹp mặc định CHỈ ở mức app: force-stop LINE để không để lại tiến
    #: trình treo. KHÔNG xoá app data trừ khi `--clear-app-data` được truyền.
    _LOG.info("[%s] Dọn dẹp: force-stop LINE (chỉ stop app%s).", email, ", sẽ xoá app data" if clear_app_data else "")
    controller.stop_app(bound, LINE_PACKAGE)
    if clear_app_data:
        controller.clear_app_data(bound, LINE_PACKAGE)
        _LOG.info("[%s] Đã xoá app data LINE (--clear-app-data).", email)
    _LOG.info("[%s] Đã force-stop LINE.", email)

    ran_phase2 = False
    phase2_ok = True
    if run_phase2_after_success and result.status == "SUCCESS" and not _stopped(stop_event):
        _LOG.info("[%s] Phase 1 thành công — chạy tiếp Phase 2 (source=%s)...", email, source)
        ran_phase2 = True
        phase2_code = run_phase2_once(
            config_path,
            index=resolved_index,
            name=None,
            email=email,
            source=source,
            remove_instance=False,
            log_dir=log_dir,
            stop_event=stop_event,
        )
        phase2_ok = phase2_code == 0
        _LOG.info("[%s] Phase 2 %s.", email, "OK" if phase2_ok else "chưa hoàn tất")

    removed = False
    if _stopped(stop_event):
        _LOG.info("[%s] Dừng theo yêu cầu — bỏ qua xoá instance dù --remove-instance có bật.", email)
    elif remove_instance:
        if excel_ok and result.status == "SUCCESS" and (not ran_phase2 or phase2_ok):
            _LOG.info(
                "[%s] Đủ điều kiện an toàn (excel_ok, Phase 1 SUCCESS%s) — xoá instance %s.",
                email, ", Phase 2 SUCCESS" if ran_phase2 else "", resolved_index,
            )
            try:
                ldplayer.remove_instance(resolved_index)
                removed = True
            except LDPlayerError as exc:
                _LOG.error("[%s] Xoá instance %s thất bại: %s", email, resolved_index, exc)
        else:
            _LOG.info(
                "[%s] --remove-instance được truyền nhưng CHƯA đủ điều kiện an toàn "
                "(excel_ok=%s phase1_status=%s ran_phase2=%s phase2_ok=%s) — giữ lại instance để debug/chạy lại.",
                email, excel_ok, result.status, ran_phase2, phase2_ok,
            )

    ok = result.status == "SUCCESS" and excel_ok and (not ran_phase2 or phase2_ok)
    _LOG.info(
        "PHASE1 TEST %s — email=%s status=%s excel_ok=%s ran_phase2=%s phase2_ok=%s instance_removed=%s",
        "OK" if ok else "INCOMPLETE",
        email, result.status, excel_ok, ran_phase2, phase2_ok, removed,
    )
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return run_phase1_once(
        args.config,
        index=args.index,
        name=args.name,
        email=args.email,
        remove_instance=args.remove_instance,
        run_phase2_after_success=args.run_phase2_after_success,
        source=args.source,
        clear_app_data=args.clear_app_data,
        log_dir=args.log_dir,
    )


if __name__ == "__main__":
    sys.exit(main())
