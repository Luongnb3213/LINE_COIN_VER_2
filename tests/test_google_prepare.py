"""Test logic chuẩn bị Google không đụng thiết bị thật."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flows.google_prepare import next_prepare_name, next_prepare_names
from ldplayer.adapter import LDInstance


class FakeLDPlayer:
    def __init__(self, names: list[str]) -> None:
        self._names = names

    def list_instances(self) -> list[LDInstance]:
        return [
            LDInstance(index=index, name=name, running=False)
            for index, name in enumerate(self._names)
        ]


class PrepareNameTests(unittest.TestCase):
    def test_next_prepare_names_fill_gaps_and_do_not_repeat_reserved_names(self) -> None:
        ldplayer = FakeLDPlayer(["LDPlayer", "LineViet-g01", "LineViet-g03", "Other-g02"])

        self.assertEqual(
            next_prepare_names(ldplayer, "LineViet-g", 4),  # type: ignore[arg-type]
            ["LineViet-g02", "LineViet-g04", "LineViet-g05", "LineViet-g06"],
        )

    def test_next_prepare_name_keeps_legacy_single_name_behavior(self) -> None:
        ldplayer = FakeLDPlayer(["LineViet-g01"])

        self.assertEqual(next_prepare_name(ldplayer, "LineViet-g"), "LineViet-g02")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
