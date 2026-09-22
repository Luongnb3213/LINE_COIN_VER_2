"""Adapter vòng đời LDPlayer: list/create/clone/start/stop/remove instance.

Package này CHỈ nói chuyện với `ldconsole.exe` (quản lý instance) và
`adb.exe` (chờ device lên, đọc boot_completed). Nó không biết gì về UI bên
trong máy ảo — việc đó thuộc `xiaowei`.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from core.logging_utils import get_logger

_LOG = get_logger("ldplayer")

_CREATE_NO_WINDOW = 0x08000000


class LDPlayerError(RuntimeError):
    """Lệnh `ldconsole`/`adb` thất bại hoặc timeout."""


@dataclass(frozen=True)
class LDPlayerConfig:
    ld_console: str
    adb_path: str
    boot_timeout: int = 180


@dataclass(frozen=True)
class LDInstance:
    """Một dòng trong `ldconsole list2`."""

    index: int
    name: str
    running: bool


def _run(
    args: list[str],
    *,
    timeout: float = 30.0,
    allow_nonzero: bool = False,
) -> str:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise LDPlayerError(f"Không tìm thấy chương trình: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise LDPlayerError(f"Lệnh timeout sau {timeout:g}s: {' '.join(args)}") from exc

    if result.returncode != 0 and not allow_nonzero and not result.stdout.strip():
        raise LDPlayerError(
            f"Lệnh thất bại (exit={result.returncode}): {' '.join(args)}\n{result.stderr.strip()}"
        )
    return result.stdout


class LDPlayerAdapter:
    """Bọc `ldconsole.exe` + `adb.exe` theo config."""

    def __init__(self, config: LDPlayerConfig) -> None:
        self._config = config

    def _ldconsole(
        self,
        *args: str,
        timeout: float = 30.0,
        allow_nonzero: bool = False,
    ) -> str:
        return _run(
            [self._config.ld_console, *args],
            timeout=timeout,
            allow_nonzero=allow_nonzero,
        )

    def _adb(self, *args: str, timeout: float = 30.0) -> str:
        return _run([self._config.adb_path, *args], timeout=timeout)

    # -- instance queries ------------------------------------------------

    def list_instances(self) -> list[LDInstance]:
        """Đọc `ldconsole list2`.

        Định dạng mỗi dòng (phân cách bởi dấu phẩy):
        index,name,top_window_handle,bind_window_handle,is_running,pid,vbox_pid
        """
        output = self._ldconsole("list2")
        instances: list[LDInstance] = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            fields = line.split(",")
            if len(fields) < 5:
                continue
            try:
                index = int(fields[0])
                running = fields[4].strip() == "1"
            except ValueError:
                continue
            instances.append(LDInstance(index=index, name=fields[1], running=running))
        return instances

    def index_of(self, name: str) -> int:
        for instance in self.list_instances():
            if instance.name == name:
                return instance.index
        raise LDPlayerError(f"Không tìm thấy instance LDPlayer tên `{name}`.")

    def is_running(self, index: int) -> bool:
        for instance in self.list_instances():
            if instance.index == index:
                return instance.running
        raise LDPlayerError(f"Không tìm thấy instance LDPlayer index {index}.")

    def adb_serial(self, index: int) -> str:
        """Serial ADB suy ra từ index theo công thức của LDPlayer.

        Đây là một công thức, không phải giá trị đọc trực tiếp từ LDPlayer —
        `smoke_test.py` phải đối chiếu nó với serial Xiaowei thực sự báo cáo
        trước khi tin tưởng dùng nó để map thiết bị.
        """
        return f"emulator-{5554 + index * 2}"

    def instance_config_path(self, index: int) -> Path:
        """File config LDPlayer của instance.

        LDPlayer chỉ đọc cờ ADB debugging lúc boot, nên mọi chỉnh sửa phải
        diễn ra trước `launch`.
        """
        ld_dir = Path(self._config.ld_console).resolve().parent
        return ld_dir / "vms" / "config" / f"leidian{index}.config"

    def ensure_adb_enabled(self, index: int) -> bool:
        """Bật ADB debugging cho instance nếu đang tắt.

        Nếu cờ này tắt, LDPlayer vẫn mở cửa sổ bình thường nhưng `adb devices`
        sẽ rỗng. Trả về True khi vừa sửa config để caller có thể reboot
        instance đang chạy.
        """
        path = self.instance_config_path(index)
        if not path.exists():
            _LOG.warning("Không thấy file config %s; bỏ qua bật ADB.", path)
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - log rồi tiếp tục fail ở bước adb nếu cần
            _LOG.warning("Không đọc được config LDPlayer %s: %s", path, exc)
            return False
        if int(data.get("basicSettings.adbDebug", 0)) == 1:
            return False
        data["basicSettings.adbDebug"] = 1
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")
        os.replace(tmp, path)
        _LOG.info("Đã bật ADB debugging cho instance index %s.", index)
        return True

    # -- lifecycle ---------------------------------------------------------

    def create_instance(self, name: str) -> int:
        self._ldconsole("add", "--name", name)
        for _ in range(10):
            for instance in self.list_instances():
                if instance.name == name:
                    return instance.index
            time.sleep(1)
        raise LDPlayerError(f"Tạo instance `{name}` xong nhưng không thấy trong list2.")

    def clone_instance(self, template_index: int, new_name: str) -> int:
        """Clone một instance.

        `ldconsole copy` trả exit code = index của bản clone chứ KHÔNG phải
        0/1 thành-công/thất-bại — không được tin cậy exit code, phải poll
        `list2` cho tới khi thấy tên mới xuất hiện.
        """
        # LDPlayer returns the new instance index as the process exit code
        # (for example exit=1), so a non-zero code is not a copy failure.
        self._ldconsole(
            "copy",
            "--name",
            new_name,
            "--from",
            str(template_index),
            allow_nonzero=True,
        )
        for _ in range(60):
            for instance in self.list_instances():
                if instance.name == new_name:
                    return instance.index
            time.sleep(1)
        raise LDPlayerError(
            f"Clone `{new_name}` từ index {template_index} xong nhưng không thấy trong list2."
        )

    def start_instance(self, index: int, timeout: float | None = None) -> str:
        if self.ensure_adb_enabled(index) and self.is_running(index):
            _LOG.info("Instance %s đang chạy với ADB tắt; tắt để áp cấu hình mới.", index)
            self.stop_instance(index)
            time.sleep(5)
        self._ldconsole("launch", "--index", str(index))
        serial = self.adb_serial(index)
        self._wait_boot(serial, index, timeout or self._config.boot_timeout)
        return serial

    def stop_instance(self, index: int) -> None:
        self._ldconsole("quit", "--index", str(index))

    def remove_instance(self, index: int) -> None:
        try:
            if self.is_running(index):
                self.stop_instance(index)
        except LDPlayerError:
            _LOG.warning("Không dừng được instance %s trước khi xoá, vẫn tiếp tục xoá.", index)
        try:
            self._ldconsole("remove", "--index", str(index))
        except LDPlayerError:
            _LOG.error("Xoá instance %s thất bại.", index)
            raise

    # -- waiting -------------------------------------------------------------

    def _wait_device_listed(self, serial: str, retries: int = 1) -> bool:
        for _ in range(retries):
            output = self._adb("devices")
            for line in output.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == serial and parts[1] == "device":
                    return True
            time.sleep(2)
        return False

    def _wait_boot(self, serial: str, index: int, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if not self._wait_device_listed(serial, retries=1):
                    time.sleep(3)
                    continue
                output = self._adb("-s", serial, "shell", "getprop", "sys.boot_completed", timeout=10)
                if output.strip() == "1":
                    self._adb("-s", serial, "shell", "pm", "list", "packages", "-3", timeout=30)
                    return
            except LDPlayerError:
                pass
            time.sleep(3)
        raise LDPlayerError(f"Instance {index} ({serial}) không boot xong sau {timeout:g}s.")


__all__ = ["LDInstance", "LDPlayerAdapter", "LDPlayerConfig", "LDPlayerError"]
