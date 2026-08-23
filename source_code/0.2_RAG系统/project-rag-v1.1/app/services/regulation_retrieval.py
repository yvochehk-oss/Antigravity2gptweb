"""
regulation_retrieval.py
======================
法规知识引擎核心检索服务。

架构：
    ① LLM/规则解析问题 → 提取关键维度
    ② Metadata 硬过滤 → 生效/失效/地区/税种
    ③ PostgreSQL FTS / BM25 → 关键词检索
    ④ Embedding 语义补召回 → 处理口语化查询
    ⑤ RRF 合并
    ⑥ 可选 Reranker (第一版可跳过)
    ⑦ 长上下文模型精读 → 输出结论和引用

对小规模法规库(≤500份)，这套方案比纯向量RAG更可靠。
"""

import json, math, re, logging
from datetime import datetime
from collections import Counter
from sqlalchemy import select, and_, or_, func, text
from ..models import Regulation, RegulationArticle
from ..config import IS_POSTGRES, EMBEDDING_DIM, RERANK_TOP_N
from .embeddings import embed, cosine
from .reranker import rerank

logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+")
def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall((text or "").lower())

def _bm25(query_tokens: list[str], docs_tokens: list[list[str]]) -> list[float]:
    """Python BM25 实现，适用于中文"""
    n = len(docs_tokens) or 1
    avg = sum(len(x) for x in docs_tokens) / n or 1
    df = Counter()
    for ds in docs_tokens:
        for t in set(ds):
            df[t] += 1
    
    scores = []
    k1, b = 1.5, 0.75
    for ds in docs_tokens:
        tf = Counter(ds)
        dl = len(ds)
        score = 0.0
        for q in query_tokens:
            if not tf[q]:
                continue
            idf = math.log(1 + (n - df[q] + 0.5) / (df[q] + 0.5))
            score += idf * (tf[q] * (k1 + 1)) / (tf[q] + k1 * (1 - b + b * dl / avg))
        scores.append(score)
    
    mx = max(scores) if scores else 0
    return [s / mx if mx else 0 for s in scores]


def _parse_query_dimensions(query: str) -> dict:
    """从用户查询中提取关键维度，用于 Metadata 过滤"""
    dims = {
        "jurisdiction": None,
        "tax_type": None,
        "industry": None,
        "legal_level": None,
        "effective_date": None,
    }
    q_lower = query.lower()
    
    # 地区识别
    region_map = {
        "四川": ["四川", "成都市", "成都"],
        "北京": ["北京"],
        "上海": ["上海"],
        "广东": ["广东", "广州", "深圳"],
        "浙江": ["浙江", "杭州"],
        "江苏": ["江苏", "南京", "苏州"],
        "全国": ["全国", "中华人民共和国", "财政部", "税务总局", "国务院"],
    }
    for region, keywords in region_map.items():
        if any(k in q_lower for k in keywords):
            dims["jurisdiction"] = region
            break
    
    # 税种识别
    tax_map = {
        "增值税": ["增值税", "进项税", "销项税", "简易计税", "一般纳税人", "小规模"],
        "企业所得税": ["企业所得税", "所得税", "企业所得"],
        "个人所得税": ["个人所得税", "个税"],
        "房产税": ["房产税"],
        "土地增值税": ["土地增值税"],
        "印花税": ["印花税"],
        "城建税": ["城建税", "城市维护建设税"],
    }
    for tax, keywords in tax_map.items():
        if any(k in q_lower for k in keywords):
            dims["tax_type"] = tax
            break
    
    # 行业识别
    industry_map = {
        "建筑业": ["建筑", "施工", "工程", "劳务"],
        "房地产业": ["房地产", "地产", "买房", "商品房"],
        "制造业": ["制造", "生产"],
        "服务业": ["服务", "咨询"],
    }
    for ind, keywords in industry_map.items():
        if any(k in q_lower for k in keywords):
            dims["industry"] = ind
            break
    
    return dims


def _build_fts_filter(query: str, search_fields: list[str]) -> str:
    """构建 PostgreSQL 全文搜索查询"""
    q_tokens = tokens(query)
    if not q_tokens:
        return ""
    
    # 转为 tsquery 格式（简单版本，关键词AND组合）
    ts_tokens = " & ".join(q_tokens)
    return ts_tokens


def retrieve_regulations(
    db,
    query: str,
    jurisdiction: str | None = None,
    tax_type: str | None = None,
    industry: str | None = None,
    status: str = "effective",
    legal_level: str | None = None,
    effective_date: str | None = None,
    top_k: int = 10,
    include_expired: bool = False,
    use_rerank: bool = True,
) -> list[dict]:
    """
    法规混合检索主函数
    
    检索策略：
    1. Metadata 硬过滤
    2. BM25 全文检索
    3. Embedding 语义补召回
    4. RRF 合并
    """
    today = datetime.now().strftime("%Y-%m-%d")
    
    # 构建基础查询
    conditions = []
    
    # 状态过滤
    if not include_expired:
        status_cond = Regulation.status == "effective"
        if effective_date:
            status_cond = and_(
                status_cond,
                or_(Regulation.effective_date.is_(None), Regulation.effective_date <= effective_date),
                or_(Regulation.expiry_date.is_(None), Regulation.expiry_date >= effective_date)
            )
        else:
            status_cond = and_(
                status_cond,
                or_(Regulation.effective_date.is_(None), Regulation.effective_date <= today),
                or_(Regulation.expiry_date.is_(None), Regulation.expiry_date >= today)
            )
        conditions.append(status_cond)
    elif status:
        conditions.append(Regulation.status == status)
    
    # Metadata 维度过滤
    if jurisdiction:
        conditions.append(Regulation.jurisdiction.in_([jurisdiction, "全国"]))
    if tax_type:
        conditions.append(or_(Regulation.tax_type == "", Regulation.tax_type.contains(tax_type)))
    if industry:
        conditions.append(or_(Regulation.industry == "", Regulation.industry.contains(industry)))
    if legal_level:
        conditions.append(Regulation.legal_level == legal_level)
    
    # 查询法规
    stmt = select(Regulation)
    if conditions:
        stmt = stmt.where(and_(*conditions))
    
    try:
        regulations = db.execute(stmt.order_by(Regulation.publish_date.desc())).scalars().all()
    except Exception as e:
        logger.warning(f"法规查询失败: {e}")
        regulations = []
    
    if not regulations:
        return []
    
    # --- BM25 全文检索 ---
    q_tokens = tokens(query)
    all_search_texts = [(r.id, r.search_text or r.title or "") for r in regulations]
    all_search_tokens = [tokens(s) for _, s in all_search_texts]
    bm_scores = _bm25(q_tokens, all_search_tokens)
    
    bm_rank = sorted(range(len(regulations)), key=lambda i: bm_scores[i], reverse=True)
    lexical_candidates = bm_rank[:max(RERANK_TOP_N, top_k * 3)]
    
    # --- Embedding 语义召回 ---
    vector_scores = {}
    try:
        qvec = embed(query)
        if len(qvec) == EMBEDDING_DIM:
            for r in regulations:
                if r.embedding:
                    if IS_POSTGRES:
                        vs = max(0.0, cosine(qvec, r.embedding))
                    else:
                        try:
                            vec = json.loads(r.embedding_json or "[]")
                            vs = max(0.0, cosine(qvec, vec))
                        except:
                            vs = 0.0
                    if vs > 0:
                        vector_scores[r.id] = vs
    except Exception as e:
        logger.debug(f"Embedding 召回失败: {e}")
    
    # --- RRF 合并 ---
    score_map = {}
    reg_map = {}
    
    # 词汇搜索得分
    for rank, idx in enumerate(lexical_candidates, 1):
        r = regulations[idx]
        reg_map[r.id] = r
        score_map[r.id] = score_map.get(r.id, 0) + 1.0 / (60 + rank)
    
    # 向量搜索得分
    for rid, vs in vector_scores.items():
        reg_map[rid] = reg_map.get(rid)
        if reg_map.get(rid) is None:
            for r in regulations:
                if r.id == rid:
                    reg_map[rid] = r
                    break
        score_map[rid] = score_map.get(rid, 0) + vs
    
    # 合并排序
    merged = sorted(score_map.items(), key=lambda kv: kv[1], reverse=True)
    candidates = []
    
    for rid, score in merged[:max(RERANK_TOP_N, top_k * 3)]:
        r = reg_map.get(rid)
        if r is None:
            continue
        
        candidates.append({
            "score": round(float(score), 6),
            "regulation_id": r.id,
            "title": r.title,
            "document_no": r.document_no,
            "issuer": r.issuer,
            "legal_level": r.legal_level,
            "jurisdiction": r.jurisdiction,
            "tax_type": r.tax_type,
            "industry": r.industry,
            "publish_date": r.publish_date,
            "effective_date": r.effective_date,
            "expiry_date": r.expiry_date,
            "status": r.status,
            "source": r.source,
            "text": r.full_text or r.title,
            "search_text": r.search_text or "",
        })
    
    # Reranker（可选，第一版可跳过）
    if use_rerank and candidates:
        reranked = rerank(query, candidates, top_k)
        return reranked
    
    return candidates[:top_k]


def retrieve_regulation_articles(
    db,
    regulation_id: int,
    query: str,
    top_k: int = 5,
) -> list[dict]:
    """
    检索特定法规的条款，支持条款级命中和大粒度读取
    """
    # 查询该法规的所有条款
    stmt = select(RegulationArticle).where(
        RegulationArticle.regulation_id == regulation_id
    ).order_by(RegulationArticle.sort_order, RegulationArticle.article_no)
    
    articles = db.execute(stmt).scalars().all()
    
    if not articles:
        return []
    
    # BM25 检索
    q_tokens = tokens(query)
    article_tokens = [tokens(a.search_text or a.text) for a in articles]
    bm_scores = _bm25(q_tokens, article_tokens)
    
    ranked = sorted(range(len(articles)), key=lambda i: bm_scores[i], reverse=True)
    
    results = []
    for idx in ranked[:top_k]:
        a = articles[idx]
        results.append({
            "article_id": a.id,
            "regulation_id": a.regulation_id,
            "chapter": a.chapter,
            "article_no": a.article_no,
            "paragraph_no": a.paragraph_no,
            "heading": a.heading,
            "text": a.text,
            "full_chapter_text": a.full_chapter_text,
            "score": round(bm_scores[idx], 4),
        })
    
    return results


def expand_context(articles: list[dict], all_articles: list[dict]) -> str:
    """
    将命中的条款扩展为完整上下文：
    - 包含前一条
    - 包含命中条款全文
    - 包含后一条
    - 必要时包含完整章节
    """
    if not articles:
        return ""
    
    contexts = []
    for art in articles:
        # 添加条款前后的上下文
        art_no = art.get("article_no", "")
        art_idx = next((i for i, a in enumerate(all_articles) if a.article_no == art_no), -1)
        
        if art_idx > 0:
            prev = all_articles[art_idx - 1]
            contexts.append(f"【前一条 {prev['article_no']}】{prev['text'][:500]}")
        
        contexts.append(f"【{art['article_no']}】{art['text']}")
        
        if art_idx < len(all_articles) - 1 and art_idx >= 0:
            next_art = all_articles[art_idx + 1]
            contexts.append(f"【后一条 {next_art['article_no']}】{next_art['text'][:500]}")
        
        if art.get("full_chapter_text"):
            contexts.append(f"【{art['chapter']}完整章节】\n{art['full_chapter_text'][:2000]}")
    
    return "\n\n".join(contexts)


def answer_regulation_query(query: str, regulations: list[dict], articles: list[dict] | None = None) -> dict:
    """
    使用长上下文模型回答法规问题
    
    返回结构：
    {
        "conclusion": "结论",
        "conditions": ["适用条件1", "适用条件2"],
        "exceptions": ["例外情况"],
        "legal_basis": "法规依据",
        "citations": [{"title": "", "article_no": "", "text": ""}],
        "risk_alerts": ["风险提示"],
    }
    """
    from .llm import answer_with_llm
    
    # 构建上下文
    reg_texts = []
    for r in regulations[:5]:
        text = f"【{r['title']}】{r.get('document_no', '')}"
        if r.get('effective_date'):
            text += f"（生效日期：{r['effective_date']}）"
        text += f"\n{r.get('text', r.get('search_text', ''))[:3000]}"
        reg_texts.append(text)
    
    context = "\n\n---\n\n".join(reg_texts)
    
    prompt = f"""你是建筑税务法规专家。请根据提供的法规资料回答问题。

【查询】
{query}

【法规资料】
{context}

【回答要求】
请按以下格式回答：
1. **结论**：直接给出答案
2. **适用条件**：列出具体条件（如有）
3. **例外情况**：列出特殊情况（如有）
4. **法规依据**：引用具体法规名称和文号
5. **条款引用**：引用具体条款编号和内容摘要
6. **风险提示**：使用时需要注意的风险（如有）

如果资料不足以回答，请明确说明。"""
    
    answer = answer_with_llm(query, [{"text": context, "filename": "法规库", "page_start": 1}])
    
    return {
        "query": query,
        "answer": answer,
        "regulation_count": len(regulations),
        "citations": [
            {"title": r["title"], "document_no": r["document_no"], "jurisdiction": r["jurisdiction"]}
            for r in regulations[:5]
        ]
    }
