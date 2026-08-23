"""
build_regulation_md.py
======================
辅助工具：根据一份"法规元信息 + 关键条款号"清单，从 PKU Law MCP 拉真实正文，
组装成跟 regulations_data/national_vat/* 一致的 Markdown 格式（含 frontmatter）。

用法：
  python -m regulations_data.scripts.build_regulation_md \\
      --plan regulations_data/scripts/plan_construction.yaml \\
      --out regulations_data/business_roles/construction

计划文件格式（YAML）：
  regulations:
    - doc_no: "财税〔2017〕XX号"
      title: "..."
      issuer: "财政部"
      legal_level: "规范性文件"
      jurisdiction: "全国"
      tax_types: ["增值税"]
      industries: ["建筑业"]
      publish_date: "2017-XX-XX"
      effective_date: "2017-XX-XX"
      status: "现行有效"
      url: "https://..."
      key_articles: ["第一条", "第二条", "第十七条"]   # 要拉的条号
      summary: |                                        # 文档头部的背景说明
        本文件主要规定 ...
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import yaml

from .providers.pkulaw_client import get_article, search_articles


BUSINESS_ROLE_ALIASES = {
    "a": "construction",
    "b": "trade",
    "c": "labor",
    "d": "equipment",
    "construction": "construction",
    "trade": "trade",
    "labor": "labor",
    "equipment": "equipment",
}


def canonical_business_role(value: object) -> str:
    """Normalize a role label without treating it as an entity identifier."""
    role = str(value or "").strip().lower()
    try:
        return BUSINESS_ROLE_ALIASES[role]
    except KeyError as exc:
        raise ValueError(
            f"unsupported business_role {value!r}; expected construction/trade/labor/equipment"
        ) from exc


def fetch_one(doc_no: str, title: str, articles: list[str]) -> list[str]:
    """用 get_article 逐条拉取正文，返回 [(条号, 文本), ...]"""
    out: list[tuple[str, str]] = []
    for num in articles:
        a = get_article(title, num)
        if a is None:
            print(f"   ⚠️  [{doc_no}] 条号 [{num}] 未拉到")
            continue
        out.append((num, a.article.strip()))
    return out


def build_md(plan: dict) -> str:
    """根据 plan + MCP 拉取结果拼装 Markdown"""
    fm = dict(plan["frontmatter"])
    role = fm.get("business_role") or plan.get("business_role")
    if role:
        fm["business_role"] = canonical_business_role(role)
    key_articles: list[str] = plan.get("key_articles", [])
    title = fm["title"]

    body_parts: list[str] = []

    # 头部说明
    if plan.get("summary"):
        body_parts.append("> " + plan["summary"].strip().replace("\n", "\n> "))

    body_parts.append("")

    # 拉条款
    if key_articles:
        body_parts.append(f"## 关键条款（{len(key_articles)} 条）\n")
        for num, text in fetch_one(fm["document_no"], title, key_articles):
            body_parts.append(f"### {num}\n")
            body_parts.append(text)
            body_parts.append("")
    else:
        body_parts.append("> 注：本法规元信息已记录，正文待人工补全。\n")

    # frontmatter
    fm_str = "---\n"
    for k, v in fm.items():
        if isinstance(v, list):
            fm_str += f"{k}:\n"
            for item in v:
                fm_str += f"  - {item}\n"
        else:
            fm_str += f'{k}: "{v}"\n'
    fm_str += "---\n\n"

    return fm_str + "\n".join(body_parts)


def safe_filename(doc_no: str) -> str:
    safe = doc_no.replace(" ", "_")
    safe = safe.replace("/", "_").replace(":", "_").replace("?", "_").replace("*", "_")
    safe = safe.replace('"', "_").replace("<", "_").replace(">", "_").replace("|", "_")
    safe = safe.replace("〔", "_").replace("〕", "_").replace("[", "_").replace("]", "_")
    return safe + ".md"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--dry", action="store_true", help="只打印不写文件")
    args = parser.parse_args()

    plan_path = Path(args.plan)
    out_dir = Path(args.out)
    if any(part in {"entity_a", "entity_b", "entity_c", "entity_d"} for part in out_dir.parts):
        raise ValueError(
            "legacy entity_* output directories are not supported; use "
            "regulations_data/business_roles/{construction,trade,labor,equipment}"
        )
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(plan_path, encoding="utf-8") as f:
        plan_data = yaml.safe_load(f)

    for reg in plan_data["regulations"]:
        md = build_md(reg)
        fn = safe_filename(reg["frontmatter"]["document_no"])
        out_path = out_dir / fn

        if args.dry:
            print(f"[DRY] would write {out_path} ({len(md)} chars)")
            print(md[:500])
            print("...\n")
        else:
            out_path.write_text(md, encoding="utf-8")
            print(f"✅ wrote {out_path}")


if __name__ == "__main__":
    main()
