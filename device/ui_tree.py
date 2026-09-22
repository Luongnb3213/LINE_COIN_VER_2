"""Đọc cây accessibility (uiautomator dump) và tìm phần tử theo nội dung.

Đây là lý do tool không phải hard-code toạ độ: `uiautomator dump` trả về XML có
`text`, `resource-id`, `content-desc` và `bounds` theo pixel thật của thiết bị.
Tìm nút bằng chữ trên nút, rồi lấy tâm `bounds` của chính nó — toạ độ luôn đúng
với độ phân giải hiện tại, không cần cấu hình gì thêm.

Một chi tiết thực tế của Android: chữ thường nằm trong `TextView` con, còn thuộc
tính `clickable="true"` lại nằm ở `ViewGroup` cha. Vì vậy sau khi tìm thấy chữ,
phải đi ngược lên tổ tiên gần nhất bấm được — xem :meth:`UiNode.clickable_self_or_ancestor`.

Module này thuần XML, không phụ thuộc Xiaowei hay LDPlayer — copy nguyên vẹn từ
LINE_COIN/device/ui_tree.py để dùng chung cho cả hai nguồn.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from xml.etree import ElementTree

#: `bounds="[0,0][1080,1920]"`
_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")

#: Lớp Android thường dùng cho ô nhập liệu.
_EDIT_HINTS = ("EditText", "AutoCompleteTextView", "SearchView")


class UiTreeError(Exception):
    """Không đọc được cây UI."""


def normalise(text: str) -> str:
    """Chuẩn hoá chuỗi trước khi so khớp.

    NFKC gộp các dạng ký tự khác nhau (nửa/đầy chiều rộng, khoảng trắng không
    ngắt...) về một, nếu không thì một nhãn hiển thị y hệt nhau trên màn hình
    lại không khớp chuỗi.
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", str(text))
    return re.sub(r"\s+", " ", folded).strip().casefold()


@dataclass(frozen=True)
class Rect:
    """Hình chữ nhật theo pixel thật của thiết bị."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def center(self) -> tuple[int, int]:
        return (self.left + self.right) // 2, (self.top + self.bottom) // 2

    @property
    def area(self) -> int:
        return max(self.width, 0) * max(self.height, 0)

    @property
    def visible(self) -> bool:
        """Phần tử có kích thước thật; `0x0` là node ẩn, bấm vào vô nghĩa."""
        return self.width > 0 and self.height > 0

    def contains_point(self, x: int, y: int) -> bool:
        return self.left <= x <= self.right and self.top <= y <= self.bottom

    def clamp_point(self, x: int, y: int, *, margin: int = 0) -> tuple[int, int]:
        """Đưa một điểm vào trong rect, tránh sát mép nếu rect đủ rộng/cao."""
        left = self.left + max(margin, 0)
        right = self.right - max(margin, 0)
        top = self.top + max(margin, 0)
        bottom = self.bottom - max(margin, 0)
        if left > right:
            left, right = self.left, self.right
        if top > bottom:
            top, bottom = self.top, self.bottom
        return min(max(x, left), right), min(max(y, top), bottom)

    def __str__(self) -> str:
        return f"[{self.left},{self.top}][{self.right},{self.bottom}]"

    @classmethod
    def parse(cls, raw: str) -> "Rect":
        match = _BOUNDS_RE.search(raw or "")
        if not match:
            return cls(0, 0, 0, 0)
        left, top, right, bottom = (int(value) for value in match.groups())
        return cls(left, top, right, bottom)


@dataclass
class UiNode:
    """Một node trong cây UI."""

    text: str = ""
    resource_id: str = ""
    content_desc: str = ""
    class_name: str = ""
    package: str = ""
    bounds: Rect = field(default_factory=lambda: Rect(0, 0, 0, 0))
    clickable: bool = False
    enabled: bool = True
    focused: bool = False
    focusable: bool = False
    password: bool = False
    depth: int = 0
    parent: "UiNode | None" = field(default=None, repr=False, compare=False)
    children: list["UiNode"] = field(default_factory=list, repr=False, compare=False)

    @property
    def label(self) -> str:
        """Chuỗi nhận diện dễ đọc, dùng cho log và thông báo lỗi."""
        for value in (self.text, self.content_desc, self.resource_id):
            if value:
                return value
        return self.class_name or "node"

    @property
    def editable(self) -> bool:
        return any(hint in self.class_name for hint in _EDIT_HINTS)

    @property
    def short_id(self) -> str:
        """`com.app:id/phone_input` -> `phone_input`."""
        return self.resource_id.rsplit("/", 1)[-1] if self.resource_id else ""

    def ancestors(self) -> list["UiNode"]:
        chain: list[UiNode] = []
        node = self.parent
        while node is not None:
            chain.append(node)
            node = node.parent
        return chain

    def clickable_self_or_ancestor(self) -> "UiNode | None":
        """Node bấm được gần nhất: chính nó, hoặc tổ tiên gần nhất bấm được.

        Trả về ``None`` nếu không có gì bấm được — khi đó không nên đoán bừa mà
        phải báo lỗi rõ ràng.
        """
        if self.clickable and self.enabled:
            return self
        for ancestor in self.ancestors():
            if ancestor.clickable and ancestor.enabled:
                return ancestor
        return None

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def describe(self) -> str:
        parts = [self.class_name.rsplit(".", 1)[-1] or "node"]
        if self.text:
            parts.append(f'text="{self.text}"')
        if self.short_id:
            parts.append(f"id={self.short_id}")
        if self.content_desc:
            parts.append(f'desc="{self.content_desc}"')
        parts.append(str(self.bounds))
        return " ".join(parts)


@dataclass(frozen=True)
class Selector:
    """Tiêu chí tìm phần tử. Mọi tiêu chí khai báo đều phải đúng (AND).

    Ưu tiên dùng ``text``/``text_contains``/``resource_id`` — chúng bám vào nội
    dung app, không bám vào vị trí, nên không vỡ khi đổi độ phân giải hay layout.
    """

    text: str | None = None
    text_contains: str | None = None
    resource_id: str | None = None
    content_desc: str | None = None
    class_contains: str | None = None
    clickable: bool | None = None
    editable: bool | None = None
    focused: bool | None = None
    enabled: bool | None = None
    #: Bỏ qua node kích thước 0x0 (không nhìn thấy, không bấm được).
    visible_only: bool = True

    def describe(self) -> str:
        shown = {
            key: value
            for key, value in (
                ("text", self.text),
                ("text_contains", self.text_contains),
                ("resource_id", self.resource_id),
                ("content_desc", self.content_desc),
                ("class_contains", self.class_contains),
                ("clickable", self.clickable),
                ("editable", self.editable),
                ("focused", self.focused),
            )
            if value is not None
        }
        return ", ".join(f"{key}={value!r}" for key, value in shown.items()) or "bất kỳ"

    def matches(self, node: UiNode) -> bool:
        if self.visible_only and not node.bounds.visible:
            return False
        if self.text is not None and normalise(node.text) != normalise(self.text):
            return False
        if self.text_contains is not None:
            needle = normalise(self.text_contains)
            haystack = f"{normalise(node.text)} {normalise(node.content_desc)}"
            if needle not in haystack:
                return False
        if self.resource_id is not None:
            wanted = self.resource_id
            if node.resource_id != wanted and node.short_id != wanted:
                return False
        if self.content_desc is not None:
            if normalise(node.content_desc) != normalise(self.content_desc):
                return False
        if self.class_contains is not None:
            if self.class_contains.lower() not in node.class_name.lower():
                return False
        if self.clickable is not None and node.clickable is not self.clickable:
            return False
        if self.editable is not None and node.editable is not self.editable:
            return False
        if self.focused is not None and node.focused is not self.focused:
            return False
        if self.enabled is not None and node.enabled is not self.enabled:
            return False
        return True


class UiTree:
    """Cây UI đã phân tích, kèm các phép tìm kiếm."""

    def __init__(self, root: UiNode | None, raw: str = "") -> None:
        self.root = root
        self.raw = raw

    @property
    def empty(self) -> bool:
        return self.root is None

    @property
    def nodes(self) -> list[UiNode]:
        return list(self.root.walk()) if self.root is not None else []

    @property
    def screen(self) -> Rect:
        """Kích thước màn hình suy ra từ chính bản dump.

        Lấy từ node gốc thay vì gọi thêm `wm size`: bounds trong dump và kích
        thước này chắc chắn cùng một hệ toạ độ, kể cả khi máy đang xoay ngang.
        """
        if self.root is None:
            return Rect(0, 0, 0, 0)
        return max((node.bounds for node in self.root.walk()), key=lambda r: r.area)

    @property
    def texts(self) -> list[str]:
        return [node.text for node in self.nodes if node.text]

    def find_all(self, selector: Selector) -> list[UiNode]:
        return [node for node in self.nodes if selector.matches(node)]

    def find(self, selector: Selector) -> UiNode | None:
        """Node đầu tiên khớp, theo thứ tự tài liệu."""
        for node in self.nodes:
            if selector.matches(node):
                return node
        return None

    def contains_text(self, text: str) -> bool:
        """Có node nào mang đúng/có chứa chuỗi này không (đã chuẩn hoá)."""
        needle = normalise(text)
        if not needle:
            return False
        return any(
            needle in normalise(node.text) or needle in normalise(node.content_desc)
            for node in self.nodes
        )

    def summary(self, limit: int = 12) -> str:
        """Tóm tắt màn hình cho log/thông báo lỗi — biết mình đang đứng ở đâu."""
        seen: list[str] = []
        for node in self.nodes:
            value = (node.text or node.content_desc).strip()
            if value and value not in seen:
                seen.append(value)
            if len(seen) >= limit:
                break
        return " | ".join(seen) if seen else "(màn hình không có text nào)"


def parse_ui_dump(xml: str) -> UiTree:
    """Phân tích XML của `uiautomator dump`.

    XML rỗng không phải lỗi lập trình — nó thường có nghĩa là dump thất bại
    (màn hình đang chuyển cảnh, hoặc app chặn chụp màn hình). Trả về cây rỗng để
    người gọi tự quyết định chờ thêm hay báo lỗi.
    """
    text = (xml or "").strip()
    if not text or "<" not in text:
        return UiTree(None, raw=text)
    try:
        element = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise UiTreeError(f"Không phân tích được XML của uiautomator: {exc}") from exc

    def build(source, parent: UiNode | None, depth: int) -> UiNode:
        get = source.attrib.get
        node = UiNode(
            text=get("text", ""),
            resource_id=get("resource-id", ""),
            content_desc=get("content-desc", ""),
            class_name=get("class", ""),
            package=get("package", ""),
            bounds=Rect.parse(get("bounds", "")),
            clickable=get("clickable") == "true",
            enabled=get("enabled", "true") == "true",
            focused=get("focused") == "true",
            focusable=get("focusable") == "true",
            password=get("password") == "true",
            depth=depth,
            parent=parent,
        )
        for child_element in source:
            node.children.append(build(child_element, node, depth + 1))
        return node

    # `<hierarchy>` là thẻ bọc, không phải một view. Nếu nó chỉ có đúng một con
    # thì lấy thẳng con đó làm gốc cho gọn.
    root = build(element, None, 0)
    if root.class_name == "" and len(root.children) == 1:
        root = root.children[0]
        root.parent = None
    return UiTree(root, raw=text)


__all__ = [
    "Rect",
    "Selector",
    "UiNode",
    "UiTree",
    "UiTreeError",
    "normalise",
    "parse_ui_dump",
]
