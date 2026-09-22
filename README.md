# LINE_COIN_VER_2

Kết hợp LDPlayer (vòng đời máy ảo Android) với Xiaowei (điều khiển UI qua
WebSocket local API) thành một kiến trúc phân lớp rõ ràng, để các flow tự
động hoá không bao giờ phụ thuộc trực tiếp vào một trong hai công cụ.

## Kiến trúc

```
core/      Config + logging dùng chung.
ldplayer/  Adapter vòng đời LDPlayer: list/create/clone/start/stop/remove
           instance, lấy serial ADB. KHÔNG điều khiển màn hình.
xiaowei/   Client Xiaowei: WebSocket transport tự viết (RFC 6455 tối giản),
           registry action đã tài liệu hoá, list device, uiautomator/adb,
           tap/swipe/screenshot. KHÔNG quản lý vòng đời emulator.
device/    UiTree/Selector (parse `uiautomator dump`, tìm node theo nội
           dung), device/mapping.py (map serial ADB <-> thiết bị Xiaowei,
           chỉ khớp chính xác), DeviceController (lớp chung duy nhất mà
           flow nên gọi).
flows/     Flow nghiệp vụ Phase 2 (phase2.py: nhận gift code AirWallet /
           Ministop; xlsx_store.py: ghi kết quả vào Excel). Chỉ được gọi
           `device.controller.DeviceController` — không bao giờ import
           thẳng `xiaowei`/`ldplayer`/`uiautomator2`.
tests/     Unit test cho mapping, UI tree parser, logic Phase 2 (trích gift
           code, nhận diện màn hình) và Excel I/O.
```

Nguyên tắc tách lớp: **flow chỉ import `device.controller.DeviceController`**,
không bao giờ import thẳng `ldplayer` hay `xiaowei`. LDPlayer chỉ lo vòng đời
máy ảo; Xiaowei chỉ lo điều khiển bên trong máy ảo đã xác định.

## Yêu cầu

- Python 3.12, Windows.
- LDPlayer đã cài, `ldconsole.exe`/`adb.exe` có sẵn.
- Xiaowei client đang chạy, API local đang bật tại `ws://127.0.0.1:22222/`
  (mặc định).
- Phase 1 không cần thư viện ngoài nào. Phase 2 cần `openpyxl` để ghi Excel
  (xem [requirements.txt](requirements.txt)): `py -m pip install -r requirements.txt`.

## Cấu hình

`config.json` trong thư mục này là bản copy ban đầu từ `WIN/LineViet/config.json`.
Từ thời điểm copy xong, chỉnh gì thì chỉnh trực tiếp ở:

```text
C:/Users/ADMIN/Desktop/FPT/LINE_COIN_VER_2/config.json
```

Không dùng thẳng file config sống bên `WIN/LineViet`, để hai project không
giẫm cấu hình của nhau. Loader vẫn hiểu format flat của LineViet: tự suy ra
`ldconsole.exe` và `adb.exe` từ `ld_path`, dùng `emulator_boot_timeout`, và
mặc định Xiaowei là `ws://127.0.0.1:22222/`.

Phase 2 đọc thêm `xlsx_path` (đường dẫn file Excel) và `active_sheet` (mặc
định `"Mails"`) thẳng từ top-level của cùng file `config.json` này.

## Chạy smoke test

Luôn chạy với config local của project này (`config.json`), không dùng config
sống bên `WIN/LineViet`:

```bash
py smoke_test.py --config config.json --index 0
```

Không truyền `--index`/`--name` thì mặc định dùng instance đầu tiên trong
`list2`. Có thể chỉ định instance khác bằng `--index N` hoặc
`--name "TênInstance"`. Smoke
test sẽ: list instance LDPlayer, boot nếu cần, list thiết bị Xiaowei, **map
serial ADB sang thiết bị Xiaowei (chỉ khớp chính xác, dừng an toàn nếu
không khớp)**, dump UI, chụp màn hình, thực hiện một click an toàn (ưu tiên
nút Home tìm được qua UI tree, nếu không có thì dùng phím tắt Home) và một
swipe an toàn (cuộn dựa theo kích thước màn hình thật). Log in ra đầy đủ
index/instance/serial ADB/serial Xiaowei/package foreground ở mỗi bước và
trong dòng tổng kết `SMOKE TEST OK`.

## Chạy Phase 2 (gift code AirWallet / Ministop)

```bash
py phase2_test.py --config config.json --index 0 --email jisogabe@gmail.com
```

Yêu cầu dòng có email đó đã tồn tại sẵn trong sheet Excel (`xlsx_path`/
`active_sheet` trong config) — Phase 2 chỉ cập nhật dòng có sẵn, không tự tạo
dòng mới. Mỗi lần chạy tạo một file log riêng dưới `logs/`.

Tham số:

- `--source both|airwallet|ministop` (mặc định `both`).
- `--remove-instance`: **tắt theo mặc định**. Chỉ khi truyền cờ này VÀ ghi
  Excel thành công VÀ `phase2_status=SUCCESS` VÀ đủ toàn bộ mã đã yêu cầu,
  instance LDPlayer mới bị xoá; nếu không, instance luôn được giữ lại để
  debug/chạy lại — kể cả khi không truyền cờ này.

Flow: mở Chrome tới trang nguồn thưởng, xử lý màn hình Chrome lần đầu (welcome/
"Use without an account"/thông báo), cuộn tìm checkbox đồng ý + nút nhận gift
code (AirWallet) hoặc nút "LINE認証 & 友だち追加" (Ministop), deeplink sang
LINE, xử lý màn hình "Cho phép"/Allow, rồi trích gift code trên trang kết quả.
Việc trích code chỉ thực hiện trong phạm vi quanh nhãn "ギフトコード" đã xác
nhận đúng trang (không OCR/quét toàn màn hình), để không nhặt nhầm text quảng
cáo. Kết quả ghi vào Excel: `phase2_status=SUCCESS` chỉ khi đủ mã đã yêu cầu;
`PARTIAL` nếu chỉ lấy được một phần (không xoá instance); ghi `SUCCESS` sẽ xoá
`error_details` cũ nếu có.

## Chạy test

```bash
python -m unittest discover -s tests -t .
```

## Lưu ý an toàn

- Việc map serial ADB <-> Xiaowei **không bao giờ** dựa vào thứ tự danh
  sách — chỉ khớp chính xác `serial`/`onlySerial`. Nếu map thất bại,
  `DeviceController.bind_by_index()` raise lỗi và toàn bộ flow phải dừng lại
  thay vì đoán.
- Không được để `uiautomator2` và Xiaowei cùng điều khiển một instance tại
  cùng một thời điểm.
- Instance LDPlayer chỉ bị xoá khi được yêu cầu tường minh (`--remove-instance`)
  VÀ mọi điều kiện an toàn đạt (ghi Excel thành công, `phase2_status=SUCCESS`,
  đủ mã đã yêu cầu). Mặc định `phase2_test.py` không xoá gì, kể cả khi chạy
  thành công — không có instance nào bị xoá tự động ngoài ý muốn.
- Phase 3 (tích hợp vào worker hàng loạt của `WIN/LineViet`) chưa được triển
  khai — ngoài phạm vi hiện tại, xem [PLAN.md](PLAN.md).
