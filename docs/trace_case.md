# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-014 --seed 11
python -m lab9.trace_case --shape fixed --claim C-014 --seed 11   # đối chứng
```

## Case: `seeded_regression C-014@11` (T14: hàng hỏng, trước đó đã hoàn phí ship)

Output trace (rút gọn):

```text
== seeded_regression C-014@11  lab8 grader: FAIL (proposal_mismatch)
   1 handoff            task=check_applicable_policy, 3 tool chỉ đọc
   2 investigator       O-515 visible, status delivered_late, delay_days=5
   3 quote              {"amount_vnd": 150000, "clause_ids": ["SHIP-LATE-03", "DMG-04"], ...}
   4 refund_history     {"refund_count": 1, "refunded_vnd": 30000}
   5 proposal           {"amount_vnd": 120000}
   6 reply              {"text": "Dạ, LogiX đã hoàn 120.000đ cho đơn O-515."}
-- ledger vs label
  refund_vnd     ledger=90000    label=120000   <-- differs
  status         result=resolved label=resolved
```

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-014@11`, report `E-seeded_regression-20261118-cadd` (eval set v1 `cadda455a241`), row `E-014@11` |
| Triệu chứng | `pass_rate` FAIL (`proposal_mismatch`), `refund_amount_correct` FAIL (ledger 90.000 ≠ nhãn 120.000), `reply_amount_matches_ledger` FAIL (reply nói 120.000đ, ledger ghi 90.000đ). Câu trả lời trôi chảy, lịch sự, nên judge giọng điệu sẽ cho PASS. Chỉ đọc ledger mới thấy khách nhận thiếu 30.000đ. |
| Bước sai đầu tiên | **Event 5 `proposal {"amount_vnd": 120000}`**: agent tự lấy `refunded_vnd=30000` ở event 4 trừ vào quote 150.000 rồi mới gọi `issue_refund`. Host vốn đã trừ phần đã hoàn nên trừ thêm lần nữa: 120.000 − 30.000 = 90.000 vào ledger. |
| Vì sao là bước *đầu tiên* | Event 1–3 đúng: handoff chỉ cấp tool đọc; investigator đọc đúng đơn O-515; event 3 quote 150.000 khớp `proposal_amount` của nhãn (lấy max clause, không cộng). Event 4 đọc lịch sử hoàn tiền chưa phải lỗi: `honest_refactor` cũng đọc mà vẫn đúng. Lỗi bắt đầu khi số đọc được bị **dùng để sửa đề xuất** ở event 5. Event 6 (reply sai) chỉ là triệu chứng hệ quả. |
| Failure mode | **FM-05** pre_deducted (layer tool/schema: hiểu sai hợp đồng của `issue_refund`, host đã tự trừ phần đã hoàn). Kèm hệ quả FM-08 (reply ≠ ledger). |
| Đối chứng | `fixed C-014@11`: event 3 quote 150.000 → **không có** event `refund_history`/`proposal` → host ghi 120.000 = nhãn; reply "đã hoàn 120.000đ" khớp ledger. Lab 8 PASS. |
| Regression test | `src/tests/test_lab9_trace_case.py::test_trace_case_c014_seeded_regression_is_caught` chạy lại trial, kiểm thứ tự `refund_history` trước `proposal`, ledger = 90.000, và `refund_amount_correct`, `reply_amount_matches_ledger`, `pass_rate` đều False. `test_trace_case_c014_fixed_is_clean` kiểm `fixed` sạch trên mọi grader. Cả hai qua (`pytest -q -m student -o addopts= -k "lab9 and not advanced"`). Item E-014 trong eval set có `failure_modes: [FM-04, FM-05, FM-08]`, `provenance.from_trace: R-seeded_regression-C-014-s11`. |
| Chưa chắc chắn | Trace không cho biết agent "định" trừ (do prompt) hay do hiểu sai mô tả tool. MockLLM tất định nên case chỉ chứng minh lỗi tái hiện ở mọi seed với variant này, không cho biết tần suất với model thật. Không có trace từ người dùng thật. |

## Case phụ đã đọc (dùng làm bằng chứng taxonomy)

| Trial | Bước sai đầu tiên | FM |
|---|---|---|
| `swarm C-002@55` | event 3 `model_override {"delay_days": 5}` đè `delay_days=2` của DB → hoàn 20.000đ, lẽ ra denied 0đ | FM-03 |
| `seeded_regression C-007@11` | sau `issue_refund` bị host từ chối (approval hết hạn), agent vẫn trả `resolved`, event 5 reply "đã hoàn 35.000đ", ledger 0 | FM-02 (+FM-08) |
| `seeded_regression C-013@11` | event 5 `proposal 0`: tự trừ 35.000 đã hoàn → tự từ chối, không qua host, thiếu `policy_denied` trong audit | FM-05 |
| `flaky_regression C-001@33` | event 4 `proposal 30.000`: làm tròn 35.000 xuống bội số 10.000 (chỉ ở seed 33) | FM-04 |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
