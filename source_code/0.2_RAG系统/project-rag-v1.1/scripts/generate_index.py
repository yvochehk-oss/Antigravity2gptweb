"""Generate the regulation index from the current regulation tree.

The four former ``entity_*`` directories represented business categories, not
companies.  The current tree stores them under ``business_roles/`` and the
index exposes ``business_role`` explicitly.  Legacy directories are read only
as a compatibility fallback when this script is pointed at an older data
directory; they are never written back to the index as entity categories.
"""

from __future__ import annotations

from datetime import date
import os
import re
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
REG_DIR = Path(os.environ.get("REGULATIONS_DATA_DIR", str(PROJECT_DIR / "regulations_data")))
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

ROLE_DIRECTORIES: tuple[tuple[str, str, str], ...] = (
    ("business_roles/construction", "建筑施工业务角色", "construction"),
    ("business_roles/trade", "建材商贸业务角色", "trade"),
    ("business_roles/labor", "建筑劳务业务角色", "labor"),
    ("business_roles/equipment", "设备租赁业务角色", "equipment"),
)
LEGACY_ROLE_DIRECTORIES = {
    "entity_a": "construction",
    "entity_b": "trade",
    "entity_c": "labor",
    "entity_d": "equipment",
}
GENERAL_DIRECTORIES: tuple[tuple[str, str], ...] = (
    ("national_vat", "国家增值税法规"),
    ("national_other", "国家其他税种法规"),
    ("sichuan", "四川省税务规定"),
    ("chengdu", "成都市税务公告"),
)


def parse_fm(text: str) -> dict[str, str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}
    metadata: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            metadata[key.strip()] = value.strip().strip('"').strip("'")
    return metadata


def _role_files(directory: str, role: str) -> tuple[list[Path], str]:
    """Return files and the canonical source directory used for one role."""
    canonical = REG_DIR / directory
    if canonical.exists():
        return sorted(canonical.glob("*.md")), directory

    for legacy_dir, legacy_role in LEGACY_ROLE_DIRECTORIES.items():
        if legacy_role == role:
            fallback = REG_DIR / legacy_dir
            if fallback.exists():
                return sorted(fallback.glob("*.md")), directory
    return [], directory


def build_index() -> str:
    sections = [
        "# 法规库统一索引（INDEX）",
        "> 分类语义：`business_role` 是业务角色，不是法人或 entity_code。",
        f"> 生成日期：{date.today().isoformat()}",
        "> 状态规则：`现行有效`、`部分失效`（已注明失效条款与衔接政策）",
        "",
        "---",
        "",
        "## 一、文件目录结构与分类清单",
        "",
    ]

    total_docs = 0
    valid_count = 0
    partial_count = 0

    def add_section(label: str, files: list[Path], role: str | None, relative_root: str) -> None:
        nonlocal total_docs, valid_count, partial_count
        total_docs += len(files)
        role_suffix = f"（business_role: `{role}`）" if role else ""
        sections.extend(
            [
                f"### {label}{role_suffix}（共 {len(files)} 份）",
                "",
                "| 序号 | 文档文件名 | 标题 | business_role | 时效状态 | 效力位阶 |",
                "|:---|:---|:---|:---|:---|:---|",
            ]
        )
        for index, path in enumerate(files, 1):
            metadata = parse_fm(path.read_text(encoding="utf-8"))
            title = metadata.get("title", path.stem)
            status = metadata.get("status", "现行有效")
            level = metadata.get("legal_level", "规范性文件")
            file_role = metadata.get("business_role", role or "")
            if "部分" in status:
                partial_count += 1
            else:
                valid_count += 1
            relative_path = Path(relative_root) / path.name
            sections.append(
                f"| {index} | `{relative_path.as_posix()}` | {title} | "
                f"`{file_role}` | {status} | {level} |"
            )
        sections.append("")

    for directory, label, role in ROLE_DIRECTORIES:
        files, relative_root = _role_files(directory, role)
        add_section(label, files, role, relative_root)

    for directory, label in GENERAL_DIRECTORIES:
        path = REG_DIR / directory
        files = sorted(path.glob("*.md")) if path.exists() else []
        add_section(label, files, None, directory)

    sections.extend(
        [
            "---",
            "",
            "## 二、统计总览",
            "",
            f"- **法规库总数**：{total_docs} 份",
            f"- **现行有效**：{valid_count} 份",
            f"- **部分失效/衔接标注**：{partial_count} 份",
            "- **业务角色**：construction / trade / labor / equipment",
            "- **法人映射**：实际企业由 Canonical Entity Master 按 business_role 映射",
            "- **Frontmatter 覆盖率**：由本脚本扫描到的文件全部参与索引",
            "",
        ]
    )
    return "\n".join(sections)


if __name__ == "__main__":
    output = REG_DIR / "INDEX.md"
    output.write_text(build_index(), encoding="utf-8")
    print(f"INDEX.md generated: {output}")
