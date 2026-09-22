"""Phase 2: nhận gift code AirWallet / Ministop qua Chrome -> deeplink LINE.

Chuyển thể từ hai bản tham khảo (chỉ đọc, không sửa):
  * `WIN/LineViet/src/flows/line/phase2_rewards.py` — logic đầy đủ nhất, có xử
    lý màn hình onboarding Chrome lần đầu; đây là template nghiệp vụ chính.
  * `LINE_COIN/flows/phase2.py` — kiến trúc bọc một `PhoneSession` Xiaowei,
    gần với kiểu điều khiển thiết bị mà project này dùng.

Khác biệt bắt buộc so với việc chép nguyên văn:
  * Mọi thao tác thiết bị đi qua `device.controller.DeviceController` — không
    bao giờ gọi `uiautomator2`/Xiaowei trực tiếp từ module này.
  * Lệnh mở Chrome dùng dấu nháy đơn quanh URL (`-d '...'`) chứ không phải
    nháy kép, vì `xiaowei.client.adb_shell` xử lý sai dấu `"` lồng nhau trong
    command (đã xác nhận qua thực nghiệm ở Phase 1).
  * `_extract_code_from_text` log rõ lý do từ chối ứng viên gift code — bản
    tham khảo âm thầm bỏ qua, project này cần log để debug được ngoài hiện
    trường.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
from enum import Enum

from core.logging_utils import get_logger
from device.controller import BoundDevice, DeviceController
from device.ui_tree import Selector, UiNode, UiTree, normalise

_LOG = get_logger("phase2")

# -- packages -----------------------------------------------------------

CHROME_PACKAGE = "com.android.chrome"
LINE_PACKAGE = "jp.naver.line.android"

# -- URL nguồn thưởng -----------------------------------------------------------

AIRWALLET_URL = "https://airwallet-utapri-202609.belugacpn.jp/top?utm_source=chatgpt.com"
MINISTOP_URL = "https://www.ministop.co.jp/campaign/260914airwallet-cp/"

# -- text nhận diện màn hình -----------------------------------------------------------

CHROME_WELCOME_TEXTS = ("Welcome to Chrome", "Chào mừng bạn đến với Chrome")
CHROME_ONBOARDING_TEXTS = CHROME_WELCOME_TEXTS + (
    "Chrome notifications make things easier",
    "Chrome notifications",
)
CHROME_SKIP_ACCOUNT_TEXTS = (
    "Use without an account",
    "Sử dụng Chrome mà không cần tài khoản",
    "No thanks",
    "Không, cảm ơn",
)

CHECKBOX_TEXT = "応募規約に同意する"
RECEIVE_BUTTON_TEXT = "ギフトコードを受け取る"
MINISTOP_LINE_BUTTON_TEXT = "LINE認証 & 友だち追加"
AUTH_ALLOW_TEXTS = ("Cho phép", "Allow", "許可", "許可する")
GIFT_CODE_LABEL = "ギフトコード"
GIFT_CODE_PAGE_MARKERS = ("ご参加ありがとうございます", "ギフトコードをご利用ください")

CAMPAIGN_MARKERS = (
    "airwallet-utapri-202609",
    "ブロッコリーオンライン",
    "企画対象期間",
    CHECKBOX_TEXT,
    RECEIVE_BUTTON_TEXT,
)
MINISTOP_CAMPAIGN_MARKERS = (
    "ミニストップ",
    "ハロハロ",
    "COIN+残高",
    MINISTOP_LINE_BUTTON_TEXT,
)
AUTH_SCREEN_MARKERS = ("Xác nhận", "AirWALLET", "エアウォレット", *AUTH_ALLOW_TEXTS)

# -- timeout/retry (theo bản LineViet — đầy đủ nhất) -----------------------------------------------------------

MAX_SCROLLS = 8
MINISTOP_BUTTON_MAX_SCROLLS = 10
GIFT_CODE_MAX_SCROLLS = 6
GIFT_CODE_SCROLL_INTERVAL_POLLS = 2
CHROME_OPEN_RETRIES = 3
CHROME_LOAD_WAIT_SECONDS = 15.0
CHROME_FIRST_RUN_TIMEOUT_SECONDS = 20.0
AUTH_SCREEN_RETRIES = 2
GIFT_CODE_RETRIES = 2
AUTH_SCREEN_TIMEOUT_SECONDS = 20.0
FIRST_DEEPLINK_TIMEOUT_SECONDS = 10.0
DEEPLINK_TIMEOUT_SECONDS = 60.0
DEEPLINK_POLL_SECONDS = 2.0
POLL_INTERVAL_SECONDS = 1.0

# -- trích gift code -----------------------------------------------------------

#: Không cố định 16 ký tự — các mã thật quan sát được dài 12-24 ký tự.
_CODE_RE = re.compile(r"(?<![A-Z0-9])([A-Z0-9]{12,24})(?![A-Z0-9])")

#: Vài phông tiếng Nhật/Cyrillic hiển thị giống hệt chữ Latin trên màn hình.
_CYRILLIC_TO_LATIN = str.maketrans(
    {
        "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M",
        "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T",
        "У": "Y", "Х": "X",
    }
)


class Phase2Screen(Enum):
    GIFT_CODE_READY = "gift_code_ready"
    GIFT_CODE_PAGE = "gift_code_page"
    AUTH_ALLOW = "auth_allow"
    AUTH_SCREEN = "auth_screen"
    CAMPAIGN_PAGE = "campaign_page"
    MINISTOP_CAMPAIGN_PAGE = "ministop_campaign_page"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Phase2Result:
    status: str
    message: str
    gift_code: str = ""
    gift_code_ministop: str = ""

    @property
    def success(self) -> bool:
        return self.status == "SUCCESS"


class Phase2Error(RuntimeError):
    """Lỗi trong luồng Phase 2 — dừng bước hiện tại, không đoán tiếp."""


# -- helper thuần logic (không đụng thiết bị) -----------------------------------------------------------


def _extract_code_from_text(value: str) -> tuple[str, str]:
    """Trả `(code, lý_do_từ_chối)`. `code` rỗng nếu không có ứng viên hợp lệ.

    `lý_do_từ_chối` rỗng khi tìm được code hợp lệ hoặc khi không có ứng viên
    nào để đánh giá (không log những trường hợp không liên quan).
    """
    if not value or not value.strip():
        return "", ""
    text = unicodedata.normalize("NFKC", value).translate(_CYRILLIC_TO_LATIN)
    cleaned = re.sub(r"[^A-Za-z0-9]", "", text).upper()
    match = _CODE_RE.search(cleaned)
    if not match:
        return "", ""
    candidate = match.group(1)
    if not any(ch.isdigit() for ch in candidate):
        return "", f"no-digit:{candidate}"
    if not any(ch.isalpha() for ch in candidate):
        return "", f"no-alpha:{candidate}"
    if candidate.startswith("2026"):
        return "", f"year-prefix:{candidate}"
    if "COIN" in candidate:
        #: Denylist riêng cho mẫu quảng cáo đã quan sát được ("540ID2000COIN")
        #: — dạng này có cả số lẫn chữ, không bắt đầu "2026" nên lọt qua 3
        #: điều kiện trên; khoanh vùng vị trí (label + cửa sổ node) là tuyến
        #: phòng thủ chính, đây chỉ là lớp chặn bổ sung cho đúng mẫu đã biết.
        return "", f"ad-text-coin:{candidate}"
    return candidate, ""


def _node_contains(node: UiNode, needle: str) -> bool:
    target = normalise(needle)
    return target in normalise(node.text) or target in normalise(node.content_desc)


def _is_gift_code_page(tree: UiTree) -> bool:
    """Chỉ coi là trang gift code khi có CẢ nhãn lẫn một trong hai câu xác nhận.

    Điều kiện kép này là thứ chặn false-positive trên các trang chỉ nhắc tới
    chữ "ギフトコード" mà chưa thực sự hiển thị mã (banner quảng cáo, mô tả
    chương trình...).
    """
    if not tree.contains_text(GIFT_CODE_LABEL):
        return False
    return any(tree.contains_text(marker) for marker in GIFT_CODE_PAGE_MARKERS)


def _extract_gift_code(tree: UiTree, *, allow_label_only: bool = False, log_context: str = "") -> str:
    """Trích gift code khi đã xác nhận đang ở trang gift code (marker), hoặc,
    nếu `allow_label_only=True`, khi chỉ có nhãn (marker cảm ơn có thể đã
    cuộn khỏi viewport nhỏ của LDPlayer) NHƯNG chưa thấy dấu hiệu đây vẫn là
    trang campaign (chưa qua auth) — caller chỉ nên bật cờ này sau khi đã
    vượt qua bước auth.

    Luôn chỉ quét trong cửa sổ 10 node ngay sau mỗi node chứa nhãn
    "ギフトコード" — không OCR/quét toàn màn hình, để không nhặt nhầm text
    quảng cáo như "QUOCGIA" hay các đoạn mô tả khuyến mãi khác.
    """
    strict_ok = _is_gift_code_page(tree)
    fallback_ok = (
        allow_label_only
        and not strict_ok
        and tree.contains_text(GIFT_CODE_LABEL)
        and not any(tree.contains_text(marker) for marker in CAMPAIGN_MARKERS)
        and not any(tree.contains_text(marker) for marker in MINISTOP_CAMPAIGN_MARKERS)
    )
    if not strict_ok and not fallback_ok:
        return ""
    nodes = tree.nodes
    label_indexes = [i for i, node in enumerate(nodes) if _node_contains(node, GIFT_CODE_LABEL)]
    for label_index in label_indexes:
        window = nodes[label_index : label_index + 10]
        for node in window:
            candidate_text = f"{node.text} {node.content_desc}".strip()
            if not candidate_text:
                continue
            code, reason = _extract_code_from_text(candidate_text)
            if code:
                _LOG.info(
                    "%sgift code candidate CHẤP NHẬN (strict=%s): %r (node=%s)",
                    log_context, strict_ok, code, node.describe(),
                )
                return code
            if reason:
                _LOG.info(
                    "%sgift code candidate TỪ CHỐI (%s): text=%r node=%s",
                    log_context, reason, candidate_text, node.describe(),
                )
    return ""


def _detect_screen(tree: UiTree) -> Phase2Screen:
    if tree.empty:
        return Phase2Screen.UNKNOWN
    if _is_gift_code_page(tree):
        return Phase2Screen.GIFT_CODE_PAGE
    if tree.contains_text(GIFT_CODE_LABEL):
        return Phase2Screen.GIFT_CODE_READY
    if any(tree.contains_text(text) for text in AUTH_ALLOW_TEXTS):
        return Phase2Screen.AUTH_ALLOW
    if any(tree.contains_text(marker) for marker in AUTH_SCREEN_MARKERS):
        return Phase2Screen.AUTH_SCREEN
    if any(tree.contains_text(marker) for marker in MINISTOP_CAMPAIGN_MARKERS):
        return Phase2Screen.MINISTOP_CAMPAIGN_PAGE
    if any(tree.contains_text(marker) for marker in CAMPAIGN_MARKERS):
        return Phase2Screen.CAMPAIGN_PAGE
    return Phase2Screen.UNKNOWN


def _find_any_text(tree: UiTree, texts: tuple[str, ...]) -> UiNode | None:
    for text in texts:
        node = tree.find(Selector(text_contains=text))
        if node is not None:
            return node
    return None


def _find_ministop_line_button(tree: UiTree) -> UiNode | None:
    node = tree.find(Selector(text_contains=MINISTOP_LINE_BUTTON_TEXT))
    if node is not None and node.clickable_self_or_ancestor() is not None:
        return node
    for candidate in tree.nodes:
        haystack = f"{normalise(candidate.text)} {normalise(candidate.content_desc)}"
        if "line認証" in haystack and "友だち追加" in haystack:
            if candidate.clickable_self_or_ancestor() is not None:
                return candidate
    return None


# -- helper thao tác thiết bị (qua DeviceController) -----------------------------------------------------------


def _tap_point(matched: UiNode, target: UiNode) -> tuple[int, int]:
    mx, my = matched.bounds.center
    if matched is target or not target.bounds.visible:
        return mx, my
    return target.bounds.clamp_point(mx, my)


def _tap_logged(controller: DeviceController, bound: BoundDevice, email: str, kind: str, node: UiNode) -> None:
    target = node.clickable_self_or_ancestor()
    if target is None:
        raise Phase2Error(f"Node `{node.describe()}` ({kind}) không bấm được và không có tổ tiên bấm được.")
    x, y = _tap_point(node, target)
    _LOG.info(
        "[%s] tap(%s) text=%r bounds=%s target_bounds=%s center=(%s,%s)",
        email, kind, node.text, node.bounds, target.bounds, x, y,
    )
    controller.tap_node(bound, node)
    _LOG.info("[%s] tapped(%s) center=(%s,%s)", email, kind, x, y)


def _send_chrome_intent(controller: DeviceController, bound: BoundDevice, url: str) -> None:
    #: Nháy đơn quanh URL — `adb_shell` của Xiaowei xử lý sai nháy kép lồng nhau.
    command = f"am start -a android.intent.action.VIEW -d '{url}' {CHROME_PACKAGE}"
    controller.shell_exec(bound, command)


def _wait_for_marker(
    controller: DeviceController, bound: BoundDevice, markers: tuple[str, ...], timeout: float
) -> UiTree | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        tree = controller.ui_tree(bound)
        if any(tree.contains_text(marker) for marker in markers):
            return tree
        time.sleep(POLL_INTERVAL_SECONDS)
    return None


def _dismiss_chrome_first_run(
    controller: DeviceController, bound: BoundDevice, email: str, source: str
) -> bool:
    """Xử lý màn hình Chrome lần đầu (welcome/skip-account/notifications).

    Trả `True` nếu vừa bấm nút bỏ qua đăng nhập — báo hiệu caller phải gửi
    lại intent mở URL, vì Chrome thường quay về New Tab sau khi bấm thay vì
    tự điều hướng tới URL đã yêu cầu trước đó.
    """
    deadline = time.monotonic() + CHROME_FIRST_RUN_TIMEOUT_SECONDS
    chrome_seen = False
    while time.monotonic() < deadline:
        tree = controller.ui_tree(bound)
        chrome_nodes = [node for node in tree.nodes if node.package == CHROME_PACKAGE]
        if chrome_nodes:
            chrome_seen = True

        for text in CHROME_SKIP_ACCOUNT_TEXTS:
            node = tree.find(Selector(text_contains=text))
            if node is not None:
                _LOG.info("[%s][%s] Chrome first-run: bấm bỏ qua đăng nhập (`%s`).", email, source, text)
                _tap_logged(controller, bound, email, "chrome-skip-account", node)
                return True

        onboarding_visible = any(tree.contains_text(text) for text in CHROME_ONBOARDING_TEXTS)
        onboarding_container = any("fre_pager" in (node.resource_id or "") for node in chrome_nodes)
        if chrome_seen and not onboarding_visible and not onboarding_container:
            return False
        time.sleep(POLL_INTERVAL_SECONDS)
    return False


def _open_url_until_page(
    controller: DeviceController,
    bound: BoundDevice,
    email: str,
    url: str,
    source: str,
    page_markers: tuple[str, ...],
) -> UiTree:
    last_open_try = CHROME_OPEN_RETRIES + 1
    for open_try in range(1, last_open_try + 1):
        _LOG.info("[%s][%s] Mở Chrome lần %s/%s: %s", email, source, open_try, last_open_try, url)
        controller.stop_app(bound, LINE_PACKAGE)
        _send_chrome_intent(controller, bound, url)
        time.sleep(CHROME_LOAD_WAIT_SECONDS)

        if _dismiss_chrome_first_run(controller, bound, email, source):
            _send_chrome_intent(controller, bound, url)
            time.sleep(CHROME_LOAD_WAIT_SECONDS)

        tree = _wait_for_marker(controller, bound, page_markers, CHROME_LOAD_WAIT_SECONDS)
        if tree is not None:
            return tree

        if open_try == CHROME_OPEN_RETRIES:
            _LOG.warning(
                "[%s][%s] Chrome không lên đúng trang sau %s lần, force-stop rồi thử lại.",
                email, source, CHROME_OPEN_RETRIES,
            )
            controller.stop_app(bound, CHROME_PACKAGE)
            time.sleep(2.0)

    raise Phase2Error(f"Không mở được trang campaign ({source}) sau {last_open_try} lần thử.")


def _find_with_scroll(
    controller: DeviceController,
    bound: BoundDevice,
    email: str,
    source: str,
    text: str,
    max_scrolls: int,
) -> UiNode | None:
    tree = controller.ui_tree(bound)
    node = tree.find(Selector(text_contains=text))
    if node is not None:
        return node
    for scroll_count in range(1, max_scrolls + 1):
        controller.scroll_down(bound, tree)
        time.sleep(POLL_INTERVAL_SECONDS)
        tree = controller.ui_tree(bound)
        node = tree.find(Selector(text_contains=text))
        _LOG.info(
            "[%s][%s] scroll #%s tìm `%s`: found=%s bounds=%s",
            email, source, scroll_count, text, node is not None,
            node.bounds if node is not None else "-",
        )
        if node is not None:
            return node
    return None


def _find_ministop_line_button_with_scroll(
    controller: DeviceController, bound: BoundDevice, email: str, source: str
) -> UiNode | None:
    tree = controller.ui_tree(bound)
    node = _find_ministop_line_button(tree)
    if node is not None:
        return node
    for scroll_count in range(1, MINISTOP_BUTTON_MAX_SCROLLS + 1):
        controller.scroll_down(bound, tree)
        time.sleep(POLL_INTERVAL_SECONDS)
        tree = controller.ui_tree(bound)
        node = _find_ministop_line_button(tree)
        _LOG.info(
            "[%s][%s] scroll #%s tìm nút LINE Ministop: found=%s bounds=%s screen=%s",
            email, source, scroll_count, node is not None,
            node.bounds if node is not None else "-", tree.summary(),
        )
        if node is not None:
            return node
    return None


class Phase2Flow:
    """Chạy Phase 2 cho một thiết bị đã bind — chỉ gọi qua `DeviceController`."""

    def __init__(self, controller: DeviceController, bound: BoundDevice) -> None:
        self._controller = controller
        self._bound = bound

    # -- API công khai -----------------------------------------------------------

    def run(self, email: str, *, airwallet: bool = True, ministop: bool = True) -> Phase2Result:
        gift_code = ""
        gift_code_ministop = ""
        failures: list[str] = []

        if airwallet:
            try:
                gift_code = self._run_source(email, "airwallet")
            except Phase2Error as exc:
                failures.append(f"airwallet: {exc}")
                _LOG.warning("[%s] AirWallet thất bại: %s", email, exc)

        if ministop:
            try:
                gift_code_ministop = self._run_source(email, "ministop")
            except Phase2Error as exc:
                failures.append(f"ministop: {exc}")
                _LOG.warning("[%s] Ministop thất bại: %s", email, exc)

        requested = int(airwallet) + int(ministop)
        obtained = int(bool(gift_code)) + int(bool(gift_code_ministop))

        if requested and obtained == requested:
            status, message = "SUCCESS", "Đã lấy đủ gift code."
        elif obtained > 0:
            status, message = "PARTIAL", "; ".join(failures) or "Chỉ lấy được một phần gift code."
        else:
            status, message = "FAILED", "; ".join(failures) or "Không lấy được gift code nào."

        _LOG.info(
            "[%s] Kết quả Phase 2: status=%s gift_code=%s gift_code_ministop=%s",
            email, status, gift_code or "-", gift_code_ministop or "-",
        )
        return Phase2Result(
            status=status, message=message, gift_code=gift_code, gift_code_ministop=gift_code_ministop
        )

    # -- theo nguồn -----------------------------------------------------------

    def _source_url(self, source: str) -> tuple[str, tuple[str, ...]]:
        if source == "airwallet":
            return AIRWALLET_URL, CAMPAIGN_MARKERS
        return MINISTOP_URL, MINISTOP_CAMPAIGN_MARKERS

    def _prepare_campaign_page(self, email: str, source: str) -> None:
        if source == "airwallet":
            self._prepare_airwallet(email, source)
        else:
            self._prepare_ministop(email, source)

    def _prepare_airwallet(self, email: str, source: str) -> None:
        controller, bound = self._controller, self._bound
        checkbox = _find_with_scroll(controller, bound, email, source, CHECKBOX_TEXT, MAX_SCROLLS)
        if checkbox is None:
            raise Phase2Error(f"Không tìm thấy checkbox `{CHECKBOX_TEXT}`.")
        _tap_logged(controller, bound, email, "agree-checkbox", checkbox)
        time.sleep(POLL_INTERVAL_SECONDS)

        button = _find_with_scroll(controller, bound, email, source, RECEIVE_BUTTON_TEXT, MAX_SCROLLS)
        if button is None:
            raise Phase2Error(f"Không tìm thấy nút `{RECEIVE_BUTTON_TEXT}`.")
        _tap_logged(controller, bound, email, "receive-button", button)

    def _prepare_ministop(self, email: str, source: str) -> None:
        controller, bound = self._controller, self._bound
        button = _find_ministop_line_button_with_scroll(controller, bound, email, source)
        if button is None:
            raise Phase2Error(f"Không tìm thấy nút `{MINISTOP_LINE_BUTTON_TEXT}`.")
        _tap_logged(controller, bound, email, "ministop-line-button", button)

    def _run_source(self, email: str, source: str) -> str:
        url, markers = self._source_url(source)

        last_error: Exception | None = None
        for attempt in range(1, AUTH_SCREEN_RETRIES + 1):
            try:
                _open_url_until_page(self._controller, self._bound, email, url, source, markers)
                self._prepare_campaign_page(email, source)
                self._wait_for_authorization(email, source)
                break
            except Phase2Error as exc:
                last_error = exc
                _LOG.warning(
                    "[%s][%s] bước xác thực lần %s/%s thất bại: %s",
                    email, source, attempt, AUTH_SCREEN_RETRIES, exc,
                )
        else:
            raise Phase2Error(f"Không qua được bước xác thực sau {AUTH_SCREEN_RETRIES} lần: {last_error}")

        return self._run_gift_stage(email, source)

    def _wait_for_authorization(self, email: str, source: str) -> None:
        controller, bound = self._controller, self._bound
        deadline = time.monotonic() + AUTH_SCREEN_TIMEOUT_SECONDS
        ministop_fallback_tapped = False

        while time.monotonic() < deadline:
            tree = controller.ui_tree(bound)
            screen = _detect_screen(tree)
            _LOG.info("[%s][%s] wait-auth screen=%s", email, source, screen.value)

            #: Chỉ return khi đã thực sự sang trang gift code — AUTH_SCREEN
            #: (màn hình xác nhận AirWALLET) không đủ điều kiện return vì nút
            #: allow trên đó có thể chưa kịp render/bấm được.
            if screen in (Phase2Screen.GIFT_CODE_PAGE, Phase2Screen.GIFT_CODE_READY):
                return

            if screen in (Phase2Screen.AUTH_ALLOW, Phase2Screen.AUTH_SCREEN):
                node = _find_any_text(tree, AUTH_ALLOW_TEXTS)
                if node is not None:
                    _LOG.info("[%s][%s] auth screen: allow found, bấm.", email, source)
                    _tap_logged(controller, bound, email, "auth-allow", node)
                    time.sleep(POLL_INTERVAL_SECONDS)
                    continue
                _LOG.info("[%s][%s] auth screen waiting: allow not found, tiếp tục chờ.", email, source)

            if source == "ministop" and screen == Phase2Screen.MINISTOP_CAMPAIGN_PAGE and not ministop_fallback_tapped:
                ministop_fallback_tapped = True
                _LOG.info("[%s][%s] Vẫn ở trang campaign Ministop, thử bấm lại nút LINE.", email, source)
                button = _find_ministop_line_button_with_scroll(controller, bound, email, source)
                if button is not None:
                    _tap_logged(controller, bound, email, "ministop-line-button-retry", button)

            time.sleep(POLL_INTERVAL_SECONDS)

        raise Phase2Error(f"Hết thời gian chờ màn hình xác thực ({AUTH_SCREEN_TIMEOUT_SECONDS:g}s).")

    def _run_gift_stage(self, email: str, source: str) -> str:
        controller, bound = self._controller, self._bound
        url, markers = self._source_url(source)
        attempts = ((1, FIRST_DEEPLINK_TIMEOUT_SECONDS), (2, DEEPLINK_TIMEOUT_SECONDS))

        last_error: Exception | None = None
        for attempt_no, timeout in attempts:
            try:
                return self._wait_for_gift_code(email, source, attempt_no, timeout)
            except Phase2Error as exc:
                last_error = exc
                _LOG.warning(
                    "[%s][%s] LINE webview trống ở lần %s (%s).", email, source, attempt_no, exc,
                )
                if attempt_no < len(attempts):
                    _LOG.info("[%s][%s] Quay lại Chrome, thử lại từ đầu.", email, source)
                    controller.stop_app(bound, LINE_PACKAGE)
                    _open_url_until_page(controller, bound, email, url, source, markers)
                    self._prepare_campaign_page(email, source)
                    self._wait_for_authorization(email, source)

        raise Phase2Error(f"Không lấy được gift code {source} sau {len(attempts)} lần: {last_error}")

    def _wait_for_gift_code(self, email: str, source: str, attempt_no: int, timeout: float) -> str:
        controller, bound = self._controller, self._bound
        _LOG.info("[%s][%s] Chờ gift code lần %s, timeout=%.0fs", email, source, attempt_no, timeout)

        deadline = time.monotonic() + timeout
        scroll_count = 0
        poll_count = 0
        log_context = f"[{email}][{source}] "

        while time.monotonic() < deadline:
            tree = controller.ui_tree(bound)
            foreground = controller.foreground_package(bound)
            _LOG.info("%spoll foreground=%s empty=%s", log_context, foreground or "-", tree.empty)

            if tree.empty:
                time.sleep(DEEPLINK_POLL_SECONDS)
                continue

            screen = _detect_screen(tree)
            _LOG.info("%sscreen=%s", log_context, screen.value)

            if screen == Phase2Screen.AUTH_ALLOW:
                node = _find_any_text(tree, AUTH_ALLOW_TEXTS)
                if node is not None:
                    _tap_logged(controller, bound, email, "line-allow", node)
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            if screen in (Phase2Screen.GIFT_CODE_PAGE, Phase2Screen.GIFT_CODE_READY):
                #: allow_label_only=True vì đây đã ở gift stage (sau auth) —
                #: marker cảm ơn có thể đã cuộn khỏi viewport nhỏ của
                #: LDPlayer dù nhãn + mã thật vẫn đang hiển thị.
                code = _extract_gift_code(tree, allow_label_only=True, log_context=log_context)
                if code:
                    _LOG.info("%sGift code: %s", log_context, code)
                    return code
                _LOG.info("%sỞ trang gift code (screen=%s) nhưng chưa trích được code hợp lệ.", log_context, screen.value)

            poll_count += 1
            if poll_count % GIFT_CODE_SCROLL_INTERVAL_POLLS == 0 and scroll_count < GIFT_CODE_MAX_SCROLLS:
                scroll_count += 1
                controller.scroll_down(bound, tree)
                _LOG.info("%sscroll gift-code #%s", log_context, scroll_count)

            time.sleep(DEEPLINK_POLL_SECONDS)

        raise Phase2Error(f"Hết thời gian chờ gift code ({timeout:g}s).")


__all__ = [
    "AIRWALLET_URL",
    "CHROME_PACKAGE",
    "LINE_PACKAGE",
    "MINISTOP_URL",
    "Phase2Error",
    "Phase2Flow",
    "Phase2Result",
    "Phase2Screen",
]
