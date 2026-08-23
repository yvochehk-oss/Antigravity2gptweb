"""Tax extraction schemas and type-to-query mapping for RAG system.

This module defines the schemas and query templates used to extract
structured tax-related data (invoices, contracts, payments) from document
chunks via AI-powered extraction.
"""
from pydantic import BaseModel, Field
from typing import Literal


# ============================================================
# Extraction type definitions
# ============================================================

EXTRACT_TYPES = Literal["invoice", "contract", "payment", "tax_payment"]

# Query templates: maps extract_type → domain-specific search query
EXTRACT_QUERY_TEMPLATES: dict[EXTRACT_TYPES, str] = {
    "invoice": "增值税发票 专用发票 普通发票 发票号码 价税合计 税额",
    "contract": "合同 合同编号 合同金额 甲乙双方 签订日期",
    "payment": "付款 银行回单 收款人 付款人 付款金额 付款日期",
    "tax_payment": "完税凭证 缴纳税款 税票 所属期 实缴金额 纳税人识别号",
}

# Document type filters: maps extract_type → list of expected document_type values
EXTRACT_DOC_TYPE_FILTERS: dict[EXTRACT_TYPES, list[str]] = {
    "invoice": ["invoice", "receipt"],
    "contract": ["contract", "main_contract", "subcontract", "labor_contract", "material_contract"],
    "payment": ["payment", "bank_receipt", "transfer_record"],
    "tax_payment": ["tax_receipt", "tax_payment", "duty_receipt"],
}

# Business category filters
EXTRACT_CATEGORY_FILTERS: dict[EXTRACT_TYPES, list[str]] = {
    "invoice": ["material", "labor", "equipment", "subcontract", "service"],
    "contract": ["material", "labor", "equipment", "subcontract", "service"],
    "payment": [],
    "tax_payment": [],
}


# ============================================================
# Request / Response schemas
# ============================================================

class ExtractTaxRequest(BaseModel):
    """Request schema for tax data extraction from RAG documents."""

    project_id: int | None = Field(default=None, description="RAG project ID")
    project_code: str | None = Field(default=None, description="RAG project code")
    extract_type: EXTRACT_TYPES = Field(
        ..., description="Type of tax data to extract: invoice | contract | payment | tax_payment"
    )
    period_start: str | None = Field(
        default=None, description="Filter: period start (YYYY-MM)"
    )
    period_end: str | None = Field(
        default=None, description="Filter: period end (YYYY-MM)"
    )
    entity_code: str | None = Field(
        default=None, description="Filter: entity code of the buyer/payer"
    )
    counterparty_code: str | None = Field(
        default=None, description="Filter: counterparty code of the seller/receiver"
    )
    top_k: int = Field(
        default=30, ge=1, le=100,
        description="Number of document chunks to retrieve and extract from"
    )


class ExtractedFieldsInvoice(BaseModel):
    """Structured fields extracted from an invoice document."""
    invoice_no: str | None = Field(default=None, description="发票号码")
    invoice_date: str | None = Field(default=None, description="开票日期 YYYY-MM-DD")
    period: str | None = Field(default=None, description="所属期 YYYY-MM")
    direction: Literal["in", "out", ""] = Field(default="", description="in=进项票 out=销项票")
    invoice_type: Literal["special", "normal", "roll", ""] = Field(
        default="", description="专票special / 普票normal / 卷票roll"
    )
    seller_name: str | None = Field(default=None, description="销货单位名称")
    seller_code: str | None = Field(default=None, description="销货单位纳税人识别号")
    buyer_name: str | None = Field(default=None, description="购货单位名称")
    buyer_code: str | None = Field(default=None, description="购货单位纳税人识别号")
    total_amount: float | None = Field(default=None, description="价税合计（含税金额）")
    net_amount: float | None = Field(default=None, description="不含税金额")
    vat_amount: float | None = Field(default=None, description="税额")
    vat_rate: float | None = Field(default=None, description="税率 (0.01=1%, 0.06=6%, 0.09=9%, 0.13=13%)")
    deductible: bool | None = Field(default=None, description="是否可抵扣")
    category: str | None = Field(default=None, description="业务类别: material/labor/equipment/subcontract")
    note: str | None = Field(default=None, description="备注")


class ExtractedFieldsContract(BaseModel):
    """Structured fields extracted from a contract document."""
    contract_no: str | None = Field(default=None, description="合同编号")
    contract_date: str | None = Field(default=None, description="签订日期 YYYY-MM-DD")
    contract_type: str | None = Field(
        default=None, description="合同类型: main_contract/subcontract/labor_contract/material_contract"
    )
    party_a_name: str | None = Field(default=None, description="甲方（发包方）名称")
    party_a_code: str | None = Field(default=None, description="甲方纳税人识别号")
    party_b_name: str | None = Field(default=None, description="乙方（承包方）名称")
    party_b_code: str | None = Field(default=None, description="乙方纳税人识别号")
    total_amount: float | None = Field(default=None, description="合同总金额")
    tax_included: bool = Field(default=True, description="是否含税")
    category: str | None = Field(
        default=None, description="业务类别: material/labor/equipment/subcontract/service"
    )
    note: str | None = Field(default=None, description="备注")


class ExtractedFieldsPayment(BaseModel):
    """Structured fields extracted from a payment/bank receipt document."""
    payment_date: str | None = Field(default=None, description="付款日期 YYYY-MM-DD")
    period: str | None = Field(default=None, description="所属期 YYYY-MM")
    direction: Literal["in", "out", ""] = Field(default="", description="in=收款 out=付款")
    payer_name: str | None = Field(default=None, description="付款方名称")
    payer_account: str | None = Field(default=None, description="付款方银行账号")
    payee_name: str | None = Field(default=None, description="收款方名称")
    payee_account: str | None = Field(default=None, description="收款方银行账号")
    amount: float | None = Field(default=None, description="付款金额")
    payment_method: str | None = Field(
        default=None, description="付款方式: bank_transfer/credit/cash/other"
    )
    counterparty_code: str | None = Field(default=None, description="交易对手纳税人识别号")
    contract_no: str | None = Field(default=None, description="关联合同编号")
    note: str | None = Field(default=None, description="备注")


class ExtractedFieldsTaxPayment(BaseModel):
    """Structured fields extracted from a tax payment receipt."""
    tax_type: str | None = Field(
        default=None, description="税种: VAT/income/cit/etc"
    )
    tax_period: str | None = Field(default=None, description="税款所属期 YYYY-MM")
    payment_date: str | None = Field(default=None, description="实缴日期 YYYY-MM-DD")
    taxpayer_name: str | None = Field(default=None, description="纳税人名称")
    taxpayer_code: str | None = Field(default=None, description="纳税人识别号")
    tax_amount: float | None = Field(default=None, description="实缴金额")
    principal_amount: float | None = Field(default=None, description="本金")
    penalty_amount: float | None = Field(default=None, description="滞纳金/罚款")
    receipt_no: str | None = Field(default=None, description="完税凭证编号")
    note: str | None = Field(default=None, description="备注")


class ExtractedItem(BaseModel):
    """Single extracted item from one document chunk."""
    source_chunk_id: int = Field(..., description="来源 RAG chunk ID")
    source_document_id: int = Field(..., description="来源 RAG document ID")
    filename: str = Field(..., description="来源文件名")
    page_start: int | None = Field(default=None, description="起始页码")
    page_end: int | None = Field(default=None, description="终止页码")
    confidence: float = Field(
        ...,
        ge=0.0, le=1.0,
        description="AI 抽取置信度 0-1"
    )
    extract_type: EXTRACT_TYPES = Field(..., description="抽取数据类型")
    fields: dict = Field(..., description="结构化抽取字段")


class ExtractTaxResponse(BaseModel):
    """Response schema for tax data extraction."""
    project_id: int = Field(..., description="RAG project ID")
    extract_type: EXTRACT_TYPES = Field(..., description="抽取数据类型")
    query_used: str = Field(..., description="实际使用的检索 query")
    total_chunks: int = Field(default=0, description="检索到的文档块数量")
    total_extracted: int = Field(default=0, description="成功抽取的记录数")
    extracted_items: list[ExtractedItem] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list, description="抽取异常信息")
    llm_available: bool = Field(..., description="LLM 是否可用")
