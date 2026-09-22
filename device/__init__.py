"""UI tree, mapping serial, và DeviceController — lớp chung cho flow.

Flow chỉ nên phụ thuộc vào các module trong package này (`DeviceController`,
`Selector`, `UiNode`, `UiTree`), không import trực tiếp `ldplayer` hay
`xiaowei`.
"""
