"""安全清空 PostgreSQL 业务数据脚本（保留用户账户与迁移版本表）。"""
import os
import psycopg

DB_URL = os.getenv("DATABASE_URL", "postgresql://yvoche@localhost:5432/projectrag")

def clear_business_data():
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE';
            """)
            all_tables = [r[0] for r in cur.fetchall()]
            
            preserve_tables = {"users", "alembic_version_tax", "alembic_version_rag"}
            tables_to_truncate = [t for t in all_tables if t not in preserve_tables]
            
            if tables_to_truncate:
                quoted_tables = ", ".join(f"\"{t}\"" for t in tables_to_truncate)
                cur.execute(f"TRUNCATE TABLE {quoted_tables} RESTART IDENTITY CASCADE;")
                conn.commit()
                print(f"✅ 已清空 {len(tables_to_truncate)} 张业务数据表（RESTART IDENTITY CASCADE）")

            cur.execute("SELECT id, username, role, display_name, active FROM users;")
            users = cur.fetchall()
            print(f"👥 保留用户账户 ({len(users)} 个):")
            for u in users:
                status_str = "启用" if u[4] else "禁用"
                print(f"   - ID: {u[0]}, 用户名: {u[1]}, 角色: {u[2]}, 姓名: {u[3]}, 状态: {status_str}")

if __name__ == "__main__":
    clear_business_data()
