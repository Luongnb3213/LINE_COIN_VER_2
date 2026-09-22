"""Phase 2 test entrypoint: nhận gift code AirWallet/Ministop cho một email.

Chạy:
    py phase2_test.py --config config.json --index 0 --email jisogabe@gmail.com

Luôn dùng config local của project này (`config.json`), bind thiết bị an toàn
qua `DeviceController` (map serial ADB <-> Xiaowei chỉ khớp chính xác, dừng
ngay nếu không khớp), chạy `Phase2Flow` rồi ghi kết quả vào Excel (`xlsx_path`/
`active_sheet` đọc từ config). Mỗi lần chạy tạo một file log riêng dưới
`logs/`.

Instance LDPlayer chỉ bị xoá khi truyền `--remove-instance` VÀ mọi điều kiện
an toàn đều đạt: ghi Excel thành công, `phase2_status == SUCCESS`, đủ toàn bộ
mã đã yêu cầu (`--source`). Mặc định KHÔNG xoá gì, kể cả khi thành công —
instance được giữ lại để debug/chạy lại.

`run_phase2_once()` là hàm lõi tách riêng để `gui.py` gọi trực tiếp trong
background thread (không qua subprocess); `main()` chỉ là wrapper CLI mỏng
gọi lại đúng hàm này. `stop_event`/`log_callback` là tuỳ chọn: CLI không
truyền, GUI truyền để nhận log realtime và có đường dừng sớm tối thiểu (xem
docstring của `run_phase2_once`).
"""

from __future__ import annotations

import argparse
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from core.config import ConfigError, load_settings
from core.logging_utils import add_callback_handler, add_file_handler, get_logger, remove_handler, setup_logging
from device.controller import DeviceController, DeviceControllerError
from flows.phase2 import CHROME_PACKAGE, LINE_PACKAGE, Phase2Flow
from flows.xlsx_store import XlsxStore, XlsxStoreError
from ldplayer.adapter import LDPlayerAdapter, LDPlayerConfig, LDPlayerError
from xiaowei.client import XiaoweiClient
from xiaowei.transport import WebSocketTransport

_LOG = get_logger("phase2_test")

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Chạy Phase 2 (gift code AirWallet/Ministop) cho một email, ghi kết quả vào Excel."
    )
    parser.add_argument("--config", default="config.json", help="Đường dẫn file config JSON.")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--index", type=int, help="Index instance LDPlayer.")
    target.add_argument("--name", type=str, help="Tên instance LDPlayer.")
    parser.add_argument("--email", required=True, help="Email (dòng trong Excel) cần chạy Phase 2.")
    parser.add_argument(
        "--source",
        choices=("both", "airwallet", "ministop"),
        default="both",
        help="Chạy nguồn nào (mặc định cả hai).",
    )
    parser.add_argument(
        "--remove-instance",
        action="store_true",
        help=(
            "Cho phép xoá instance LDPlayer SAU KHI ghi Excel thành công và đủ mã yêu cầu. "
            "Mặc định KHÔNG xoá."
        ),
    )
    parser.add_argument("--log-dir", default="logs", help="Thư mục chứa log riêng cho mỗi lần chạy.")
    return parser.parse_args(argv)


def _stopped(stop_event: threading.Event | None) -> bool:
    return stop_event is not None and stop_event.is_set()


def _bind_stop_event(stop_event: threading.Event | None, controller: DeviceController, bound) -> None:
    """Gắn `controller`/`bound` vào `stop_event` sau khi bind thành công.

    Cho phép nơi gọi (nút "Dừng" của GUI) force-stop Chrome/LINE ngay khi
    người dùng yêu cầu dừng, thay vì phải đợi flow tự thoát theo timeout nội
    bộ của nó. `stop_event` chỉ cần hỗ trợ gán thuộc tính tuỳ ý (đúng với
    `threading.Event` bình thường) — không yêu cầu một subclass riêng.
    """
    if stop_event is None:
        return
    try:
        stop_event.controller = controller  # type: ignore[attr-defined]
        stop_event.bound = bound  # type: ignore[attr-defined]
    except AttributeError:
        pass


def run_phase2_once(
    config_path: str = "config.json",
    *,
    index: int | None = None,
    name: str | None = None,
    email: str = "",
    source: str = "both",
    remove_instance: bool = False,
    log_dir: str = "logs",
    stop_event: threading.Event | None = None,
    log_callback: Callable[[str], None] | None = None,
) -> int:
    """Chạy Phase 2 một lần cho một email — hàm lõi dùng chung cho CLI và GUI.

    Trả về exit code y hệt hành vi cũ của `main()`: 0 nếu ghi Excel thành
    công và `status == SUCCESS`, khác 0 nếu chưa đủ điều kiện/lỗi/đã dừng.

    `stop_event` (tuỳ chọn): được kiểm tra ở các mốc trước khi đụng tới thiết
    bị (trước khi khởi động instance, trước khi bind, ngay sau khi bind).
    KHÔNG làm gián đoạn `Phase2Flow.run()` đang chạy giữa chừng (đó là một
    vòng lặp polling nội bộ, không nhận stop_event) — đây là hỗ trợ dừng "tối
    thiểu" theo đúng yêu cầu: nơi gọi có thể tự force-stop Chrome/LINE ngay
    lập tức thông qua `stop_event.controller`/`stop_event.bound` (được gán ở
    đây ngay sau khi bind xong) mà không cần đợi hàm này quay lại. Instance
    LDPlayer không bao giờ bị xoá khi `stop_event` đã set, bất kể
    `remove_instance`.

    `log_callback` (tuỳ chọn): nhận mỗi dòng log đã format trong lúc hàm này
    chạy — cách hiện thực là gắn tạm một `CallbackHandler` vào logger gốc.
    """
    setup_logging()
    callback_handler = add_callback_handler(log_callback)
    file_handler = None
    try:
        safe_email = (email or "unknown").replace("@", "_at_").replace("/", "_")
        log_path = Path(log_dir) / f"phase2_{safe_email}_{datetime.now():%Y%m%d_%H%M%S}.log"
        file_handler = add_file_handler(log_path)
        _LOG.info("Log file riêng cho lần chạy này: %s", log_path)

        return _run_phase2(
            config_path,
            index=index,
            name=name,
            email=email,
            source=source,
            remove_instance=remove_instance,
            stop_event=stop_event,
        )
    finally:
        #: Gỡ cả hai handler khi xong — nếu không, một tiến trình GUI sống
        #: lâu gọi hàm này nhiều lần sẽ rò rỉ handler (mỗi lần chạy log lại
        #: bị ghi tiếp vào TOÀN BỘ file log của các lần chạy trước).
        remove_handler(file_handler)
        remove_handler(callback_handler)


def _run_phase2(
    config_path: str,
    *,
    index: int | None,
    name: str | None,
    email: str,
    source: str,
    remove_instance: bool,
    stop_event: threading.Event | None,
) -> int:
    try:
        settings = load_settings(config_path)
    except ConfigError as exc:
        _LOG.error("Lỗi cấu hình: %s", exc)
        return 1

    if not settings.xlsx_path:
        _LOG.error("Config thiếu `xlsx_path` — cần để ghi kết quả Phase 2.")
        return 1

    try:
        xlsx_store = XlsxStore(settings.xlsx_path, settings.active_sheet)
    except XlsxStoreError as exc:
        _LOG.error("Lỗi Excel: %s", exc)
        return 1

    #: Preflight — kiểm tra email có tồn tại trong Excel TRƯỚC KHI đụng tới
    #: LDPlayer/thiết bị thật, để không phí một lượt chạy thật (có thể claim
    #: gift code thật, chỉ dùng được một lần) rồi mới phát hiện email sai.
    try:
        if not xlsx_store.find_email(email):
            _LOG.error(
                "[%s] Không tìm thấy email trong Excel (`%s`, sheet `%s`) — dừng trước khi đụng thiết bị.",
                email, settings.xlsx_path, settings.active_sheet,
            )
            return 1
    except XlsxStoreError as exc:
        _LOG.error("Lỗi Excel khi preflight email: %s", exc)
        return 1

    #: Đọc mã cũ (nếu có) để quyết định source nào cần chạy lại, và để merge
    #: với mã mới sau khi flow chạy xong — không cần đụng thiết bị cho bước này.
    try:
        old_gift_code, old_gift_code_ministop = xlsx_store.read_existing_codes(email)
    except XlsxStoreError as exc:
        _LOG.error("Lỗi Excel khi đọc mã cũ: %s", exc)
        return 1

    if source == "both":
        run_airwallet = not bool(old_gift_code)
        run_ministop = not bool(old_gift_code_ministop)
        if not run_airwallet:
            _LOG.info(
                "[%s] Đã có gift_code (AirWallet) trong Excel: %s -> bỏ qua source airwallet.",
                email, old_gift_code,
            )
        if not run_ministop:
            _LOG.info(
                "[%s] Đã có gift_code_ministop trong Excel: %s -> bỏ qua source ministop.",
                email, old_gift_code_ministop,
            )
    elif source == "airwallet":
        run_airwallet, run_ministop = True, False
    else:
        run_airwallet, run_ministop = False, True
    _LOG.info(
        "[%s] old_gift_code=%s old_gift_code_ministop=%s -> sẽ chạy airwallet=%s ministop=%s (source=%s)",
        email, old_gift_code or "-", old_gift_code_ministop or "-",
        run_airwallet, run_ministop, source,
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

    _LOG.info(
        "[%s] Bắt đầu Phase 2: ldplayer_index=%s instance=%s adb_serial=%s xiaowei_serial=%s "
        "source=%s (airwallet=%s ministop=%s)",
        email, bound.ldplayer_index, bound.instance_name, bound.adb_serial, bound.xiaowei_serial,
        source, run_airwallet, run_ministop,
    )

    flow = Phase2Flow(controller, bound)
    result = flow.run(email, airwallet=run_airwallet, ministop=run_ministop)

    #: Merge mã mới (lần chạy này) với mã cũ (đã có sẵn trong Excel) — rerun
    #: không được phép làm mất mã đã lấy được ở lần chạy trước.
    new_gift_code = result.gift_code
    new_gift_code_ministop = result.gift_code_ministop
    final_gift_code = new_gift_code or old_gift_code
    final_gift_code_ministop = new_gift_code_ministop or old_gift_code_ministop
    _LOG.info(
        "[%s] Merge mã: old=(%s,%s) new=(%s,%s) final=(%s,%s)",
        email,
        old_gift_code or "-", old_gift_code_ministop or "-",
        new_gift_code or "-", new_gift_code_ministop or "-",
        final_gift_code or "-", final_gift_code_ministop or "-",
    )

    #: Trạng thái cuối cùng tính từ mã ĐÃ MERGE + source người dùng yêu cầu —
    #: KHÔNG dùng thẳng result.status vì Phase2Flow.run() chỉ biết về mã MỚI
    #: lấy được trong lần gọi này, không biết mã cũ đã có sẵn trong Excel.
    if source == "both":
        if final_gift_code and final_gift_code_ministop:
            status, message = "SUCCESS", "Đã có đủ gift code (mới + có sẵn từ trước)."
        elif final_gift_code or final_gift_code_ministop:
            status, message = "PARTIAL", result.message or "Chỉ có một phần gift code."
        else:
            status, message = "FAILED", result.message or "Không có gift code nào."
    elif source == "airwallet":
        if final_gift_code:
            status, message = "SUCCESS", "Đã có gift code AirWallet (mới hoặc có sẵn từ trước)."
        else:
            status, message = "FAILED", result.message or "Không lấy được gift code AirWallet."
    else:
        if final_gift_code_ministop:
            status, message = "SUCCESS", "Đã có gift code Ministop (mới hoặc có sẵn từ trước)."
        else:
            status, message = "FAILED", result.message or "Không lấy được gift code Ministop."

    try:
        xlsx_store.update_phase2(
            email,
            status=status,
            message=message,
            gift_code=final_gift_code,
            gift_code_ministop=final_gift_code_ministop,
        )
        excel_ok = True
    except XlsxStoreError as exc:
        excel_ok = False
        _LOG.error("[%s] Ghi Excel thất bại: %s", email, exc)

    #: Dọn dẹp CHỈ ở mức app: force-stop Chrome + LINE để không để lại webview
    #: treo. Đây chỉ là stop app, KHÔNG xoá dữ liệu/app data của Chrome hay
    #: LINE, và không đụng gì tới instance LDPlayer.
    _LOG.info("[%s] Dọn dẹp: force-stop Chrome + LINE (chỉ stop app, KHÔNG xoá app data).", email)
    controller.stop_app(bound, CHROME_PACKAGE)
    controller.stop_app(bound, LINE_PACKAGE)
    _LOG.info("[%s] Đã force-stop Chrome/LINE.", email)

    removed = False
    if _stopped(stop_event):
        _LOG.info("[%s] Dừng theo yêu cầu — bỏ qua xoá instance dù --remove-instance có bật.", email)
    elif remove_instance:
        if excel_ok and status == "SUCCESS":
            _LOG.info(
                "[%s] Đủ điều kiện an toàn (excel_ok, status=SUCCESS, đủ mã theo source=%s) — xoá instance %s.",
                email, source, resolved_index,
            )
            try:
                ldplayer.remove_instance(resolved_index)
                removed = True
            except LDPlayerError as exc:
                _LOG.error("[%s] Xoá instance %s thất bại: %s", email, resolved_index, exc)
        else:
            _LOG.info(
                "[%s] --remove-instance được truyền nhưng CHƯA đủ điều kiện an toàn "
                "(excel_ok=%s status=%s) — giữ lại instance để debug/chạy lại.",
                email, excel_ok, status,
            )

    ok = status == "SUCCESS" and excel_ok
    _LOG.info(
        "PHASE2 TEST %s — email=%s status=%s gift_code=%s gift_code_ministop=%s excel_ok=%s instance_removed=%s",
        "OK" if ok else "INCOMPLETE",
        email, status, final_gift_code or "-", final_gift_code_ministop or "-",
        excel_ok, removed,
    )
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return run_phase2_once(
        args.config,
        index=args.index,
        name=args.name,
        email=args.email,
        source=args.source,
        remove_instance=args.remove_instance,
        log_dir=args.log_dir,
    )


if __name__ == "__main__":
    sys.exit(main())
