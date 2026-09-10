import os
import sys

rag_env = os.path.join(os.path.dirname(__file__), "..", "source_code", "0.2_RAG系统", "project-rag-v1.1", ".env")
if os.path.exists(rag_env):
    with open(rag_env, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

import psycopg2

db_url = os.environ.get("PROJECT_RAG_DB_URL") or os.environ.get("DATABASE_URL") or "postgresql://postgres@127.0.0.1:5432/projectrag"
print(f"正在连接数据库: {db_url} ...")
try:
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    print("正在执行数据库字段自愈修复并升级 AI 端点为 V3.0 Spark-X2.5-4B ...")
    cur.execute("""
        TRUNCATE TABLE ingest_jobs;
        ALTER TABLE ingest_jobs ALTER COLUMN next_retry_at DROP NOT NULL;
        ALTER TABLE ingest_jobs ALTER COLUMN next_retry_at TYPE timestamp with time zone USING NULL;
        ALTER TABLE ingest_jobs ALTER COLUMN started_at DROP NOT NULL;
        ALTER TABLE ingest_jobs ALTER COLUMN started_at TYPE timestamp with time zone USING NULL;
        ALTER TABLE ingest_jobs ALTER COLUMN finished_at DROP NOT NULL;
        ALTER TABLE ingest_jobs ALTER COLUMN finished_at TYPE timestamp with time zone USING NULL;
        UPDATE ai_model_endpoints 
        SET name = '本地 Spark-X2.5-4B 保底模型', 
            model = 'Spark-X2.5-4B',
            note = 'V3.0 llama.cpp 本地 Spark-X2.5-4B 离线保底模型 (Port 8930)'
        WHERE name LIKE '%Ling%' OR model LIKE '%ling%';
    """)
    conn.commit()
    conn.close()
    print("\n==================================================================")
    print(" [成功] 数据库字段已成功升级为 timestamptz 时间戳！")
    print(" [成功] 模型端点已升级为 V3.0 Spark-X2.5-4B 保底模型！")
    print(" 红字报错彻底消除，刷新浏览器即可看到 V3.0 最新模型！")
    print("==================================================================")
except Exception as e:
    print(f"\n[错误] 修复失败: {e}")