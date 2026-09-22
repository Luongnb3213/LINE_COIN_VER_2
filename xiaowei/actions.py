"""Danh sách hành động Xiaowei được hỗ trợ, khai báo dưới dạng data.

Mọi action tool này có thể gửi phải khai báo ở đây, chép lại từ bảng tham số
của tài liệu Xiaowei local API. Client từ chối bất kỳ action nào không có
trong registry — biến "không tự bịa endpoint" từ quy ước thành bất biến mà
test có thể assert.

Chuyển thể từ LINE_COIN/core/actions.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class UnsupportedActionError(LookupError):
    """Action ngoài phạm vi tài liệu hoặc ngoài phạm vi hỗ trợ."""


class ActionParameterError(ValueError):
    """Thiếu tham số bắt buộc hoặc tham số sai định dạng."""


@dataclass(frozen=True)
class ActionSpec:
    """Một action đã tài liệu hoá.

    ``mutating`` quyết định chính sách retry: gửi lại một tap/keystroke sau khi
    timeout có thể chạm hai lần lên máy thật, nên chỉ action read-only mới
    được tự động retry.
    """

    name: str
    article: str
    description: str
    requires_devices: bool = True
    required_data: tuple[str, ...] = ()
    optional_data: tuple[str, ...] = ()
    mutating: bool = True
    #: ``True`` khi ``data`` trả về dạng map ``{serial: output}`` đáng đọc.
    returns_output: bool = False
    #: Giá trị hợp lệ của field ``type``, khi tài liệu liệt kê rõ.
    allowed_types: tuple[str, ...] = field(default=())

    @property
    def allowed_data(self) -> tuple[str, ...]:
        return self.required_data + self.optional_data


def _spec(*args: Any, **kwargs: Any) -> ActionSpec:
    return ActionSpec(*args, **kwargs)


ACTIONS: dict[str, ActionSpec] = {
    spec.name: spec
    for spec in (
        _spec(
            "list",
            article="25",
            description="Danh sách thiết bị đang kết nối.",
            requires_devices=False,
            mutating=False,
            returns_output=True,
        ),
        _spec(
            "apkList",
            article="35",
            description="Danh sách app bên thứ ba đã cài.",
            mutating=False,
            returns_output=True,
        ),
        _spec(
            "imeList",
            article="41",
            description="Danh sách bàn phím (IME) đang cài.",
            mutating=False,
            returns_output=True,
        ),
        _spec(
            "adb",
            article="28",
            description="Chạy command ADB đầy đủ; trả output theo serial.",
            required_data=("command",),
            # Mặc định là mutating: command quyết định. Đọc read-only đi qua
            # XiaoweiClient.adb_read(), nơi tự khai báo read_only=True.
            mutating=True,
            returns_output=True,
        ),
        _spec(
            "adb_shell",
            article="252",
            description="Chạy phần sau `adb shell`; docs ghi rõ data trả về null.",
            required_data=("command",),
            returns_output=False,
        ),
        _spec(
            "startApk",
            article="39",
            description="Mở app theo package name.",
            required_data=("apk",),
        ),
        _spec(
            "stopApk",
            article="40",
            description="Force-stop app theo package name.",
            required_data=("apk",),
        ),
        _spec(
            "screen",
            article="29",
            description="Chụp màn hình; lưu trên PC và trên /sdcard/ của phone.",
            optional_data=("savePath",),
        ),
        _spec(
            "pushEvent",
            article="31",
            description="Phím tắt: 1 recent, 2 home, 3 back.",
            required_data=("type",),
            allowed_types=("1", "2", "3"),
        ),
        _spec(
            "pointerEvent",
            article="30",
            description="Touch/move/scroll/swipe; x,y là phần trăm 0-100.",
            required_data=("type",),
            optional_data=("x", "y"),
            allowed_types=("0", "1", "2", "4", "5", "6", "7", "8", "9"),
        ),
        _spec(
            "inputText",
            article="44",
            description="Nhập text; cần focus ô nhập và IME Xiaowei đang được chọn.",
            required_data=("content",),
        ),
        _spec(
            "selectIme",
            article="43",
            description="Chọn IME theo giá trị trả về từ imeList.",
            required_data=("ime",),
        ),
        _spec(
            "writeClipBoard",
            article="32",
            description="Ghi text vào clipboard của phone.",
            required_data=("content",),
        ),
    )
}


#: Có trong tài liệu nhưng cố tình không hỗ trợ, kèm lý do.
EXCLUDED_ACTIONS: dict[str, str] = {
    "installApk": "Thay đổi phần mềm trên máy; ngoài phạm vi bản đầu.",
    "uninstallApk": "Hành động phá huỷ; ngoài phạm vi bản đầu.",
    "uploadFile": "Truyền file; ngoài phạm vi bản đầu.",
    "pullFile": "Truyền file; ngoài phạm vi bản đầu.",
    "updateDevices": "Đổi tên/thứ tự thiết bị; không cần cho automation.",
    "installInputIme": "Xiaowei thường tự cài khi kết nối; không cần chủ động gọi.",
    "getTags": "Quản lý nhóm thiết bị; không phục vụ automation một máy.",
    "addTag": "Quản lý nhóm thiết bị; không phục vụ automation một máy.",
    "updateTag": "Quản lý nhóm thiết bị; không phục vụ automation một máy.",
    "removeTag": "Quản lý nhóm thiết bị; không phục vụ automation một máy.",
    "addTagDevice": "Quản lý nhóm thiết bị; không phục vụ automation một máy.",
    "removeTagDevice": "Quản lý nhóm thiết bị; không phục vụ automation một máy.",
    "actionTasks": "Automation hàng loạt; ngoài phạm vi công cụ một máy.",
    "actionCreate": "Automation hàng loạt; ngoài phạm vi công cụ một máy.",
    "actionRemove": "Automation hàng loạt; ngoài phạm vi công cụ một máy.",
    "autojsTasks": "Automation hàng loạt; ngoài phạm vi công cụ một máy.",
    "autojsCreate": "Automation hàng loạt; ngoài phạm vi công cụ một máy.",
    "autojsRemove": "Automation hàng loạt; ngoài phạm vi công cụ một máy.",
}


def get_spec(action: str) -> ActionSpec:
    """Tra một action, từ chối bất kỳ action nào không tài liệu hoá hoặc ngoài phạm vi."""
    name = str(action or "").strip()
    if not name:
        raise UnsupportedActionError("Thiếu tên action.")
    spec = ACTIONS.get(name)
    if spec is not None:
        return spec
    if name in EXCLUDED_ACTIONS:
        raise UnsupportedActionError(
            f"Action `{name}` có trong docs nhưng cố tình không hỗ trợ ở bản này: "
            f"{EXCLUDED_ACTIONS[name]}"
        )
    raise UnsupportedActionError(
        f"Action `{name}` không có trong docs Xiaowei local. "
        "Tool không tự suy đoán endpoint."
    )


def build_request(
    action: str,
    *,
    devices: str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate tham số theo spec đã tài liệu hoá rồi dựng payload."""
    spec = get_spec(action)
    payload: dict[str, Any] = {"action": spec.name}

    if spec.requires_devices:
        target = str(devices or "").strip()
        if not target:
            raise ActionParameterError(
                f"Action `{spec.name}` bắt buộc có `devices` (serial hoặc IP:port)."
            )
        payload["devices"] = target
    elif devices:
        raise ActionParameterError(
            f"Action `{spec.name}` không nhận `devices` theo docs (article {spec.article})."
        )

    values = {key: value for key, value in (data or {}).items() if value is not None}

    missing = [key for key in spec.required_data if not str(values.get(key, "")).strip()]
    if missing:
        raise ActionParameterError(
            f"Action `{spec.name}` thiếu tham số bắt buộc: {', '.join(missing)}."
        )

    unknown = sorted(set(values) - set(spec.allowed_data))
    if unknown:
        raise ActionParameterError(
            f"Action `{spec.name}` không có tham số {unknown} trong docs "
            f"(article {spec.article}). Cho phép: {list(spec.allowed_data)}."
        )

    if spec.allowed_types and "type" in values:
        type_value = str(values["type"])
        if type_value not in spec.allowed_types:
            raise ActionParameterError(
                f"Action `{spec.name}` chỉ nhận type {list(spec.allowed_types)}, "
                f"nhận được {type_value!r}."
            )

    if values:
        # Docs: "trừ khi từng API ghi khác, tham số request là kiểu string".
        payload["data"] = {key: str(value) for key, value in values.items()}
    return payload


__all__ = [
    "ACTIONS",
    "EXCLUDED_ACTIONS",
    "ActionParameterError",
    "ActionSpec",
    "UnsupportedActionError",
    "build_request",
    "get_spec",
]
