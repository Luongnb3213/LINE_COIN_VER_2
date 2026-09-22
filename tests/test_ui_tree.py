"""Test cho device/ui_tree.py: parse dump, selector, climb-to-clickable-ancestor."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from device.ui_tree import Rect, Selector, parse_ui_dump

#: `login_btn` (TextView, không clickable) nằm BÊN TRONG `login_container`
#: (Button, clickable=true) — mô phỏng đúng kiểu Android thật: chữ nằm ở
#: TextView con, thuộc tính clickable nằm ở ViewGroup cha.
_SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" class="android.widget.FrameLayout" bounds="[0,0][1080,1920]">
    <node index="0" text="" resource-id="" class="android.widget.Button"
          clickable="true" enabled="true" bounds="[100,200][400,260]">
      <node index="0" text="Đăng nhập" resource-id="com.app:id/login_btn"
            class="android.widget.TextView" clickable="false" enabled="true"
            bounds="[140,210][360,250]" />
    </node>
    <node index="1" text="Ẩn" resource-id="com.app:id/hidden"
          class="android.widget.TextView" clickable="false" enabled="true"
          bounds="[0,0][0,0]" />
  </node>
</hierarchy>
"""


class ParseUiDumpTests(unittest.TestCase):
    def test_empty_xml_returns_empty_tree(self) -> None:
        tree = parse_ui_dump("")
        self.assertTrue(tree.empty)
        self.assertEqual(tree.nodes, [])

    def test_parses_bounds_and_text(self) -> None:
        tree = parse_ui_dump(_SAMPLE_XML)
        node = tree.find(Selector(resource_id="login_btn"))
        self.assertIsNotNone(node)
        assert node is not None
        self.assertEqual(node.text, "Đăng nhập")
        self.assertEqual(node.bounds, Rect(140, 210, 360, 250))

    def test_resource_id_matches_short_form(self) -> None:
        tree = parse_ui_dump(_SAMPLE_XML)
        full = tree.find(Selector(resource_id="com.app:id/login_btn"))
        short = tree.find(Selector(resource_id="login_btn"))
        self.assertIsNotNone(full)
        self.assertIsNotNone(short)
        assert full is not None and short is not None
        self.assertIs(full, short)

    def test_clickable_self_or_ancestor_climbs_to_button(self) -> None:
        tree = parse_ui_dump(_SAMPLE_XML)
        text_node = tree.find(Selector(text="Đăng nhập"))
        self.assertIsNotNone(text_node)
        assert text_node is not None
        self.assertFalse(text_node.clickable)

        target = text_node.clickable_self_or_ancestor()
        self.assertIsNotNone(target)
        assert target is not None
        self.assertTrue(target.clickable)
        self.assertEqual(target.bounds, Rect(100, 200, 400, 260))

    def test_visible_only_excludes_zero_size_nodes(self) -> None:
        tree = parse_ui_dump(_SAMPLE_XML)
        self.assertIsNone(tree.find(Selector(text="Ẩn")))
        found = tree.find(Selector(text="Ẩn", visible_only=False))
        self.assertIsNotNone(found)

    def test_contains_text_normalises_whitespace_and_case(self) -> None:
        tree = parse_ui_dump(_SAMPLE_XML)
        self.assertTrue(tree.contains_text("đăng nhập"))
        self.assertTrue(tree.contains_text("  Đăng   nhập  "))
        self.assertFalse(tree.contains_text("không tồn tại"))


if __name__ == "__main__":
    unittest.main()
