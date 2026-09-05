from __future__ import annotations

from decimal import Decimal
from io import BytesIO

from openpyxl import load_workbook

from app.services.excel_export_service import (
    MONEY_FORMAT,
    SHEET_ENTITIES,
    SHEET_GROUP,
    SHEET_INPUT,
    SHEET_NAMES,
    SHEET_OUTPUT,
    SHEET_PREPAY,
    SHEET_PROJECTS,
    build_excel_workbook,
    content_disposition,
)


def _sample_data() -> dict:
    return {
        "metadata": {
            "scope": "ALL",
            "view": "current",
            "period": "2026-03",
            "generated_at": "2026-09-05T00:00:00+00:00",
            "business_fact_source": "analytics_canonical_facts_current",
            "statutory_source": "tax_period_states.current_run_id->calculation_runs->entity_vat_ledgers",
            "projection_source": "TAX_ENGINE / LEGAL_ENTITY_PROJECTION (non-filing-basis)",
            "legal_entity_count": 2,
        },
        "entities": [
            {
                "entity_code": "A01",
                "entity_name": "法人 A01",
                "business_role": "A",
                "period": "2026-03",
                "opening_input_credit": Decimal("100.00"),
                "output_vat": Decimal("1300.00"),
                "input_vat": Decimal("400.00"),
                "deductible_input_vat": Decimal("300.00"),
                "nondeductible_input_vat": Decimal("50.00"),
                "pending_input_vat": Decimal("50.00"),
                "tax_prepayment": Decimal("100.00"),
                "vat_payable": Decimal("700.00"),
                "closing_input_credit": Decimal("0.00"),
                "revenue_projection": Decimal("10000.00"),
                "book_cost_projection": Decimal("6000.00"),
                "accounting_profit_projection": Decimal("4000.00"),
                "gate_status": "FORMAL_READY",
                "projection_status": "READY",
            },
            {
                "entity_code": "B01",
                "entity_name": "法人 B01",
                "business_role": "B",
                "period": "2026-03",
                # Missing formal VAT must stay blank, never become a fake zero.
                "opening_input_credit": None,
                "output_vat": None,
                "input_vat": None,
                "deductible_input_vat": Decimal("0.00"),
                "nondeductible_input_vat": Decimal("0.00"),
                "pending_input_vat": Decimal("0.00"),
                "tax_prepayment": None,
                "vat_payable": None,
                "closing_input_credit": None,
                "revenue_projection": Decimal("2500.00"),
                "book_cost_projection": Decimal("1000.00"),
                "accounting_profit_projection": Decimal("1500.00"),
                "gate_status": "FORMAL_NOT_AVAILABLE",
                "projection_status": "READY",
            },
        ],
        "projects": [
            {
                "project_code": "P01",
                "project_name": "示例项目",
                "entity_code": "A01",
                "entity_name": "法人 A01",
                "contract_total": Decimal("12000.00"),
                "real_cost": Decimal("6000.00"),
                "output_invoice_amount": Decimal("11300.00"),
                "input_invoice_amount": Decimal("5650.00"),
                "paid_amount": Decimal("5000.00"),
                "four_flow_completeness": Decimal("100.00"),
                "four_flow_source": "CANONICAL_3_FLOW_LINKAGE",
                "risk_rating": "低",
                "status": "READY",
            }
        ],
        "output_invoices": [
            {
                "invoice_date": "2026-03-05",
                "entity_code": "A01",
                "entity_name": "法人 A01",
                "project_code": "P01",
                "project_name": "示例项目",
                "contract_no": "HT-OUT-01",
                "invoice_no": "OUT-001",
                "counterparty_name": "客户甲",
                "summary": "建筑服务",
                "net_amount": Decimal("10000.00"),
                "vat_amount": Decimal("1300.00"),
                "gross_amount": Decimal("11300.00"),
                "fact_status": "CURRENT_CANONICAL",
                "fact_id": 101,
            }
        ],
        "input_invoices": [
            {
                "invoice_date": "2026-03-10",
                "entity_code": "A01",
                "entity_name": "法人 A01",
                "project_code": "P01",
                "project_name": "示例项目",
                "contract_no": "HT-IN-01",
                "invoice_no": "IN-001",
                "counterparty_name": "供应商乙",
                "summary": "材料采购",
                "net_amount": Decimal("5000.00"),
                "vat_amount": Decimal("650.00"),
                "gross_amount": Decimal("5650.00"),
                "deductibility_status": "可抵扣",
                "fact_status": "CURRENT_CANONICAL",
                "fact_id": 102,
            }
        ],
        "prepayments": [
            {
                "prepayment_date": "2026-03-20",
                "entity_code": "A01",
                "entity_name": "法人 A01",
                "project_code": "P01",
                "receipt_no": "TAX-001",
                "tax_authority": "",
                "tax_type": "VAT",
                "prepayment_amount": Decimal("100.00"),
                "note": "项目预缴",
            }
        ],
    }


def test_workbook_contains_all_six_required_sheets_and_professional_tables():
    content = build_excel_workbook(_sample_data())
    assert content[:2] == b"PK"

    wb = load_workbook(BytesIO(content), data_only=False)
    assert wb.sheetnames == SHEET_NAMES
    assert wb[SHEET_GROUP]["A1"].value == "集团税务与经营 Excel 总览"
    assert wb[SHEET_ENTITIES]["A1"].value == "法人编码"
    assert wb[SHEET_PROJECTS]["A1"].value == "项目编号"
    assert wb[SHEET_OUTPUT]["A1"].value == "开票日期"
    assert wb[SHEET_INPUT]["A1"].value == "开票日期"
    assert wb[SHEET_PREPAY]["A1"].value == "预缴日期"

    for sheet_name in SHEET_NAMES:
        ws = wb[sheet_name]
        assert ws.sheet_view.showGridLines is False
        assert ws.freeze_panes is not None


def test_money_format_and_formal_missing_value_are_preserved():
    wb = load_workbook(BytesIO(build_excel_workbook(_sample_data())))
    entities = wb[SHEET_ENTITIES]

    assert entities["E2"].value == 100
    assert entities["E2"].number_format == MONEY_FORMAT
    assert entities["F2"].value == 1300
    assert entities["F2"].number_format == MONEY_FORMAT

    # B01 has no formal statutory resource: the workbook must retain blanks.
    assert entities["E3"].value is None
    assert entities["F3"].value is None
    assert entities["L3"].value is None
    assert entities["Q3"].value == "FORMAL_NOT_AVAILABLE"


def test_invoice_and_prepayment_details_are_not_collapsed_into_summary_only():
    wb = load_workbook(BytesIO(build_excel_workbook(_sample_data())))
    assert wb[SHEET_OUTPUT]["G2"].value == "OUT-001"
    assert wb[SHEET_OUTPUT]["K2"].value == 1300
    assert wb[SHEET_INPUT]["G2"].value == "IN-001"
    assert wb[SHEET_INPUT]["M2"].value == "可抵扣"
    assert wb[SHEET_PREPAY]["E2"].value == "TAX-001"
    assert wb[SHEET_PREPAY]["H2"].value == 100


def test_content_disposition_keeps_ascii_fallback_and_utf8_filename():
    header = content_disposition("税务经营全量导出_ALL_current_2026-03.xlsx")
    assert "filename=tax-export.xlsx" in header
    assert "filename*=UTF-8''" in header
    assert "%E7%A8%8E%E5%8A%A1" in header
