"""Tax extraction schemas and type-to-query mapping for RAG system.

This module defines the schemas and query templates used to extract
structured tax-related data (invoices, contracts, payments) from document
chunks via AI-powered extraction.
"""
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Literal
import re

# Keep extraction schemas usable by worker/CLI processes without requiring an
# ORM database driver.  The explicit namespace mirrors app.models.
_CANONICAL_ENTITY_CODES = frozenset(
    {f"A{i:02d}" for i in range(1, 12)}
    | {f"B{i:02d}" for i in range(1, 11)}
    | {f"C{i:02d}" for i in range(1, 3)}
    | {f"D{i:02d}" for i in range(1, 4)}
)
_CANONICAL_ENTITY_CODE_RE = re.compile(r"^(?:A(?:0[1-9]|1[01])|B(?:0[1-9]|10)|C(?:0[1-2])|D(?:0[1-3]))$")


def normalize_entity_code(value: str | None) -> str | None:
    value = "" if value is None else str(value).strip().upper()
    return value or None


def is_canonical_entity_code(value: str | None) -> bool:
    code = normalize_entity_code(value)
    return bool(code and code in _CANONICAL_ENTITY_CODES and _CANONICAL_ENTITY_CODE_RE.fullmatch(code))


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


def _validate_extracted_entity_code(value: str | None) -> str | None:
    """Reject virtual/entity-role labels in AI extraction output."""
    if value is None or not str(value).strip():
        return None
    code = normalize_entity_code(value)
    if not is_canonical_entity_code(code):
        raise ValueError("extracted entity_code must be A01-A11/B01-B10/C01-C02/D01-D03")
    return code


class PartyReference(BaseModel):
    """Evidence-backed party identity used by normalized tax extraction.

    ``entity_code`` is the canonical company key; ``tax_id`` is the tax
    identifier; ``account`` and ``bank_reference`` are payment evidence and
    are intentionally separate fields.  None of these fields is inferred
    from another field.
    """

    entity_code: str | None = None
    tax_id: str | None = None
    name: str | None = None
    account: str | None = None
    bank_reference: str | None = None

    _validate_entity_code = field_validator("entity_code")(_validate_extracted_entity_code)


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

    @field_validator("entity_code")
    @classmethod
    def validate_entity_filter(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        code = normalize_entity_code(value)
        if not is_canonical_entity_code(code):
            raise ValueError("entity_code must be a canonical real-company code, not A/B/C/D")
        return code

    @field_validator("counterparty_code")
    @classmethod
    def reject_virtual_counterparty(cls, value: str | None) -> str | None:
        if value is not None and value.strip() in {"A", "B", "C", "D", "甲", "乙", "丙", "丁"}:
            raise ValueError("virtual role/placeholder is not a counterparty identity")
        return value


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
    seller_entity_code: str | None = Field(default=None, description="销货单位 canonical entity_code")
    seller_tax_id: str | None = Field(default=None, description="销货单位纳税人识别号/统一社会信用代码")
    seller_account: str | None = Field(default=None, description="销货单位银行账号（若凭证明确提供）")
    seller_bank_reference: str | None = Field(default=None, description="销货单位银行流水/回单参考号")
    # Deprecated input name retained for compatibility.  It means tax id,
    # never an entity code and never a bank account.
    seller_code: str | None = Field(default=None, description="销货单位纳税人识别号")
    buyer_name: str | None = Field(default=None, description="购货单位名称")
    buyer_entity_code: str | None = Field(default=None, description="购货单位 canonical entity_code")
    buyer_tax_id: str | None = Field(default=None, description="购货单位纳税人识别号/统一社会信用代码")
    buyer_account: str | None = Field(default=None, description="购货单位银行账号（若凭证明确提供）")
    buyer_bank_reference: str | None = Field(default=None, description="购货单位银行流水/回单参考号")
    buyer_code: str | None = Field(default=None, description="购货单位纳税人识别号")
    total_amount: float | None = Field(default=None, description="价税合计（含税金额）")
    net_amount: float | None = Field(default=None, description="不含税金额")
    vat_amount: float | None = Field(default=None, description="税额")
    vat_rate: float | None = Field(default=None, description="税率 (0.01=1%, 0.06=6%, 0.09=9%, 0.13=13%)")
    deductible: bool | None = Field(default=None, description="是否可抵扣")
    category: str | None = Field(default=None, description="业务类别: material/labor/equipment/subcontract")
    note: str | None = Field(default=None, description="备注")

    _validate_seller_entity_code = field_validator("seller_entity_code")(_validate_extracted_entity_code)
    _validate_buyer_entity_code = field_validator("buyer_entity_code")(_validate_extracted_entity_code)

    @model_validator(mode="after")
    def normalize_invoice_party_ids(self):
        if self.seller_tax_id is None and self.seller_code:
            self.seller_tax_id = self.seller_code
        if self.buyer_tax_id is None and self.buyer_code:
            self.buyer_tax_id = self.buyer_code
        return self


class ExtractedFieldsContract(BaseModel):
    """Structured fields extracted from a contract document."""
    contract_no: str | None = Field(default=None, description="合同编号")
    contract_date: str | None = Field(default=None, description="签订日期 YYYY-MM-DD")
    contract_type: str | None = Field(
        default=None, description="合同类型: main_contract/subcontract/labor_contract/material_contract"
    )
    party_a_name: str | None = Field(default=None, description="甲方（发包方）名称")
    party_a_entity_code: str | None = Field(default=None, description="甲方 canonical entity_code")
    party_a_tax_id: str | None = Field(default=None, description="甲方纳税人识别号")
    party_a_code: str | None = Field(default=None, description="甲方纳税人识别号")
    party_b_name: str | None = Field(default=None, description="乙方（承包方）名称")
    party_b_entity_code: str | None = Field(default=None, description="乙方 canonical entity_code")
    party_b_tax_id: str | None = Field(default=None, description="乙方纳税人识别号")
    party_b_code: str | None = Field(default=None, description="乙方纳税人识别号")
    total_amount: float | None = Field(default=None, description="合同总金额")
    tax_included: bool = Field(default=True, description="是否含税")
    category: str | None = Field(
        default=None, description="业务类别: material/labor/equipment/subcontract/service"
    )
    note: str | None = Field(default=None, description="备注")

    _validate_party_a_entity_code = field_validator("party_a_entity_code")(_validate_extracted_entity_code)
    _validate_party_b_entity_code = field_validator("party_b_entity_code")(_validate_extracted_entity_code)

    @model_validator(mode="after")
    def normalize_contract_party_ids(self):
        if self.party_a_tax_id is None and self.party_a_code:
            self.party_a_tax_id = self.party_a_code
        if self.party_b_tax_id is None and self.party_b_code:
            self.party_b_tax_id = self.party_b_code
        return self


class ExtractedFieldsPayment(BaseModel):
    """Structured fields extracted from a payment/bank receipt document."""
    payment_date: str | None = Field(default=None, description="付款日期 YYYY-MM-DD")
    period: str | None = Field(default=None, description="所属期 YYYY-MM")
    direction: Literal["in", "out", ""] = Field(default="", description="in=收款 out=付款")
    payer_name: str | None = Field(default=None, description="付款方名称")
    payer_entity_code: str | None = Field(default=None, description="付款方 canonical entity_code")
    payer_tax_id: str | None = Field(default=None, description="付款方纳税人识别号")
    payer_account: str | None = Field(default=None, description="付款方银行账号")
    payer_bank_reference: str | None = Field(default=None, description="付款方银行流水/回单参考号")
    payee_name: str | None = Field(default=None, description="收款方名称")
    payee_entity_code: str | None = Field(default=None, description="收款方 canonical entity_code")
    payee_tax_id: str | None = Field(default=None, description="收款方纳税人识别号")
    payee_account: str | None = Field(default=None, description="收款方银行账号")
    payee_bank_reference: str | None = Field(default=None, description="收款方银行流水/回单参考号")
    amount: float | None = Field(default=None, description="付款金额")
    net_amount: float | None = Field(default=None, description="净支付金额（不含手续费等明确可分项）")
    payment_method: str | None = Field(
        default=None, description="付款方式: bank_transfer/credit/cash/other"
    )
    counterparty_entity_code: str | None = Field(default=None, description="交易对手 canonical entity_code")
    counterparty_tax_id: str | None = Field(default=None, description="交易对手纳税人识别号")
    # Deprecated field retained as a tax-id alias.  It is not an entity code.
    counterparty_code: str | None = Field(default=None, description="交易对手纳税人识别号（兼容字段）")
    contract_no: str | None = Field(default=None, description="关联合同编号")
    note: str | None = Field(default=None, description="备注")

    _validate_payer_entity_code = field_validator("payer_entity_code")(_validate_extracted_entity_code)
    _validate_payee_entity_code = field_validator("payee_entity_code")(_validate_extracted_entity_code)
    _validate_counterparty_entity_code = field_validator("counterparty_entity_code")(_validate_extracted_entity_code)

    @model_validator(mode="after")
    def normalize_payment_party_ids(self):
        if self.counterparty_tax_id is None and self.counterparty_code:
            self.counterparty_tax_id = self.counterparty_code
        return self


class ExtractedFieldsTaxPayment(BaseModel):
    """Structured fields extracted from a tax payment receipt."""
    tax_type: str | None = Field(
        default=None, description="税种: VAT/income/cit/etc"
    )
    tax_period: str | None = Field(default=None, description="税款所属期 YYYY-MM")
    payment_date: str | None = Field(default=None, description="实缴日期 YYYY-MM-DD")
    taxpayer_name: str | None = Field(default=None, description="纳税人名称")
    taxpayer_entity_code: str | None = Field(default=None, description="纳税人 canonical entity_code")
    taxpayer_tax_id: str | None = Field(default=None, description="纳税人纳税人识别号")
    taxpayer_code: str | None = Field(default=None, description="纳税人识别号")
    tax_amount: float | None = Field(default=None, description="实缴金额")
    principal_amount: float | None = Field(default=None, description="本金")
    penalty_amount: float | None = Field(default=None, description="滞纳金/罚款")
    receipt_no: str | None = Field(default=None, description="完税凭证编号")
    note: str | None = Field(default=None, description="备注")

    _validate_taxpayer_entity_code = field_validator("taxpayer_entity_code")(_validate_extracted_entity_code)

    @model_validator(mode="after")
    def normalize_taxpayer_id(self):
        if self.taxpayer_tax_id is None and self.taxpayer_code:
            self.taxpayer_tax_id = self.taxpayer_code
        return self


def _party_payload(
    fields: dict,
    *,
    prefix: str,
    legacy_code_key: str | None = None,
) -> dict[str, str | None]:
    """Normalize one flattened party without conflating identifiers."""
    code = fields.get(f"{prefix}_entity_code")
    tax_id = fields.get(f"{prefix}_tax_id")
    legacy = fields.get(legacy_code_key) if legacy_code_key else None
    if code is None and isinstance(legacy, str) and is_canonical_entity_code(legacy):
        code = normalize_entity_code(legacy)
    elif tax_id is None and legacy:
        # Legacy ``*_code`` fields in the extractor mean tax id.  Account
        # fields are never inspected or copied here.
        tax_id = str(legacy).strip()
    return {
        "entity_code": code,
        "tax_id": tax_id,
        "name": fields.get(f"{prefix}_name"),
        "account": fields.get(f"{prefix}_account"),
        "bank_reference": fields.get(f"{prefix}_bank_reference"),
    }


def normalize_extracted_fields(extract_type: str, fields: dict | None) -> dict:
    """Add normalized buyer/seller/payer/payee evidence fields.

    Existing extractor prompts use legacy flat names.  This adapter keeps
    those names for compatibility while exposing explicit identity, tax-id,
    account and bank-reference fields.  It never promotes an account to a tax
    id, and invalid/virtual entity codes stay empty with a warning.
    """
    out = dict(fields or {})
    warnings = list(out.get("extraction_warnings") or [])
    party_specs: list[tuple[str, str, str | None]] = []
    if extract_type == "invoice":
        party_specs = [("seller", "seller", "seller_code"), ("buyer", "buyer", "buyer_code")]
    elif extract_type == "payment":
        party_specs = [("payer", "payer", None), ("payee", "payee", None)]

    for output_key, prefix, legacy_code_key in party_specs:
        payload = _party_payload(out, prefix=prefix, legacy_code_key=legacy_code_key)
        raw_code = out.get(f"{prefix}_entity_code")
        if raw_code and not is_canonical_entity_code(raw_code):
            warnings.append(f"{prefix}_entity_code is not canonical; left unresolved")
            payload["entity_code"] = None
        legacy_value = out.get(legacy_code_key) if legacy_code_key else None
        if isinstance(legacy_value, str) and len(legacy_value.strip()) == 1 and legacy_value.strip().upper() in {"A", "B", "C", "D"}:
            warnings.append(f"{prefix}_code is a business role, not a tax id")
            payload["tax_id"] = None
        out[output_key] = payload
        for key, value in payload.items():
            out.setdefault(f"{prefix}_{key}", value)

    if extract_type == "tax_payment":
        payload = _party_payload(out, prefix="taxpayer", legacy_code_key="taxpayer_code")
        raw_code = out.get("taxpayer_entity_code")
        if raw_code and not is_canonical_entity_code(raw_code):
            warnings.append("taxpayer_entity_code is not canonical; left unresolved")
            payload["entity_code"] = None
        taxpayer_code = out.get("taxpayer_code")
        if isinstance(taxpayer_code, str) and len(taxpayer_code.strip()) == 1 and taxpayer_code.strip().upper() in {"A", "B", "C", "D"}:
            warnings.append("taxpayer_code is a business role, not a tax id")
            payload["tax_id"] = None
        out["taxpayer"] = payload
        for key, value in payload.items():
            out.setdefault(f"taxpayer_{key}", value)

    if warnings:
        out["extraction_warnings"] = warnings
    return out


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
