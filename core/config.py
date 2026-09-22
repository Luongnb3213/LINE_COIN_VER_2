"""Cấu hình LDPlayer + Xiaowei cho LINE_COIN_VER_2, đọc từ một file JSON.

Hỗ trợ hai format:

* Format mới của LINE_COIN_VER_2: có object `ldplayer` và `xiaowei`.
* Format cũ của LineViet: flat keys như `ld_path`, `ld_console`,
  `adb_path`, `emulator_boot_timeout`. Nhờ vậy GUI/logic LineViet có thể
  giữ nguyên config, còn tầng thao tác UI chuyển sang Xiaowei.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Config không đọc được hoặc thiếu giá trị bắt buộc."""


@dataclass(frozen=True)
class LDPlayerConfig:
    ld_console: str
    adb_path: str
    boot_timeout: int = 180


@dataclass(frozen=True)
class XiaoweiConfig:
    ws_url: str = "ws://127.0.0.1:22222/"
    connect_timeout: float = 10.0
    request_timeout: float = 20.0
    max_retries: int = 2
    retry_backoff: float = 0.5
    #: Trống = không truyền savePath, để Xiaowei tự chọn nơi lưu.
    screenshot_dir: str = ""


@dataclass(frozen=True)
class AppSettings:
    ldplayer: LDPlayerConfig
    xiaowei: XiaoweiConfig
    #: Excel dùng cho Phase 2 (ghi gift code). Rỗng nếu config không khai báo.
    xlsx_path: str = ""
    active_sheet: str = "Mails"


def _build(cls: type, payload: dict[str, Any]) -> Any:
    known = {f.name for f in fields(cls)}
    unknown = set(payload) - known
    if unknown:
        raise ConfigError(f"{cls.__name__} có khoá không hợp lệ: {sorted(unknown)}")
    return cls(**payload)


def load_settings(path: str | Path = "config.json") -> AppSettings:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(
            f"Không tìm thấy {config_path}. Copy config.example.json thành "
            f"{config_path.name} rồi điền đường dẫn LDPlayer."
        )
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Không đọc được {config_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"{config_path} phải là JSON object.")

    ldplayer_payload, xiaowei_payload = _normalize_payload(payload)
    if not isinstance(ldplayer_payload, dict) or not isinstance(xiaowei_payload, dict):
        raise ConfigError("`ldplayer` và `xiaowei` trong config phải là object.")

    ldplayer = _build(LDPlayerConfig, ldplayer_payload)
    xiaowei = _build(XiaoweiConfig, xiaowei_payload)

    if not ldplayer.ld_console or not ldplayer.adb_path:
        raise ConfigError("Thiếu `ldplayer.ld_console` hoặc `ldplayer.adb_path` trong config.")
    if not xiaowei.ws_url.startswith(("ws://", "wss://")):
        raise ConfigError(
            f"xiaowei.ws_url phải bắt đầu bằng ws:// hoặc wss:// (đang là {xiaowei.ws_url!r})."
        )

    return AppSettings(
        ldplayer=ldplayer,
        xiaowei=xiaowei,
        xlsx_path=str(payload.get("xlsx_path") or ""),
        active_sheet=str(payload.get("active_sheet") or "Mails"),
    )


def _normalize_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if "ldplayer" in payload or "xiaowei" in payload:
        return payload.get("ldplayer", {}), payload.get("xiaowei", {})

    ld_path = str(payload.get("ld_path") or "").strip().rstrip("\\/")
    ld_console = str(payload.get("ld_console") or "").strip()
    adb_path = str(payload.get("adb_path") or "").strip()
    if ld_path:
        ld_console = ld_console or f"{ld_path}/ldconsole.exe"
        adb_path = adb_path or f"{ld_path}/adb.exe"

    ldplayer_payload = {
        "ld_console": ld_console,
        "adb_path": adb_path,
        "boot_timeout": int(payload.get("emulator_boot_timeout") or 180),
    }
    xiaowei_payload = {
        "ws_url": str(payload.get("xiaowei_ws_url") or "ws://127.0.0.1:22222/"),
        "connect_timeout": float(payload.get("xiaowei_connect_timeout") or 10.0),
        "request_timeout": float(payload.get("xiaowei_request_timeout") or 20.0),
        "max_retries": int(payload.get("xiaowei_max_retries") or 2),
        "retry_backoff": float(payload.get("xiaowei_retry_backoff") or 0.5),
        "screenshot_dir": str(payload.get("xiaowei_screenshot_dir") or ""),
    }
    return ldplayer_payload, xiaowei_payload


__all__ = ["AppSettings", "ConfigError", "LDPlayerConfig", "XiaoweiConfig", "load_settings"]
