# Judge audit

Chạy (trong `src/`):

```bash
python -m lab9.judge --csv data/eval/judge_tone_public.csv --audit 10
python -m lab9.judge --csv data/eval/judge_helpful_public.csv --audit 21
```

Positive = **FAIL**, vì việc của judge là tìm lỗi. Backend: replay (kết quả judge lưu sẵn trong CSV, không cần key).

| Tiêu chí | n | κ | Precision | Recall | Ma trận (tp/fp/fn/tn) | Tỉ lệ FAIL quan sát → hiệu chỉnh RG | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | 24 / 4 / 6 / 66 | 0.28 → 0.30 | **Đạt** | **dùng** cho metric giọng điệu (không chặn tiền) |
| helpful | 100 | 0.375 | 1.000 | 0.300 | 9 / 0 / 21 / 70 | 0.09 → 0.30 | **Trượt** (κ, R) | **loại**, không dùng làm gate |

Rogan–Gladen: π̂ = (p_obs + Sp − 1) / (Se + Sp − 1). Với helpful: (0.09 + 1 − 1) / (0.30 + 1 − 1) = 0.30. Judge báo 9% fail ("91% helpful"), nhưng tỉ lệ lỗi thật ước lượng khoảng **30%**.

### Vì sao `helpful` bị loại

Recall theo loại lỗi người ghi (`human_failure` trong CSV):

| Loại lỗi (người) | Số FAIL | Judge bắt được |
|---|---|---|
| sai tiền | 18 | **0** |
| sai xưng hô | 3 | 0 |
| giọng cụt | 4 | 4 |
| không hỏi lại mã đơn | 3 | 3 |
| trả lời lạc đề | 2 | 2 |

- Judge **không bắt được lỗi tiền nào** (0/18), dù đây là lỗi đắt nhất. Rubric "helpful" không có tiêu chí số tiền, và judge chỉ đọc câu chữ, không đọc ledger. Ví dụ HL-016, HL-028, HL-096: câu xin lỗi dài, rất lịch sự, nhưng số tiền nói ra khác ledger (50.000đ so với 35.000đ). Đây là **verbosity bias**: judge thưởng lời xin lỗi dài.
- Accuracy 0.79 trông ổn nhưng che recall 0.30. Precision 1.0 chỉ cho biết khi judge nói FAIL thì đúng, không cho biết nó bỏ sót bao nhiêu.
- Nếu dùng judge này làm gate, 91% "helpful" sẽ cho qua phần lớn regression sai tiền. Lỗi tiền đã được chấm bằng code: `refund_amount_correct` (ledger vs nhãn) và `reply_amount_matches_ledger` (FM-08: số trong reply vs ledger).

## ≥5 case audit thủ công (tone_ok, cả 10 bất đồng)

| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú / sửa rubric |
|---|---|---|---|---|---|
| 1 | H-008 "Em đã hoàn tiền cho anh rồi nha…" | PASS | FAIL | Người | Xưng "em" + "nha" quá suồng sã với khách khiếu nại. Rubric thêm: xưng hô chuẩn "anh/chị – LogiX", cấm "em/bạn" và tiểu từ "nha/nhé". Thêm H-008 làm ví dụ FAIL. |
| 2 | H-017 "Dạ, bạn ơi LogiX đã hoàn 25.000đ…" | PASS | FAIL | Người | Cùng lỗi xưng hô ("bạn"). Gộp vào tiêu chí xưng hô ở #1. |
| 3 | H-029 "Dạ, vui lòng chờ… không cần nhắn thêm." | PASS | FAIL | Người | Judge chỉ thấy "Dạ, vui lòng". Rubric thêm: câu mệnh lệnh hoặc chặn khách liên hệ ("không cần nhắn thêm") là FAIL dù có kính ngữ. |
| 4 | H-042 "Dạ, đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ." | FAIL | PASS | Người | Judge phạt vì "thiếu xin lỗi". Rubric ghi rõ: **không bắt buộc xin lỗi**, câu ngắn đủ ý là PASS (giảm verbosity bias). |
| 5 | H-051 "Dạ, LogiX đã refund 90.000đ…" | FAIL | PASS | Người | CSKH LogiX cho phép từ "refund". Thêm vào rubric danh sách từ mượn được phép. |
| 6 | H-063 "…đã hoàn 20.000đ và sẽ hoàn thêm cho anh/chị ạ." | PASS | FAIL | Người | Hứa hoàn thêm ngoài kết quả hệ thống là lỗi **nội dung** chứ không phải giọng. Tách khỏi tone thành check code: reply không được hứa số tiền/hành động ngoài ledger (mở rộng `reply_amount_matches_ledger`). |
| 7 | H-070 "…cho đơn O-909 ạ 😂😂" | PASS | FAIL | Người | Rubric thêm: không dùng emoji trong phản hồi khiếu nại. Có thể chặn bằng code (regex emoji) trước khi gọi judge. |
| 8 | H-081 "Dạ, đơn O-107 chưa đủ điều kiện hoàn…" | FAIL | PASS | **Judge** (nhãn người sai) | Người đọc nhầm ticket. **Sửa nhãn**, ghi lý do vào log. Bất đồng không phải lúc nào cũng là lỗi của judge. |
| 9 | H-088 "Dạ, dạ, LogiX đã hoàn 40.000đ…" | FAIL | PASS | Người (gần hòa) | Lặp "Dạ" do lỗi gõ, không phải lỗi giọng. Rubric ghi: lỗi gõ nhỏ không đổi nghĩa thì không FAIL tone. |
| 10 | H-094 "Da, LogiX da hoan 40.000đ cho don O-341…" | PASS | FAIL | Người | Mất dấu tiếng Việt (dịch máy). Rubric thêm tiêu chí: tiếng Việt có dấu đầy đủ. |

Tổng kết: 8/10 người đúng, 1/10 judge đúng (nhãn người sai), 1/10 gần hòa. FN chủ yếu do judge coi suồng sã là "thân thiện" (#1, #2, #10), FP do judge phạt câu ngắn hoặc từ mượn (#4, #5).

## Kết luận

- **tone_ok: dùng được** (κ 0.757, P 0.857, R 0.800, đều đạt sàn). Chỉ dùng cho giọng điệu, metric năng lực không chặn tiền. Mọi kết quả judge phải ghi model + version.
- **helpful: loại.** κ 0.375 < 0.6 và R 0.300 < 0.7, bắt 0/18 lỗi tiền. Không dùng làm gate. Thay bằng các tiêu chí tách riêng: tiền bằng code (`refund_amount_correct`, `reply_amount_matches_ledger`), giọng bằng `tone_ok` đã calibrate, "hỏi lại mã đơn" bằng code (status `needs_info`).
- Rubric chỉ được sửa trên tập public. Sau khi sửa phải chấm lại trên tập held-out mới **một lần**, không tune trên held-out.
- **Rủi ro còn lại**: judge chỉ được kiểm trên dữ liệu replay đã công khai (100 mẫu/tiêu chí, 30 FAIL). Cần nhãn người mới để có held-out thật. Chưa đo order swap / family swap vì backend là replay.
