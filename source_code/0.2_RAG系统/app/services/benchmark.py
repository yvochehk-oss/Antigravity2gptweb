"""Automatic RAG evaluation / benchmark service.

Provides offline metrics for retrieval-augmented generation systems:
recall@k, MRR, citation recall, faithfulness, no-answer accuracy.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..logging_config import get_logger
from ..models import BenchmarkQuestion, BenchmarkRun, Document, Chunk, Project

logger = get_logger(__name__)


# ============================================
# Public metric functions
# ============================================

def compute_recall_at_k(retrieved_doc_ids: list[int], gold_doc_ids: list[int], k: int) -> float:
    """Binary recall@k based on document id overlap.

    Returns 1.0 if any gold_doc_id is present in the first k retrieved ids,
    otherwise 0.0. Empty gold list yields 0.0.
    """
    if not gold_doc_ids:
        return 0.0
    top_k_ids = set(retrieved_doc_ids[:max(0, int(k))])
    if any(g in top_k_ids for g in gold_doc_ids):
        return 1.0
    return 0.0


def compute_mrr(retrieved_doc_ids: list[int], gold_doc_ids: list[int]) -> float:
    """Reciprocal rank of the first gold document in the retrieved list.

    Position 1 -> 1.0, position 2 -> 0.5, etc. No match -> 0.0.
    """
    if not gold_doc_ids:
        return 0.0
    gold_set = set(gold_doc_ids)
    for idx, doc_id in enumerate(retrieved_doc_ids, start=1):
        if doc_id in gold_set:
            return 1.0 / idx
    return 0.0


def compute_citation_recall(answer: str, gold_keywords: list[str]) -> float:
    """Fraction of gold keywords that appear in the answer text."""
    if not gold_keywords:
        return 0.0
    answer_text = answer or ""
    hits = sum(1 for kw in gold_keywords if kw and kw in answer_text)
    return hits / len(gold_keywords)


def compute_faithfulness(answer: str, evidence: list[dict]) -> float:
    """Heuristic faithfulness score.

    Score components:
      - Fraction of sentences that contain a numbered citation like [1]/[2].
      - Bonus when the answer mentions at least one source filename in the form
        【xxx】 or 《xxx》 indicating direct file citation.

    No evidence -> 0.0. The maximum returned is 1.0.
    """
    if not evidence:
        return 0.0
    answer_text = (answer or "").strip()
    if not answer_text:
        return 0.0

    # Sentence split supporting Chinese punctuation
    sentence_pattern = re.compile(r"[^。！？!?；;\.]+[。！？!?；;\.]*")
    sentences = [s.strip() for s in sentence_pattern.findall(answer_text) if s.strip()]
    if not sentences:
        sentences = [answer_text]

    citation_re = re.compile(r"\[\d+\]")
    cited = sum(1 for s in sentences if citation_re.search(s))
    cite_ratio = cited / len(sentences)

    file_marker_re = re.compile(r"[【《].*?[】》]")
    file_bonus = 0.0
    if file_marker_re.search(answer_text):
        file_bonus = 0.1

    score = min(1.0, cite_ratio + file_bonus)
    return round(score, 4)


def compute_no_answer_accuracy(
    answerable: bool,
    answer: str,
    evidence_count: int,
) -> float:
    """Evaluate how correctly the system answers an unanswerable question.

    - answerable + answered   (any evidence) -> 1.0
    - answerable + no evidence (漏答)        -> 0.0
    - unanswerable + no evidence (拒绝回答)   -> 1.0
    - unanswerable + answered (over-answer)   -> 0.5
    """
    answer_text = (answer or "").strip()
    answered = bool(answer_text)

    # Heuristic refusal: explicit "无法回答"/"资料不足" content.
    refuse_phrases = (
        "无法回答",
        "无法作答",
        "资料不足",
        "无法提供",
        "没有足够",
        "无法检索",
        "无法找到",
        "not enough",
    )
    refused = answered and any(p in answer_text for p in refuse_phrases)

    if answerable:
        if answered and not refused and evidence_count >= 1:
            return 1.0
        return 0.0

    # answerable == False
    if (not answered) or (refused and evidence_count == 0):
        return 1.0
    if evidence_count == 0:
        return 1.0
    return 0.5


# ============================================
# Document id helpers
# ============================================

def _resolve_gold_doc_ids(
    db: Session,
    project_id: Optional[int],
    question: dict,
) -> list[int]:
    """Return the list of document IDs that count as 'gold' for a question.

    Resolution order:
      1. Use explicit ``gold_document_ids`` from the question, if any.
      2. Otherwise, look up documents whose ``document_type`` matches any value
         in ``gold_document_types`` (scoped to the project if provided).
    """
    gold_ids = list(question.get("gold_document_ids") or [])
    if gold_ids:
        return [int(x) for x in gold_ids if x is not None]

    types = list(question.get("gold_document_types") or [])
    if not types:
        return []

    stmt = select(Document.id).where(Document.document_type.in_(types))
    if project_id:
        stmt = stmt.where(Document.project_id == project_id)
    rows = db.execute(stmt).all()
    return [int(r[0]) for r in rows]


# ============================================
# Benchmark runner
# ============================================

def run_benchmark(
    db: Session,
    project_id: int | None,
    questions: list[dict],
    config: dict | None = None,
) -> dict:
    """Run the full benchmark over a list of question dicts.

    Each question dict must contain: question, gold_answer, answerable,
    gold_keywords, difficulty, category. Optionally gold_document_ids and
    gold_document_types.

    Args:
        db: Database session.
        project_id: Optional project scope for retrieval / gold resolution.
        questions: List of question dicts (raw, including the answerable flag).
        config: Optional config:
            - top_k (int): retrieval depth, default 10
            - deep (bool): whether to enable deep mode (placeholder flag)
            - rewrite (bool): whether to call the rewrite module (placeholder flag)
            - hyde (bool): whether to call the hyde helper (placeholder flag)
            - name (str): logical name of this run

    Returns:
        Dict with: config, averages, per_question list, total_questions, started_at,
        finished_at. Average metric keys are prefixed with ``avg_``.
    """
    from .retrieval import retrieve  # local import to avoid cycles
    try:
        from .llm import answer_with_llm  # type: ignore
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"answer_with_llm unavailable: {e}")
        answer_with_llm = None  # type: ignore

    cfg = dict(config or {})
    top_k = int(cfg.get("top_k") or 10)
    deep = bool(cfg.get("deep", False))
    rewrite = bool(cfg.get("rewrite", False))
    hyde = bool(cfg.get("hyde", False))
    name = str(cfg.get("name") or "")

    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    per_question: list[dict] = []

    metric_keys = (
        "recall_at_k",
        "mrr",
        "citation_recall",
        "faithfulness",
        "no_answer_accuracy",
    )
    totals = {k: 0.0 for k in metric_keys}
    counts = {k: 0 for k in metric_keys}

    for idx, q in enumerate(questions, start=1):
        question_text = q.get("question") or ""
        gold_keywords = list(q.get("gold_keywords") or [])
        answerable = bool(q.get("answerable", True))

        gold_doc_ids = _resolve_gold_doc_ids(db, project_id, q)

        result: dict = {
            "index": idx,
            "question": question_text,
            "category": q.get("category", "general"),
            "difficulty": q.get("difficulty", "medium"),
            "answerable": answerable,
            "gold_document_ids": gold_doc_ids,
        }

        retrieval_error: Optional[str] = None

        try:
            evidence = retrieve(
                db,
                project_id=project_id or 1,
                query=question_text,
                filters={},
                top_k=top_k,
                use_rerank=True,
            )
        except Exception as e:
            logger.error(f"retrieve failed on question {idx}: {e}")
            evidence = []
            retrieval_error = str(e)

        retrieved_doc_ids = [int(x.get("document_id")) for x in evidence if x.get("document_id") is not None]

        # === Compute retrieval metrics ===
        recall = compute_recall_at_k(retrieved_doc_ids, gold_doc_ids, top_k)
        mrr = compute_mrr(retrieved_doc_ids, gold_doc_ids)

        totals["recall_at_k"] += recall
        totals["mrr"] += mrr
        counts["recall_at_k"] += 1
        counts["mrr"] += 1

        # === Generate answer if LLM available ===
        answer_text = ""
        if answer_with_llm is not None:
            try:
                answer_text = answer_with_llm(question_text, evidence)
            except Exception as e:
                logger.warning(f"answer_with_llm failed on question {idx}: {e}")
                answer_text = ""
        else:
            answer_text = ""

        # === Compute answer quality metrics ===
        cit = compute_citation_recall(answer_text, gold_keywords)
        faith = compute_faithfulness(answer_text, evidence)
        no_ans = compute_no_answer_accuracy(answerable, answer_text, len(evidence))

        totals["citation_recall"] += cit
        totals["faithfulness"] += faith
        totals["no_answer_accuracy"] += no_ans
        counts["citation_recall"] += 1
        counts["faithfulness"] += 1
        counts["no_answer_accuracy"] += 1

        result.update({
            "retrieved_doc_ids": retrieved_doc_ids,
            "evidence_count": len(evidence),
            "retrieval_error": retrieval_error,
            "answer_preview": (answer_text or "")[:300],
            "metrics": {
                "recall_at_k": round(recall, 4),
                "mrr": round(mrr, 4),
                "citation_recall": round(cit, 4),
                "faithfulness": round(faith, 4),
                "no_answer_accuracy": round(no_ans, 4),
            },
        })
        per_question.append(result)

    finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    averages = {}
    for key in metric_keys:
        c = counts[key]
        averages[f"avg_{key}"] = round(totals[key] / c, 4) if c else 0.0

    metrics = {
        **averages,
        "total_questions": len(per_question),
        "answered_with_llm": bool(answer_with_llm is not None),
    }

    return {
        "name": name,
        "project_id": project_id,
        "config": {
            "top_k": top_k,
            "deep": deep,
            "rewrite": rewrite,
            "hyde": hyde,
            "name": name,
        },
        "metrics": metrics,
        "per_question": per_question,
        "total_questions": len(per_question),
        "started_at": started_at,
        "finished_at": finished_at,
    }


# ============================================
# Persistence helpers
# ============================================

def persist_benchmark_run(db: Session, run_result: dict) -> int:
    """Persist a benchmark run result to ``BenchmarkRun`` and return the new id.

    The metric dict, config and per-question list are JSON-encoded. The function
    is idempotent only at the row level (each call inserts a new row).
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    row = BenchmarkRun(
        project_id=run_result.get("project_id"),
        config_json=json.dumps(run_result.get("config") or {}, ensure_ascii=False),
        metrics_json=json.dumps(run_result.get("metrics") or {}, ensure_ascii=False),
        per_question_json=json.dumps(run_result.get("per_question") or [], ensure_ascii=False),
        total_questions=int(run_result.get("total_questions") or 0),
        status="completed",
        started_at=run_result.get("started_at") or now,
        finished_at=run_result.get("finished_at") or now,
        note=run_result.get("name") or "",
    )

    db.add(row)
    db.commit()
    db.refresh(row)

    logger.info(
        f"Persisted BenchmarkRun id={row.id} questions={row.total_questions}"
    )
    return int(row.id)


# ============================================
# Initialization: load questions from JSON
# ============================================

def _default_questions_path() -> Path:
    """Resolve the bundled benchmark_questions.json path."""
    return Path(__file__).resolve().parent / "benchmark_questions.json"


def init_benchmark_questions(
    db: Session,
    questions_json_path: str | None = None,
) -> int:
    """Initialize the ``BenchmarkQuestion`` table from a JSON file.

    Args:
        db: Database session.
        questions_json_path: Optional path to a JSON file with a top-level list
            of question dicts. Defaults to the bundled
            ``app/services/benchmark_questions.json``.

    Returns:
        Number of rows successfully inserted.
    """
    path = Path(questions_json_path) if questions_json_path else _default_questions_path()
    if not path.exists():
        logger.error(f"benchmark questions file not found: {path}")
        return 0

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse benchmark questions JSON: {e}")
        return 0

    if not isinstance(payload, list):
        logger.error("benchmark questions JSON must be a list of question objects")
        return 0

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    inserted = 0

    for item in payload:
        if not isinstance(item, dict):
            continue
        question_text = (item.get("question") or "").strip()
        if not question_text:
            continue

        row = BenchmarkQuestion(
            project_id=item.get("project_id"),
            question=question_text,
            gold_answer=item.get("gold_answer", "") or "",
            answerable=bool(item.get("answerable", True)),
            gold_document_ids_json=json.dumps(
                item.get("gold_document_ids") or [], ensure_ascii=False
            ),
            gold_keywords_json=json.dumps(
                item.get("gold_keywords") or [], ensure_ascii=False
            ),
            difficulty=str(item.get("difficulty") or "medium"),
            category=str(item.get("category") or "general"),
            note=item.get("note", "") or "",
            created_at=now,
        )
        db.add(row)
        inserted += 1

    db.commit()
    logger.info(f"Initialized {inserted} benchmark questions from {path}")
    return inserted
