"""Phase 1: đăng ký/đăng nhập LINE bằng tài khoản Google đã có sẵn trên máy ảo.

Chuyển thể từ ba file tham khảo (chỉ đọc, không sửa):
  * `WIN/LineViet/src/flows/line/step1_launch.py` — mở app, dẹp ANR, tới màn
    "Verify your account".
  * `WIN/LineViet/src/flows/line/step2_google.py` — bấm "Continue with
    Google", chọn tài khoản có sẵn, vượt các màn xác nhận SSO.
  * `WIN/LineViet/src/flows/line/step4_profile.py` — điền tên hiển thị, đặt
    mật khẩu, dẹp popup sau đăng ký, xác nhận vào tới màn chính.
Selector là THẬT, dò trên LINE 26.5.0 / LDPlayer 14 / Android 14 / en_US.

Khác biệt bắt buộc so với bản gốc:
  * Mọi thao tác thiết bị đi qua `device.controller.DeviceController` — không
    bao giờ dùng uiautomator2/Xiaowei trực tiếp từ module này.
  * Bản gốc phân biệt nhiều màn hình dùng CẢ activity lẫn UI tree; project
    này CHỈ có `DeviceController.foreground_activity()` (thêm mới, đọc từ
    `dumpsys`) — không có API tương đương `device.app_current()["activity"]`
    đầy đủ hơn của uiautomator2, nên các nhánh phụ thuộc UI tree (chứ không
    riêng activity) được ưu tiên trước.
  * Không có nhánh SIM/OTP — chỉ nhánh Google (project không có yêu cầu SIM).
  * Nếu máy chưa có tài khoản Google đúng email, flow có thể bấm "Add another
    account" và tự nhập `email_password` từ Excel. Nếu Google hỏi 2FA/captcha,
    flow chỉ log rõ và chờ người vận hành xử lý tay.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
import re

from core.logging_utils import get_logger
from device.controller import BoundDevice, DeviceController
from device.ui_tree import Selector, UiNode, UiTree

_LOG = get_logger("phase1_line")

# -- packages / activities -----------------------------------------------

LINE_PACKAGE = "jp.naver.line.android"
_LD_OVERLAY_PACKAGE = "com.android.ld.appstore"
_GMS_PACKAGE = "com.google.android.gms"

_SPLASH_ACTIVITY = f"{LINE_PACKAGE}/.activity.SplashActivity"
_REGISTRATION_ACTIVITY = "com.linecorp.line.registration.ui.RegistrationActivity"
_HOME_ACTIVITY = "main.MainActivity"
_BACK_OUT_ACTIVITIES = ("NotificationPermissionGuide", "PermissionGuide")

# -- màn chào / Verify your account ---------------------------------------

_SIGN_UP = Selector(text="Sign up")
_COUNTRY_CODE = Selector(resource_id=f"{LINE_PACKAGE}:id/country_code")

# -- ANR --------------------------------------------------------------------

_ANR_TITLE = Selector(resource_id="android:id/alertTitle")
_ANR_CLOSE = Selector(resource_id="android:id/aerr_close")
_ANR_WAIT = Selector(resource_id="android:id/aerr_wait")

# -- Google SSO ---------------------------------------------------------

_GOOGLE_LOGIN = Selector(resource_id=f"{LINE_PACKAGE}:id/google_login")
_PHONE_GROUP = Selector(resource_id=f"{LINE_PACKAGE}:id/phone_number")
_ACCOUNT_CONSENT = Selector(resource_id=f"{_GMS_PACKAGE}:id/continue_button")
_CONTINUE_BUTTON = Selector(text="Continue")
_CREATE_ACCOUNT_TITLE = Selector(text="Create a new account")
#: Dấu hiệu lỡ vào màn "Add another account" của Google thay vì chọn được
#: tài khoản có sẵn — khớp CHÍNH XÁC (không dùng contains) để không nhầm với
#: "Create a new account" (màn hồ sơ LINE, chuỗi khác nhau).
_ADD_ACCOUNT_MARKERS = (
    Selector(text="Email or phone"),
    Selector(text="Forgot email?"),
    Selector(text="Create account"),
)
_ADD_ANOTHER_ACCOUNT = Selector(text="Add another account")
_GOOGLE_EDIT_TEXT = Selector(class_contains="EditText", editable=True)
_GOOGLE_NEXT_BUTTONS = (
    Selector(text="NEXT"),
    Selector(text="Next"),
    Selector(text="Continue"),
)
_GOOGLE_POST_LOGIN_BUTTONS = (
    Selector(text="I agree"),
    Selector(text="ACCEPT"),
    Selector(text="Accept"),
    Selector(text="More"),
    Selector(text="MORE"),
    Selector(text="YES I’M IN"),
    Selector(text="YES I'M IN"),
    Selector(text="Yes, I'm in"),
    Selector(text="Skip"),
    Selector(text="Not now"),
)
_GOOGLE_MANUAL_MARKERS = (
    "Verify it’s you",
    "Verify it's you",
    "2-Step Verification",
    "Enter the code",
    "captcha",
    "Couldn’t sign you in",
    "Couldn't sign you in",
    "Check your",
    "TRY ANOTHER WAY",
)

_GOOGLE_LOGIN_RETRY_MAX = 3

# -- tên hiển thị / mật khẩu ------------------------------------------------

_NAME_GROUP = Selector(resource_id=f"{LINE_PACKAGE}:id/name")
_NAME_FIELD = Selector(resource_id=f"{LINE_PACKAGE}:id/edit_text")
_NEXT = Selector(resource_id=f"{LINE_PACKAGE}:id/next")
_PASSWORD_TITLE = Selector(text="Create password")
_PASSWORD_FIELD = Selector(resource_id=f"{LINE_PACKAGE}:id/edit_text", content_desc="Password")
_PASSWORD_REENTER = Selector(resource_id=f"{LINE_PACKAGE}:id/edit_text", content_desc="Reenter password")
_SYNC_PROGRESS = Selector(resource_id=f"{LINE_PACKAGE}:id/progress_bar")
_SYNC_TITLE = Selector(resource_id=f"{LINE_PACKAGE}:id/common_dialog_title_text", text="Syncing account data...")

# -- popup sau đăng ký -------------------------------------------------------

#: Thứ tự ưu tiên — bấm nút TỪ CHỐI đầu tiên tìm thấy, không bao giờ bấm
#: "Add to contacts" hay tương đương chấp nhận.
_DECLINE_BUTTONS = (
    Selector(resource_id=f"{LINE_PACKAGE}:id/common_dialog_cancel_btn"),
    Selector(resource_id=f"{LINE_PACKAGE}:id/secondary_button"),
    Selector(resource_id="com.android.permissioncontroller:id/permission_deny_button"),
    Selector(text="Cancel"),
    Selector(text="Close"),
    Selector(text="Not now"),
    Selector(text="Later"),
    Selector(text="Skip"),
    Selector(text="Don't allow"),
    Selector(text="No thanks"),
)
_HOME_NAME = Selector(resource_id=f"{LINE_PACKAGE}:id/home_tab_name")

# -- timeout / retry ----------------------------------------------------

POLL_INTERVAL_SECONDS = 2.0
LAUNCH_TIMEOUT_SECONDS = 180.0
SIGNUP_CLICK_TIMEOUT_SECONDS = 30.0
VERIFY_SCREEN_TIMEOUT_SECONDS = 60.0
GOOGLE_BUTTON_TIMEOUT_SECONDS = 30.0
GOOGLE_ACCOUNT_TIMEOUT_SECONDS = 30.0
GOOGLE_EMAIL_FIELD_TIMEOUT_SECONDS = 45.0
GOOGLE_PASSWORD_FIELD_TIMEOUT_SECONDS = 90.0
GOOGLE_POST_LOGIN_TIMEOUT_SECONDS = 180.0
SSO_CONSENT_TIMEOUT_SECONDS = 150.0
PROFILE_SCREEN_TIMEOUT_SECONDS = 60.0
NAME_FIELD_TIMEOUT_SECONDS = 20.0
PASSWORD_SCREEN_TIMEOUT_SECONDS = 60.0
SYNC_TIMEOUT_SECONDS = 180.0
SYNC_STABLE_SECONDS = 8.0
POST_SIGNUP_MAX_STEPS = 20


class Phase1Screen(Enum):
    WELCOME = "welcome"
    VERIFY_ACCOUNT = "verify_account"
    GOOGLE_ADD_ACCOUNT = "google_add_account"
    CREATE_ACCOUNT_NAME = "create_account_name"
    CREATE_PASSWORD = "create_password"
    SYNCING = "syncing"
    HOME = "home"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Phase1Result:
    status: str
    message: str

    @property
    def success(self) -> bool:
        return self.status == "SUCCESS"


class Phase1Error(RuntimeError):
    """Lỗi trong luồng Phase 1 — dừng bước hiện tại, không đoán tiếp."""


_STOPPED_MESSAGE = "STOPPED"


# -- helper thuần logic (không đụng thiết bị) -----------------------------


def classify_screen(tree: UiTree, activity: str = "") -> Phase1Screen:
    """Nhận diện màn hình CHỈ bằng nội dung UI tree (+ activity nếu có).

    Dùng cho log/chẩn đoán và cho test không cần thiết bị — luồng chính vẫn
    tự chờ selector cụ thể ở từng bước thay vì gọi hàm này, để giữ đúng tinh
    thần "không đoán, không nhận diện nhầm" của bản gốc (vd. `edit_text` dùng
    lại ở 2 màn khác nhau, phải phân biệt bằng tiêu đề/resource-id container).
    """
    if tree.empty:
        return Phase1Screen.UNKNOWN
    if tree.find(_HOME_NAME) is not None and _HOME_ACTIVITY in activity:
        return Phase1Screen.HOME
    if tree.find(_SYNC_PROGRESS) is not None or tree.find(_SYNC_TITLE) is not None:
        return Phase1Screen.SYNCING
    if is_google_add_account_screen(tree):
        return Phase1Screen.GOOGLE_ADD_ACCOUNT
    if tree.find(_PASSWORD_TITLE) is not None or tree.find(_PASSWORD_FIELD) is not None:
        return Phase1Screen.CREATE_PASSWORD
    if tree.find(_CREATE_ACCOUNT_TITLE) is not None or tree.find(_NAME_GROUP) is not None:
        return Phase1Screen.CREATE_ACCOUNT_NAME
    if tree.find(_COUNTRY_CODE) is not None:
        return Phase1Screen.VERIFY_ACCOUNT
    if tree.find(_SIGN_UP) is not None:
        return Phase1Screen.WELCOME
    return Phase1Screen.UNKNOWN


def is_google_add_account_screen(tree: UiTree) -> bool:
    """True nếu bộ chọn tài khoản Google đang ở màn "Add another account".

    Đây là hàng rào chặn KHÔNG BAO GIỜ được bấm nhầm — caller phải bấm Back
    và coi như chọn tài khoản thất bại khi hàm này trả True.
    """
    return any(tree.find(selector) is not None for selector in _ADD_ACCOUNT_MARKERS)


def find_google_account_node(tree: UiTree, email: str) -> UiNode | None:
    """Tìm node khớp CHÍNH XÁC với `email` trong bộ chọn tài khoản Google."""
    target = (email or "").strip()
    if not target:
        return None
    return tree.find(Selector(text=target))


def visible_account_emails(tree: UiTree) -> list[str]:
    """Toàn bộ text trông giống email đang hiển thị — dùng để log khi không
    tìm thấy tài khoản mong muốn, giúp debug ngoài hiện trường."""
    return sorted({node.text for node in tree.nodes if "@" in node.text})


def google_accounts_from_dumpsys(text: str) -> list[str]:
    """Parse danh sách Google account từ output `dumpsys account`."""
    return [email.strip() for email in re.findall(r"name=([^,}]+), *type=com\.google", text or "") if email.strip()]


def screen_has_manual_google_verification(tree: UiTree) -> bool:
    summary = " | ".join(tree.texts)
    return any(marker in summary for marker in _GOOGLE_MANUAL_MARKERS)


def _stopped(stop_event) -> bool:
    return stop_event is not None and stop_event.is_set()


# -- helper thao tác thiết bị (qua DeviceController) -----------------------


def _tap_point(matched: UiNode, target: UiNode) -> tuple[int, int]:
    mx, my = matched.bounds.center
    if matched is target or not target.bounds.visible:
        return mx, my
    return target.bounds.clamp_point(mx, my)


def _tap_logged(controller: DeviceController, bound: BoundDevice, email: str, kind: str, node: UiNode) -> None:
    target = node.clickable_self_or_ancestor()
    if target is None:
        raise Phase1Error(f"Node `{node.describe()}` ({kind}) không bấm được và không có tổ tiên bấm được.")
    x, y = _tap_point(node, target)
    _LOG.info(
        "[%s] tap(%s) text=%r bounds=%s target_bounds=%s center=(%s,%s)",
        email, kind, node.text, node.bounds, target.bounds, x, y,
    )
    controller.tap_node(bound, node)
    _LOG.info("[%s] tapped(%s) center=(%s,%s)", email, kind, x, y)


def _wait_for_selector(
    controller: DeviceController,
    bound: BoundDevice,
    selector: Selector,
    timeout: float,
    *,
    stop_event=None,
) -> UiNode | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _stopped(stop_event):
            return None
        node = controller.ui_tree(bound).find(selector)
        if node is not None:
            return node
        time.sleep(POLL_INTERVAL_SECONDS)
    return None


def _wait_for_any(
    controller: DeviceController,
    bound: BoundDevice,
    selectors: tuple[Selector, ...],
    timeout: float,
    *,
    stop_event=None,
) -> tuple[Selector | None, UiNode | None]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _stopped(stop_event):
            return None, None
        tree = controller.ui_tree(bound)
        for selector in selectors:
            node = tree.find(selector)
            if node is not None:
                return selector, node
        time.sleep(POLL_INTERVAL_SECONDS)
    return None, None


def _dismiss_ld_overlay(controller: DeviceController, bound: BoundDevice, email: str) -> None:
    """Dọn overlay quảng cáo của launcher LDPlayer — best-effort, không chặn
    flow chính nếu lệnh lỗi (máy có thể không có app này)."""
    try:
        controller.shell_exec(bound, f"appops set {_LD_OVERLAY_PACKAGE} SYSTEM_ALERT_WINDOW deny")
        controller.shell_exec(bound, f"am force-stop {_LD_OVERLAY_PACKAGE}")
    except Exception as exc:  # noqa: BLE001 - dọn overlay là phụ, không được làm hỏng flow chính
        _LOG.debug("[%s] Không dọn được overlay LDPlayer (bỏ qua): %s", email, exc)


def _dismiss_anr(controller: DeviceController, bound: BoundDevice, email: str, rounds: int = 4) -> int:
    closed = 0
    for _ in range(rounds):
        tree = controller.ui_tree(bound)
        close_node = tree.find(_ANR_CLOSE)
        wait_node = tree.find(_ANR_WAIT)
        if close_node is None and wait_node is None:
            break
        title_node = tree.find(_ANR_TITLE)
        title = title_node.text if title_node is not None else ""
        prefer_wait = "line" in title.lower()
        target = (wait_node if prefer_wait else close_node) or wait_node or close_node
        label = "Wait" if target is wait_node else "Close app"
        if target is None:
            break
        _LOG.warning("[%s] ANR %r -> bấm %s", email, title or "(không rõ)", label)
        _tap_logged(controller, bound, email, "anr-dismiss", target)
        closed += 1
        time.sleep(5.0)
    return closed


class Phase1LineFlow:
    """Chạy Phase 1 cho một thiết bị đã bind — chỉ gọi qua `DeviceController`."""

    def __init__(self, controller: DeviceController, bound: BoundDevice) -> None:
        self._controller = controller
        self._bound = bound

    # -- API công khai -------------------------------------------------------

    def run(
        self,
        email: str,
        display_name: str,
        password: str,
        *,
        email_password: str = "",
        stop_event=None,
    ) -> Phase1Result:
        _LOG.info(
            "[%s] Bắt đầu Phase 1: ldplayer_index=%s instance=%s adb_serial=%s xiaowei_serial=%s",
            email,
            self._bound.ldplayer_index,
            self._bound.instance_name,
            self._bound.adb_serial,
            self._bound.xiaowei_serial,
        )
        try:
            needs_signup = self._launch_and_reach_verify_screen(email, stop_event)
            if needs_signup:
                self._run_google_sso(email, email_password, stop_event)
                self._fill_profile(email, display_name, stop_event)
                self._set_password(email, password, stop_event)
                self._wait_sync(email, stop_event)
                self._advance_until_home(email, stop_event)
        except Phase1Error as exc:
            if str(exc) == _STOPPED_MESSAGE:
                _LOG.info("[%s] Phase 1 dừng theo yêu cầu người dùng.", email)
                return Phase1Result(status="STOPPED", message="Đã dừng theo yêu cầu người dùng.")
            _LOG.error("[%s] Phase 1 THẤT BẠI: %s", email, exc)
            return Phase1Result(status="FAILED", message=str(exc))

        _LOG.info("[%s] Phase 1 THÀNH CÔNG — đã ở màn chính LINE.", email)
        return Phase1Result(status="SUCCESS", message="Đã đăng ký/đăng nhập LINE, vào được màn chính.")

    # -- bước 1: mở app, tới màn Verify your account -------------------------

    def _launch_and_reach_verify_screen(self, email: str, stop_event) -> bool:
        """True nếu cần chạy tiếp đăng ký (đang ở màn Verify your account).

        False nếu app mở thẳng vào Home — tài khoản này ĐÃ đăng nhập sẵn trên
        máy, không cần đăng ký lại, coi là thành công ngay (nhánh "login" của
        "register/login" trong yêu cầu).
        """
        controller, bound = self._controller, self._bound
        _LOG.info("[%s] 1. Mở LINE...", email)
        _dismiss_ld_overlay(controller, bound, email)
        controller.shell_exec(bound, f"am start -n {_SPLASH_ACTIVITY}")

        deadline = time.monotonic() + LAUNCH_TIMEOUT_SECONDS
        reached_registration = False
        while time.monotonic() < deadline:
            if _stopped(stop_event):
                raise Phase1Error(_STOPPED_MESSAGE)
            activity = controller.foreground_activity(bound)
            if _REGISTRATION_ACTIVITY in activity:
                reached_registration = True
                break
            if _HOME_ACTIVITY in activity:
                tree = controller.ui_tree(bound)
                if tree.find(_HOME_NAME) is not None:
                    _LOG.info("[%s] LINE đã đăng nhập sẵn trên máy này — bỏ qua đăng ký.", email)
                    return False
            time.sleep(POLL_INTERVAL_SECONDS)

        if not reached_registration:
            activity = controller.foreground_activity(bound)
            raise Phase1Error(
                f"LINE_START_FAILED: không thấy {_REGISTRATION_ACTIVITY} sau "
                f"{LAUNCH_TIMEOUT_SECONDS:g}s (activity hiện tại={activity or '-'})"
            )

        _LOG.info("[%s] App đã lên màn đăng ký.", email)
        time.sleep(3.0)
        _dismiss_anr(controller, bound, email)

        tree = controller.ui_tree(bound)
        if tree.find(_COUNTRY_CODE) is not None:
            _LOG.info("[%s] Đã ở màn 'Verify your account'.", email)
            return True

        sign_up = _wait_for_selector(controller, bound, _SIGN_UP, SIGNUP_CLICK_TIMEOUT_SECONDS, stop_event=stop_event)
        if sign_up is None:
            if _stopped(stop_event):
                raise Phase1Error(_STOPPED_MESSAGE)
            raise Phase1Error("LINE_NO_SIGNUP: không thấy nút 'Sign up' trên màn chào")
        _tap_logged(controller, bound, email, "sign-up", sign_up)

        if _wait_for_selector(controller, bound, _COUNTRY_CODE, VERIFY_SCREEN_TIMEOUT_SECONDS, stop_event=stop_event) is None:
            if _stopped(stop_event):
                raise Phase1Error(_STOPPED_MESSAGE)
            raise Phase1Error("LINE_NO_VERIFY_SCREEN: không tới được màn 'Verify your account'")
        _LOG.info("[%s] Đang ở màn 'Verify your account'.", email)
        return True

    # -- bước 2: Google SSO ---------------------------------------------------

    def _run_google_sso(self, email: str, email_password: str, stop_event) -> None:
        controller, bound = self._controller, self._bound
        _LOG.info("[%s] 2. Đăng nhập Google...", email)

        button = _wait_for_selector(controller, bound, _GOOGLE_LOGIN, GOOGLE_BUTTON_TIMEOUT_SECONDS, stop_event=stop_event)
        if button is None:
            if _stopped(stop_event):
                raise Phase1Error(_STOPPED_MESSAGE)
            raise Phase1Error("LINE_NO_GOOGLE_BUTTON: không thấy nút 'Continue with Google'")
        _tap_logged(controller, bound, email, "continue-with-google", button)
        _LOG.info("[%s] Đã bấm 'Continue with Google', chờ bộ chọn tài khoản...", email)

        if not self._select_or_login_google_account(email, email_password, stop_event):
            if _stopped(stop_event):
                raise Phase1Error(_STOPPED_MESSAGE)
            raise Phase1Error(
                f"GOOGLE_ACCOUNT_NOT_READY: không tìm/chọn/login được tài khoản Google `{email}` "
                "trên máy ảo."
            )

        self._pass_sso_consents(email, stop_event)

        selector, _node = _wait_for_any(controller, bound, (_PHONE_GROUP, _CREATE_ACCOUNT_TITLE), 20.0, stop_event=stop_event)
        if selector is _PHONE_GROUP:
            raise Phase1Error("LINE_NEEDS_PHONE: LINE yêu cầu số điện thoại sau đăng nhập Google — không hỗ trợ nhánh SIM/OTP.")
        if selector is _CREATE_ACCOUNT_TITLE:
            _LOG.info("[%s] Đã qua SSO, tới màn 'Create a new account'.", email)
            return
        if _stopped(stop_event):
            raise Phase1Error(_STOPPED_MESSAGE)
        tree = controller.ui_tree(bound)
        raise Phase1Error(
            f"GOOGLE_SSO_INCOMPLETE: bấm xong nhưng không tới được màn tạo hồ sơ lẫn màn nhập số. "
            f"screen={tree.summary()}"
        )

    def _select_or_login_google_account(self, email: str, email_password: str, stop_event) -> bool:
        controller, bound = self._controller, self._bound
        target = (email or "").strip()
        if not target:
            return False

        if self._device_has_google_account(target):
            _LOG.info("[%s] Android đã có Google account `%s`.", email, target)

        node = None
        add_account_node = None
        deadline = time.monotonic() + GOOGLE_ACCOUNT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if _stopped(stop_event):
                return False
            tree = controller.ui_tree(bound)
            node = find_google_account_node(tree, target)
            if node is not None:
                break
            add_account_node = tree.find(_ADD_ANOTHER_ACCOUNT)
            if add_account_node is not None:
                break
            time.sleep(POLL_INTERVAL_SECONDS)

        if node is None:
            tree = controller.ui_tree(bound)
            add_account_node = add_account_node or tree.find(_ADD_ANOTHER_ACCOUNT)
            if add_account_node is not None:
                if not email_password:
                    _LOG.error(
                        "[%s] Máy chưa có Google `%s` và Excel thiếu `email_password`, không thể tự login.",
                        email, target,
                    )
                    return False
                _LOG.info("[%s] Không thấy account trong chooser — bấm 'Add another account' để tự login Google.", email)
                _tap_logged(controller, bound, email, "google-add-another-account", add_account_node)
                return self._auto_login_google(email, email_password, stop_event)

            emails = visible_account_emails(tree)
            _LOG.error(
                "[%s] Không thấy tài khoản Google `%s` trong bộ chọn. Các email nhìn thấy trên màn: %s",
                email, target, ", ".join(emails) or "(không có)",
            )
            return False

        _tap_logged(controller, bound, email, "select-google-account", node)

        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if _stopped(stop_event):
                return False
            tree = controller.ui_tree(bound)
            if find_google_account_node(tree, target) is None:
                if is_google_add_account_screen(tree):
                    _LOG.warning("[%s] Lỡ vào màn 'Add another account' — bấm Back, không dùng.", email)
                    controller.press_back(bound)
                    return False
                return True
            time.sleep(1.0)
        _LOG.warning("[%s] Bộ chọn tài khoản Google không biến mất sau khi bấm `%s`.", email, target)
        return False

    def _auto_login_google(self, email: str, email_password: str, stop_event) -> bool:
        controller, bound = self._controller, self._bound
        _LOG.info("[%s] Tự nhập Google email/password từ Excel.", email)

        email_field = self._wait_google_edit_text(email, GOOGLE_EMAIL_FIELD_TIMEOUT_SECONDS, stop_event)
        if email_field is None:
            _LOG.warning("[%s] Không thấy ô email Google; bạn xử lý tay nếu Google đang hỏi xác minh.", email)
            return False
        controller.set_text(bound, email_field, email)
        time.sleep(1.0)
        if not self._tap_first_google_button(email, "google-email-next"):
            _LOG.warning("[%s] Không bấm được NEXT sau email Google.", email)
            return False

        password_field = self._wait_google_password_screen(email, stop_event)
        if password_field is None:
            return False
        controller.set_text(bound, password_field, email_password)
        time.sleep(1.0)
        if not self._tap_first_google_button(email, "google-password-next"):
            _LOG.warning("[%s] Không bấm được NEXT sau password Google.", email)
            return False

        return self._wait_google_login_done(email, stop_event)

    def _wait_google_edit_text(self, email: str, timeout: float, stop_event) -> UiNode | None:
        controller, bound = self._controller, self._bound
        deadline = time.monotonic() + timeout
        warned_manual = False
        while time.monotonic() < deadline:
            if _stopped(stop_event):
                return None
            tree = controller.ui_tree(bound)
            node = tree.find(_GOOGLE_EDIT_TEXT)
            if node is not None:
                return node
            if screen_has_manual_google_verification(tree):
                if not warned_manual:
                    _LOG.warning(
                        "[%s] Google yêu cầu xác minh tay. Hãy xử lý trên máy ảo, tool vẫn chờ: %s",
                        email, tree.summary(),
                    )
                    warned_manual = True
            time.sleep(POLL_INTERVAL_SECONDS)
        return None

    def _wait_google_password_screen(self, email: str, stop_event) -> UiNode | None:
        controller, bound = self._controller, self._bound
        deadline = time.monotonic() + GOOGLE_PASSWORD_FIELD_TIMEOUT_SECONDS
        warned_manual = False
        while time.monotonic() < deadline:
            if _stopped(stop_event):
                return None
            tree = controller.ui_tree(bound)
            if screen_has_manual_google_verification(tree):
                if not warned_manual:
                    _LOG.warning(
                        "[%s] Google yêu cầu xác minh tay. Hãy xử lý trên máy ảo, tool vẫn chờ: %s",
                        email, tree.summary(),
                    )
                    warned_manual = True
                time.sleep(POLL_INTERVAL_SECONDS)
                continue
            node = tree.find(_GOOGLE_EDIT_TEXT)
            text = " | ".join(tree.texts)
            if node is not None and (
                "Enter your password" in text or "Show password" in text or "Welcome" in text
            ):
                return node
            time.sleep(POLL_INTERVAL_SECONDS)
        _LOG.warning("[%s] Không thấy màn password Google sau khi nhập email.", email)
        return None

    def _tap_first_google_button(self, email: str, kind: str) -> bool:
        tree = self._controller.ui_tree(self._bound)
        for selector in _GOOGLE_NEXT_BUTTONS:
            node = tree.find(selector)
            if node is not None:
                _tap_logged(self._controller, self._bound, email, kind, node)
                time.sleep(3.0)
                return True
        return False

    def _wait_google_login_done(self, email: str, stop_event) -> bool:
        controller, bound = self._controller, self._bound
        deadline = time.monotonic() + GOOGLE_POST_LOGIN_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if _stopped(stop_event):
                return False
            if self._device_has_google_account(email):
                _LOG.info("[%s] Android đã nhận Google account `%s`.", email, email)
                return True

            tree = controller.ui_tree(bound)
            if tree.find(_CREATE_ACCOUNT_TITLE) is not None or tree.find(_PHONE_GROUP) is not None:
                _LOG.info("[%s] Google login đã chuyển tiếp về LINE.", email)
                return True
            if screen_has_manual_google_verification(tree):
                _LOG.warning(
                    "[%s] Google yêu cầu xác minh tay. Tool sẽ chờ bạn xử lý trên máy ảo: %s",
                    email, tree.summary(),
                )
                time.sleep(5.0)
                continue
            for selector in _GOOGLE_POST_LOGIN_BUTTONS:
                node = tree.find(selector)
                if node is not None:
                    _LOG.info("[%s] Google post-login: bấm %s.", email, selector.text)
                    _tap_logged(controller, bound, email, "google-post-login", node)
                    time.sleep(4.0)
                    break
            else:
                time.sleep(POLL_INTERVAL_SECONDS)
        _LOG.warning("[%s] Chưa xác nhận được Google account sau khi nhập password.", email)
        return False

    def _device_has_google_account(self, email: str) -> bool:
        try:
            output = self._controller.shell_read(self._bound, 'dumpsys account | grep "Account {"')
        except Exception as exc:  # noqa: BLE001 - chỉ dùng để đối chiếu, lỗi thì fallback UI
            _LOG.debug("[%s] Không đọc được dumpsys account: %s", email, exc)
            return False
        target = email.strip().casefold()
        return any(item.casefold() == target for item in google_accounts_from_dumpsys(output))

    def _pass_sso_consents(self, email: str, stop_event) -> None:
        controller, bound = self._controller, self._bound
        deadline = time.monotonic() + SSO_CONSENT_TIMEOUT_SECONDS
        retry_count = 0

        while time.monotonic() < deadline:
            if _stopped(stop_event):
                raise Phase1Error(_STOPPED_MESSAGE)
            tree = controller.ui_tree(bound)
            if tree.find(_CREATE_ACCOUNT_TITLE) is not None or tree.find(_PHONE_GROUP) is not None:
                return

            node = tree.find(_ACCOUNT_CONSENT) or tree.find(_CONTINUE_BUTTON)
            if node is not None:
                _tap_logged(controller, bound, email, "sso-consent", node)
                time.sleep(5.0)
                continue

            if retry_count < _GOOGLE_LOGIN_RETRY_MAX:
                retry_button = tree.find(_GOOGLE_LOGIN)
                if retry_button is not None:
                    retry_count += 1
                    _LOG.warning(
                        "[%s] Vẫn ở màn 'Verify your account'; bấm lại 'Continue with Google' "
                        "(lần %s/%s, có kiểm soát).",
                        email, retry_count, _GOOGLE_LOGIN_RETRY_MAX,
                    )
                    _tap_logged(controller, bound, email, f"retry-google-login-{retry_count}", retry_button)
                    time.sleep(8.0)
                    continue

            time.sleep(POLL_INTERVAL_SECONDS)

    # -- bước 4: tên hiển thị / mật khẩu / popup sau đăng ký ------------------

    def _fill_profile(self, email: str, display_name: str, stop_event) -> None:
        controller, bound = self._controller, self._bound
        _LOG.info("[%s] 3. Điền hồ sơ...", email)

        selector, _node = _wait_for_any(
            controller, bound, (_CREATE_ACCOUNT_TITLE, _NAME_GROUP), PROFILE_SCREEN_TIMEOUT_SECONDS, stop_event=stop_event
        )
        if selector is None:
            if _stopped(stop_event):
                raise Phase1Error(_STOPPED_MESSAGE)
            raise Phase1Error("LINE_NO_PROFILE_SCREEN: không thấy màn 'Create a new account'")

        field = _wait_for_selector(controller, bound, _NAME_FIELD, NAME_FIELD_TIMEOUT_SECONDS, stop_event=stop_event)
        if field is None:
            if _stopped(stop_event):
                raise Phase1Error(_STOPPED_MESSAGE)
            raise Phase1Error("LINE_NO_NAME_FIELD: không thấy ô nhập tên hiển thị")
        _LOG.info("[%s] Điền tên hiển thị: %s", email, display_name)
        controller.set_text(bound, field, display_name)
        time.sleep(1.0)

        next_button = controller.ui_tree(bound).find(_NEXT)
        if next_button is None:
            raise Phase1Error("LINE_NO_NEXT_BUTTON: không thấy nút Next sau khi điền tên")
        _tap_logged(controller, bound, email, "name-next", next_button)
        time.sleep(5.0)

    def _set_password(self, email: str, password: str, stop_event) -> None:
        controller, bound = self._controller, self._bound
        _LOG.info("[%s] 4. Đặt mật khẩu...", email)

        selector, _node = _wait_for_any(
            controller, bound, (_PASSWORD_TITLE, _PASSWORD_FIELD), PASSWORD_SCREEN_TIMEOUT_SECONDS, stop_event=stop_event
        )
        if selector is None:
            if _stopped(stop_event):
                raise Phase1Error(_STOPPED_MESSAGE)
            raise Phase1Error("LINE_NO_PASSWORD_SCREEN: không thấy màn 'Create password'")

        tree = controller.ui_tree(bound)
        if tree.find(_PASSWORD_FIELD) is None or tree.find(_PASSWORD_REENTER) is None:
            raise Phase1Error(
                "LINE_PASSWORD_FIELDS_MISSING: không tách được hai ô mật khẩu "
                "(cùng resourceId, phải phân biệt bằng content-desc)"
            )

        for label, field_selector in (("password", _PASSWORD_FIELD), ("reenter-password", _PASSWORD_REENTER)):
            if _stopped(stop_event):
                raise Phase1Error(_STOPPED_MESSAGE)
            node = controller.ui_tree(bound).find(field_selector)
            if node is None:
                raise Phase1Error(f"LINE_PASSWORD_FIELD_GONE: ô `{label}` biến mất trước khi kịp điền")
            _LOG.info("[%s] Điền %s (%d ký tự).", email, label, len(password))
            controller.set_text(bound, node, password)
            time.sleep(1.0)

        next_button = controller.ui_tree(bound).find(_NEXT)
        if next_button is None:
            raise Phase1Error("LINE_NO_NEXT_BUTTON: không thấy nút Next sau khi điền mật khẩu")
        _tap_logged(controller, bound, email, "password-next", next_button)
        time.sleep(5.0)

    def _wait_sync(self, email: str, stop_event) -> None:
        controller, bound = self._controller, self._bound
        _LOG.info("[%s] 5. Chờ đồng bộ tài khoản...", email)

        #: Chờ tối đa một khoảng ngắn xem có xuất hiện dấu hiệu đồng bộ
        #: không — mạng nhanh có thể đi qua bước này gần như tức thì và
        #: không bao giờ lọt vào giữa hai lần poll; nếu sau khoảng chờ này
        #: chưa từng thấy gì thì coi như đã xong/được bỏ qua, đi tiếp ngay
        #: thay vì chờ hết SYNC_TIMEOUT_SECONDS một cách vô ích.
        grace_deadline = time.monotonic() + 10.0
        seen_syncing = False
        while time.monotonic() < grace_deadline:
            if _stopped(stop_event):
                raise Phase1Error(_STOPPED_MESSAGE)
            tree = controller.ui_tree(bound)
            if tree.find(_SYNC_PROGRESS) is not None or tree.find(_SYNC_TITLE) is not None:
                seen_syncing = True
                break
            time.sleep(1.0)

        if not seen_syncing:
            _LOG.info("[%s] Không thấy dấu hiệu đồng bộ — coi như đã xong/được bỏ qua.", email)
            return

        deadline = time.monotonic() + SYNC_TIMEOUT_SECONDS
        absent_since: float | None = None
        while time.monotonic() < deadline:
            if _stopped(stop_event):
                raise Phase1Error(_STOPPED_MESSAGE)
            tree = controller.ui_tree(bound)
            syncing = tree.find(_SYNC_PROGRESS) is not None or tree.find(_SYNC_TITLE) is not None
            if syncing:
                absent_since = None
                time.sleep(4.0)
                continue
            if absent_since is None:
                absent_since = time.monotonic()
            if time.monotonic() - absent_since >= SYNC_STABLE_SECONDS:
                _LOG.info("[%s] Đồng bộ xong.", email)
                return
            time.sleep(2.0)

        raise Phase1Error(f"LINE_SYNC_TIMEOUT: còn đồng bộ sau {SYNC_TIMEOUT_SECONDS:g}s")

    def _advance_until_home(self, email: str, stop_event) -> None:
        controller, bound = self._controller, self._bound
        _LOG.info("[%s] 6. Dẹp popup sau đăng ký, chờ vào màn chính...", email)

        for _step in range(1, POST_SIGNUP_MAX_STEPS + 1):
            if _stopped(stop_event):
                raise Phase1Error(_STOPPED_MESSAGE)

            if self._at_home():
                _LOG.info("[%s] Đã ở màn chính LINE.", email)
                return

            tree = controller.ui_tree(bound)
            decline_node = None
            for selector in _DECLINE_BUTTONS:
                decline_node = tree.find(selector)
                if decline_node is not None:
                    break
            if decline_node is not None:
                _LOG.info(
                    "[%s] Đóng popup: %r", email, decline_node.text or decline_node.content_desc or "(không nhãn)"
                )
                _tap_logged(controller, bound, email, "decline-popup", decline_node)
                time.sleep(4.0)
                continue

            activity = controller.foreground_activity(bound)
            if any(marker in activity for marker in _BACK_OUT_ACTIVITIES):
                _LOG.info("[%s] Màn hướng dẫn không có nút từ chối (%s) — bấm Back.", email, activity)
                controller.press_back(bound)
                time.sleep(4.0)
                continue
            if _HOME_ACTIVITY in activity:
                time.sleep(3.0)
                continue

            raise Phase1Error(
                f"LINE_UNKNOWN_SCREEN: màn sau đăng ký không nhận ra (activity={activity or '-'}, "
                f"screen={tree.summary()}). Dừng để không bấm bừa lên tài khoản vừa tạo."
            )

        raise Phase1Error(f"LINE_TOO_MANY_SCREENS: qua {POST_SIGNUP_MAX_STEPS} màn vẫn chưa vào được màn chính.")

    def _at_home(self) -> bool:
        controller, bound = self._controller, self._bound
        if controller.foreground_package(bound) != LINE_PACKAGE:
            return False
        if _HOME_ACTIVITY not in controller.foreground_activity(bound):
            return False
        tree = controller.ui_tree(bound)
        for selector in _DECLINE_BUTTONS[:3]:
            if tree.find(selector) is not None:
                return False
        return tree.find(_HOME_NAME) is not None


__all__ = [
    "LINE_PACKAGE",
    "Phase1Error",
    "Phase1LineFlow",
    "Phase1Result",
    "Phase1Screen",
    "classify_screen",
    "find_google_account_node",
    "is_google_add_account_screen",
    "visible_account_emails",
]
