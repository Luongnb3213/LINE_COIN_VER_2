"""Test cho flows/phase2.py: chỉ phần logic thuần (không đụng thiết bị thật).

Trọng tâm: trích gift code không được nhặt nhầm text quảng cáo/nhãn khác
("QUOCGIA", "540ID2000COIN"...), và `_detect_screen` phân loại đúng màn hình.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from device.ui_tree import Rect, UiNode, UiTree
from flows.phase2 import (
    AUTH_ALLOW_TEXTS,
    CAMPAIGN_MARKERS,
    CHECKBOX_TEXT,
    GIFT_CODE_LABEL,
    GIFT_CODE_PAGE_MARKERS,
    MINISTOP_CAMPAIGN_MARKERS,
    RECEIVE_BUTTON_TEXT,
    Phase2Screen,
    _detect_screen,
    _extract_code_from_text,
    _extract_gift_code,
    _is_gift_code_page,
)

#: 4 mã thật đã quan sát được — không cố định 16 ký tự, không phải mẫu.
REAL_CODE_EXAMPLES = (
    "GRM29G7WLBMZVL3C",
    "GY5LSVDB5C73LZNA",
    "E5PSWAFE32663X25",
    "KYVXRPUN7W2H9K7F",
)


def _node(text: str = "", *, content_desc: str = "", bounds: Rect | None = None) -> UiNode:
    return UiNode(text=text, content_desc=content_desc, bounds=bounds or Rect(0, 0, 200, 60))


def _tree(nodes: list[UiNode]) -> UiTree:
    root = UiNode(class_name="root", bounds=Rect(0, 0, 1080, 1920))
    root.children = nodes
    for node in nodes:
        node.parent = root
    return UiTree(root)


class ExtractCodeFromTextTests(unittest.TestCase):
    def test_accepts_real_code_examples(self) -> None:
        for code in REAL_CODE_EXAMPLES:
            with self.subTest(code=code):
                extracted, reason = _extract_code_from_text(code)
                self.assertEqual(extracted, code)
                self.assertEqual(reason, "")

    def test_accepts_code_embedded_in_surrounding_text(self) -> None:
        extracted, reason = _extract_code_from_text("コード: GRM29G7WLBMZVL3C です")
        self.assertEqual(extracted, "GRM29G7WLBMZVL3C")
        self.assertEqual(reason, "")

    def test_rejects_no_digit(self) -> None:
        extracted, reason = _extract_code_from_text("ABCDEFGHIJKL")
        self.assertEqual(extracted, "")
        self.assertTrue(reason.startswith("no-digit:"))

    def test_rejects_no_alpha(self) -> None:
        extracted, reason = _extract_code_from_text("123456789012")
        self.assertEqual(extracted, "")
        self.assertTrue(reason.startswith("no-alpha:"))

    def test_rejects_year_prefix(self) -> None:
        extracted, reason = _extract_code_from_text("2026ABCDEFGH")
        self.assertEqual(extracted, "")
        self.assertTrue(reason.startswith("year-prefix:"))

    def test_short_ad_text_yields_no_candidate(self) -> None:
        #: "QUOCGIA" chỉ 7 ký tự — dưới ngưỡng 12, không khớp regex nên
        #: không có ứng viên nào để đánh giá (không phải bị từ chối).
        extracted, reason = _extract_code_from_text("QUOCGIA")
        self.assertEqual(extracted, "")
        self.assertEqual(reason, "")

    def test_rejects_known_ad_text_pattern_540id2000coin(self) -> None:
        """`540ID2000COIN` (13 ký tự, có số+chữ, không bắt đầu `2026`) lọt qua
        3 điều kiện digit/alpha/năm — nên có thêm denylist riêng cho đúng mẫu
        quảng cáo đã quan sát được này, làm lớp phòng thủ bổ sung cho khoanh
        vùng vị trí (nhãn + marker/context) ở `_extract_gift_code`.
        """
        extracted, reason = _extract_code_from_text("540ID2000COIN")
        self.assertEqual(extracted, "")
        self.assertTrue(reason.startswith("ad-text-coin:"))

    def test_empty_value_returns_nothing(self) -> None:
        self.assertEqual(_extract_code_from_text(""), ("", ""))
        self.assertEqual(_extract_code_from_text("   "), ("", ""))


class IsGiftCodePageTests(unittest.TestCase):
    def test_false_when_label_missing(self) -> None:
        tree = _tree([_node("Một số text bất kỳ")])
        self.assertFalse(_is_gift_code_page(tree))

    def test_false_when_label_present_but_no_confirmation_marker(self) -> None:
        #: Trang chỉ NHẮC tới "ギフトコード" (banner/mô tả) chứ chưa hiển thị mã thật.
        tree = _tree([_node(GIFT_CODE_LABEL)])
        self.assertFalse(_is_gift_code_page(tree))

    def test_true_when_label_and_marker_present(self) -> None:
        tree = _tree([_node(GIFT_CODE_LABEL), _node(GIFT_CODE_PAGE_MARKERS[0])])
        self.assertTrue(_is_gift_code_page(tree))


class ExtractGiftCodeTests(unittest.TestCase):
    def test_returns_empty_when_not_gift_code_page(self) -> None:
        #: Có "540ID2000COIN" trong cây nhưng KHÔNG phải trang gift code thật
        #: (thiếu marker xác nhận) -> không được trích ra bất cứ gì.
        tree = _tree([_node(GIFT_CODE_LABEL), _node("540ID2000COIN")])
        self.assertEqual(_extract_gift_code(tree), "")

    def test_extracts_real_code_within_window_after_label(self) -> None:
        nodes = [
            _node(GIFT_CODE_PAGE_MARKERS[0]),
            _node(GIFT_CODE_LABEL),
            _node("QUOCGIA"),  # ứng viên rác, không match regex -> bị bỏ qua êm
            _node(REAL_CODE_EXAMPLES[0]),
        ]
        tree = _tree(nodes)
        self.assertEqual(_extract_gift_code(tree), REAL_CODE_EXAMPLES[0])

    def test_ignores_ad_text_placed_before_label(self) -> None:
        """`540ID2000COIN` đứng TRƯỚC nhãn (vd. banner quảng cáo phía trên) phải
        bị bỏ qua hoàn toàn — cửa sổ quét chỉ nhìn về phía SAU nhãn.
        """
        nodes = [
            _node("540ID2000COIN"),
            _node(GIFT_CODE_PAGE_MARKERS[1]),
            _node(GIFT_CODE_LABEL),
            _node(REAL_CODE_EXAMPLES[1]),
        ]
        tree = _tree(nodes)
        self.assertEqual(_extract_gift_code(tree), REAL_CODE_EXAMPLES[1])

    def test_ignores_ad_text_outside_ten_node_window(self) -> None:
        far_nodes = [_node(f"filler-{i}") for i in range(12)]
        nodes = [_node(GIFT_CODE_LABEL), *far_nodes, _node("540ID2000COIN"), _node(GIFT_CODE_PAGE_MARKERS[0])]
        tree = _tree(nodes)
        #: Marker có mặt (nên _is_gift_code_page = True) nhưng mã thật lại
        #: nằm ngoài cửa sổ 10 node -> không trích được gì, KHÔNG được lấy
        #: nhầm "540ID2000COIN".
        self.assertEqual(_extract_gift_code(tree), "")

    def test_no_gift_code_label_at_all_never_extracts(self) -> None:
        tree = _tree([_node("540ID2000COIN"), _node(GIFT_CODE_PAGE_MARKERS[0])])
        self.assertEqual(_extract_gift_code(tree), "")

    def test_allow_label_only_false_still_refuses_without_marker(self) -> None:
        #: Rule cũ (mặc định allow_label_only=False) phải giữ nguyên: thiếu
        #: marker xác nhận thì không được trích, dù có mã thật gần nhãn.
        tree = _tree([_node(GIFT_CODE_LABEL), _node(REAL_CODE_EXAMPLES[0])])
        self.assertEqual(_extract_gift_code(tree, allow_label_only=False), "")

    def test_allow_label_only_extracts_when_marker_scrolled_off_but_context_ok(self) -> None:
        #: Viewport nhỏ của LDPlayer: marker cảm ơn đã cuộn khỏi màn nhưng
        #: nhãn + mã thật vẫn hiển thị, và không có dấu hiệu campaign page
        #: (context = LINE webview/Chrome custom tab sau auth) -> fallback
        #: phải cho phép trích.
        tree = _tree([_node(GIFT_CODE_LABEL), _node(REAL_CODE_EXAMPLES[2])])
        self.assertEqual(_extract_gift_code(tree, allow_label_only=True), REAL_CODE_EXAMPLES[2])

    def test_allow_label_only_never_extracts_540id2000coin(self) -> None:
        #: Ngay cả ở chế độ fallback, "540ID2000COIN" vẫn phải bị chặn —
        #: đây là mẫu quảng cáo đã biết, không phải gift code thật.
        tree = _tree([_node(GIFT_CODE_LABEL), _node("540ID2000COIN")])
        self.assertEqual(_extract_gift_code(tree, allow_label_only=True), "")

    def test_allow_label_only_refuses_on_campaign_page(self) -> None:
        #: Trang campaign chỉ NHẮC tới nhãn (vd. nút "ギフトコードを受け取る"
        #: chứa nhãn như một substring) chứ chưa qua auth -> fallback không
        #: được phép trích dù allow_label_only=True.
        tree = _tree([_node(GIFT_CODE_LABEL), _node(RECEIVE_BUTTON_TEXT), _node(CHECKBOX_TEXT)])
        self.assertEqual(_extract_gift_code(tree, allow_label_only=True), "")


class DetectScreenTests(unittest.TestCase):
    def test_empty_tree_is_unknown(self) -> None:
        self.assertEqual(_detect_screen(UiTree(None)), Phase2Screen.UNKNOWN)

    def test_gift_code_page_when_label_and_marker(self) -> None:
        tree = _tree([_node(GIFT_CODE_LABEL), _node(GIFT_CODE_PAGE_MARKERS[0])])
        self.assertEqual(_detect_screen(tree), Phase2Screen.GIFT_CODE_PAGE)

    def test_gift_code_ready_when_only_label(self) -> None:
        tree = _tree([_node(GIFT_CODE_LABEL)])
        self.assertEqual(_detect_screen(tree), Phase2Screen.GIFT_CODE_READY)

    def test_auth_allow(self) -> None:
        tree = _tree([_node(AUTH_ALLOW_TEXTS[0])])
        self.assertEqual(_detect_screen(tree), Phase2Screen.AUTH_ALLOW)

    def test_auth_screen(self) -> None:
        tree = _tree([_node("AirWALLET")])
        self.assertEqual(_detect_screen(tree), Phase2Screen.AUTH_SCREEN)

    def test_ministop_campaign_page(self) -> None:
        tree = _tree([_node(MINISTOP_CAMPAIGN_MARKERS[0])])
        self.assertEqual(_detect_screen(tree), Phase2Screen.MINISTOP_CAMPAIGN_PAGE)

    def test_campaign_page(self) -> None:
        tree = _tree([_node(CHECKBOX_TEXT)])
        self.assertIn(CHECKBOX_TEXT, CAMPAIGN_MARKERS)
        self.assertEqual(_detect_screen(tree), Phase2Screen.CAMPAIGN_PAGE)

    def test_unrelated_text_is_unknown(self) -> None:
        tree = _tree([_node("Một trang web bất kỳ không liên quan")])
        self.assertEqual(_detect_screen(tree), Phase2Screen.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
