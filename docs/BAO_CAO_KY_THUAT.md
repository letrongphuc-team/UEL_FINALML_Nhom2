# Báo cáo kỹ thuật

## Dự báo doanh thu và nhu cầu nhập kho sản phẩm chăm sóc tóc

**Môn học:** Machine Learning  
**Nhóm thực hiện:** Nhóm 2

## 1. Mục tiêu và phạm vi

Đề tài xây dựng hệ thống dự báo chuỗi thời gian cho doanh thu và số lượng bán, kết hợp đề xuất nhập kho trong ứng dụng quản trị cửa hàng. Đầu ra gồm dự báo ba tháng và mười hai tuần, khả năng xem theo danh mục, cùng số lượng nhập hàng đề xuất cho từng sản phẩm.

Phạm vi mô hình tập trung vào hồi quy, đặc trưng trễ, đặc trưng lịch và đánh giá theo thứ tự thời gian. Hệ thống không sử dụng mô hình học sâu hoặc dịch vụ dự báo bên ngoài.

## 2. Dữ liệu

### 2.1. Nguồn và cấu trúc

Lịch sử giao dịch xuất phát từ Online Retail II, bộ dữ liệu giao dịch bán lẻ trực tuyến được công bố trên UCI Machine Learning Repository. Dữ liệu nguồn bao phủ giai đoạn 01/12/2009–09/12/2011. Nguồn: Chen, D. (2012), [Online Retail II](https://doi.org/10.24432/C5CG6D), giấy phép CC BY 4.0.

Dữ liệu sử dụng trong mô hình gồm 59 sản phẩm, 43.601 quan sát sản phẩm–ngày và được lưu tại bảng `Daily_Sales`. Các trường chính là ngày bán, mã sản phẩm, số lượng bán và doanh thu. Danh mục HairCare được ánh xạ từ mã sản phẩm của dữ liệu nguồn; trường `is_simulated` ghi nhận tính chất ánh xạ này. Việc ánh xạ không tạo thành bằng chứng về hoạt động bán hàng thực tế của thương hiệu.

Doanh thu nguồn sử dụng GBP. Giá và đơn hàng của ứng dụng quản trị sử dụng USD và được xử lý riêng; không quy đổi hoặc cộng gộp trực tiếp hai nguồn tiền tệ.

### 2.2. Tiền xử lý

Pipeline chuẩn hóa mã sản phẩm, kiểu dữ liệu ngày và các trường số; tổng hợp giao dịch hợp lệ về cấp sản phẩm–ngày; tạo bảng chuỗi thời gian nhất quán; kiểm tra trùng khóa, giá trị thiếu và giá trị không hợp lệ. Dữ liệu được tổng hợp thành tuần hoặc tháng trước khi xây dựng đặc trưng.

Tháng chưa hoàn tất ở thời điểm cuối dataset được loại khỏi đánh giá tháng. Mốc dữ liệu đầy đủ cuối cùng là 30/11/2011 đối với tháng và 04/12/2011 đối với tuần. Dữ liệu ngày có đến 09/12/2011.

Các bản dữ liệu và kết quả chuẩn bị được lưu trong `data/`. Trong quá trình vận hành, dữ liệu đầu vào được truy vấn từ MySQL; lỗi kết nối không kích hoạt cơ chế thay thế bằng CSV hoặc SQLite.

## 3. Phương pháp mô hình

### 3.1. Đặc trưng

Mô hình sử dụng giá trị trễ, trung bình trượt và độ lệch chuẩn tính từ các kỳ trước. Các đặc trưng lịch bao gồm tháng, tín hiệu mùa vụ sin/cos, số ngày trong kỳ và xu hướng thời gian. Toàn bộ thống kê theo biến mục tiêu được dịch ít nhất một kỳ để tránh sử dụng thông tin hiện tại hoặc tương lai.

Với mô hình tháng, bộ đặc trưng lịch sử ngắn sử dụng lag 1, 2, 3 và cửa sổ trung bình 3/6 tháng. Giá trị cùng kỳ năm trước được biểu diễn cùng cờ có dữ liệu để giữ các quan sát hợp lệ khi chưa đủ lịch sử một năm. Các phép chuẩn hóa được fit trên tập huấn luyện.

### 3.2. Mô hình sử dụng

- **Dự báo tháng:** hồi quy Ridge trên chuỗi tổng, kết hợp bộ đặc trưng lịch sử ngắn, `alpha=100` và biến đổi mục tiêu `log1p`. Kết quả được chuyển về thang đo bằng `expm1` và giới hạn dưới bằng 0. Dự báo tổng được phân bổ về sản phẩm theo tỷ trọng lịch sử.
- **Dự báo tuần:** CatBoost ở cấp sản phẩm, với mục tiêu trên thang đo gốc.
- **Mô hình đối chứng:** lag gần nhất, trung bình trượt và seasonal naive. Chương trình cũng hỗ trợ các ứng viên hồi quy và mô hình cây để so sánh trong quá trình huấn luyện.

Các mô hình đã fit được lưu trong `package.pkl`. Inference sử dụng package đã lưu và không gọi lại thao tác fit. Dự báo nhiều kỳ được thực hiện theo phương pháp đệ quy, nên mức sai số có thể tăng theo độ xa của kỳ dự báo.

## 4. Thiết kế đánh giá

### 4.1. Phân chia theo thời gian

Đánh giá sử dụng các mốc rolling validation, trong đó tập huấn luyện luôn đứng trước kỳ được dự báo. Tám mốc validation được sử dụng cho mỗi tần suất. Với dự báo tháng, các mốc validation trải từ tháng 3 đến tháng 10/2011; kỳ test là tháng 11/2011. Với tuần, validation và các kỳ test được kiểm tra để không chồng lấn.

Ứng viên được lựa chọn dựa trên validation, sau đó ghi nhận cấu hình lựa chọn trước khi đánh giá test. Bộ test hồi cứu đã được sử dụng trong bước chấp nhận mô hình triển khai; vì vậy kết quả test không được xem là một kiểm chứng độc lập hoàn toàn. Cần dữ liệu ngoài giai đoạn hiện có để đánh giá khả năng tổng quát hóa một cách độc lập.

### 4.2. Chỉ số

- **WAPE:** tổng sai số tuyệt đối chia cho tổng giá trị thực, nhân 100. Giá trị thấp hơn thể hiện sai số nhỏ hơn; không diễn giải WAPE thành phần trăm accuracy.
- **MAE:** trung bình sai số tuyệt đối, cùng đơn vị với biến mục tiêu.
- **RMSE:** căn bậc hai của trung bình bình phương sai số, nhạy hơn với sai số lớn.
- **SKU WAPE:** đo sai số ở cấp sản phẩm, giúp kiểm tra trường hợp sai số của các sản phẩm bù trừ khi tính ở cấp tổng.

## 5. Kết quả đánh giá

Các kết quả sau được tổng hợp từ `metadata.json`, `scores.csv` và `origin_scores.csv` của mô hình triển khai.

### 5.1. Dự báo tháng

**Số lượng bán:**

- Validation: total WAPE 13,38%; SKU WAPE 36,91%; MAE 8.944,27 đơn vị; RMSE 9.942,99 đơn vị.
- Test tháng 11/2011: total WAPE 27,39%; SKU WAPE 39,91%; MAE và RMSE tổng 28.315,15 đơn vị.

**Doanh thu:**

- Validation: total WAPE 10,84%; SKU WAPE 37,67%; MAE 15.302,83 GBP; RMSE 18.414,58 GBP.
- Test tháng 11/2011: total WAPE 33,60%; SKU WAPE 44,08%; MAE và RMSE tổng 82.323,30 GBP.

MAE và RMSE ở cấp tổng bằng nhau trong kỳ test tháng vì chỉ có một kỳ đánh giá; hai giá trị này không phải hai bằng chứng độc lập. Sai số ở cấp sản phẩm vẫn cao hơn sai số tổng và cần được xem xét khi ra quyết định nhập kho.

### 5.2. Dự báo tuần

Đối với số lượng bán, total WAPE ở kỳ test trước một tuần là 1,83% và trước hai tuần là 24,75%. Đối với doanh thu, hai giá trị tương ứng là 9,02% và 2,68%. Chênh lệch giữa các kỳ cho thấy kết quả phụ thuộc vào horizon và không thể sử dụng riêng kỳ tốt nhất để mô tả toàn bộ năng lực dự báo.

Ứng dụng cung cấp dự báo đến mười hai tuần và ba tháng; các chỉ số test nêu trên không đại diện cho toàn bộ horizon dài hạn. Kết quả chẩn đoán theo horizon được lưu trong thư mục `extensions` của mô hình và có phạm vi đánh giá hồi cứu riêng.

### 5.3. Phân tích độ nhạy và chẩn đoán bổ sung

Các phân tích này được tái lập bằng `python -m scripts.sensitivity_analysis` (kết quả ghi vào `runtime/analysis/`). Chúng không thay đổi mô hình đã chọn.

**Độ nhạy theo tham số alpha của RidgeShort (total WAPE, số lượng bán):**

| alpha | Validation | Test 11/2011 |
|---|---|---|
| 1 | 18,95% | 0,05% |
| 10 | 15,55% | 10,31% |
| 100 (triển khai) | 13,38% | 27,39% |
| 1000 | 12,65% | 32,25% |

Thứ hạng theo validation và theo test ngược chiều nhau: alpha tốt nhất trên validation lại cho sai số test cao nhất. Với 8 điểm validation và một tháng test, chênh lệch giữa các cấu hình chủ yếu phản ánh độ nhiễu của mẫu đánh giá nhỏ. Mô hình được giữ theo tiêu chí validation đã đóng băng trước test; không chọn lại alpha theo test. Các thử nghiệm giảm số đặc trưng, hiệu chỉnh smearing cho biến đổi log và refit trên toàn bộ dữ liệu khi retrain cũng không cải thiện kết quả một cách nhất quán.

**Dự báo tuần theo horizon (6 mốc hồi cứu, h1–12):** CatBoost triển khai có total WAPE trung bình 20,34% (số lượng) và 21,21% (doanh thu), với độ lệch dương tăng dần theo horizon do sai số tích lũy khi dự báo đệ quy. Kết hợp 50% CatBoost với 50% trung bình 4 tuần của từng sản phẩm cho 18,57% và 17,75%, độ lệch gần 0, nhưng kém hơn ở validation h1–2 (23,56% so với 20,56%). Phương án kết hợp được đề xuất sau khi đã xem kết quả test và các mốc h1–12 trùng giai đoạn validation, nên chỉ được trình bày như chẩn đoán, không thay thế mô hình triển khai.

## 6. Đề xuất nhập kho

Số lượng nhập kho được tính từ nhu cầu dự báo trong thời gian chờ hàng và kỳ xem xét tồn kho, cộng tồn kho an toàn, sau đó trừ tồn kho khả dụng và lượng hàng đang về:

```text
Tồn kho khả dụng = Tồn kho thực tế − Số lượng đã giữ
Số lượng đề xuất = max(0, làm tròn lên(Nhu cầu dự báo + Tồn kho an toàn
                                     − Tồn kho khả dụng − Hàng đang về))
```

Tồn kho an toàn được tính theo sai số dự báo của chính mô hình thay vì một hằng số chung:

```text
σ_SKU            = RMSE của dự báo tháng (số lượng) của sản phẩm trên các kỳ backtest
Tồn kho an toàn  = max(Tồn kho an toàn tối thiểu, z × σ_SKU × √((Lead time + Review period) / Số ngày trong tháng))
z                = phân vị chuẩn của mức phục vụ mục tiêu (mặc định 95% → z ≈ 1,645)
```

Backtest trên 6 tháng × 59 sản phẩm (σ chỉ lấy từ các kỳ trước tháng được đánh giá):

| Chính sách | Tồn kho an toàn TB/SKU | Tỷ lệ kỳ không thiếu hàng | Fill rate |
|---|---|---|---|
| Hằng số 15 đơn vị (trước đây) | 15 | 54,8% | 80,2% |
| max(15, z·σ), mục tiêu 95% | 843 | 88,1% | 94,1% |
| Hằng số cùng tổng lượng với z·σ | 843 | 92,4% | 93,3% |

Mức 15 đơn vị quá nhỏ so với nhu cầu trung bình khoảng 1.184 đơn vị/sản phẩm/tháng. Giá trị chính của công thức z·σ là gắn tồn kho an toàn với mục tiêu phục vụ và sai số đo được; khi so cùng tổng lượng hàng, phân bổ theo σ từng sản phẩm không vượt trội hằng số vì σ được ước lượng từ ít kỳ. Mức phục vụ thực tế 88% thấp hơn mục tiêu 95% cho thấy σ còn bị ước lượng thấp.

Các thông số thời gian chờ, kỳ xem xét, tồn kho an toàn tối thiểu và mức phục vụ (`service_level_pct`; giá trị 0 chỉ dùng tồn kho an toàn cố định) được đọc từ cấu hình MySQL. σ của từng sản phẩm được lưu cùng lần inference trong bảng `Forecast_Run`. Chức năng ghi nhận nhập hàng cập nhật tồn kho trong database; quản trị viên kiểm tra đề xuất và thực hiện đặt hàng với nhà cung cấp.

## 7. Tích hợp và kiểm thử

Giao diện gửi yêu cầu đến API Python. API xác thực tài khoản quản trị, truy vấn MySQL, thực hiện inference hoặc đọc dự báo đã lưu và trả kết quả cho trình duyệt. Các cập nhật tồn kho và trạng thái đơn hàng được xử lý bằng transaction.

Bộ kiểm thử tại `Tests/` kiểm tra đặc trưng không sử dụng thông tin tương lai; khả năng nạp model và tái lập dự báo; đơn vị và horizon; đối chiếu API với dữ liệu SQL; xác thực và CSRF; cập nhật tồn kho; hoàn tác khi yêu cầu không hợp lệ; tính ổn định của inference và xử lý khi database không khả dụng.

Các kiểm thử ghi dữ liệu sử dụng schema kiểm thử riêng. Hướng dẫn thực thi được trình bày trong README.md.

## 8. Giới hạn và hướng phát triển

Dữ liệu có thời gian quan sát hạn chế và nhãn sản phẩm HairCare được ánh xạ phục vụ học tập. Kết quả không chứng minh khả năng dự báo thị trường HairCare hiện tại. Sai số doanh thu tháng và sai số ở cấp sản phẩm còn đáng kể; các đề xuất nhập kho cần được quản trị viên xem xét theo điều kiện kinh doanh.

Tập test hồi cứu có số kỳ nhỏ và đã tham gia bước chấp nhận triển khai. Đề tài không khẳng định ưu thế thống kê hoặc khoảng dự báo đã được hiệu chỉnh. Hướng phát triển gồm bổ sung dữ liệu bán hàng thực tế, đánh giá trên giai đoạn mới, kiểm tra sai số theo nhóm sản phẩm và ước lượng σ ổn định hơn khi có thêm kỳ backtest, và đưa tiêu chí sai số nhiều horizon vào bước chọn mô hình trước khi xem dữ liệu test mới.
