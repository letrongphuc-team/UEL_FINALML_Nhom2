# UEL_FINALML_Nhom2

## Dự báo doanh thu và nhu cầu nhập kho sản phẩm chăm sóc tóc

**Môn học:** Machine Learning  
**Nhóm thực hiện:** Nhóm 2  
**Ứng dụng:** FOLMARÉ HairCare Admin

Ứng dụng hỗ trợ quản trị cửa hàng, dự báo doanh thu và số lượng bán theo tuần/tháng, đồng thời đề xuất số lượng nhập kho dựa trên nhu cầu dự báo và tồn kho hiện có. Dữ liệu vận hành, lịch sử bán hàng và kết quả dự báo được lưu trữ trong MySQL.

## Chức năng

- Đăng nhập và xác thực tài khoản quản trị.
- Theo dõi doanh thu, đơn hàng và tình trạng tồn kho.
- Quản lý sản phẩm, đơn hàng, khách hàng, mã giảm giá và đánh giá.
- Dự báo doanh thu GBP hoặc số lượng bán, lọc theo danh mục và xuất CSV.
- Đề xuất nhập kho, ghi nhận hàng đang về và hàng đã nhận.

## Yêu cầu môi trường

- Python 3.14; phiên bản đã kiểm chứng: Python 3.14.8.
- MySQL Server 8.x.
- Trình duyệt Chrome, Edge, Firefox hoặc Safari.
- Các thư viện Python được liệt kê trong `requirements.txt`.

Node.js chỉ cần cho kiểm tra JavaScript và kiểm thử giao diện tự động. Trên macOS, LightGBM có thể cần thư viện OpenMP (`libomp`).

## Cài đặt

Thực hiện tại thư mục gốc dự án.

macOS/Linux:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Windows:

```bat
py -3.14 -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

### Cấu hình database

Tạo file `.env` theo mẫu `.env.example` và thiết lập các thông số:

```dotenv
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DATABASE=haircare_shop
MYSQL_USER=haircare_app
MYSQL_PASSWORD=your_mysql_password
MYSQL_CONNECT_TIMEOUT=10
```

Đối với máy đã có database `haircare_shop`, sử dụng cấu hình kết nối tương ứng. Đối với máy chưa có database, mở `connector/KHOI_TAO_DATABASE.sql` bằng MySQL Workbench và thực thi trên database trống. File SQL chứa cấu trúc bảng, dữ liệu bán hàng, danh mục sản phẩm và dữ liệu phục vụ ứng dụng.

Sau khi khởi tạo database lần đầu, đồng bộ kết quả dự báo bằng mô hình đã huấn luyện:

```sh
python -B main.py --refresh-forecast
```

Lệnh này đọc lịch sử bán hàng từ MySQL và ghi dự báo trở lại database; không huấn luyện lại mô hình.

## Khởi chạy

```sh
python -B main.py
```

Truy cập **http://127.0.0.1:4174/**.

Có thể mở trực tiếp `Start FOLMARE.command` trên macOS hoặc `Start FOLMARE.bat` trên Windows sau khi cài đặt môi trường.

Tài khoản quản trị phục vụ trình diễn:

- Email: `manager@haircare.local`
- Mật khẩu: `DemoAdmin123!`

Để thay đổi cổng:

```sh
python -B main.py --port 4180
```

## Tổ chức chương trình

```text
UEL_FINALML_Nhom2/
├── main.py              # Khởi động ứng dụng
├── UI/                  # Giao diện quản trị
├── Models/              # Huấn luyện, mô hình và kết quả đánh giá
├── api/                 # API và xử lý nghiệp vụ
├── connector/           # Kết nối MySQL và cấu trúc database
├── Tests/               # Kiểm thử tự động
├── data/                # Dữ liệu nghiên cứu và tiền xử lý
├── images/              # Ảnh sản phẩm
├── scripts/             # Phân tích và xuất kết quả mô hình
├── docs/                # Báo cáo kỹ thuật
├── requirements.txt     # Phiên bản thư viện
└── .env.example         # Mẫu cấu hình môi trường
```

`Models/active_plan_a.json` xác định mô hình sử dụng. `Models/active_analysis.json` xác định kết quả phân tích và các kỳ dự báo tương ứng. Thư mục mô hình tham chiếu phục vụ đánh giá và kiểm thử được giữ cùng mô hình triển khai.

## Kiểm thử

Sau khi kích hoạt môi trường Python:

```sh
python -B -m pytest Tests -q
```

Kiểm thử MySQL yêu cầu tài khoản có quyền tạo và xóa database kiểm thử. Các schema thử nghiệm có tên ngẫu nhiên, tách biệt với database ứng dụng.

Kiểm tra giao diện và JavaScript:

```sh
npm run build
npm run check
```

Kiểm thử giao diện tự động sử dụng database kiểm thử riêng:

```sh
npm install
npm run test:browser
```

Lệnh này cần Google Chrome hoặc trình duyệt Playwright đã cài đặt. Kết quả được tạo trong `Tests/artifacts/` khi chạy kiểm thử.

## Huấn luyện và phân tích mô hình

Huấn luyện từ dữ liệu MySQL:

```sh
python -m Models.research
```

Huấn luyện tạo một bộ kết quả mới và cập nhật mô hình khi đạt các điều kiện kiểm tra. Ứng dụng đã có mô hình được huấn luyện nên có thể khởi chạy mà không thực hiện bước này.

Xuất các hình phân tích từ mô hình đang sử dụng:

```sh
python -m scripts.export_figures
```

Phương pháp xử lý dữ liệu, đặc trưng, mô hình và kết quả đánh giá được trình bày trong [Báo cáo kỹ thuật](docs/BAO_CAO_KY_THUAT.md).

## Dữ liệu và phạm vi sử dụng

Lịch sử giao dịch sử dụng bộ dữ liệu **Online Retail II** và được ánh xạ với danh mục sản phẩm HairCare để phục vụ bài toán học tập. Cờ `is_simulated` được lưu trong database để nhận diện dữ liệu ánh xạ. Đây không phải lịch sử kinh doanh thực tế của thương hiệu HairCare.

Doanh thu của dữ liệu dự báo được tính bằng GBP; dữ liệu vận hành cửa hàng sử dụng USD. Các kỳ dự báo tiếp nối mốc lịch sử của dataset. Ứng dụng truy vấn MySQL trong quá trình vận hành; các file CSV đi kèm phục vụ nghiên cứu, tái lập và đánh giá mô hình.

Nguồn dữ liệu: Chen, D. (2012). *Online Retail II* [Dataset]. UCI Machine Learning Repository. [https://doi.org/10.24432/C5CG6D](https://doi.org/10.24432/C5CG6D). Giấy phép CC BY 4.0.
