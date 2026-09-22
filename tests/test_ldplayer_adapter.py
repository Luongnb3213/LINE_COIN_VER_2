"""Test cho ranh giới LDPlayer: launch không được poll boot bằng adb.exe."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ldplayer.adapter import LDPlayerAdapter, LDPlayerConfig


class StartInstanceTests(unittest.TestCase):
    def test_start_instance_launches_without_calling_adb(self) -> None:
        adapter = LDPlayerAdapter(
            LDPlayerConfig(
                ld_console="C:/LDPlayer/LDPlayer14/ldconsole.exe",
                adb_path="C:/LDPlayer/LDPlayer14/adb.exe",
            )
        )
        adapter.ensure_adb_enabled = lambda index: False  # type: ignore[method-assign]

        with patch("ldplayer.adapter._run", return_value="") as run:
            serial = adapter.start_instance(2)

        self.assertEqual(serial, "emulator-5558")
        run.assert_called_once_with(
            ["C:/LDPlayer/LDPlayer14/ldconsole.exe", "launch", "--index", "2"],
            timeout=30.0,
            allow_nonzero=False,
        )


if __name__ == "__main__":
    unittest.main()
