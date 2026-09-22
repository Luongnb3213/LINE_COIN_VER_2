"""Test import cho gui.py — không tạo cửa sổ Tk thật (chỉ import module và
kiểm tra các method chính có mặt), để chạy được trong môi trường không có
màn hình (CI/headless)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class GuiImportTests(unittest.TestCase):
    def test_module_imports_without_creating_a_window(self) -> None:
        import gui

        self.assertTrue(hasattr(gui, "Phase2GUI"))

    def test_phase2_gui_exposes_expected_run_handlers(self) -> None:
        import gui

        for name in ("start_run", "_start_phase2_only", "_run_full_flow", "_run_phase2", "start_smoke_test", "stop_bot"):
            with self.subTest(method=name):
                self.assertTrue(callable(getattr(gui.Phase2GUI, name, None)), f"thiếu method {name}")


if __name__ == "__main__":
    unittest.main()
