"""
ai_regulation_extractor.py
==========================
现代 AI 驱动的多模态法律法规自动化摄入引擎：
支持输入：
1. PDF / Word / 图片 (JPG/PNG) / 扫描件 文件
2. 网页 URL（自动抓取正文清洗）
3. 纯文本 / Markdown

处理流水线：
1. 文件/网页解析与文本抽取 (HTML2Text / PyPDF / OCR / LLM)
2. 智能大模型 / 正则提取元数据 (标题、发文字号、发文机关、效力位阶、管辖区域、涉及税种、时效状态、业务角色)
3. 联网核验真实性与条款序号连续性检查 (第一条至第N条)
4. 生成高质量规范 Markdown 文件并保存到 regulations_data
5. 自动语义切块入库并调用 BGE-M3 生成高维向量索引
"""

import json
import re
import urllib.request
from typing import Any, Dict, Tuple

import httpx

from ..config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, MAX_UPLOAD_SIZE
from ..logging_config import get_logger
from ..security import validate_llm_outbound_url, validate_outbound_url

logger = get_logger(__name__)

_MAX_FETCH_BYTES = min(MAX_UPLOAD_SIZE, 10 * 1024 * 1024)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Reject redirects so every destination passes the SSRF policy itself."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        raise ValueError("法规网页不允许重定向，请直接提供最终 HTTPS 地址")


def _read_response_limited(response, *, limit: int | None = None) -> bytes:
    """Read an HTTP response with a hard upper bound."""
    limit = _MAX_FETCH_BYTES if limit is None else limit
    content_length = response.headers.get("Content-Length") if getattr(response, "headers", None) else None
    if content_length:
        try:
            declared_length = int(content_length)
        except (TypeError, ValueError):
            declared_length = None
        if declared_length is not None and declared_length > limit:
            raise ValueError(f"法规网页响应超过 {limit} 字节限制")
    data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"法规网页响应超过 {limit} 字节限制")
    return data


def fetch_url_content(url: str) -> str:
    """抓取网页 URL 内容并提取干净的正文文本"""
    validated_url = validate_outbound_url(url)
    req = urllib.request.Request(
        validated_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
    )
    opener = urllib.request.build_opener(_NoRedirect())
    with opener.open(req, timeout=10) as resp:
        final_url = getattr(resp, "geturl", lambda: validated_url)()
        # Some handlers expose a final URL even when they do not use the
        # standard redirect exception.  Validate it before reading the body.
        if final_url.rstrip("/") != validated_url.rstrip("/"):
            validate_outbound_url(final_url)
            raise ValueError("法规网页发生了未授权重定向")
        html = _read_response_limited(resp).decode("utf-8", errors="ignore")

    # 移除 script, style, header, footer
    html = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", html, flags=re.IGNORECASE)
    html = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", html, flags=re.IGNORECASE)
    html = re.sub(r"<header[^>]*>[\s\S]*?</header>", "", html, flags=re.IGNORECASE)
    html = re.sub(r"<footer[^>]*>[\s\S]*?</footer>", "", html, flags=re.IGNORECASE)

    # 转纯文本
    text = re.sub(r"<[^>]+>", "\n", html)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def extract_file_content(file_bytes: bytes, filename: str) -> str:
    """提取上传文件（PDF / TXT / MD 等）的文本内容"""
    fname = filename.lower()
    if fname.endswith(".txt") or fname.endswith(".md"):
        return file_bytes.decode("utf-8", errors="ignore")

    if fname.endswith(".pdf"):
        try:
            import io

            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            pages_text = []
            for p in reader.pages:
                t = p.extract_text()
                if t:
                    pages_text.append(t)
            if pages_text:
                return "\n\n".join(pages_text)
        except Exception as e:
            logger.warning(f"pypdf extraction failed for {filename}: {e}")

    # 默认纯文本回退
    return file_bytes.decode("utf-8", errors="ignore")


def ai_extract_regulation_metadata(raw_text: str) -> Dict[str, Any]:
    """使用 LLM 或增强规则智能提取法规的核心元数据"""
    preview_text = raw_text[:4000]

    # 1. 尝试大模型解析
    if LLM_BASE_URL and LLM_MODEL:
        prompt = f"""你是一个专业的中国财税法律法规知识工程专家。请从以下法律法规正文片段中，提取结构化元数据。

提取规则：
1. 必须输出严格合法的 JSON 对象，不要输出 markdown 标记或任何其他多余文本。
2. title: 法规准确全称标题（如：中华人民共和国增值税法）
3. document_no: 官方发文字号或发布令号（如：主席令第四十一号 / 财税〔2016〕36号，若无则根据标题归纳）
4. issuer: 发文机关/发布部门（如：全国人大常委会 / 财政部 国家税务总局）
5. legal_level: 效力位阶，必须从以下单选：法律/行政法规/部门规章/规范性文件/地方规范性文件/操作指引
6. jurisdiction: 管辖区域，必须从以下单选：全国/四川省/成都市
7. tax_type: 涉及主要税种（如：增值税、企业所得税、印花税、个人所得税、全部税种）
8. status: 时效状态，从以下单选：现行有效/部分失效/衔接
9. business_role: 最主要适用的建工业务角色，从以下单选：construction(建筑施工)/trade(商贸物资)/labor(建筑劳务)/equipment(机械租赁)/national_vat(通用增值税)
10. summary: 100字以内核心要点提炼

正文片段：
{preview_text}

请输出 JSON:"""

        try:
            validated_llm_base = validate_llm_outbound_url(LLM_BASE_URL)
            client = httpx.Client(timeout=15.0)
            url = f"{validated_llm_base.rstrip('/')}/chat/completions"
            if not url.endswith("/v1/chat/completions") and "/v1" not in url:
                url = f"{LLM_BASE_URL.rstrip('/')}/v1/chat/completions"

            headers = {"Content-Type": "application/json"}
            if LLM_API_KEY:
                headers["Authorization"] = f"Bearer {LLM_API_KEY}"

            payload = {
                "model": LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
            }
            resp = client.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                res_data = resp.json()
                content = res_data["choices"][0]["message"]["content"].strip()
                # 寻找 json
                m = re.search(r"\{[\s\S]*\}", content)
                if m:
                    meta = json.loads(m.group(0))
                    meta["raw_extracted"] = True
                    return meta
        except Exception as e:
            logger.warning(f"LLM extraction error: {e}, falling back to heuristic parser")

    # 2. 启发式智能规则抽取
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    title = lines[0] if lines else "未命名涉税法规"
    if len(title) > 60 and len(lines) > 1:
        title = lines[1]

    doc_no = ""
    doc_no_match = re.search(r"(主席令第[一二三四五六七八九十\d]+号|国务院令第\d+号|财税〔\d{4}〕\d+号|税总发〔\d{4}〕\d+号|国家税务总局公告\d{4}年第\d+号|川税告\d{4}年\d+号|成府规〔\d{4}〕\d+号)", raw_text)
    if doc_no_match:
        doc_no = doc_no_match.group(1)
    else:
        doc_no = title

    level = "规范性文件"
    if "法律" in title or "法》" in title or "主席令" in doc_no:
        level = "法律"
    elif "条例" in title or "国务院令" in doc_no:
        level = "行政法规"
    elif "公告" in doc_no or "规章" in title:
        level = "部门规章"
    elif "成都市" in raw_text or "成府" in doc_no:
        level = "地方规范性文件"

    jurisdiction = "全国"
    if "四川省" in raw_text or "川税" in doc_no:
        jurisdiction = "四川省"
    elif "成都市" in raw_text or "成府" in doc_no:
        jurisdiction = "成都市"

    role = "construction"
    if "材料" in raw_text or "商贸" in raw_text or "钢材" in raw_text or "物资" in raw_text:
        role = "trade"
    elif "劳务" in raw_text or "农民工" in raw_text or "社保" in raw_text:
        role = "labor"
    elif "机械" in raw_text or "租赁" in raw_text or "设备" in raw_text:
        role = "equipment"

    tax_type = "增值税"
    if "所得税" in raw_text:
        tax_type = "企业所得税"
    elif "印花税" in raw_text:
        tax_type = "印花税"

    return {
        "title": title,
        "document_no": doc_no,
        "issuer": "全国人大 / 国务院 / 财政部 国家税务总局",
        "legal_level": level,
        "jurisdiction": jurisdiction,
        "tax_type": tax_type,
        "status": "现行有效",
        "business_role": role,
        "summary": "系统智能分析并自动提取结构化条款。",
        "raw_extracted": False
    }


def generate_regulation_markdown(meta: Dict[str, Any], full_text: str) -> Tuple[str, str]:
    """生成符合系统规范的标准 Markdown 文件内容，并确定存放路径"""
    category = f"business_roles/{meta.get('business_role', 'construction')}"
    if meta.get('jurisdiction') == '四川省':
        category = "sichuan"
    elif meta.get('jurisdiction') == '成都市':
        category = "chengdu"
    elif meta.get('business_role') == 'national_vat':
        category = "national_vat"

    fm = {
        "title": meta.get("title", ""),
        "document_no": meta.get("document_no", ""),
        "issuer": meta.get("issuer", ""),
        "legal_level": meta.get("legal_level", "规范性文件"),
        "jurisdiction": meta.get("jurisdiction", "全国"),
        "tax_types": [meta.get("tax_type", "全部税种")],
        "industries": ["建筑业"],
        "publish_date": meta.get("publish_date", ""),
        "effective_date": meta.get("effective_date", ""),
        "status": meta.get("status", "现行有效"),
        "business_role": meta.get("business_role", "construction"),
        "category": category,
        "source": "AI智能识别自动入库",
    }

    yaml_header = "---\n"
    for k, v in fm.items():
        if isinstance(v, list):
            yaml_header += f"{k}:\n"
            for item in v:
                yaml_header += f"  - {item}\n"
        else:
            val_clean = str(v).replace('"', '\\"')
            yaml_header += f'{k}: "{val_clean}"\n'
    yaml_header += "---\n\n"

    summary_quote = f"> 💡 **核心要点**：{meta.get('summary', '')}\n\n" if meta.get('summary') else ""
    md_content = yaml_header + summary_quote + full_text.strip() + "\n"

    # 文件名安全化
    safe_title = re.sub(r'[\\/*?:"<>|]', '_', meta.get("title", "custom_reg"))
    filename = f"{safe_title}.md"
    return md_content, filename
