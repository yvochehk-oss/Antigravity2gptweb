"""
往来单位基础资料导入脚本（SQLite 直连模式）
从 Excel 往来统计表提取单位基本信息，直接写入本地 SQLite 数据库。

用法：
    python scripts/import_entities.py --dry-run          # 仅模拟
    python scripts/import_entities.py --db-url sqlite:///...  # 指定数据库
    python scripts/import_entities.py --api-url http://...   # 通过 API 录入（需服务运行）

当服务未启动时，自动降级为 SQLite 直连模式。
"""
import sys
import os
import re
import json
import argparse
import urllib.request
import urllib.error
from datetime import datetime, timezone

EXCEL_PATH = "/Users/yvoche/Library/Mobile Documents/com~apple~CloudDocs/往来统计截止2026年7月31日校验后.xlsx"


def api_post(path, data):
    url = f"{API_BASE}{path}"
    req = urllib.request.Request(url, data=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise Exception(f"HTTP {e.code}: {body}")


def api_get(path):
    url = f"{API_BASE}{path}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise Exception(f"HTTP {e.code}: {e.read().decode()}")


# ─── Excel 解析 ───────────────────────────────────────────────

def parse_legal_rep(text):
    if not text: return ("", "", "")
    text = text.replace("\n", " ").strip()
    id_match = re.search(r'\b(\d{17}[\dXx])\b', text)
    id_num = id_match.group(1) if id_match else ""
    phone_match = re.search(r'\b(1[3-9]\d{9})\b', text)
    phone = phone_match.group(1) if phone_match else ""
    name = text
    name = re.sub(r'[\(（]A证[）\)]', '', name)
    name = re.sub(r'[\(（]过渡[）\)]', '', name)
    name = re.sub(r'\d{17}[\dXx]', '', name)
    name = re.sub(r'1[3-9]\d{9}', '', name)
    name = name.strip().strip('/').strip()
    name = re.sub(r'\s+', ' ', name)
    if '/' in name:
        name = name.split('/')[0].strip()
    return (name, id_num, phone)


def parse_shareholder(text):
    return text.replace("\n", " ").strip() if text else ""


def extract_entities(excel_path):
    from openpyxl import load_workbook
    wb = load_workbook(excel_path, read_only=True, data_only=True)
    entities = {}

    def add(name, data):
        if not name or name == "合计": return
        key = name.strip()
        if key not in entities:
            entities[key] = {"name": key, **data}
        else:
            for k, v in data.items():
                if v and (k not in entities[key] or not entities[key][k]):
                    entities[key][k] = v

    industry_map = {"工程": "工程", "劳务": "劳务", "贸易": "贸易", "机械": "机械", "个体": "个体工商户"}

    ws3 = wb.worksheets[2] if len(wb.worksheets) > 2 else None
    if ws3:
        for row in ws3.iter_rows(min_row=2, values_only=True):
            if not row[2]: continue
            name = str(row[2]).strip()
            if name == "公司名称": continue
            entity_type = ""
            if "分公司" in name: entity_type = "分公司"
            elif "个体工商户" in name or "经营部" in name: entity_type = "个体工商户"
            industry = industry_map.get(str(row[1]).strip() if row[1] else "", "")
            legal_rep, legal_id, legal_phone = parse_legal_rep(str(row[6]).strip() if row[6] else "")
            sup_raw = str(row[8]).strip() if row[8] else ""
            supervisor = re.sub(r'[\(（].*?[）\)]', '', sup_raw).replace("（", "").replace("）", "").strip()
            supervisor = supervisor.replace("监事", "").replace("财务负责人", "").strip()

            def to_float(v):
                if v is None: return 0.0
                s = str(v).strip().replace(",", "")
                try: return float(s)
                except: return 0.0

            add(name, {
                "entity_type": entity_type,
                "industry": industry,
                "legal_representative": legal_rep,
                "legal_rep_id": legal_id,
                "legal_rep_phone": legal_phone,
                "shareholders": parse_shareholder(str(row[7]) if row[7] else ""),
                "supervisor": supervisor,
                "registered_capital": str(row[5]).strip() if row[5] else "",
                "establishment_date": str(row[3]).strip() if row[3] else "",
                "acquisition_date": str(row[4]).strip() if row[4] else "",
                "registration_authority": str(row[9]).strip() if row[9] else "",
                "contributed_legal": to_float(row[11]),
                "contributed_shareholder": to_float(row[13]),
                "note": str(row[14]).strip() if row[14] else "",
                "source": "excel",
            })

    ws1 = wb.worksheets[0] if wb.worksheets else None
    seen = set()
    if ws1:
        for row in ws1.iter_rows(min_row=3, values_only=True):
            if row[2]:
                n = str(row[2]).strip()
                if n and n != "合计": seen.add(n)
    for name in seen:
        if name not in entities:
            add(name, {"industry": "未知", "source": "excel", "note": "来源：内部往来表"})

    # 合并广元玖硕商贸有限公司
    if "广元玖硕商贸有限公司" in entities and "广元市玖硕商贸有限公司" in entities:
        full = entities.pop("广元市玖硕商贸有限公司")
        partial = entities["广元玖硕商贸有限公司"]
        for k, v in full.items():
            if v and (k not in partial or not partial[k]):
                partial[k] = v

    # 智能推断行业
    for name in entities:
        d = entities[name]
        if d.get("industry") in ("", "未知"):
            if "建筑工程" in name or "建设工程" in name:
                d["industry"] = "工程"
            elif "劳务" in name:
                d["industry"] = "劳务"
            elif "商贸" in name or "贸易" in name:
                d["industry"] = "贸易"
            elif "租赁" in name:
                d["industry"] = "机械"
            elif "广告" in name:
                d["industry"] = "其他服务"
            elif "经营部" in name or "服务部" in name:
                d["industry"] = "个体工商户"
                d["entity_type"] = "个体工商户"

    return entities


# ─── SQLite 直连写入 ────────────────────────────────────────

def import_via_sqlite(entities, db_path, dry_run):
    try:
        import sqlite3
    except ImportError:
        print("❌ Python 内置 sqlite3 模块不可用")
        sys.exit(1)

    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 建表（兼容旧表结构）
    cur.execute("""
    CREATE TABLE IF NOT EXISTS entities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        short_name TEXT DEFAULT '',
        entity_type TEXT DEFAULT '',
        industry TEXT DEFAULT '',
        legal_representative TEXT DEFAULT '',
        legal_rep_id TEXT DEFAULT '',
        legal_rep_phone TEXT DEFAULT '',
        shareholders TEXT DEFAULT '',
        supervisor TEXT DEFAULT '',
        finance_officer TEXT DEFAULT '',
        registered_capital TEXT DEFAULT '',
        establishment_date TEXT DEFAULT '',
        acquisition_date TEXT DEFAULT '',
        registration_authority TEXT DEFAULT '',
        registration_number TEXT DEFAULT '',
        unified_social_credit_code TEXT DEFAULT '',
        business_scope TEXT DEFAULT '',
        contributed_legal REAL DEFAULT 0.0,
        contributed_shareholder REAL DEFAULT 0.0,
        note TEXT DEFAULT '',
        source TEXT DEFAULT 'excel',
        data_as_of TEXT DEFAULT '',
        created_at TEXT DEFAULT '',
        updated_at TEXT DEFAULT ''
    )
    """)
    conn.commit()

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    created = 0
    updated = 0
    skipped = 0

    for i, (name, data) in enumerate(sorted(entities.items()), 1):
        cur.execute("SELECT id FROM entities WHERE name = ?", (name,))
        row = cur.fetchone()

        fields = {
            "short_name": data.get("short_name", ""),
            "entity_type": data.get("entity_type", ""),
            "industry": data.get("industry", ""),
            "legal_representative": data.get("legal_representative", ""),
            "legal_rep_id": data.get("legal_rep_id", ""),
            "legal_rep_phone": data.get("legal_rep_phone", ""),
            "shareholders": data.get("shareholders", ""),
            "supervisor": data.get("supervisor", ""),
            "finance_officer": data.get("finance_officer", ""),
            "registered_capital": data.get("registered_capital", ""),
            "establishment_date": data.get("establishment_date", ""),
            "acquisition_date": data.get("acquisition_date", ""),
            "registration_authority": data.get("registration_authority", ""),
            "business_scope": data.get("business_scope", ""),
            "contributed_legal": float(data.get("contributed_legal", 0)),
            "contributed_shareholder": float(data.get("contributed_shareholder", 0)),
            "note": data.get("note", ""),
            "source": data.get("source", "excel"),
            "data_as_of": "2026-07-31",
            "updated_at": now,
        }

        if row:
            # UPDATE：只更新空字段
            set_parts = []
            vals = []
            for k, v in fields.items():
                set_parts.append(f"{k} = COALESCE(NULLIF(:{k}, ''), {k})")
                vals.append(v)
            set_parts.append("updated_at = :updated_at")
            vals.append(now)
            vals.append(name)
            cur.execute(
                f"UPDATE entities SET updated_at = :updated_at WHERE name = :name",
                {"updated_at": now, "name": name}
            )
            for k, v in fields.items():
                if v:
                    cur.execute(f"UPDATE entities SET {k} = ? WHERE name = ? AND ({k} = '' OR {k} IS NULL)", (v, name))
            updated += 1
            if not dry_run:
                print(f"  [{i:>2}] 🔄 更新: {name}")
        else:
            all_fields = {"name": name, **fields, "created_at": now}
            cols = list(all_fields.keys())
            placeholders = [f":{c}" for c in cols]
            cur.execute(
                f"INSERT INTO entities ({','.join(cols)}) VALUES ({','.join(placeholders)})",
                all_fields
            )
            created += 1
            if not dry_run:
                print(f"  [{i:>2}] ✅ 新建: {name}")

    if not dry_run:
        conn.commit()

    # 统计
    cur.execute("SELECT industry, COUNT(*) FROM entities GROUP BY industry ORDER BY industry")
    rows = cur.fetchall()
    total = sum(r[1] for r in rows)
    conn.close()

    return created, updated, total, rows


# ─── 主程序 ─────────────────────────────────────────────────

def main():
    global API_BASE

    parser = argparse.ArgumentParser(description="往来单位基础资料导入工具")
    parser.add_argument("--dry-run", action="store_true", help="仅模拟，不写入")
    parser.add_argument("--excel", default=EXCEL_PATH, help="Excel 文件路径")
    parser.add_argument("--api-url", default="", help="RAG 服务地址（留空则使用 SQLite）")
    parser.add_argument("--db-url", default="sqlite:///./data/entities.db",
                        help="SQLite 数据库路径（仅 SQLite 模式使用）")
    args = parser.parse_args()

    if not os.path.exists(args.excel):
        print(f"错误：文件不存在: {args.excel}")
        sys.exit(1)

    entities = extract_entities(args.excel)
    print(f"共提取 {len(entities)} 个往来单位\n")

    # 显示摘要
    print(f"{'#':<3} {'单位名称':<36} {'行业':<10} {'法人代表':<10} {'注册资金'}")
    print("-" * 75)
    for i, (name, d) in enumerate(sorted(entities.items()), 1):
        print(f"{i:<3} {name:<36} {d.get('industry',''):<10} {d.get('legal_representative',''):<10} {d.get('registered_capital','')}")

    if args.dry_run:
        print(f"\n✅ 模拟完成，共 {len(entities)} 个单位")
        return

    if args.api_url:
        # ── API 模式 ──
        API_BASE = args.api_url.rstrip("/")
        try:
            health = api_get("/api/v1/health")
            print(f"\n✅ RAG 服务正常: v{health.get('version', '?')}")
        except Exception as e:
            print(f"\n❌ 无法连接 RAG 服务: {e}")
            print("   请先启动服务或使用 SQLite 模式（不加 --api-url）")
            sys.exit(1)

        print(f"\n通过 API 录入到 {API_BASE}...")
        created = updated = errors = 0
        for i, (name, data) in enumerate(sorted(entities.items()), 1):
            payload = {
                "name": data["name"],
                "industry": data.get("industry", ""),
                "entity_type": data.get("entity_type", ""),
                "legal_representative": data.get("legal_representative", ""),
                "legal_rep_id": data.get("legal_rep_id", ""),
                "legal_rep_phone": data.get("legal_rep_phone", ""),
                "shareholders": data.get("shareholders", ""),
                "supervisor": data.get("supervisor", ""),
                "registered_capital": data.get("registered_capital", ""),
                "establishment_date": data.get("establishment_date", ""),
                "acquisition_date": data.get("acquisition_date", ""),
                "registration_authority": data.get("registration_authority", ""),
                "business_scope": data.get("business_scope", ""),
                "contributed_legal": data.get("contributed_legal", 0.0),
                "contributed_shareholder": data.get("contributed_shareholder", 0.0),
                "note": data.get("note", ""),
                "source": data.get("source", "excel"),
                "data_as_of": "2026-07-31",
            }
            try:
                result = api_post("/api/v1/entities/batch", [payload])
                item = result.get("items", [{}])[0]
                action = item.get("action", "")
                if action == "created": created += 1
                elif action == "updated": updated += 1
                print(f"  [{i:>2}] {'✅ 新建' if action=='created' else '🔄 更新'}: {name}")
            except Exception as e:
                errors += 1
                print(f"  [{i:>2}] ❌ 失败: {name} -> {e}")
        print(f"\n完成：新建 {created}，更新 {updated}，失败 {errors}")
        summary = api_get("/api/v1/entities/summary")
        print(f"数据库现有 {summary.get('total', '?')} 个单位")

    else:
        # ── SQLite 模式 ──
        db_path = args.db_url.replace("sqlite:///", "")
        if db_path.startswith("sqlite://"):
            db_path = db_path.replace("sqlite://", "")
        print(f"\n通过 SQLite 直连写入: {db_path}")
        created, updated, total, rows = import_via_sqlite(entities, db_path, dry_run=False)
        print(f"\n完成：新建 {created}，更新 {updated}")
        print(f"数据库现有 {total} 个单位：")
        for industry, count in rows:
            print(f"  {industry or '未分类'}: {count}")


if __name__ == "__main__":
    main()
