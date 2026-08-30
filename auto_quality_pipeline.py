#!/usr/bin/env python3
"""
auto_quality_pipeline.py — 自动化双轮解析质量流水线
====================================================

流程：
  第一轮（已在运行）：4 并发 pipeline 快速解析全部文件
  ↓ 等待队列清空
  打分检查：找出 parse_quality_score < THRESHOLD 的文件
  ↓ 如果有低分文件
  自动切换 .env → hybrid-engine，并发降为 1
  重启 RAG 服务
  第二轮：hybrid-engine 逐一精解析低分文件
  ↓ 等待第二轮清空
  自动恢复 .env → pipeline，并发恢复 4
  重启 RAG 服务，回到正常状态

使用方法（在后台启动，让它静默监控）：
  cd /Users/yvoche/AI开发/073_成都建工/V2.0
  nohup uv run python auto_quality_pipeline.py > auto_pipeline.log 2>&1 &
  tail -f auto_pipeline.log

可选参数：
  --threshold 60    质量分阈值，低于此值触发 hybrid 重跑（默认 60）
  --poll 30         轮询间隔秒数（默认 30）
"""
import argparse
import os
import signal
import subprocess
import sys
import time
from datetime import datetime

# ── 路径配置 ──────────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
RAG_DIR    = os.path.join(BASE_DIR, "source_code/0.2_RAG系统/project-rag-v1.1")
ENV_FILE   = os.path.join(RAG_DIR, ".env")
START_SH   = os.path.join(BASE_DIR, "start_all.sh")
STOP_SH    = os.path.join(BASE_DIR, "stop_all.sh")

# ── DB 连接（直接用 RAG 的 ORM） ─────────────────────────────────────────
sys.path.insert(0, RAG_DIR)
os.chdir(RAG_DIR)

try:
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE)
except Exception:
    pass

from app.db import SessionLocal
from app.models import Document, IngestJob


# ──────────────────────────────────────────────────────────────────────────
def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def read_env() -> str:
    with open(ENV_FILE, "r") as f:
        return f.read()


def write_env(content: str):
    with open(ENV_FILE, "w") as f:
        f.write(content)


def set_worker_concurrency(concurrency: int):
    """Update the worker concurrency without selecting a parser backend."""
    content = read_env()

    # WORKER_CONCURRENCY
    if "PROJECT_RAG_WORKER_CONCURRENCY=" in content:
        lines = content.splitlines()
        lines = [
            f"PROJECT_RAG_WORKER_CONCURRENCY={concurrency}"
            if l.startswith("PROJECT_RAG_WORKER_CONCURRENCY=") else l
            for l in lines
        ]
        content = "\n".join(lines) + "\n"
    else:
        content += f"\nPROJECT_RAG_WORKER_CONCURRENCY={concurrency}\n"

    write_env(content)
    log(f"✅ .env → WORKER_CONCURRENCY={concurrency}")


def restart_rag():
    """停止并重启所有服务。"""
    log("🔄 停止服务...")
    subprocess.run([STOP_SH], check=False)
    time.sleep(3)
    log("🚀 重启服务（pipeline 4-worker → hybrid 1-worker）...")
    subprocess.run([START_SH], check=False)
    time.sleep(5)
    log("✅ 服务重启完成")


def get_queue_snapshot() -> dict:
    """返回当前队列各状态计数。"""
    db = SessionLocal()
    try:
        from sqlalchemy import func
        rows = (
            db.query(IngestJob.status, func.count(IngestJob.id))
            .group_by(IngestJob.status)
            .all()
        )
        counts = {status: cnt for status, cnt in rows}
        return counts
    finally:
        db.close()


def is_queue_clear(counts: dict) -> bool:
    """队列清空：没有 QUEUED / RUNNING / RETRY 状态的任务。"""
    pending = counts.get("QUEUED", 0) + counts.get("RUNNING", 0) + counts.get("RETRY", 0)
    return pending == 0


def find_low_quality_docs(threshold: float) -> list:
    """找出低于阈值或解析失败的文档。"""
    db = SessionLocal()
    try:
        low = (
            db.query(Document)
            .filter(
                Document.parse_status == "INDEXED",
                Document.parse_quality_score < threshold,
                Document.parse_quality_score > 0,
            )
            .order_by(Document.parse_quality_score.asc())
            .all()
        )
        failed = (
            db.query(Document)
            .filter(Document.parse_status == "PARSE_FAILED")
            .all()
        )
        seen = {d.id for d in low}
        all_docs = low + [d for d in failed if d.id not in seen]
        return [(d.id, d.document_code, d.original_name or "", d.parse_quality_score)
                for d in all_docs]
    finally:
        db.close()


def requeue_docs(doc_ids: list[int]):
    """清除旧 job，重新创建 QUEUED job，重置文档状态。"""
    db = SessionLocal()
    try:
        for doc_id in doc_ids:
            # 删旧 job
            db.query(IngestJob).filter(IngestJob.document_id == doc_id).delete()
            # 新建 job
            db.add(IngestJob(
                document_id=doc_id,
                status="QUEUED",
                attempts=0,
                max_attempts=2,
                message="hybrid-engine 高精度重解析",
            ))
            # 重置文档
            doc = db.get(Document, doc_id)
            if doc:
                doc.parse_status = "QUEUED"
                doc.parse_message = "等待 hybrid-engine 高精度重解析"
        db.commit()
        log(f"✅ 已将 {len(doc_ids)} 个文件重新加入队列")
    finally:
        db.close()


# ──────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="自动化双轮解析质量流水线")
    parser.add_argument("--threshold", type=float, default=60.0,
                        help="质量分阈值（默认 60）")
    parser.add_argument("--poll", type=int, default=30,
                        help="轮询间隔秒数（默认 30）")
    args = parser.parse_args()

    log("=" * 60)
    log("🚀 自动化解析质量流水线启动")
    log(f"   质量阈值：{args.threshold} 分")
    log(f"   轮询间隔：{args.poll} 秒")
    log("=" * 60)

    # ── 第一阶段：等待 pipeline 全部跑完 ─────────────────────────────────
    log("\n📋 第一阶段：等待 pipeline（4-worker）完成所有任务...")
    while True:
        counts = get_queue_snapshot()
        queued   = counts.get("QUEUED",    0)
        running  = counts.get("RUNNING",   0)
        retry    = counts.get("RETRY",     0)
        done     = counts.get("COMPLETED", 0)
        failed   = counts.get("FAILED",    0)

        log(f"   队列状态 → 排队:{queued}  运行:{running}  重试:{retry}  "
            f"完成:{done}  失败:{failed}")

        if is_queue_clear(counts):
            log("✅ 第一阶段完成，pipeline 队列已清空！")
            break

        time.sleep(args.poll)

    # ── 打分阶段：找低分文件 ──────────────────────────────────────────────
    log("\n🔍 打分检查阶段：扫描解析质量...")
    low_docs = find_low_quality_docs(args.threshold)

    if args.dry_run:
        print(f"\n[dry-run] 不写数据库，退出。")
        return

    if not low_docs:
        log("🎉 所有文件质量良好！无需 hybrid 重跑。流水线结束。")
        return

    log(f"\n⚠️  发现 {len(low_docs)} 个低质量/失败文件，将用 hybrid-engine 重解析：")
    log(f"{'分数':>6}  {'文件名'}")
    log("-" * 60)
    for doc_id, code, name, score in low_docs:
        score_str = f"{score:.1f}" if score else "FAIL"
        log(f"{score_str:>6}  {name[:52] or code}")

    # ── 第二阶段：切换 hybrid，重跑低分文件 ──────────────────────────────
    log("\n🔄 第二阶段：切换到 hybrid-engine（串行，高精度）...")

    # 1. 修改 .env
    set_worker_concurrency(1)

    # 2. 把低分文件重新入队
    requeue_docs([doc_id for doc_id, *_ in low_docs])

    # 3. 重启 RAG 服务载入新配置
    restart_rag()

    # 4. 等待 hybrid 轮跑完
    log("\n📋 等待 hybrid-engine 完成精解析...")
    while True:
        counts = get_queue_snapshot()
        queued  = counts.get("QUEUED",    0)
        running = counts.get("RUNNING",   0)
        retry   = counts.get("RETRY",     0)
        done    = counts.get("COMPLETED", 0)
        failed  = counts.get("FAILED",    0)

        log(f"   hybrid 队列 → 排队:{queued}  运行:{running}  重试:{retry}  "
            f"完成:{done}  失败:{failed}")

        if is_queue_clear(counts):
            log("✅ 第二阶段完成，hybrid-engine 队列已清空！")
            break

        time.sleep(args.poll)

    # ── 收尾：恢复 pipeline 4-worker ─────────────────────────────────────
    log("\n🔄 收尾：恢复 pipeline 后端（4-worker）...")
    set_worker_concurrency(4)
    restart_rag()

    # ── 最终统计 ──────────────────────────────────────────────────────────
    log("\n" + "=" * 60)
    log("🎉 自动化解析质量流水线全部完成！")
    final_low = find_low_quality_docs(args.threshold)
    if final_low:
        log(f"⚠️  仍有 {len(final_low)} 个文件质量不达标（可能是扫描质量差）：")
        for _, code, name, score in final_low:
            log(f"   {score:.1f}分  {name[:50] or code}")
    else:
        log("✅ 全部文件质量达标！")
    log("=" * 60)


if __name__ == "__main__":
    main()
