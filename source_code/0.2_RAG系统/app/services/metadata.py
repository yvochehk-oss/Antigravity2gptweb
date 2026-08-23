"""Metadata inference service with deterministic rules."""
from pathlib import Path
import re
from ..logging_config import get_logger

logger = get_logger(__name__)

# Classification rules: (keywords, business_category, document_type, tax_category)
DOC_RULES = [
    # Labor documents
    (("工资", "考勤", "社保", "实名制"), "labor", "labor_record", ""),
    (("劳务结算", "劳务计量"), "labor", "labor_settlement", ""),
    (("劳务合同", "劳务协议"), "labor", "labor_contract", ""),

    # Equipment documents
    (("台班", "设备使用", "机械使用"), "equipment", "equipment_shift", ""),
    (("设备结算", "机械结算"), "equipment", "equipment_settlement", ""),
    (("设备租赁", "机械租赁", "设备合同"), "equipment", "equipment_contract", ""),

    # Material documents
    (("材料验收", "收料", "入库"), "material", "material_acceptance", ""),
    (("材料采购", "钢材采购", "商砼采购", "材料合同"), "material", "material_contract", ""),

    # Subcontract documents
    (("分包结算",), "subcontract", "subcontract_settlement", ""),
    (("专业分包", "分包合同"), "subcontract", "subcontract_contract", ""),

    # Tax documents (NEW)
    (("增值税", "进项税", "销项税", "增值税专用发票", "增值税普通发票"), "", "tax_invoice", "vat"),
    (("企业所得税", "所得税预缴", "所得税汇算"), "", "tax_document", "enterprise_income"),
    (("个人所得税", "代扣代缴", "劳务个税"), "", "tax_document", "individual_income"),
    (("附加税", "城建税", "教育费附加", "地方教育附加"), "", "tax_document", "surtax"),
    (("土地增值税",), "", "tax_document", "land_income"),
    (("印花税",), "", "tax_document", "stamp_duty"),
    (("环保税", "环境保护税"), "", "tax_document", "environmental"),
    (("税务申报", "纳税申报", "完税证明", "税票", "缴税凭证"), "", "tax_payment_record", ""),

    # Other documents (no business category)
    (("会议纪要", "会议记录"), "", "meeting_minutes", ""),
    (("补充协议",), "", "supplementary_agreement", ""),
    (("签证", "变更单", "工程变更"), "", "change_order", ""),
    (("结算",), "", "settlement_document", ""),
    (("总包合同", "施工合同", "主合同"), "", "main_contract", ""),
]


def infer_from_filename(filename: str) -> dict:
    """Infer document metadata from filename.

    Args:
        filename: Document filename

    Returns:
        Dict with inferred metadata fields
    """
    name = Path(filename).stem
    result = {
        "document_type": "other",
        "business_category": "",
        "tax_category": "",
        "entity_code": "",
        "counterparty_code": "",
        "period": "",
        "confidence": 0.25
    }

    # Apply classification rules
    for keys, cat, doc_type, tax_cat in DOC_RULES:
        if any(k in name for k in keys):
            result["document_type"] = doc_type
            result["business_category"] = cat
            result["tax_category"] = tax_cat
            result["confidence"] = 0.70
            break

    # Entity codes: A, B, C, D
    for code in ("A", "B", "C", "D"):
        if re.search(rf"(?<![A-Za-z]){code}(?![A-Za-z])", name):
            result["entity_code"] = code
            result["confidence"] += 0.08
            break

    # Counterparty codes: 甲, 乙, 丙, 丁
    for code in ("甲", "乙", "丙", "丁"):
        if code in name:
            result["counterparty_code"] = code
            result["confidence"] += 0.08
            break

    # Period extraction: 2024-01, 2024年01月, etc.
    m = re.search(r"(20\d{2})[-年./_](0?[1-9]|1[0-2])", name)
    if m:
        result["period"] = f"{m.group(1)}-{int(m.group(2)):02d}"
        result["confidence"] += 0.07

    return result


def refine_from_content(current: dict, text: str) -> dict:
    """Refine metadata from document content.

    This is a second-pass classifier that only fills in missing fields.
    It never overwrites explicit user metadata.

    Args:
        current: Current metadata (may have user-specified values)
        text: Document content preview

    Returns:
        Updated metadata dict
    """
    if not text:
        return current

    # Use filename inference on a cleaned version of the text
    sample = text.replace("\n", " ")[:500]
    probe = infer_from_filename(sample)

    out = dict(current)

    # Only fill in missing or "other" values
    if (not out.get("document_type") or out.get("document_type") == "other") and probe.get("document_type") != "other":
        out["document_type"] = probe["document_type"]
        logger.debug(f"Refined document_type from content: {probe['document_type']}")

    if not out.get("business_category") and probe.get("business_category"):
        out["business_category"] = probe["business_category"]
        logger.debug(f"Refined business_category from content: {probe['business_category']}")

    if not out.get("tax_category") and probe.get("tax_category"):
        out["tax_category"] = probe["tax_category"]
        logger.debug(f"Refined tax_category from content: {probe['tax_category']}")

    return out


def get_category_display_name(category: str) -> str:
    """Get human-readable category name.

    Args:
        category: Category code

    Returns:
        Display name
    """
    names = {
        "labor": "劳务",
        "equipment": "设备租赁",
        "material": "材料",
        "subcontract": "分包",
        "tax": "税务",
        "": "未分类"
    }
    return names.get(category, category)


def get_tax_category_display_name(category: str) -> str:
    """Get human-readable tax category name.

    Args:
        category: Tax category code

    Returns:
        Display name
    """
    names = {
        "vat": "增值税",
        "enterprise_income": "企业所得税",
        "individual_income": "个人所得税",
        "surtax": "附加税",
        "land_income": "土地增值税",
        "stamp_duty": "印花税",
        "environmental": "环境保护税",
        "": "未分类"
    }
    return names.get(category, category)


def get_document_type_display_name(doc_type: str) -> str:
    """Get human-readable document type name.

    Args:
        doc_type: Document type code

    Returns:
        Display name
    """
    names = {
        "labor_record": "工资/考勤记录",
        "labor_settlement": "劳务结算单",
        "labor_contract": "劳务合同",
        "equipment_shift": "设备台班记录",
        "equipment_settlement": "设备结算单",
        "equipment_contract": "设备租赁合同",
        "material_acceptance": "材料验收单",
        "material_contract": "材料采购合同",
        "subcontract_settlement": "分包结算单",
        "subcontract_contract": "分包合同",
        "meeting_minutes": "会议纪要",
        "supplementary_agreement": "补充协议",
        "change_order": "工程变更/签证",
        "tax_invoice": "增值税发票",
        "tax_document": "税务文件",
        "tax_payment_record": "缴税凭证",
        "settlement_document": "结算文件",
        "main_contract": "主合同",
        "other": "其他"
    }
    return names.get(doc_type, doc_type)
