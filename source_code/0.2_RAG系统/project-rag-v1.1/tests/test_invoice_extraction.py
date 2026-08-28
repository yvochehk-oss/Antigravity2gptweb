from app.services.extractor import extract_invoice_fields_from_text, validate_invoice_fields

OCR_PREFIX = (
    "增值税专用发票 发票代码: 110019999999 发票号码: 12345678 "
    "开票日期: 2026年08月20日 销方名称: 甲建筑有限公司 "
    "销方纳税人识别号: 91510100000000001 购方名称: 乙建设有限公司 "
    "购方纳税人识别号: 91510100000000002 合计 12,000,000 1,080,000 "
    "价税合计: 13,080,000 税率: "
)


def test_invoice_ocr_rejects_inconsistent_declared_rate():
    fields = extract_invoice_fields_from_text(OCR_PREFIX + "13% 可抵扣: 是")

    assert fields["invoice_no"] == "12345678"
    assert fields["invoice_code"] == "110019999999"
    assert fields["invoice_date"] == "2026-08-20"
    assert fields["seller_name"] == "甲建筑有限公司"
    assert fields["buyer_name"] == "乙建设有限公司"
    assert fields["net_amount"] == 12_000_000
    assert fields["vat_amount"] == 1_080_000
    assert fields["total_amount"] == 13_080_000
    assert fields["validation_status"] == "PENDING_REVIEW"
    assert fields["arithmetic_validation"]["vat_equals_net_times_rate"] is False
    assert any("税额与不含税金额" in error for error in fields["validation_errors"])


def test_invoice_ocr_accepts_consistent_nine_percent_rate_and_keeps_evidence():
    fields = extract_invoice_fields_from_text(OCR_PREFIX + "9% 可抵扣: 是")

    assert fields["validation_status"] == "VALID"
    assert fields["arithmetic_validation"] == {
        "gross_equals_net_plus_vat": True,
        "computed_gross": 13_080_000,
        "vat_equals_net_times_rate": True,
        "computed_vat": 1_080_000,
        "vat_rate": 0.09,
    }
    for key in (
        "invoice_no", "invoice_code", "invoice_date", "seller_name",
        "seller_tax_id", "buyer_name", "buyer_tax_id", "net_amount",
        "vat_amount", "total_amount", "vat_rate", "deductible",
    ):
        assert fields["evidence"].get(key), key


def test_invoice_validation_requires_document_identity_and_does_not_use_filename():
    fields = validate_invoice_fields({
        "invoice_no": "invoice_12345678.pdf",
        "net_amount": 100,
        "vat_amount": 9,
        "total_amount": 109,
        "vat_rate": 0.09,
    })

    assert fields["validation_status"] == "PENDING_REVIEW"
    assert any("invoice_code" in error for error in fields["validation_errors"])
    assert any("seller_name" in error for error in fields["validation_errors"])
    assert any("buyer_tax_id" in error for error in fields["validation_errors"])
