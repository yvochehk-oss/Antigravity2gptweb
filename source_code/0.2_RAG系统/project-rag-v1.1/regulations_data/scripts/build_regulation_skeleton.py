"""
build_regulation_skeleton.py
===========================
为法规生成"frontmatter + 待补正文"的 .md 骨架文件（无需 MCP 调用）。
用法：
  python -m regulations_data.scripts.build_regulation_skeleton \\
      --plan regulations_data/scripts/plan_xxx.yaml \\
      --out regulations_data/xxx
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml


def safe_filename(doc_no: str) -> str:
    safe = doc_no.replace(" ", "_")
    safe = safe.replace("/", "_").replace(":", "_").replace("?", "_").replace("*", "_")
    safe = safe.replace('"', "_").replace("<", "_").replace(">", "_").replace("|", "_")
    safe = safe.replace("〔", "_").replace("〕", "_").replace("[", "_").replace("]", "_")
    return safe + ".md"


def build_skeleton(plan: dict) -> str:
    fm = plan["frontmatter"]
    key_articles = plan.get("key_articles", [])

    fm_str = "---\n"
    for k, v in fm.items():
        if isinstance(v, list):
            fm_str += f"{k}:\n"
            for item in v:
                fm_str += f"  - {item}\n"
        else:
            fm_str += f'{k}: "{v}"\n'
    fm_str += "---\n\n"

    parts = [fm_str]

    if plan.get("summary"):
        parts.append("> " + plan["summary"].strip().replace("\n", "\n> "))
        parts.append("")

    parts.append("> ⚠️ **正文待补**：本文件 frontmatter 已就绪，关键条款列表已记录，")
    parts.append(f"> 待 token 恢复后用 `build_regulation_md.py` 拉取 {len(key_articles)} 个条款。")
    parts.append("")

    if key_articles:
        parts.append("## 计划拉取的条款\n")
        for num in key_articles:
            parts.append(f"- {num}")
        parts.append("")

    parts.append("## 待补充正文\n")
    parts.append("(空，由 `regulations_data/scripts/build_regulation_md.py --plan plan_xxx.yaml --out xxx` 补全)\n")

    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(args.plan, encoding="utf-8") as f:
        plan_data = yaml.safe_load(f)

    for reg in plan_data["regulations"]:
        md = build_skeleton(reg)
        out_path = out_dir / safe_filename(reg["frontmatter"]["document_no"])
        out_path.write_text(md, encoding="utf-8")
        print(f"📄 skeleton {out_path}")


if __name__ == "__main__":
    main()