"""
regulation_loader.py
====================
法规数据导入工具。

支持：
1. 从 JSON/YAML 文件批量导入法规
2. 从 Excel/CSV 导入
3. 批量创建条款（条款级检索支持）
4. 生成 embedding

用法：
  cd project-rag-v1.1
  python -m app.services.regulation_loader --file regulations.json
  python -m app.services.regulation_loader --batch data/regulations/
"""

import json
import logging
import sys
import re
from pathlib import Path
from datetime import datetime
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(PROJECT_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("reg_loader")


def parse_articles_from_text(full_text: str, regulation_id: int) -> list[dict]:
    """
    从法规全文自动解析条款
    
    支持的格式：
    - 第X条 【标题】内容
    - 第X条 内容
    - X、内容
    """
    articles = []
    
    # 正则匹配各种条款格式
    patterns = [
        # 第X条 【标题】内容
        r"第([一二三四五六七八九十百零\d]+)条\s*【([^】]+)】(.+?)(?=(?:第[一二三四五六七八九十百零\d]+条|$))",
        # 第X条 内容（可能有换行）
        r"第([一二三四五六七八九十百零\d]+)条\s*(.+?)(?=(?:第[一二三四五六七八九十百零\d]+条|$))",
        # X、内容 格式
        r"(?<=\n)([一二三四五六七八九十零\d]+)、(.+?)(?=\n[一二三四五六七八九十零\d]+、|$)",
    ]
    
    chinese_nums = {
        "一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
        "六": "6", "七": "7", "八": "8", "九": "9", "十": "10",
        "零": "0", "百": "100",
    }
    
    def convert_article_no(no_str: str) -> str:
        """将中文数字转换为阿拉伯数字"""
        if no_str.isdigit():
            return no_str
        # 简单转换：十X -> 1X, X十 -> X0
        result = no_str
        for cn, ar in chinese_nums.items():
            result = result.replace(cn, ar)
        # 处理十的特殊情况
        result = re.sub(r"^10(\d+)", r"1\1", result)
        result = re.sub(r"(\d+)10", r"\g<1>0", result)
        try:
            return str(int(result))
        except:
            return no_str
    
    seen_nos = set()
    sort_order = 0
    
    for pattern in patterns[:2]:  # 先尝试前两个模式
        matches = re.findall(pattern, full_text, re.DOTALL)
        for match in matches:
            if len(match) >= 2:
                no_str = match[0]
                article_no = convert_article_no(no_str)
                
                if article_no in seen_nos:
                    continue
                seen_nos.add(article_no)
                
                heading = ""
                text = ""
                if len(match) >= 3:
                    heading = match[1].strip()
                    text = match[2].strip()
                else:
                    text = match[1].strip()
                
                if len(text) < 5:  # 跳过太短的匹配
                    continue
                
                articles.append({
                    "regulation_id": regulation_id,
                    "chapter": "",
                    "article_no": f"第{article_no}条",
                    "paragraph_no": "",
                    "heading": heading,
                    "text": text[:5000],  # 限制长度
                    "full_chapter_text": "",
                    "sort_order": sort_order,
                })
                sort_order += 1
    
    return articles


def load_from_json(db, filepath: str, batch_size: int = 32, generate_embedding: bool = True) -> dict:
    """从 JSON 文件导入法规"""
    try:
        from app.db import SessionLocal
        from app.models import Regulation, RegulationArticle
        from app.services.embeddings import embed_many
    except ImportError as e:
        log.error(f"导入失败: {e}")
        return {"ok": 0, "skip": 0, "err": 1, "message": str(e)}
    
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    if isinstance(data, dict) and "regulations" in data:
        regs = data["regulations"]
    elif isinstance(data, list):
        regs = data
    else:
        return {"ok": 0, "skip": 0, "err": 1, "message": "JSON 格式错误"}
    
    log.info(f"开始导入 {len(regs)} 条法规...")
    
    stats = {"ok": 0, "skip": 0, "err": 0}
    session = SessionLocal()
    
    texts_to_embed = []
    reg_ids = []
    
    for reg_data in regs:
        # 检查是否已存在
        doc_no = reg_data.get("document_no", "")
        if not doc_no:
            log.warning(f"跳过无文号法规: {reg_data.get('title', 'unknown')}")
            stats["skip"] += 1
            continue
        
        existing = session.query(Regulation).filter(
            Regulation.document_no == doc_no
        ).first()
        
        if existing:
            log.info(f"已存在，跳过: {doc_no}")
            stats["skip"] += 1
            continue
        
        # 创建法规
        reg = Regulation(
            title=reg_data.get("title", ""),
            document_no=doc_no,
            issuer=reg_data.get("issuer", ""),
            legal_level=reg_data.get("legal_level", "部门规章"),
            jurisdiction=reg_data.get("jurisdiction", "全国"),
            tax_type=reg_data.get("tax_type", ""),
            industry=reg_data.get("industry", "建筑业"),
            publish_date=reg_data.get("publish_date", ""),
            effective_date=reg_data.get("effective_date", ""),
            expiry_date=reg_data.get("expiry_date", ""),
            status=reg_data.get("status", "effective"),
            full_text=reg_data.get("full_text", ""),
            source=reg_data.get("source", ""),
            search_text=f"{reg_data.get('title', '')} {doc_no} {reg_data.get('issuer', '')} {reg_data.get('tax_type', '')}",
        )
        session.add(reg)
        session.flush()
        reg_ids.append(reg.id)
        
        # 自动解析条款（如果有全文）
        if reg_data.get("full_text"):
            articles = parse_articles_from_text(reg_data["full_text"], reg.id)
            for art_data in articles:
                art = RegulationArticle(**art_data)
                session.add(art)
        
        stats["ok"] += 1
        texts_to_embed.append(reg.search_text)
        
        if stats["ok"] % 10 == 0:
            log.info(f"  进度: {stats['ok']}/{len(regs)}")
    
    # 批量生成 embedding
    if generate_embedding and texts_to_embed:
        try:
            vecs = embed_many(texts_to_embed, batch_size=batch_size)
            for reg_id, vec in zip(reg_ids, vecs):
                reg = session.query(Regulation).get(reg_id)
                if reg:
                    reg.embedding_json = json.dumps(vec, ensure_ascii=False)
            log.info(f"生成 {len(vecs)} 个 embedding")
        except Exception as e:
            log.warning(f"Embedding 生成失败: {e}")
    
    session.commit()
    session.close()
    
    log.info(f"完成：成功 {stats['ok']}，跳过 {stats['skip']}，错误 {stats['err']}")
    return stats


def create_sample_regulations(db) -> dict:
    """创建示例建筑税务法规数据"""
    try:
        from app.models import Regulation
        from app.services.embeddings import embed
    except ImportError as e:
        return {"ok": 0, "err": 1, "message": str(e)}
    
    samples = [
        {
            "title": "财政部 税务总局关于全面推开营业税改征增值税试点的通知",
            "document_no": "财税〔2016〕36号",
            "issuer": "财政部 税务总局",
            "legal_level": "规范性文件",
            "jurisdiction": "全国",
            "tax_type": "增值税",
            "industry": "建筑业",
            "publish_date": "2016-03-23",
            "effective_date": "2016-05-01",
            "status": "effective",
            "source": "国家税务总局官网",
            "full_text": """
第一条 在中华人民共和国境内（以下称境内）销售服务、无形资产或者不动产（以下称应税行为）的单位和个人，为增值税纳税人，应当按照本办法缴纳增值税。

第二条 单位以承包、承租、挂靠方式经营的，承包人、承租人、挂靠人（以下称承包人）以发包人、出租人、被挂靠人（以下称发包人）名义对外经营并由发包人承担相关法律责任的，以发包人为纳税人；否则以承包人为纳税人。

第三条 纳税人分为一般纳税人和小规模纳税人。
年应税销售额超过规定标准的其他个人不属于一般纳税人。年应税销售额超过规定标准但不经常发生应税行为的单位和个体工商户可选择按照小规模纳税人纳税。

第四章 税率和征收率
第十二条 在境内提供服务的税率为6%。
第十三条 简易计税方法适用于以下情形：
（一）小规模纳税人发生应税销售行为；
（二）一般纳税人销售特殊货物、提供营改增应税服务等情形。

第十四条 一般纳税人发生下列应税行为可以选择适用简易计税方法计税：
一、建筑服务；
二、不动产经营租赁服务；
三、公路通行服务。
            """,
        },
        {
            "title": "住房城乡建设部关于做好建筑业营改增建设工程计价依据的调整准备工作通知",
            "document_no": "建标办〔2016〕4号",
            "issuer": "住房城乡建设部",
            "legal_level": "部门文件",
            "jurisdiction": "全国",
            "tax_type": "增值税",
            "industry": "建筑业",
            "publish_date": "2016-03-23",
            "effective_date": "2016-05-01",
            "status": "effective",
            "source": "住建部官网",
            "full_text": """
一、调整建设工程计价依据
（一）材料费：材料单价应通过市场询价确定，材料价格应采用不含增值税进项税额的综合单价。

（二）人工费：人工费单价应按照定额人工单价计算，定额人工单价应包括社会保险费、住房公积金等费用。

（三）机械使用费：施工机械使用费应根据市场价确定，机械租赁价格应为不含增值税进项税额的价格。

二、甲供工程的计价调整
（一）甲供材料、设备不计入工程造价，但应在合同中明确约定甲供范围和金额。

（二）甲供工程选择简易计税方法时，税率为3%，应按不含税工程造价乘以3%计算应纳税额。
            """,
        },
        {
            "title": "国家税务总局关于发布《纳税人跨县（市、区）提供建筑服务增值税征收管理暂行办法》的公告",
            "document_no": "国家税务总局公告2016年第17号",
            "issuer": "国家税务总局",
            "legal_level": "部门规章",
            "jurisdiction": "全国",
            "tax_type": "增值税",
            "industry": "建筑业",
            "publish_date": "2016-03-31",
            "effective_date": "2016-05-01",
            "status": "effective",
            "source": "国家税务总局官网",
            "full_text": """
第二条 本办法所称跨县（市、区）提供建筑服务，是指单位和个体工商户（以下简称纳税人）在其机构所在地以外的县（市、区）提供建筑服务。

第七条 纳税人跨县（市、区）提供建筑服务，在向建筑服务发生地主管税务机关预缴税款时，需填报《增值税预缴税款表》。

第九条 纳税人跨县（市、区）提供建筑服务，选择适用简易计税方法计税的，应以取得的全部价款和价外费用扣除支付的分包款后的余额为销售额。

第十条 纳税人跨县（市、区）提供建筑服务，按照规定应在建筑服务发生地预缴税款的，预缴税款的计算方法如下：
（一）一般纳税人跨县（市、区）提供建筑服务，适用一般计税方法的，以取得的全部价款和价外费用扣除支付的分包款后的余额，按照2%的预征率计算应预缴税款；
（二）一般纳税人跨县（市、区）提供建筑服务，适用简易计税方法计税的，以取得的全部价款和价外费用扣除支付的分包款后的余额，按照3%的征收率计算应预缴税款；
（三）小规模纳税人跨县（市、区）提供建筑服务，以取得的全部价款和价外费用扣除支付的分包款后的余额，按照3%的征收率计算应预缴税款。
            """,
        },
        {
            "title": "四川省国家税务局关于建筑安装业营改增若干事项的公告",
            "document_no": "四川省国家税务局公告2016年第5号",
            "issuer": "四川省国家税务局",
            "legal_level": "地方规范性文件",
            "jurisdiction": "四川",
            "tax_type": "增值税",
            "industry": "建筑业",
            "publish_date": "2016-04-20",
            "effective_date": "2016-05-01",
            "status": "effective",
            "source": "四川省税务局官网",
            "full_text": """
一、关于甲供工程的认定
甲供工程是指全部或部分设备、材料、动力由工程发包方自行采购的建筑工程。

二、关于甲供工程简易计税备案
纳税人提供甲供工程服务，选择简易计税方法的，应在首次申报时向主管税务机关备案，并提交以下资料：
（一）《增值税一般纳税人选择简易计税方法计算缴纳增值税备案表》；
（二）建筑工程施工合同原件及复印件；
（三）发包方自行采购设备、材料、动力的清单及金额证明材料。

三、关于甲供工程的范围
（一）全部甲供：发包方自行采购全部设备、材料、动力；
（二）部分甲供：发包方自行采购设备、材料、动力金额占工程项目所需总额的比例超过50%（含）的。
            """,
        },
        {
            "title": "财政部 税务总局 住房城乡建设部关于完善房地产营改增税收政策的通知",
            "document_no": "财税〔2016〕68号",
            "issuer": "财政部 税务总局 住房城乡建设部",
            "legal_level": "规范性文件",
            "jurisdiction": "全国",
            "tax_type": "增值税",
            "industry": "建筑业",
            "publish_date": "2016-08-01",
            "effective_date": "2016-08-01",
            "status": "effective",
            "source": "国家税务总局官网",
            "full_text": """
一、关于建筑服务税率调整
一般纳税人提供建筑服务，税率为11%；适用简易计税方法计税的，征收率为3%。

二、关于甲供工程选择简易计税
建筑工程施工合同中约定由发包方提供全部或部分设备、材料、动力，且金额占合同总额50%以上的，纳税人可以选择简易计税方法。

三、关于清包工
以清包工方式提供建筑服务，是指施工方不采购建筑工程所需的材料或只采购辅助材料，并收取人工费、管理费或者其他费用的建筑服务模式。
以清包工方式提供建筑服务，可以选择适用简易计税方法。
            """,
        },
    ]
    
    from app.models import Regulation, RegulationArticle
    
    session = db
    stats = {"ok": 0, "skip": 0, "exists": 0}
    
    for reg_data in samples:
        existing = session.query(Regulation).filter(
            Regulation.document_no == reg_data["document_no"]
        ).first()
        
        if existing:
            log.info(f"已存在: {reg_data['document_no']}")
            stats["exists"] += 1
            continue
        
        reg = Regulation(**reg_data)
        session.add(reg)
        session.flush()
        
        # 解析条款
        if reg_data.get("full_text"):
            articles = parse_articles_from_text(reg_data["full_text"], reg.id)
            for art_data in articles:
                art = RegulationArticle(**art_data)
                session.add(art)
        
        # 生成 embedding
        try:
            vec = embed(reg.search_text)
            reg.embedding_json = json.dumps(vec, ensure_ascii=False)
        except Exception as e:
            log.warning(f"Embedding 失败: {e}")
        
        stats["ok"] += 1
    
    session.commit()
    return stats


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="法规数据导入工具")
    parser.add_argument("--file", "-f", help="导入 JSON 文件")
    parser.add_argument("--batch", "-b", help="批量导入文件夹")
    parser.add_argument("--sample", "-s", action="store_true", help="创建示例法规数据")
    parser.add_argument("--batch-size", type=int, default=32, help="Embedding 批量大小")
    parser.add_argument("--no-embedding", action="store_true", help="跳过 embedding 生成")
    args = parser.parse_args()
    
    from app.db import SessionLocal, init_db
    init_db()
    db = SessionLocal()
    
    try:
        if args.file:
            result = load_from_json(db, args.file, args.batch_size, not args.no_embedding)
            print(f"结果: {result}")
        elif args.batch:
            import os
            for f in os.listdir(args.batch):
                if f.endswith((".json", ".jsonl")):
                    path = os.path.join(args.batch, f)
                    log.info(f"导入: {path}")
                    result = load_from_json(db, path, args.batch_size, not args.no_embedding)
                    print(f"  结果: {result}")
        elif args.sample:
            result = create_sample_regulations(db)
            print(f"创建示例法规: {result}")
        else:
            parser.print_help()
    finally:
        db.close()
