#!/usr/bin/env python
"""
ledger_import.py — 台账（Ledger）批量导入 CLI
==============================================
用法示例：

  # 1. 预览（不写入）台账文件
  python -m scripts.ledger_import --path ./data/项目台账.xlsx --table projects

  # 2. 预览并指定项目 ID（发票/合同台账需要）
  python -m scripts.ledger_import --path ./data/发票台账.xlsx --table documents --project-id 3

  # 3. 实际写入（加 --commit）
  python -m scripts.ledger_import --path ./data/项目台账.xlsx --table projects --commit

  # 4. 扫描整个目录，批量导入所有 Excel 文件
  python -m scripts.ledger_import --path ./data --scan

  # 5. 目录扫描 + 自动识别台账类型
  python -m scripts.ledger_import --path ./data --scan --auto-detect

  # 6. 强制指定 sheet 编号（默认第0个）
  python -m scripts.ledger_import --path ./data/发票台账.xlsx --table documents --sheet 1

支持的表：
  projects        — 项目台账
  documents       — 发票/合同台账（含增值税等税务字段）
  external_parties — 供应商/往来单位台账
  entities        — 内部单位台账

支持的格式：.xlsx / .xls / .docx / .pdf（.docx 和 .pdf 仅提取表格文本，精度较 Excel 略低）
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

import click
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services.ledger_importer import (
    VALID_TABLES,
    _detect_table_from_headers,
    import_excel_ledger,
    import_ledger,
    parse_excel,
    _build_alias_map,
    PROJECTS_ALIASES,
    DOCUMENTS_ALIASES,
    EXTERNAL_PARTIES_ALIASES,
    ENTITIES_ALIASES,
)
from app.config import DATABASE_URL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("ledger_import")


# ---------------------------------------------------------------------------
# 数据库连接
# ---------------------------------------------------------------------------

def _get_db_session():
    db_url = os.getenv("PROJECT_RAG_DB_URL") or os.getenv("DATABASE_URL") or DATABASE_URL
    engine = create_engine(db_url, echo=False)
    Session = sessionmaker(bind=engine)
    return Session(), engine


# ---------------------------------------------------------------------------
# 文件发现
# ---------------------------------------------------------------------------

LEDGER_EXTENSIONS = {".xlsx", ".xls", ".docx", ".pdf"}


def _find_ledger_files(path: str) -> list[str]:
    """递归扫描目录，返回所有台账文件路径。"""
    p = Path(path)
    results = []
    if p.is_file():
        if p.suffix.lower() in LEDGER_EXTENSIONS:
            results.append(str(p))
    else:
        for ext in LEDGER_EXTENSIONS:
            results.extend(str(f) for f in p.rglob(f"*{ext}"))
    return sorted(results)


# ---------------------------------------------------------------------------
# 台账类型自动识别
# ---------------------------------------------------------------------------

def _auto_table_type(file_path: str) -> str | None:
    """读取 Excel 文件表头，猜测台账类型。"""
    try:
        with open(file_path, "rb") as f:
            data = f.read()
        wb_data = __import__("openpyxl").load_workbook(
            __import__("io").BytesIO(data), data_only=True
        )
        ws = wb_data.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return None
        headers = [str(c or "").strip() for c in rows[0]]
        return _detect_table_from_headers(headers)
    except Exception as exc:
        logger.warning("无法识别 %s 的台账类型: %s", file_path, exc)
        return None


# ---------------------------------------------------------------------------
# 表格渲染（美化输出）
# ---------------------------------------------------------------------------

def _render_preview(rows: list[dict], max_show: int = 10) -> str:
    """将预览结果渲染为可读表格。"""
    if not rows:
        return "  （无数据）"

    # 取前 max_show 行
    sample = rows[:max_show]
    # 收集所有字段（排除 _row）
    all_keys = sorted({k for r in sample for k in r if k != "_row"})
    col_widths = {k: max(len(k), max(len(str(r.get(k, ""))) for r in sample)) for k in all_keys}

    lines = []
    # 表头
    header = "  " + " | ".join(k.ljust(col_widths[k]) for k in all_keys)
    lines.append(header)
    lines.append("-" * len(header))
    for r in sample:
        line = "  " + " | ".join(
            str(r.get(k, "")).ljust(col_widths[k])[:col_widths[k]] for k in all_keys
        )
        lines.append(line)

    if len(rows) > max_show:
        lines.append(f"  ... 共 {len(rows)} 行，以上为前 {max_show} 行预览")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Click CLI
# ---------------------------------------------------------------------------

@click.command()
@click.option(
    "--path", "-p", required=True,
    help="台账 Excel/Word/PDF 文件路径，或包含多个台账文件的目录"
)
@click.option(
    "--table", "-t",
    type=click.Choice(sorted(VALID_TABLES)),
    default=None,
    help=f"目标数据库表：{', '.join(sorted(VALID_TABLES))}"
)
@click.option(
    "--auto-route", is_flag=True,
    help="自动识别系统内/外单位并路由到正确表（entities 或 external_parties），"
         "外部代码格式 E000001-E999999（每系列近百万家，8系列合计近800万家）"
)
@click.option(
    "--project-id", "-pid", type=int, default=None,
    help="所属项目 ID（documents 表必须指定）"
)
@click.option(
    "--sheet", "-s", type=int, default=0,
    help="Excel sheet 编号（从 0 开始，默认 0）"
)
@click.option(
    "--header-row", type=int, default=0,
    help="表头所在行号（从 0 开始，默认 0）"
)
@click.option(
    "--commit", is_flag=True,
    help="实际写入数据库（不加此参数则只预览）"
)
@click.option(
    "--scan", is_flag=True,
    help="扫描目录批量处理所有台账文件"
)
@click.option(
    "--auto-detect", "auto_detect", is_flag=True,
    help="扫描模式下：自动识别每个文件的台账类型（需配合 --scan 使用）"
)
@click.option(
    "--dry-run/--no-dry-run", default=True,
    help="预览模式（默认 True），关闭则直接写入"
)
@click.option(
    "--output", "-o", type=click.Path(), default=None,
    help="将预览结果 JSON 保存到指定文件"
)
def cli(
    path: str,
    table: str | None,
    auto_route: bool,
    project_id: int | None,
    sheet: int,
    header_row: int,
    commit: bool,
    scan: bool,
    auto_detect: bool,
    dry_run: bool,
    output: str | None,
):
    """
    台账（Ledger）批量导入工具 — 将整理好的 Excel/Word/PDF 台账文件
    按数据库已有表单格式批量导入到 RAG 系统。
    """
    dry_run = dry_run and not commit

    logger.info("=" * 60)
    logger.info("台账导入工具  (commit=%s, dry_run=%s)", commit, dry_run)
    logger.info("=" * 60)

    # 1. 收集文件
    if scan:
        files = _find_ledger_files(path)
        if not files:
            click.echo(f"❌ 在 {path} 中未找到任何 Excel/Word/PDF 台账文件", err=True)
            sys.exit(1)
        logger.info("找到 %d 个台账文件", len(files))
    else:
        if not Path(path).exists():
            click.echo(f"❌ 文件不存在: {path}", err=True)
            sys.exit(1)
        files = [path]

    # 2. 确定每个文件的台账类型
    file_tables: list[tuple[str, str]] = []  # (file_path, table)
    for fp in files:
        t = table
        if auto_route:
            t = "entities_or_external"
            logger.info("  [%s] → entities_or_external（自动路由）", Path(fp).name)
        elif not t:
            if scan and auto_detect:
                t = _auto_table_type(fp)
                if t:
                    logger.info("  [%s] → %s", Path(fp).name, t)
                else:
                    logger.warning("  [%s] 无法自动识别台账类型，跳过", Path(fp).name)
                    continue
            else:
                click.echo(f"❌ 需要 --table 参数指定目标表，或使用 --scan --auto-detect 自动识别，或使用 --auto-route 自动路由", err=True)
                sys.exit(1)
        file_tables.append((fp, t))

    # 3. 预览每个文件
    all_results: list[dict] = []
    for fp, t in file_tables:
        click.echo(f"\n📄 {Path(fp).name}  →  表: {t}")
        try:
            result = import_excel_ledger(fp, t, project_id=project_id, sheet_index=sheet, header_row=header_row)
            all_results.append(result)

            click.echo(f"   总行数: {result['total']}")
            click.echo(f"   映射字段: {', '.join(result['mapped_fields'])}")

            if result["errors"]:
                click.echo(f"   ⚠️  解析警告: {len(result['errors'])} 行有问题（见下方详情）")

            click.echo("\n   数据预览（不含内部字段 _row）：")
            preview_text = _render_preview(result["rows"], max_show=5)
            for line in preview_text.splitlines():
                click.echo(f"   {line}")

            if result["errors"]:
                click.echo(f"\n   ⚠️  前 5 条警告：")
                for err in result["errors"][:5]:
                    click.echo(f"     行 {err['row']}: {'; '.join(err['errors'])}")

        except Exception as exc:
            logger.error("处理 %s 时出错: %s", fp, exc)
            click.echo(f"   ❌ 错误: {exc}", err=True)
            all_results.append({"file": fp, "error": str(exc)})

    # 4. 汇总预览
    total_rows = sum(r.get("total", 0) for r in all_results if "error" not in r)
    click.echo(f"\n{'=' * 60}")
    click.echo(f"📊 汇总：共 {len(all_results)} 个文件，{total_rows} 行数据（预览模式）")
    if dry_run:
        click.echo("💡 如确认无误，加上 --commit 参数即可实际写入数据库")
    if auto_route or (file_tables and file_tables[0][1] == "entities_or_external"):
        click.echo("🔀 自动路由预览：")
        for fp, t in file_tables:
            if t == "entities_or_external":
                # 从解析结果中找到对应文件的路由摘要
                for r in all_results:
                    if r.get("file", "").replace("/", "\\") == fp.replace("/", "\\"):
                        rows = r.get("rows", [])
                        internal = sum(1 for row in rows if row.get("entity_code") and
                                       row.get("entity_code", "").strip().upper()[:1] in list("ABCD"))
                        external = len(rows) - internal
                        click.echo(f"   {Path(fp).name}: {internal} 家内 → entities, {external} 家外 → external_parties")
                        break
    click.echo(f"{'=' * 60}")

    # 5. 保存 JSON（如指定）
    if output:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
        click.echo(f"💾 预览结果已保存至: {output}")

    # 6. 实际写入
    if commit:
        if not (os.getenv("PROJECT_RAG_DB_URL") or os.getenv("DATABASE_URL")):
            click.echo("❌ 写入数据库需要设置 PROJECT_RAG_DB_URL 或 DATABASE_URL 环境变量", err=True)
            sys.exit(1)

        click.echo("\n🔄 开始写入数据库...")
        db, _ = _get_db_session()
        total_written = 0
        total_errors = 0

        for fp, t in file_tables:
            try:
                result = import_ledger(t, fp, project_id=project_id, dry_run=False, db=db)
                db_write = result.get("db_write", {})
                written = db_write.get("written", 0)
                errors = db_write.get("errors", [])
                total_written += written
                total_errors += len(errors)
                click.echo(f"  ✅ {Path(fp).name}: 写入 {written} 行" +
                           (f"，{len(errors)} 个错误" if errors else ""))
                routing = db_write.get("routing_summary")
                if routing:
                    click.echo(f"     路由：{routing.get('entities', 0)} → entities，{routing.get('external_parties', 0)} → external_parties")
                for err in errors[:3]:
                    click.echo(f"     行 {err['row']}: {err['error']}")
            except Exception as exc:
                logger.error("写入 %s 时出错: %s", fp, exc)
                click.echo(f"  ❌ {Path(fp).name}: {exc}", err=True)

        db.close()
        click.echo(f"\n✅ 写入完成：共 {total_written} 行入库，{total_errors} 行跳过/报错")


# ---------------------------------------------------------------------------
# 辅助：显示支持的列映射
# ---------------------------------------------------------------------------

def show_column_mapping(table: str):
    """打印指定表支持的列名别名映射。"""
    alias_maps = {
        "projects": PROJECTS_ALIASES,
        "documents": DOCUMENTS_ALIASES,
        "external_parties": EXTERNAL_PARTIES_ALIASES,
        "entities": ENTITIES_ALIASES,
    }
    aliases = alias_maps.get(table)
    if not aliases:
        click.echo(f"未知表: {table}")
        return

    click.echo(f"\n📋 {table} 表支持的 Excel 列名映射：\n")
    for field, names in aliases.items():
        click.echo(f"  {field}")
        for n in names:
            click.echo(f"    = {n}")
    click.echo()


if __name__ == "__main__":
    cli()
