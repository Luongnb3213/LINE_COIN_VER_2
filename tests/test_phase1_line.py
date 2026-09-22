"""Test cho flows/phase1_line.py: chỉ phần logic thuần (không đụng thiết bị thật).

Trọng tâm: `classify_screen` phân loại đúng màn hình, và bộ chọn tài khoản
Google không bao giờ nhầm màn "Add another account" thành đã chọn được tài
khoản (an toàn bắt buộc — không được bấm "Add another account").
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from device.ui_tree import Rect, UiNode, UiTree
from flows.phase1_line import (
    LINE_PACKAGE,
    Phase1Screen,
    _ADD_ACCOUNT_MARKERS,
    _COUNTRY_CODE,
    _CREATE_ACCOUNT_TITLE,
    _find_google_password_field,
    _HOME_ACTIVITY,
    _HOME_NAME,
    _NAME_GROUP,
    _PASSWORD_FIELD,
    _PASSWORD_TITLE,
    _SIGN_UP,
    _SYNC_PROGRESS,
    _SYNC_TITLE,
    classify_screen,
    find_google_account_node,
    is_google_add_account_screen,
    visible_account_emails,
)


def _node(text: str = "", *, content_desc: str = "", resource_id: str = "", bounds: Rect | None = None) -> UiNode:
    return UiNode(text=text, content_desc=content_desc, resource_id=resource_id, bounds=bounds or Rect(0, 0, 200, 60))


def _tree(nodes: list[UiNode]) -> UiTree:
    root = UiNode(class_name="root", bounds=Rect(0, 0, 1080, 1920))
    root.children = nodes
    for node in nodes:
        node.parent = root
    return UiTree(root)


def _home_name_node() -> UiNode:
    return _node(resource_id=_HOME_NAME.resource_id)


class ClassifyScreenTests(unittest.TestCase):
    def test_empty_tree_is_unknown(self) -> None:
        self.assertEqual(classify_screen(UiTree(None)), Phase1Screen.UNKNOWN)

    def test_home_requires_both_node_and_activity(self) -> None:
        #: Có node home_tab_name nhưng activity KHÔNG khớp -> không được đoán
        #: là HOME (activity sai nghĩa là node trùng resource-id nhưng khác
        #: màn hình thật sự).
        tree = _tree([_home_name_node()])
        self.assertNotEqual(classify_screen(tree, activity="SomeOtherActivity"), Phase1Screen.HOME)

    def test_home_when_node_and_activity_match(self) -> None:
        tree = _tree([_home_name_node()])
        self.assertEqual(classify_screen(tree, activity=f"{LINE_PACKAGE}/.{_HOME_ACTIVITY}"), Phase1Screen.HOME)

    def test_syncing_via_progress_bar(self) -> None:
        tree = _tree([_node(resource_id=_SYNC_PROGRESS.resource_id)])
        self.assertEqual(classify_screen(tree), Phase1Screen.SYNCING)

    def test_syncing_via_title(self) -> None:
        tree = _tree([_node(_SYNC_TITLE.text, resource_id=_SYNC_TITLE.resource_id)])
        self.assertEqual(classify_screen(tree), Phase1Screen.SYNCING)

    def test_google_add_account_screen(self) -> None:
        tree = _tree([_node(_ADD_ACCOUNT_MARKERS[0].text)])
        self.assertEqual(classify_screen(tree), Phase1Screen.GOOGLE_ADD_ACCOUNT)

    def test_create_password_by_title(self) -> None:
        tree = _tree([_node(_PASSWORD_TITLE.text)])
        self.assertEqual(classify_screen(tree), Phase1Screen.CREATE_PASSWORD)

    def test_create_password_by_field(self) -> None:
        tree = _tree([_node(resource_id=_PASSWORD_FIELD.resource_id, content_desc="Password")])
        self.assertEqual(classify_screen(tree), Phase1Screen.CREATE_PASSWORD)

    def test_create_account_name_by_title(self) -> None:
        tree = _tree([_node(_CREATE_ACCOUNT_TITLE.text)])
        self.assertEqual(classify_screen(tree), Phase1Screen.CREATE_ACCOUNT_NAME)

    def test_create_account_name_by_name_group(self) -> None:
        tree = _tree([_node(resource_id=_NAME_GROUP.resource_id)])
        self.assertEqual(classify_screen(tree), Phase1Screen.CREATE_ACCOUNT_NAME)

    def test_verify_account_via_country_code(self) -> None:
        tree = _tree([_node(resource_id=_COUNTRY_CODE.resource_id)])
        self.assertEqual(classify_screen(tree), Phase1Screen.VERIFY_ACCOUNT)

    def test_welcome_via_sign_up(self) -> None:
        tree = _tree([_node(_SIGN_UP.text)])
        self.assertEqual(classify_screen(tree), Phase1Screen.WELCOME)

    def test_unknown_when_nothing_matches(self) -> None:
        tree = _tree([_node("Một màn hình không liên quan")])
        self.assertEqual(classify_screen(tree), Phase1Screen.UNKNOWN)

    def test_google_add_account_takes_priority_over_password_screen(self) -> None:
        #: Nếu cả hai marker cùng xuất hiện (không nên xảy ra thật, nhưng thứ
        #: tự ưu tiên trong classify_screen phải rõ ràng) -> vẫn phải nhận
        #: diện là màn "Add another account" để caller KHÔNG bấm tiếp.
        tree = _tree([_node(_ADD_ACCOUNT_MARKERS[0].text), _node(_PASSWORD_TITLE.text)])
        self.assertEqual(classify_screen(tree), Phase1Screen.GOOGLE_ADD_ACCOUNT)


class IsGoogleAddAccountScreenTests(unittest.TestCase):
    def test_false_when_no_markers(self) -> None:
        tree = _tree([_node("Chọn tài khoản"), _node("someone@gmail.com")])
        self.assertFalse(is_google_add_account_screen(tree))

    def test_true_for_each_known_marker(self) -> None:
        for marker in _ADD_ACCOUNT_MARKERS:
            with self.subTest(marker=marker.text):
                tree = _tree([_node(marker.text)])
                self.assertTrue(is_google_add_account_screen(tree))


class FindGoogleAccountNodeTests(unittest.TestCase):
    def test_returns_none_for_empty_email(self) -> None:
        tree = _tree([_node("someone@gmail.com")])
        self.assertIsNone(find_google_account_node(tree, ""))
        self.assertIsNone(find_google_account_node(tree, "   "))

    def test_finds_exact_email_match(self) -> None:
        tree = _tree([_node("other@gmail.com"), _node("target@gmail.com")])
        node = find_google_account_node(tree, "target@gmail.com")
        self.assertIsNotNone(node)
        self.assertEqual(node.text, "target@gmail.com")

    def test_no_match_returns_none(self) -> None:
        tree = _tree([_node("other@gmail.com")])
        self.assertIsNone(find_google_account_node(tree, "target@gmail.com"))

    def test_never_matches_add_another_account_screen(self) -> None:
        """An toàn cốt lõi: khi chỉ có màn "Add another account" (không có
        node đúng email), `find_google_account_node` phải trả None — flow
        dựa vào điều này để KHÔNG BAO GIỜ bấm nhầm "Add another account".
        """
        tree = _tree([_node(marker.text) for marker in _ADD_ACCOUNT_MARKERS])
        self.assertTrue(is_google_add_account_screen(tree))
        self.assertIsNone(find_google_account_node(tree, "target@gmail.com"))


class VisibleAccountEmailsTests(unittest.TestCase):
    def test_collects_unique_sorted_emails(self) -> None:
        tree = _tree([_node("b@gmail.com"), _node("a@gmail.com"), _node("b@gmail.com")])
        self.assertEqual(visible_account_emails(tree), ["a@gmail.com", "b@gmail.com"])

    def test_ignores_text_without_at_symbol(self) -> None:
        tree = _tree([_node("Chọn tài khoản"), _node("target@gmail.com")])
        self.assertEqual(visible_account_emails(tree), ["target@gmail.com"])

    def test_empty_tree_yields_no_emails(self) -> None:
        tree = _tree([])
        self.assertEqual(visible_account_emails(tree), [])


class GooglePasswordFieldTests(unittest.TestCase):
    def test_password_screen_with_try_another_way_is_still_password_screen(self) -> None:
        field = UiNode(
            class_name="android.widget.EditText",
            bounds=Rect(0, 0, 200, 60),
        )
        tree = _tree([
            _node("Welcome"),
            _node("Show password"),
            _node("TRY ANOTHER WAY"),
            _node("NEXT"),
            field,
        ])

        self.assertIs(_find_google_password_field(tree), field)


if __name__ == "__main__":
    unittest.main()
