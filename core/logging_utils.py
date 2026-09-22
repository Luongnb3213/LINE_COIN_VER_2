"""Logger setup dùng chung cho LINE_COIN_VER_2."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

_ROOT_NAME = "line_coin_ver2"

_FILE_FORMAT = logging.Formatter(
    "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def get_logger(name: str = "") -> logging.Logger:
    full = f"{_ROOT_NAME}.{name}" if name else _ROOT_NAME
    return logging.getLogger(full)


def setup_logging(level: int | str = logging.INFO) -> logging.Logger:
    """Cấu hình logger gốc của tool. Gọi lại nhiều lần vẫn an toàn."""
    logger = logging.getLogger(_ROOT_NAME)
    logger.setLevel(level)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    logger.addHandler(handler)
    return logger


def add_file_handler(path: str | Path, level: int | str = logging.INFO) -> logging.Handler:
    """Thêm một `FileHandler` riêng ghi toàn bộ log ra một file cho một lần chạy.

    Dùng cho các entrypoint cần "mỗi lần chạy một file log" (vd. `phase2_test.py`),
    tách biệt với stream handler chung do `setup_logging()` cài đặt. An toàn khi
    gọi nhiều lần — mỗi lần thêm một handler mới, không gỡ handler cũ.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(_ROOT_NAME)
    handler = logging.FileHandler(target, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(_FILE_FORMAT)
    logger.addHandler(handler)
    return handler


class CallbackHandler(logging.Handler):
    """Handler chuyển mỗi log line đã format thành một lời gọi callback.

    Dùng để stream log theo thời gian thực lên GUI mà không cần sửa bất kỳ
    chỗ nào khác đang gọi `_LOG.info(...)` — chỉ cần gắn handler này vào
    logger gốc cho một lần chạy rồi gỡ ra khi xong (xem `add_callback_handler`).
    """

    def __init__(self, callback: Callable[[str], None]) -> None:
        super().__init__()
        self._callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._callback(self.format(record))
        except Exception:
            pass


def add_callback_handler(
    callback: Callable[[str], None] | None, level: int | str = logging.INFO
) -> logging.Handler | None:
    """Gắn một `CallbackHandler` vào logger gốc; trả None nếu `callback` rỗng.

    Người gọi chịu trách nhiệm gỡ handler (qua `remove_handler`) khi chạy
    xong, để không rò rỉ handler qua nhiều lần chạy trong một tiến trình GUI
    sống lâu (vd. `gui.py` gọi `run_phase2_once`/`run_smoke_test_once` nhiều lần).
    """
    if callback is None:
        return None
    handler = CallbackHandler(callback)
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)-7s  %(name)s  %(message)s", datefmt="%H:%M:%S")
    )
    logging.getLogger(_ROOT_NAME).addHandler(handler)
    return handler


def remove_handler(handler: logging.Handler | None) -> None:
    """Gỡ một handler khỏi logger gốc — an toàn (no-op) khi `handler` là None."""
    if handler is not None:
        logging.getLogger(_ROOT_NAME).removeHandler(handler)


__all__ = [
    "CallbackHandler",
    "add_callback_handler",
    "add_file_handler",
    "get_logger",
    "remove_handler",
    "setup_logging",
]
