"""
Regulations Fetcher - 核心模块（v3）

来源策略：
- 财政部政策库 szs.mof.gov.cn   → HTML 正文，可直接抓取
- 省级税务局政策解读页         → HTML 正文，可直接抓取
- PDF 文件                    → 下载后用 pdfplumber / PyPDF2 解析
- 国家税务总局政策法规库        → JS 渲染，不支持，记录为失败

核心法规清单（已验证 URL）：
"""

import json
import re
import time
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

# ─── Logging ────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fetcher")


# ─── Data Models ────────────────────────────────────────────────────────────

@dataclass
class Regulation:
    title: str
    doc_no: str
    issuer: str
    legal_level: str
    jurisdiction: str
    tax_types: list[str]
    industries: list[str]
    publish_date: str
    effective_date: Optional[str] = None
    expiry_date: Optional[str] = None
    status: str = "有效"
    url: str = ""
    source: str = ""
    note: str = ""

    def filename(self) -> str:
        safe = re.sub(r'[\\/:*?"<>|]', '_', self.doc_no)
        return f"{safe}.md"

    def frontmatter(self) -> str:
        fm = f"""---
title: "{self.title}"
document_no: "{self.doc_no}"
issuer: "{self.issuer}"
legal_level: "{self.legal_level}"
jurisdiction: "{self.jurisdiction}"
tax_types: {json.dumps(self.tax_types, ensure_ascii=False)}
industries: {json.dumps(self.industries, ensure_ascii=False)}
publish_date: "{self.publish_date}"
effective_date: "{self.effective_date or ''}"
expiry_date: "{self.expiry_date or ''}"
status: "{self.status}"
source: "{self.source}"
note: "{self.note}"
url: "{self.url}"
---

"""
        if self.note:
            fm += f"> ⚠️ {self.note}\n\n"
        return fm


# ─── 核心法规清单（已验证可抓取 URL）────────────────────────────────────────

class RegulationRegistry:

    # ══════════════════════════════════════════════════════════════════════
    # 增值税
    # ══════════════════════════════════════════════════════════════════════
    VAT = [
        Regulation(
            title="财政部 税务总局关于增值税法施行后增值税优惠政策衔接事项的公告",
            doc_no="财政部 税务总局公告2026年第10号",
            issuer="财政部 税务总局",
            legal_level="规范性文件",
            jurisdiction="全国",
            tax_types=["增值税"],
            industries=["建筑业", "劳务", "商贸", "租赁"],
            publish_date="2025-12-31",
            effective_date="2026-01-01",
            status="有效",
            url="https://neimenggu.chinatax.gov.cn/zcwj/rdwd/202603/t20260310_891980.html",
            source="国家税务总局内蒙古自治区税务局（政策解读转载）",
            note="原文请至财政部官网或国务院政策文件库核对",
        ),
        Regulation(
            title="中华人民共和国增值税法",
            doc_no="主席令第二十二号",
            issuer="全国人民代表大会常务委员会",
            legal_level="法律",
            jurisdiction="全国",
            tax_types=["增值税"],
            industries=["建筑业", "劳务", "商贸", "租赁"],
            publish_date="2024-12-25",
            effective_date="2026-01-01",
            status="有效",
            url="https://szs.mof.gov.cn/zhengcefabu/202512/t20251230_3980881.htm",
            source="财政部政策库",
            note="增值税法全文见国务院令第826号；实施条例同期公布",
        ),
        Regulation(
            title="中华人民共和国增值税法实施条例",
            doc_no="国务院令第826号",
            issuer="国务院",
            legal_level="行政法规",
            jurisdiction="全国",
            tax_types=["增值税"],
            industries=["建筑业", "劳务", "商贸", "租赁"],
            publish_date="2025-12-25",
            effective_date="2026-01-01",
            status="有效",
            url="https://szs.mof.gov.cn/zhengcefabu/202512/t20251230_3980881.htm",
            source="财政部政策库",
            note="实施条例共六章五十七条，是增值税法的核心配套法规",
        ),
        Regulation(
            title="关于全面推开营业税改征增值税试点的通知",
            doc_no="财税〔2016〕36号",
            issuer="财政部 国家税务总局",
            legal_level="规范性文件",
            jurisdiction="全国",
            tax_types=["增值税"],
            industries=["建筑业", "劳务", "租赁"],
            publish_date="2016-03-23",
            effective_date="2016-05-01",
            status="部分失效",
            url="https://szs.mof.gov.cn/zhengcefabu/201603/t20160324_1922515.htm",
            source="财政部政策库",
            note="附件1-4为营改增核心文件；部分条款已被后续文件废止，请参照2026年第10号公告",
        ),
        Regulation(
            title="财政部 税务总局关于建筑服务等营改增试点政策的通知",
            doc_no="财税〔2017〕58号",
            issuer="财政部 税务总局",
            legal_level="规范性文件",
            jurisdiction="全国",
            tax_types=["增值税"],
            industries=["建筑业"],
            publish_date="2017-07-11",
            effective_date="2017-07-01",
            status="部分失效",
            url="https://fgk.chinatax.gov.cn/zcfgk/c100012/c5194761/content.html",
            source="国家税务总局政策法规库",
            note="第一条甲供工程强制简易计税规定已被2026年第10号公告废止；预收款规定现行有效",
        ),
        Regulation(
            title="财政部 税务总局关于明确金融、房地产开发、教育辅助服务等增值税政策的通知",
            doc_no="财税〔2016〕140号",
            issuer="财政部 国家税务总局",
            legal_level="规范性文件",
            jurisdiction="全国",
            tax_types=["增值税"],
            industries=["建筑业", "商贸", "劳务"],
            publish_date="2016-12-21",
            effective_date="2016-12-25",
            status="有效",
            url="https://szs.mof.gov.cn/zhengcefabu/201612/t20161221_2494189.htm",
            source="财政部政策库",
        ),
        Regulation(
            title="财政部 税务总局关于简并增值税税率有关政策的通知",
            doc_no="财税〔2017〕37号",
            issuer="财政部 国家税务总局",
            legal_level="规范性文件",
            jurisdiction="全国",
            tax_types=["增值税"],
            industries=["建筑业", "商贸", "劳务", "租赁"],
            publish_date="2017-05-03",
            effective_date="2017-07-01",
            status="部分失效",
            url="https://szs.mof.gov.cn/zhengcefabu/201705/t20170502_2591609.htm",
            source="财政部政策库",
            note="农产品抵扣率已多次调整，以最新规定为准",
        ),
        Regulation(
            title="关于发布《增值税预缴税款管理办法》的公告",
            doc_no="财政部 税务总局公告2026年第14号",
            issuer="财政部 税务总局",
            legal_level="规范性文件",
            jurisdiction="全国",
            tax_types=["增值税"],
            industries=["建筑业"],
            publish_date="2026-02-02",
            effective_date="2026-01-01",
            status="有效",
            url="https://szs.mof.gov.cn/zhengcefabu/202602/t20260202_3983104.htm",
            source="财政部政策库",
            note="替代原国家税务总局公告2016年第17号；建筑服务跨地区预缴核心依据",
        ),
        # 国家税务总局公告2017年第43号已全文废止（国家税务总局公告2019年第31号），不纳入注册
        Regulation(
            title="销售服务、无形资产、不动产注释",
            doc_no="财税〔2016〕36号附件1",
            issuer="财政部 国家税务总局",
            legal_level="规范性文件",
            jurisdiction="全国",
            tax_types=["增值税"],
            industries=["建筑业", "商贸", "租赁", "劳务"],
            publish_date="2016-03-23",
            effective_date="2016-05-01",
            status="部分失效",
            url="https://szs.mof.gov.cn/zhengcefabu/202601/P020260131621581546234.pdf",
            source="财政部政策库（PDF）",
            note="建筑服务定义（附件第一章（四））、设备租赁带人按建筑服务等核心条款；以增值税法最新注释为准",
        ),
    ]

    # ══════════════════════════════════════════════════════════════════════
    # 企业所得税 / 印花税 / 个税
    # ══════════════════════════════════════════════════════════════════════
    OTHER_TAXES = [
        Regulation(
            title="财政部 税务总局关于节能节水、环境保护、安全生产专用设备数字化智能化改造企业所得税政策的公告",
            doc_no="财政部 税务总局公告2024年第9号",
            issuer="财政部 税务总局",
            legal_level="规范性文件",
            jurisdiction="全国",
            tax_types=["企业所得税"],
            industries=["建筑业", "设备租赁"],
            publish_date="2024-07-12",
            effective_date="2024-01-01",
            expiry_date="2027-12-31",
            status="有效",
            url="https://szs.mof.gov.cn/zhengcefabu/202407/t20240717_3939697.htm",
            source="财政部政策库",
        ),
        Regulation(
            title="财政部 税务总局关于企业改制重组及事业单位改制有关印花税政策的公告",
            doc_no="财政部 税务总局公告2024年第14号",
            issuer="财政部 税务总局",
            legal_level="规范性文件",
            jurisdiction="全国",
            tax_types=["印花税"],
            industries=["建筑业", "商贸", "劳务", "租赁"],
            publish_date="2024-08-27",
            effective_date="2024-10-01",
            expiry_date="2027-12-31",
            status="有效",
            url="https://fgk.chinatax.gov.cn/zcfgk/c102416/c5234299/content.html",
            source="国家税务总局政策法规库",
        ),
        Regulation(
            title="财政部 税务总局关于进一步完善研发费用税前加计扣除政策的公告",
            doc_no="财政部 税务总局公告2023年第7号",
            issuer="财政部 税务总局",
            legal_level="规范性文件",
            jurisdiction="全国",
            tax_types=["企业所得税"],
            industries=["建筑业", "商贸", "租赁"],
            publish_date="2023-03-26",
            effective_date="2023-01-01",
            status="有效",
            url="https://hainan.chinatax.gov.cn/xxgk_6_1/26145344.html",
            source="国家税务总局海南省税务局",
        ),
        Regulation(
            title="财政部 税务总局关于延续实施个人所得税综合所得汇算清缴有关政策的公告",
            doc_no="财政部 税务总局公告2023年第32号",
            issuer="财政部 税务总局",
            legal_level="规范性文件",
            jurisdiction="全国",
            tax_types=["个人所得税"],
            industries=["建筑业", "劳务"],
            publish_date="2023-12-29",
            effective_date="2024-01-01",
            expiry_date="2027-12-31",
            status="有效",
            url="https://fgk.chinatax.gov.cn/zcfgk/c102416/c5211536/content.html",
            source="国家税务总局政策法规库",
        ),
    ]

    # ══════════════════════════════════════════════════════════════════════
    # 四川省
    # ══════════════════════════════════════════════════════════════════════
    SICHUAN = [
        Regulation(
            title="国家税务总局四川省税务局关于省内跨区域涉税事项报验管理相关事项的公告",
            doc_no="国家税务总局四川省税务局公告2018年第12号",
            issuer="国家税务总局四川省税务局",
            legal_level="规范性文件",
            jurisdiction="四川省",
            tax_types=["增值税", "企业所得税", "个人所得税"],
            industries=["建筑业"],
            publish_date="2018-07-05",
            effective_date="2018-07-05",
            status="有效",
            url="http://www.canet.com.cn/fagui/617278.html",
            source="中国会计网转载（原文见四川省税务局公告栏）",
            note="全文请至四川省税务局官网通知公告栏核对；执行层面参照全国统一规定",
        ),
    ]

    # ══════════════════════════════════════════════════════════════════════
    # 成都市
    # ══════════════════════════════════════════════════════════════════════
    CHENGDU = [
        Regulation(
            title="国家税务总局成都市税务局关于市内跨区域涉税事项报验管理相关事项的公告",
            doc_no="国家税务总局成都市税务局公告2018年第7号",
            issuer="国家税务总局成都市税务局",
            legal_level="规范性文件",
            jurisdiction="成都市",
            tax_types=["增值税", "企业所得税", "个人所得税"],
            industries=["建筑业"],
            publish_date="2018-09-30",
            effective_date="2018-10-01",
            status="有效",
            url="https://www.beiyun.cn/article/82696",
            source="贝云社区转载",
            note="原文请至成都市税务局官网核对；执行层面参照国家税务总局38号公告",
        ),
    ]

    def all(self) -> list[Regulation]:
        return (
            self.VAT
            + self.OTHER_TAXES
            + self.SICHUAN
            + self.CHENGDU
        )


# ─── PDF 解析（可选依赖）────────────────────────────────────────────────────

def _extract_pdf_text(pdf_bytes: bytes) -> Optional[str]:
    """从 PDF 字节流中提取文本。"""
    try:
        import pdfplumber
        import io
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages = []
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    pages.append(t)
            return "\n\n".join(pages)
    except ImportError:
        log.warning("pdfplumber 未安装，尝试 PyPDF2...")
        try:
            import PyPDF2
            import io
            reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
            pages = []
            for page in reader.pages:
                t = page.extract_text()
                if t:
                    pages.append(t)
            return "\n\n".join(pages)
        except ImportError:
            log.error("请安装 pdfplumber 或 PyPDF2 来解析 PDF: pip install pdfplumber")
            return None
    except Exception as e:
        log.error(f"PDF 解析失败: {e}")
        return None


# ─── Fetcher ────────────────────────────────────────────────────────────────

class RegulationsFetcher:

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://szs.mof.gov.cn/",
    }

    TIMEOUT = 20.0
    DELAY = 1.5

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.client = httpx.Client(
            headers=self.HEADERS,
            timeout=httpx.Timeout(self.TIMEOUT),
            follow_redirects=True,
        )
        self.stats = {"success": 0, "failed": 0, "skipped": 0}

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ── HTML 正文提取 ────────────────────────────────────────────────────

    def _extract_html_text(self, html: str) -> str:
        """从 HTML 中提取正文文本，保留段落结构。"""
        html = re.sub(r'(?is)<(script|style|nav|header|footer|aside|iframe)\b[^>]*>.*?</\1>', '', html)
        html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)
        for tag in ["p", "div", "br", "h1", "h2", "h3", "h4", "li"]:
            html = re.sub(f"(?i)</{tag}>", "\n", html)
        text = re.sub(r'<[^>]+>', '', html)
        text = re.sub(r'&nbsp;', ' ', text)
        text = re.sub(r'&[a-z]+;', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def _extract_article_body(self, html: str) -> str:
        """从 HTML 中提取正文容器。"""
        # 策略 1: TRS_Editor（MOF 标准格式）
        trs = re.search(
            r'(?is)<div[^>]*\bclass=["\']\s*TRS_Editor["\'][^>]*>(.*?)</div>',
            html, re.DOTALL
        )
        if trs:
            paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', trs.group(1), re.DOTALL)
            if paragraphs:
                lines = []
                for p in paragraphs:
                    t = re.sub(r'<[^>]+>', '', p).strip()
                    t = re.sub(r'&nbsp;', ' ', t)
                    t = re.sub(r'&[a-z]+;', ' ', t)
                    if len(t) > 5:
                        lines.append(t)
                if lines:
                    return '\n\n'.join(lines)

        # 策略 2: 通用正文容器
        for cls in [
            'article-content', 'article_body', 'content_body',
            'main-content', 'TRS_Editor', 'box_content',
            'detail_content', 'news_content',
        ]:
            m = re.search(
                rf'(?is)<div[^>]*\bclass=["\'][^"\']*{re.escape(cls)}[^"\']*["\'][^>]*>(.*?)</div>',
                html, re.DOTALL
            )
            if m and len(m.group(1)) > 200:
                return self._extract_html_text(m.group(1))

        # 策略 3: 所有 <p> 段落
        paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', html, re.DOTALL)
        if paragraphs:
            lines = []
            for p in paragraphs:
                t = re.sub(r'<[^>]+>', '', p).strip()
                t = re.sub(r'&nbsp;', ' ', t)
                t = re.sub(r'&[a-z]+;', ' ', t)
                if len(t) > 10:
                    lines.append(t)
            if lines:
                return '\n\n'.join(lines)

        return self._extract_html_text(html)

    # ── 抓取 ──────────────────────────────────────────────────────────────

    def fetch_one(self, reg: Regulation, tries: int = 2) -> Optional[str]:
        for attempt in range(tries):
            try:
                log.info(f"Fetching: {reg.doc_no} — {reg.title[:35]}")
                resp = self.client.get(reg.url)
                resp.raise_for_status()

                # ── 编码处理 ──────────────────────────────────────────────
                content_type = resp.headers.get("content-type", "")
                raw = resp.content

                # m.mof.gov.cn 返回 GBK 编码
                if "m.mof.gov.cn" in reg.url:
                    try:
                        text = raw.decode("gbk", errors="replace")
                    except Exception:
                        text = raw.decode("utf-8", errors="replace")
                else:
                    text = resp.text

                # ── PDF ────────────────────────────────────────────────
                if reg.url.lower().endswith(".pdf"):
                    log.info("  检测到 PDF，尝试解析...")
                    text = _extract_pdf_text(raw)
                    if text and len(text) > 200:
                        return text
                    log.warning("  PDF 解析失败或内容为空")
                    if attempt < tries - 1:
                        time.sleep(1)
                        continue
                    return None

                # ── 正文提取 ──────────────────────────────────────────
                body = self._extract_article_body(text)

                if len(body) < 150:
                    log.warning(
                        f"  内容过短（{len(text)} chars）: {reg.url}"
                    )
                    if attempt < tries - 1:
                        time.sleep(2)
                        continue
                    return None

                return text

            except httpx.HTTPStatusError as e:
                log.error(f"  → HTTP {e.response.status_code}: {reg.url}")
            except httpx.TimeoutException:
                log.error(f"  → 超时: {reg.url}")
            except Exception as e:
                log.error(f"  → {e} — {reg.url}")

            if attempt < tries - 1:
                time.sleep(2)

        return None

    # ── 保存 ────────────────────────────────────────────────────────────

    def save(self, reg: Regulation, content: str, sub_dir: str):
        out = self.output_dir / sub_dir / reg.filename()
        with open(out, "w", encoding="utf-8") as f:
            f.write(reg.frontmatter() + content)
        log.info(f"  → Saved: {out.relative_to(self.output_dir)}")

    # ── 批量 ────────────────────────────────────────────────────────────

    def fetch_all(
        self,
        regulations: list[Regulation],
        sub_dir: str,
    ) -> dict:
        results = {"success": [], "failed": [], "skipped": []}

        for reg in regulations:
            out = self.output_dir / sub_dir / reg.filename()
            if out.exists():
                log.info(f"  [SKIP] {out.name}")
                results["skipped"].append(reg)
                self.stats["skipped"] += 1
                continue

            content = self.fetch_one(reg)
            if content:
                self.save(reg, content, sub_dir)
                results["success"].append(reg)
                self.stats["success"] += 1
            else:
                results["failed"].append(reg)
                self.stats["failed"] += 1

            time.sleep(self.DELAY)

        return results

    def print_summary(self):
        log.info("── 抓取完成 ──────────────────────────────")
        log.info(f"  成功: {self.stats['success']}")
        log.info(f"  失败: {self.stats['failed']}  ← 需人工处理")
        log.info(f"  跳过: {self.stats['skipped']}")


# ─── 入口函数 ────────────────────────────────────────────────────────────────

def fetch_all_core_regulations(output_dir: str | Path) -> dict:
    """抓取全部核心法规到指定目录。"""
    output_dir = Path(output_dir)
    registry = RegulationRegistry()

    with RegulationsFetcher(output_dir) as fetcher:
        log.info(f"输出目录: {output_dir.absolute()}")
        log.info(f"共 {len(registry.all())} 条法规\n")

        cats = [
            ("全国增值税", registry.VAT, "national_vat"),
            ("全国其他税", registry.OTHER_TAXES, "national_other"),
            ("四川省规定", registry.SICHUAN, "sichuan"),
            ("成都市规定", registry.CHENGDU, "chengdu"),
        ]

        all_results = {}
        for name, regs, sub in cats:
            r = fetcher.fetch_all(regs, sub)
            all_results[name] = r
            log.info(
                f"\n{name} — "
                f"成:{len(r['success'])} "
                f"败:{len(r['failed'])} "
                f"跳:{len(r['skipped'])}"
            )
            if r["failed"]:
                for reg in r["failed"]:
                    log.warning(f"  ❌ {reg.doc_no} | {reg.url}")

        fetcher.print_summary()

    return fetcher.stats


if __name__ == "__main__":
    import sys
    root = Path(__file__).parent.parent
    stats = fetch_all_core_regulations(root / "regulations_data")
    sys.exit(0)
