# LINE_COIN_VER_2

## Mục tiêu

Kết hợp LDPlayer để quản lý vòng đời instance với Xiaowei để đọc UI và thực hiện thao tác click/scroll ổn định.

## Nguyên tắc

- LDPlayer chịu trách nhiệm tạo, clone, boot, stop và xóa instance.
- Xiaowei chịu trách nhiệm list thiết bị, dump UI, click, nhập text, swipe và screenshot.
- UI, worker, Excel mapping và config chính sẽ ưu tiên tái sử dụng từ `WIN\LineViet`.
- `LINE_COIN_VER_2` chỉ thay tầng thao tác UI: các lệnh `uiautomator2` dần được thay bằng `DeviceController`/Xiaowei.
- Mỗi instance chỉ dùng một backend điều khiển tại một thời điểm.
- Không sửa trực tiếp `LINE_COIN` hoặc `WIN\LineViet` trong giai đoạn thử nghiệm.
- Mọi thao tác quan trọng phải có log và nhận diện đúng serial.

## Phase 1: Spike kết nối

1. Copy tối thiểu các module Xiaowei client, transport, models, actions và UI tree.
2. Tạo adapter `XiaoweiDevice` với các hàm chung: `start_app`, `stop_app`, `ui_tree`, `tap`, `swipe`, `screenshot`, `shell_read`.
3. Khởi động một instance LDPlayer.
4. Đối chiếu serial từ ADB/LDPlayer với serial do Xiaowei trả về.
5. Chạy smoke test: mở LINE, dump UI, tap một nút an toàn, swipe và chụp màn hình.

## Phase 2: Chuyển riêng Phase 2

1. Đưa flow AirWallet và Ministop sang adapter Xiaowei.
2. Tái sử dụng parser `UiTree`, selector theo text/bounds và logic scroll của `LINE_COIN`.
3. Xử lý deeplink Chrome, onboarding Chrome, màn Cho phép và retry gift code.
4. Ghi đầy đủ log: serial, màn hiện tại, số lần scroll, bounds và nút đã bấm.
5. Chạy thử một account đã đăng ký LINE; chỉ xóa instance khi lấy đủ hai mã.

## Phase 3: Tích hợp worker

1. Gắn adapter Xiaowei vào worker của `LineViet`.
2. Giữ Excel mapping, `line_status`, `phase2_status` và cơ chế chạy lại Phase 2.
3. Bảo đảm stop/xóa instance và dọn mapping kho máy đúng cả khi lỗi.
4. Chạy song song nhiều instance sau khi smoke test một máy ổn định.

## Tiêu chí hoàn thành

- Instance LDPlayer xuất hiện đúng trong Xiaowei.
- Click theo UI tree hoạt động đúng, không phụ thuộc tọa độ cố định.
- Phase 2 lấy được đủ hai mã và ghi đúng Excel.
- Lỗi hoặc dừng giữa chừng không làm mất mapping sai.
- Chỉ xóa instance sau khi toàn bộ flow thành công.
