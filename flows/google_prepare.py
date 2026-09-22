"""Chuẩn bị instance Google cho LINE_COIN_VER_2.

Mỗi instance chuẩn bị là một bản clone từ `Instance mẫu`, được đăng nhập đúng
một Google account lấy từ Excel. LDPlayer lo vòng đời clone/start/stop; mọi
thao tác UI bên trong máy đi qua `DeviceController`/Xiaowei.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from core.logging_utils import get_logger
from device.adb_controller import AdbBoundDevice, AdbDeviceController
from device.controller import BoundDevice, DeviceController
from device.ui_tree import Selector, UiNode, UiTree
from ldplayer.adapter import LDPlayerAdapter

from flows.phase1_line import google_accounts_from_dumpsys, screen_has_manual_google_verification

_LOG = get_logger("google_prepare")

_ADD_ACCOUNT_INTENT = "am start -a android.settings.ADD_ACCOUNT_SETTINGS --esa account_types com.google"
_GOOGLE_EDIT_TEXT = Selector(class_contains="EditText", editable=True)
_GOOGLE_ACCOUNT_TYPE = Selector(text="Google")
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

POLL_SECONDS = 2.0
EMAIL_FIELD_TIMEOUT_SECONDS = 60.0
PASSWORD_FIELD_TIMEOUT_SECONDS = 120.0
LOGIN_DONE_TIMEOUT_SECONDS = 900.0


def _find_google_password_field(tree: UiTree) -> UiNode | None:
    node = tree.find(_GOOGLE_EDIT_TEXT)
    if node is None:
        return None
    text = " | ".join(tree.texts)
    if "Enter your password" in text or "Show password" in text or "Welcome" in text:
        return node
    return None


@dataclass(frozen=True)
class GoogleCredential:
    email: str
    password: str


@dataclass(frozen=True)
class PreparedGoogleInstance:
    name: str
    index: int
    email: str


def next_prepare_name(ldplayer: LDPlayerAdapter, prefix: str) -> str:
    return next_prepare_names(ldplayer, prefix, 1)[0]


def next_prepare_names(ldplayer: LDPlayerAdapter, prefix: str, count: int) -> list[str]:
    used: set[int] = set()
    for instance in ldplayer.list_instances():
        name = instance.name
        if name.startswith(prefix) and name[len(prefix):].isdigit():
            used.add(int(name[len(prefix):]))
    names: list[str] = []
    number = 1
    while len(names) < count:
        while number in used:
            number += 1
        names.append(f"{prefix}{number:02d}")
        used.add(number)
        number += 1
    return names


def prepare_google_instance(
    *,
    ldplayer: LDPlayerAdapter,
    controller: DeviceController,
    template_name: str,
    credential: GoogleCredential,
    prefix: str = "LineViet-g",
    new_name: str | None = None,
    stop_event=None,
    log: Callable[[str], None] | None = None,
) -> PreparedGoogleInstance:
    """Clone instance mẫu rồi đăng nhập một Google account."""
    if _stopped(stop_event):
        raise RuntimeError("STOPPED")
    template_index = ldplayer.index_of(template_name)
    new_name = new_name or next_prepare_name(ldplayer, prefix)
    _emit(log, f"Nhân bản {template_name} -> {new_name} cho {credential.email}...")
    index = ldplayer.clone_instance(template_index, new_name)
    try:
        _emit(log, f"Bật {new_name} (index {index})...")
        ldplayer.start_instance(index)
        active_controller, bound = _bind_google_prepare_device(
            ldplayer=ldplayer,
            controller=controller,
            index=index,
            stop_event=stop_event,
            log=log,
        )
        _open_add_google_account(active_controller, bound, credential.email)
        _login_google(active_controller, bound, credential, stop_event=stop_event, log=log)
        _emit(log, f"Google template sẵn sàng: {new_name} ({credential.email})")
        return PreparedGoogleInstance(name=new_name, index=index, email=credential.email)
    except Exception:
        _LOG.exception("Chuẩn bị Google thất bại cho %s trên %s", credential.email, new_name)
        raise
    finally:
        try:
            ldplayer.stop_instance(index)
            _emit(log, f"Đã tắt {new_name} (index {index}).")
        except Exception as exc:  # noqa: BLE001
            _emit(log, f"Không tắt được {new_name}: {exc}")


def _bind_google_prepare_device(
    *,
    ldplayer: LDPlayerAdapter,
    controller: DeviceController,
    index: int,
    stop_event,
    log: Callable[[str], None] | None,
):
    """Bind instance for Google prep.

    Prefer Xiaowei when it can see the LDPlayer emulator. If it cannot, fall
    back to direct ADB for this preparation-only flow so the GUI does not get
    stuck just because Xiaowei is currently listing a disconnected box device.
    """
    xiaowei_timeout = min(float(ldplayer.boot_timeout), 20.0)
    try:
        bound = controller.wait_for_boot_by_index(
            index,
            timeout=xiaowei_timeout,
            stop_event=stop_event,
            log=log,
        )
        return controller, bound
    except Exception as exc:  # noqa: BLE001 - fallback is intentionally broad here
        if _stopped(stop_event):
            raise
        _emit(
            log,
            "Xiaowei chưa map được instance chuẩn bị Google "
            f"({exc}); chuyển sang ADB trực tiếp cho riêng bước này.",
        )
    adb_controller = AdbDeviceController(ldplayer)
    bound = adb_controller.wait_for_boot_by_index(
        index,
        timeout=ldplayer.boot_timeout,
        stop_event=stop_event,
        log=log,
    )
    _emit(log, f"ADB fallback đã bind {bound.instance_name} ({bound.adb_serial}) cho chuẩn bị Google.")
    return adb_controller, bound


def _open_add_google_account(controller, bound: BoundDevice | AdbBoundDevice, email: str) -> None:
    controller.shell_exec(bound, _ADD_ACCOUNT_INTENT)
    time.sleep(4.0)
    tree = controller.ui_tree(bound)
    google = tree.find(_GOOGLE_ACCOUNT_TYPE)
    if google is not None:
        _tap_logged(controller, bound, email, "google-account-type", google)
        time.sleep(4.0)


def _login_google(
    controller,
    bound: BoundDevice | AdbBoundDevice,
    credential: GoogleCredential,
    *,
    stop_event=None,
    log: Callable[[str], None] | None = None,
) -> None:
    email = credential.email
    if _device_has_google_account(controller, bound, email):
        _emit(log, f"{bound.instance_name} đã có sẵn Google account {email}.")
        return

    email_field = _wait_edit_text(controller, bound, email, EMAIL_FIELD_TIMEOUT_SECONDS, stop_event, log)
    if email_field is None:
        raise RuntimeError(f"GOOGLE_EMAIL_FIELD_TIMEOUT: không thấy ô nhập email cho {email}.")
    _emit(log, f"Tự nhập Google email {email}.")
    controller.set_text(bound, email_field, email)
    time.sleep(1.0)
    if not _tap_first_button(controller, bound, email, "google-email-next"):
        raise RuntimeError(f"GOOGLE_EMAIL_NEXT_NOT_FOUND: không thấy nút NEXT sau email {email}.")

    password_field = _wait_password_field(controller, bound, email, stop_event, log)
    if password_field is None:
        raise RuntimeError(f"GOOGLE_PASSWORD_FIELD_TIMEOUT: không thấy ô password cho {email}.")
    _emit(log, f"Tự nhập Google password cho {email}.")
    controller.set_text(bound, password_field, credential.password)
    time.sleep(1.0)
    if not _tap_first_button(controller, bound, email, "google-password-next"):
        raise RuntimeError(f"GOOGLE_PASSWORD_NEXT_NOT_FOUND: không thấy nút NEXT sau password {email}.")

    if not _wait_login_done(controller, bound, email, stop_event, log):
        raise RuntimeError(f"GOOGLE_LOGIN_TIMEOUT: Android chưa nhận Google account {email}.")


def _wait_edit_text(
    controller,
    bound: BoundDevice | AdbBoundDevice,
    email: str,
    timeout: float,
    stop_event,
    log: Callable[[str], None] | None,
) -> UiNode | None:
    deadline = time.monotonic() + timeout
    warned_manual = False
    while time.monotonic() < deadline:
        if _stopped(stop_event):
            raise RuntimeError("STOPPED")
        tree = controller.ui_tree(bound)
        node = tree.find(_GOOGLE_EDIT_TEXT)
        if node is not None:
            return node
        if screen_has_manual_google_verification(tree) and not warned_manual:
            _emit(log, f"Google yêu cầu xác minh tay cho {email}: {tree.summary()}")
            warned_manual = True
        time.sleep(POLL_SECONDS)
    return None


def _wait_password_field(
    controller,
    bound: BoundDevice | AdbBoundDevice,
    email: str,
    stop_event,
    log: Callable[[str], None] | None,
) -> UiNode | None:
    deadline = time.monotonic() + PASSWORD_FIELD_TIMEOUT_SECONDS
    warned_manual = False
    while time.monotonic() < deadline:
        if _stopped(stop_event):
            raise RuntimeError("STOPPED")
        tree = controller.ui_tree(bound)
        node = _find_google_password_field(tree)
        if node is not None:
            return node
        if screen_has_manual_google_verification(tree):
            if not warned_manual:
                _emit(log, f"Google yêu cầu xác minh tay cho {email}: {tree.summary()}")
                warned_manual = True
            time.sleep(POLL_SECONDS)
            continue
        time.sleep(POLL_SECONDS)
    return None


def _wait_login_done(
    controller,
    bound: BoundDevice | AdbBoundDevice,
    email: str,
    stop_event,
    log: Callable[[str], None] | None,
) -> bool:
    deadline = time.monotonic() + LOGIN_DONE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _stopped(stop_event):
            raise RuntimeError("STOPPED")
        if _device_has_google_account(controller, bound, email):
            return True

        tree = controller.ui_tree(bound)
        if screen_has_manual_google_verification(tree):
            _emit(log, f"Đang chờ bạn xác minh Google tay cho {email}: {tree.summary()}")
            time.sleep(5.0)
            continue
        for selector in _GOOGLE_POST_LOGIN_BUTTONS:
            node = tree.find(selector)
            if node is not None:
                _tap_logged(controller, bound, email, "google-post-login", node)
                time.sleep(4.0)
                break
        else:
            time.sleep(POLL_SECONDS)
    return False


def _tap_first_button(controller, bound: BoundDevice | AdbBoundDevice, email: str, kind: str) -> bool:
    tree = controller.ui_tree(bound)
    for selector in _GOOGLE_NEXT_BUTTONS:
        node = tree.find(selector)
        if node is not None:
            _tap_logged(controller, bound, email, kind, node)
            time.sleep(3.0)
            return True
    return False


def _tap_logged(controller, bound: BoundDevice | AdbBoundDevice, email: str, kind: str, node: UiNode) -> None:
    x, y = node.bounds.center
    _LOG.info("[%s] tap(%s) %s center=(%s,%s)", email, kind, node.describe(), x, y)
    controller.tap_node(bound, node)


def _device_has_google_account(controller, bound: BoundDevice | AdbBoundDevice, email: str) -> bool:
    output = controller.shell_read(bound, 'dumpsys account | grep "Account {"')
    target = email.strip().casefold()
    return any(item.casefold() == target for item in google_accounts_from_dumpsys(output))


def _stopped(stop_event) -> bool:
    return stop_event is not None and stop_event.is_set()


def _emit(callback: Callable[[str], None] | None, message: str) -> None:
    _LOG.info(message)
    if callback is not None:
        callback(message)


__all__ = [
    "GoogleCredential",
    "PreparedGoogleInstance",
    "next_prepare_name",
    "next_prepare_names",
    "prepare_google_instance",
]
