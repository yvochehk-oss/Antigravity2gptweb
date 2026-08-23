"""
北大法宝 MCP 客户端（v3 — MCP JSON-RPC 协议）

已验证可用端点：
  https://apim-gateway.pkulaw.com/mcp-law-search-service

可用工具：
  search_article  — 语义检索法条（返回正文片段）
  get_article     — 按标题+条号精准获取法条内容

Token 来源：仅允许通过环境变量 PKULAW_TOKEN 注入，不提供仓库内默认值。
"""

import json
import os
import httpx
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

# ─── Config ───────────────────────────────────────────────────────────────────

TOKEN = os.environ.get(
    "PKULAW_TOKEN", "",
)
BASE_URL = "https://apim-gateway.pkulaw.com/mcp-law-search-service"

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
}
if TOKEN:
    HEADERS["Authorization"] = f"Bearer {TOKEN}"


# ─── MCP JSON-RPC Client ───────────────────────────────────────────────────────

def _mcp_call(tool_name: str, arguments: dict) -> dict:
    """
    调用 MCP 端点，返回 JSON-RPC response['result']。
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }
    resp = httpx.post(BASE_URL, headers=HEADERS, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    # 检查 RPC 错误
    if "error" in data:
        raise RuntimeError(f"MCP error: {data['error']}")

    return data.get("result", {})


def _list_tools() -> list[dict]:
    """列出所有可用工具（调试用）。"""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {},
    }
    resp = httpx.post(BASE_URL, headers=HEADERS, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json().get("result", {}).get("tools", [])


# ─── Data Models ──────────────────────────────────────────────────────────────

@dataclass
class LawArticle:
    """单条法条检索结果。"""
    gid: str
    title: str
    article: str       # 法条正文
    doc_no: str
    timeliness: str    # 时效性：现行有效 / 废止或失效 / 部分废止或失效
    effectiveness: str # 效力位阶
    issue_department: str
    issue_date: str
    implementation_date: str
    lib: str           # 法规库：中央 / 地方
    url: str

    @property
    def is_valid(self) -> bool:
        return "废止" not in self.timeliness and "失效" not in self.timeliness


def _parse_article(raw: dict) -> LawArticle:
    """从原始字典解析 LawArticle。"""
    return LawArticle(
        gid=raw.get("gid", ""),
        title=raw.get("title", ""),
        article=raw.get("article", ""),
        doc_no=raw.get("doc_no", ""),
        timeliness=raw.get("timeliness", ""),
        effectiveness=raw.get("effectiveness", ""),
        issue_department=raw.get("issue_department", ""),
        issue_date=raw.get("issue_date", ""),
        implementation_date=raw.get("implementation_date", ""),
        lib=raw.get("lib", ""),
        url=raw.get("url", ""),
    )


def _extract_articles(result: dict) -> list[LawArticle]:
    """从 MCP search_article / get_article 响应提取 LawArticle 列表。

    MCP 返回结构：
      result = {
        "content": [{"type": "text", "text": "<JSON 字符串>"}],
        "structuredContent": {"result": [ {...}, ... ]}
      }
    """
    articles: list[LawArticle] = []

    # 路径 1：structuredContent.result（结构化）
    sc = result.get("structuredContent") or {}
    if isinstance(sc, dict) and isinstance(sc.get("result"), list):
        for item in sc["result"]:
            if isinstance(item, dict):
                articles.append(_parse_article(item))

    # 路径 2：content[0].text 是 JSON 字符串（兼容老格式）
    if not articles:
        for item in result.get("content", []):
            payload = None
            if isinstance(item, dict):
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    try:
                        payload = json.loads(item["text"])
                    except Exception:
                        payload = None
                elif "gid" in item or "title" in item:
                    payload = item
            elif isinstance(item, str):
                try:
                    payload = json.loads(item)
                except Exception:
                    payload = None
            if isinstance(payload, list):
                for sub in payload:
                    if isinstance(sub, dict):
                        articles.append(_parse_article(sub))
            elif isinstance(payload, dict):
                articles.append(_parse_article(payload))

    return articles


# ─── Core API ─────────────────────────────────────────────────────────────────

def search_articles(
    text: str,
    lib: str = "中央",
    timeliness: str = "现行有效",
    issue_department: Optional[str] = None,
    implement_date_start: Optional[str] = None,
    implement_date_end: Optional[str] = None,
    size: int = 10,
) -> list[LawArticle]:
    """
    语义检索法条。

    参数：
        text            检索关键词或自然语言
        lib             法规库："中央" 或 "地方"
        timeliness      时效性筛选："现行有效" / "废止或失效" / "部分废止或失效" 等
        issue_department 制定机关全称（如 "财政部"）
        implement_date_start 施行日期起始（ISO: YYYY-MM-DD）
        implement_date_end    施行日期截止
        size            返回条数（最大 20）

    返回：
        list[LawArticle]
    """
    args = {
        "text": text,
        "lib": lib,
        "timeliness": timeliness,
        "size": min(size, 20),
    }
    if issue_department:
        args["issue_department"] = issue_department
    if implement_date_start:
        args["implement_date_start"] = implement_date_start
    if implement_date_end:
        args["implement_date_end"] = implement_date_end

    result = _mcp_call("search_article", args)

    return _extract_articles(result)


def get_article(title: str, number: str) -> Optional[LawArticle]:
    """
    精准获取法条内容（需已知法规标题和条号）。

    参数：
        title  法规标题（中文）
        number 法条序号，如 "第四十八条"、"第七条"

    返回：
        LawArticle 或 None
    """
    result = _mcp_call("get_article", {"title": title, "number": number})
    articles = _extract_articles(result)
    return articles[0] if articles else None


# ─── Batch Helpers ─────────────────────────────────────────────────────────────

def search_by_topics(
    topics: list[str],
    out_dir: Path,
    lib: str = "中央",
    timeliness: str = "现行有效",
) -> dict:
    """
    批量搜索多个主题，将结果保存到 out_dir。

    topics: [(搜索词, 文件名前缀), ...]
    返回: {"saved": [...], "failed": [...]}
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    saved, failed = [], []

    for topic, prefix in topics:
        safe_prefix = "".join(c if c.isalnum() else "_" for c in prefix)[:40]
        filename = f"pkulaw_{safe_prefix}.json"

        print(f"\n🔍 [{prefix}] → {topic}")
        try:
            articles = search_articles(topic, lib=lib, timeliness=timeliness, size=10)

            # 过滤已失效
            valid = [a for a in articles if a.is_valid]
            if not valid:
                print(f"   ⚠️ 无现行有效结果")
                failed.append((topic, "无有效结果"))
                continue

            # 保存为 JSON
            out_path = out_dir / filename
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "topic": topic,
                        "prefix": prefix,
                        "articles": [
                            {
                                "gid": a.gid,
                                "title": a.title,
                                "article": a.article,
                                "doc_no": a.doc_no,
                                "timeliness": a.timeliness,
                                "effectiveness": a.effectiveness,
                                "issue_department": a.issue_department,
                                "issue_date": a.issue_date,
                                "implementation_date": a.implementation_date,
                                "lib": a.lib,
                                "url": a.url,
                            }
                            for a in valid
                        ],
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            saved.append(str(out_path))
            print(f"   ✅ 保存 {len(valid)} 条 → {filename}")

        except Exception as e:
            print(f"   ❌ {e}")
            failed.append((topic, str(e)))

    return {"saved": saved, "failed": failed}


# ─── Predefined Search Plans ─────────────────────────────────────────────────

CHENGDU_JianzHU_TOPICS = [
    # (搜索词, 文件名前缀)
    ("甲供工程 简易计税 建筑服务", "jia_gong_gong_cheng"),
    ("建筑服务 增值税 预缴税款 跨地区", "jian_zhu_fu_wu_yu_jiao"),
    ("劳务派遣 增值税 差额扣除", "lao_wu_pai_qian"),
    ("设备租赁 干租 湿租 增值税 税目", "she_bei_zu_lin"),
    ("建材商贸 大宗商品 增值税 发票", "jian_cai_shang_mao"),
    ("农民工工资 专户 个税 代扣代缴", "nong_min_gong_zi"),
    ("小微企业 普惠性 税收减免 增值税", "xiao_wei_qi_ye"),
    ("跨地区 经营 所得税 总分机构", "kua_di_qu_suo_de"),
]


# ─── CLI Entry ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    root = Path(__file__).parent.parent.parent
    out_dir = root / "regulations_data" / "pkulaw_cache"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("北大法宝 MCP 法规检索工具（JSON-RPC 协议）")
    print("=" * 60)

    # 快速测试
    print("\n🧪 连通性测试...")
    try:
        tools = _list_tools()
        print(f"   ✅ 可用工具: {[t['name'] for t in tools]}")
    except Exception as e:
        print(f"   ❌ 连通失败: {e}")
        sys.exit(1)

    # 演示检索
    print("\n🔍 演示：搜索「甲供工程 简易计税」...")
    articles = search_articles("甲供工程 简易计税", size=3)
    for a in articles:
        print(f"\n  [{a.doc_no}] {a.title}")
        print(f"  时效: {a.timeliness} | 效力: {a.effectiveness}")
        print(f"  {a.article[:200]}...")

    # 批量搜索
    if "--batch" in sys.argv:
        result = search_by_topics(CHENGDU_JianzHU_TOPICS, out_dir)
        print(f"\n完成：保存 {len(result['saved'])} 条，失败 {len(result['failed'])} 条")
