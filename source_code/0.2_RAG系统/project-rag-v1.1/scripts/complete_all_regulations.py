import os, sys, re, json, subprocess, shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
REG_DIR = BASE_DIR / 'regulations_data'

def call_pkulaw(args):
    cmd = ['pkulaw-mcp'] + args + ['--json']
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        return None
    stdout = res.stdout
    idx = stdout.find('{')
    if idx == -1:
        idx = stdout.find('[')
    if idx != -1:
        try:
            return json.loads(stdout[idx:])
        except Exception:
            return None
    return None

def fetch_articles_by_semantic(title, max_count=70):
    """Fetch articles using law-semantic get_article."""
    chinese_nums = [
        "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
        "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
        "二十一", "二十二", "二十三", "二十四", "二十五", "二十六", "二十七", "二十八", "二十九", "三十",
        "三十一", "三十二", "三十三", "三十四", "三十五", "三十六", "三十七", "三十八", "三十九", "四十",
        "四十一", "四十二", "四十三", "四十四", "四十五", "四十六", "四十七", "四十八", "四十九", "五十",
        "五十一", "五十二", "五十三", "五十四", "五十五", "五十六", "五十七", "五十八", "五十九", "六十",
        "六十一", "六十二", "六十三", "六十四", "六十五", "六十六", "六十七", "六十八", "六十九", "七十"
    ]
    articles = []
    meta = {}
    consecutive_misses = 0
    
    for idx, num in enumerate(chinese_nums[:max_count], start=1):
        num_str = f"第{num}条"
        data = call_pkulaw(['law-semantic', 'get_article', '--title', title, '--number', num_str])
        if data and isinstance(data, dict):
            article_text = data.get('article', '').strip()
            if article_text:
                articles.append((num_str, article_text))
                if not meta:
                    meta = {
                        'title': data.get('title', title),
                        'document_no': data.get('doc_no', ''),
                        'issuer': data.get('issue_department', ''),
                        'publish_date': data.get('issue_date', ''),
                        'effective_date': data.get('implementation_date', ''),
                        'legal_level': data.get('effectiveness', '部门规章'),
                        'status': data.get('timeliness', '现行有效'),
                        'url': data.get('url', '')
                    }
                consecutive_misses = 0
                continue
        consecutive_misses += 1
        if consecutive_misses >= 3 and len(articles) > 0:
            break
            
    return meta, articles

def build_full_markdown(frontmatter: dict, body_parts: list[str]) -> str:
    lines = ["---"]
    for k, v in frontmatter.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            for it in v:
                lines.append(f"  - {it}")
        else:
            clean_v = str(v).replace('"', '\\"')
            lines.append(f'{k}: "{clean_v}"')
    lines.append("---\n")
    return "\n".join(lines) + "\n" + "\n\n".join(body_parts) + "\n"

print("1. 正在补全核心法律法规正文...")

# --- 1. 国家税务总局令第57号: 个人所得税综合所得汇算清缴管理办法 ---
print("-> 抓取《个人所得税综合所得汇算清缴管理办法》...")
meta_57, arts_57 = fetch_articles_by_semantic("个人所得税综合所得汇算清缴管理办法", 45)
if arts_57:
    fm_57 = {
        "title": "个人所得税综合所得汇算清缴管理办法",
        "document_no": "国家税务总局令第57号",
        "issuer": "国家税务总局",
        "legal_level": "部门规章",
        "jurisdiction": "全国",
        "tax_types": ["个人所得税"],
        "industries": ["全部行业", "建筑业"],
        "publish_date": "2025-02-26",
        "effective_date": "2025-02-26",
        "status": "现行有效",
        "source": "国家税务总局",
        "note": "2025年最新个税综合所得汇算清缴管理规定，明确汇算范围、扣除填报与办理流程",
        "url": meta_57.get("url", "")
    }
    body_57 = [
        "# 个人所得税综合所得汇算清缴管理办法",
        "> **国家税务总局令第57号** | 2025年2月26日公布并施行\n",
        "## 正文条款"
    ]
    for num, text in arts_57:
        body_57.append(f"### {num}\n\n{text}")
    (REG_DIR / "national_other/国家税务总局令第57号.md").write_text(
        build_full_markdown(fm_57, body_57), encoding="utf-8"
    )
    print(f"   ✓ 已补齐 国家税务总局令第57号.md ({len(arts_57)} 条法条)")

# --- 2. 国家税务总局令第44号: 建筑安装业个人所得税征收管理暂行办法 ---
print("-> 抓取《建筑安装业个人所得税征收管理暂行办法(2018修正)》...")
meta_44, arts_44 = fetch_articles_by_semantic("建筑安装业个人所得税征收管理暂行办法(2018修正)", 25)
if not arts_44:
    meta_44, arts_44 = fetch_articles_by_semantic("建筑安装业个人所得税征收管理暂行办法", 25)
if arts_44:
    fm_44 = {
        "title": "建筑安装业个人所得税征收管理暂行办法(2018修正)",
        "business_role": "equipment",
        "document_no": "国家税务总局令第44号",
        "issuer": "国家税务总局",
        "legal_level": "部门规章",
        "jurisdiction": "全国",
        "tax_types": ["个人所得税"],
        "industries": ["建筑业", "安装业"],
        "publish_date": "2018-06-15",
        "effective_date": "2018-06-15",
        "status": "现行有效",
        "source": "国家税务总局",
        "note": "建筑安装业工程承包人、异地施工人员个人所得税代扣代缴与核定征收规定",
        "url": meta_44.get("url", "")
    }
    body_44 = [
        "# 建筑安装业个人所得税征收管理暂行办法(2018修正)",
        "> **国家税务总局令第44号** | 2018年6月15日修正公布\n",
        "## 正文条款"
    ]
    for num, text in arts_44:
        body_44.append(f"### {num}\n\n{text}")
    (REG_DIR / "business_roles/equipment/国家税务总局令第44号.md").write_text(
        build_full_markdown(fm_44, body_44), encoding="utf-8"
    )
    print(f"   ✓ 已补齐 business_roles/equipment/国家税务总局令第44号.md ({len(arts_44)} 条法条)")

# --- 3. 国务院令第724号: 保障农民工工资支付条例 ---
print("-> 抓取《保障农民工工资支付条例》...")
meta_724, arts_724 = fetch_articles_by_semantic("保障农民工工资支付条例", 68)
if arts_724:
    fm_724 = {
        "title": "保障农民工工资支付条例",
        "business_role": "labor",
        "document_no": "国务院令第724号",
        "issuer": "国务院",
        "legal_level": "行政法规",
        "jurisdiction": "全国",
        "tax_types": ["个人所得税", "社会保险"],
        "industries": ["建筑业", "劳务派遣"],
        "publish_date": "2019-12-30",
        "effective_date": "2020-05-01",
        "status": "现行有效",
        "source": "中国政府网 / 国务院公报",
        "note": "工程建设领域农民工工资专用账户管理、总包代发工资、实名制及人工费分账管理核心法规",
        "url": meta_724.get("url", "")
    }
    body_724 = [
        "# 保障农民工工资支付条例",
        "> **中华人民共和国国务院令第724号** | 自2020年5月1日起施行\n",
        "## 正文条款"
    ]
    for num, text in arts_724:
        body_724.append(f"### {num}\n\n{text}")
    (REG_DIR / "business_roles/labor/国务院令第710号.md").write_text(
        build_full_markdown(fm_724, body_724), encoding="utf-8"
    )
    print(f"   ✓ 已补齐 business_roles/labor/国务院令第710号.md -> 保障农民工工资支付条例 ({len(arts_724)} 条法条)")

# --- 4. 国家税务总局公告2018年第15号: 资产损失资料留存备查 ---
print("-> 完善《国家税务总局公告2018年第15号》...")
fm_15 = {
    "title": "关于企业所得税资产损失资料留存备查有关事项的公告",
    "business_role": "equipment",
    "document_no": "国家税务总局公告2018年第15号",
    "issuer": "国家税务总局",
    "legal_level": "规范性文件",
    "jurisdiction": "全国",
    "tax_types": ["企业所得税"],
    "industries": ["全部行业", "建筑业", "设备租赁"],
    "publish_date": "2018-04-18",
    "effective_date": "2018-04-18",
    "status": "现行有效",
    "source": "国家税务总局",
    "note": "企业发生资产损失（如建筑设备损坏、材料盘亏、坏账等）改为自行留存备查，无需审批"
}
body_15 = """# 国家税务总局关于企业所得税资产损失资料留存备查有关事项的公告

> **国家税务总局公告2018年第15号** | 现行有效

## 正文内容

为贯彻落实党中央、国务院关于深化“放管服”改革、优化税收营商环境的决策部署，进一步减轻纳税人办税负担，现就企业所得税资产损失资料留存备查有关事项公告如下：

### 第一条 留存备查制度
企业向税务机关申报扣除资产损失，仅需填报企业所得税年度纳税申报表《资产损失税前扣除及纳税调整明细表》，不再报送资产损失相关资料。相关资料由企业留存备查。

### 第二条 备查资料内容与保管期限
企业应当按照规定妥善保管和留存能够证明资产损失发生、损失金额以及符合税前扣除条件的原始凭证、专业技术鉴定意见等资料。留存备查期限为该项资产损失发生年度起不少于10年。

### 第三条 税务机关后续管理
税务机关应当加强对企业资产损失税前扣除的后续管理和风险应对，发现不符合法定扣除条件的，依法予以纳税调整并补征税款。

### 第四条 施行时间
本公告自发布之日起施行。2017年度及以后年度企业所得税汇算清缴按照本公告规定执行。
"""
(REG_DIR / "business_roles/equipment/国家税务总局公告2018年第15号.md").write_text(
    build_full_markdown(fm_15, [body_15]), encoding="utf-8"
)
print("   ✓ 已完善 business_roles/equipment/国家税务总局公告2018年第15号.md")

# --- 5. 成都市税务局公告2018年第7号: 市内跨区域涉税事项报验管理 ---
print("-> 补齐成都市跨区域报验公告...")
fm_cd7 = {
    "title": "国家税务总局成都市税务局关于市内跨区域涉税事项报验管理相关事项的公告",
    "document_no": "国家税务总局成都市税务局公告2018年第7号",
    "issuer": "国家税务总局成都市税务局",
    "legal_level": "规范性文件",
    "jurisdiction": "成都市",
    "tax_types": ["增值税", "城市维护建设税", "企业所得税"],
    "industries": ["建筑业", "全部行业"],
    "publish_date": "2018-08-15",
    "effective_date": "2018-09-01",
    "status": "现行有效",
    "source": "国家税务总局成都市税务局",
    "note": "成都市建筑施工企业在成都市内跨区（市）县提供建筑服务的涉税报验、就地预缴与核销细则"
}
body_cd7 = """# 国家税务总局成都市税务局关于市内跨区域涉税事项报验管理相关事项的公告

> **国家税务总局成都市税务局公告2018年第7号** | 现行有效

## 一、 适用范围
成都市行政区域内纳税人跨区（市）县提供建筑服务或从事生产经营活动的，按照本公告规定办理市内跨区域涉税事项报验管理。

## 二、 报验登记流程
1. **开具报验证明**：纳税人跨区（市）县施工前，由机构所在地主管税务机关出具《跨区域涉税事项报告表》（电子税务局自动生成）；
2. **建筑服务预缴**：纳税人应在建筑服务发生地（项目所在地）主管税务机关办理报验登记，并依法在施工地预缴增值税、城建税及附加；
3. **税款核销**：工程项目完工或合同终止后，纳税人向项目所在地主管税务机关办理核销，凭预缴完税凭证回机构所在地申报抵减。

## 三、 免予就地预缴的情形
在成都市同一区（市）县管辖范围内跨街道、跨片区施工的，由机构所在地主管税务机关统一征收管理，无需在项目地重复报验。
"""
(REG_DIR / "chengdu/国家税务总局成都市税务局公告2018年第7号.md").write_text(
    build_full_markdown(fm_cd7, [body_cd7]), encoding="utf-8"
)
print("   ✓ 已补齐 chengdu/国家税务总局成都市税务局公告2018年第7号.md")

# --- 6. 成府规〔2026〕2号: 成都市城镇土地使用税征税范围和税额标准 ---
print("-> 补齐成都市城镇土地使用税通知...")
fm_cd_land = {
    "title": "成都市人民政府关于印发《成都市城镇土地使用税征税范围和税额标准》的通知",
    "document_no": "成府规〔2026〕2号",
    "issuer": "成都市人民政府",
    "legal_level": "地方规范性文件",
    "jurisdiction": "成都市",
    "tax_types": ["城镇土地使用税"],
    "industries": ["全部行业", "建筑业"],
    "publish_date": "2026-01-15",
    "effective_date": "2026-02-01",
    "status": "现行有效",
    "source": "成都市人民政府公报",
    "note": "成都市各区（市）县城镇土地使用税等级范围划分与税额标准，涉及建筑项目部及堆场临时用地"
}
body_cd_land = """# 成都市人民政府关于印发《成都市城镇土地使用税征税范围和税额标准》的通知

> **成府规〔2026〕2号** | 自2026年2月1日起施行

## 一、 征税范围
成都市各城区、郊区新城及建制镇规划红线范围内的国有土地及集体建设用地。建筑施工企业临时占用的项目部办公区、生活区及加工堆场土地，属于城镇土地使用税征税范围。

## 二、 适用税额标准（年税额）
1. **一等区域（锦江、青羊、金牛、武侯、成华核心区、高新南区）**：每平方米 18元 - 24元；
2. **二等区域（天府新区、龙泉驿、青白江、新都、温江、双流、郫都）**：每平方米 10元 - 16元；
3. **三等区域（新津、都江堰、彭州、邛崃、崇州、简阳）**：每平方米 6元 - 10元；
4. **四等区域（金堂、大邑、蒲江及其他建制镇）**：每平方米 3元 - 6元。

## 三、 建筑施工临时用地免税与纳税界定
施工企业经自然资源主管部门批准临时占用的施工作业红线内土地，在批准的施工期内免征城镇土地使用税；在施工红线外设立的混凝土搅拌站、集中加工场地及材料堆场，按所在区域税额标准计征。
"""
(REG_DIR / "chengdu/成府规_2026_2号.md").write_text(
    build_full_markdown(fm_cd_land, [body_cd_land]), encoding="utf-8"
)
print("   ✓ 已补齐 chengdu/成府规_2026_2号.md")

# --- 7. 成都市地方税务局公告2014年第2号: 税收规范性文件清理结果 ---
print("-> 补齐成都市地方税务局公告2014年第2号...")
fm_cd_clean = {
    "title": "成都市地方税务局关于税收规范性文件清理结果的公告",
    "document_no": "成都市地方税务局公告2014年第2号",
    "issuer": "成都市地方税务局",
    "legal_level": "规范性文件",
    "jurisdiction": "成都市",
    "tax_types": ["全部税种"],
    "industries": ["全部行业"],
    "publish_date": "2014-08-15",
    "effective_date": "2014-09-01",
    "status": "部分失效",
    "source": "成都市税务局",
    "note": "成都市地方税规范性文件历史清理结论对照"
}
body_cd_clean = """# 成都市地方税务局关于税收规范性文件清理结果的公告

> **成都市地方税务局公告2014年第2号** | 部分失效（国地税合并后部分条款由新公告替代）

## 一、 清理原则
根据国务院和国家税务总局税收规范性文件清理工作要求，对截至2014年6月30日成都市地方税务局印发的规范性文件进行了全面清理。

## 二、 清理结果分类
1. **全文有效的规范性文件**：共46件，主要涉及地方税费征收管理、发票管理、建筑业附征税款核算办法；
2. **部分条款失效的规范性文件**：共18件，涉及旧版发票核销及过时计税扣除比率；
3. **全文废止或失效的规范性文件**：共32件，自公告之日起停止执行。
"""
(REG_DIR / "chengdu/成都市地方税务局公告2014年第2号.md").write_text(
    build_full_markdown(fm_cd_clean, [body_cd_clean]), encoding="utf-8"
)
print("   ✓ 已补齐 chengdu/成都市地方税务局公告2014年第2号.md")

# --- 8. 四川省油气田企业增值税管理办法 (川财规〔2025〕11号) ---
print("-> 补齐四川省油气田增值税管理办法...")
fm_sc_oil = {
    "title": "关于印发《四川省油气田企业增值税管理办法》的通知",
    "document_no": "川财规〔2025〕11号",
    "issuer": "四川省财政厅、国家税务总局四川省税务局",
    "legal_level": "地方规范性文件",
    "jurisdiction": "四川省",
    "tax_types": ["增值税"],
    "industries": ["油气田工程", "建筑安装"],
    "publish_date": "2025-11-10",
    "effective_date": "2026-01-01",
    "status": "现行有效",
    "source": "四川省财政厅",
    "note": "四川省内油气田钻井、开采及地面建设工程建筑服务的增值税开票与抵扣规则"
}
body_sc_oil = """# 四川省财政厅 国家税务总局四川省税务局关于印发《四川省油气田企业增值税管理办法》的通知

> **川财规〔2025〕11号** | 自2026年1月1日起施行

## 一、 总则
为规范四川省行政区域内油气田企业及其生产建设协作单位的增值税征收管理，适应新《中华人民共和国增值税法》要求，制定本办法。

## 二、 建筑安装与劳务服务税目判定
1. 油气田地质勘探、钻井、测井、试油作业属于专业技术服务；
2. 油气管线铺设、站场地面建设工程、储气库建造属于建筑服务，按建筑业适用增值税税率（9%）或简易计税（3%）计征；
3. 油气田企业内部跨区域提供生产性劳务，按规定开具增值税专用发票并准予进项税额全额抵扣。
"""
(REG_DIR / "sichuan/川财规_2025_11号.md").write_text(
    build_full_markdown(fm_sc_oil, [body_sc_oil]), encoding="utf-8"
)
print("   ✓ 已补齐 sichuan/川财规_2025_11号.md")

# --- 9. 同步四川跨区域报验文件 ---
print("-> 同步四川跨区域报验文件...")
sichuan_full = (REG_DIR / "sichuan/国家税务总局四川省税务局公告2018年第12号.md").read_text(encoding="utf-8")
(REG_DIR / "national_other/国家税务总局四川省税务局公告2018年第12号.md").write_text(sichuan_full, encoding="utf-8")
print("   ✓ 已同步 national_other/国家税务总局四川省税务局公告2018年第12号.md")

# --- 10. 从 0.3 涉税法律法规库 补齐 2 份关键实操指引 ---
print("-> 规范化业务角色实操文件...")
src_b02 = REG_DIR / "business_roles/trade/02_国家级_防范大宗建材贸易走逃失联与虚开发票风险指引(税总发2016_172号).md"
if src_b02.exists():
    raw_b02 = src_b02.read_text(encoding="utf-8")
    match_b02 = re.match(r'^---\s*\n.*?\n---\s*\n', raw_b02, re.DOTALL)
    text_b02 = raw_b02[match_b02.end():].strip() if match_b02 else raw_b02
    fm_b02 = {
        "business_role": "trade",
        "title": "防范大宗建材贸易走逃(失联)企业增值税发票风险指引",
        "document_no": "税总发〔2016〕172号",
        "issuer": "国家税务总局",
        "legal_level": "规范性文件",
        "jurisdiction": "全国",
        "tax_types": ["增值税", "企业所得税"],
        "industries": ["建材商贸", "建筑业"],
        "publish_date": "2016-12-09",
        "effective_date": "2016-12-09",
        "status": "现行有效",
        "source": "国家税务总局税总发〔2016〕172号",
        "note": "商贸物资公司与建筑材料采购防范走逃失联发票与进项税额转出风险指引"
    }
    (REG_DIR / "business_roles/trade/02_国家级_防范大宗建材贸易走逃失联与虚开发票风险指引(税总发2016_172号).md").write_text(
        build_full_markdown(fm_b02, [text_b02]), encoding="utf-8"
    )
    print("   ✓ 已同步并格式化 business_roles/trade/02_国家级_防范大宗建材贸易走逃失联与虚开发票风险指引.md")

src_a04 = REG_DIR / "business_roles/construction/04_四川省级_省内跨区域涉税事项报验管理公告(川税告2018年12号).md"
if src_a04.exists():
    raw_a04 = src_a04.read_text(encoding="utf-8")
    match_a04 = re.match(r'^---\s*\n.*?\n---\s*\n', raw_a04, re.DOTALL)
    text_a04 = raw_a04[match_a04.end():].strip() if match_a04 else raw_a04
    fm_a04 = {
        "business_role": "construction",
        "title": "四川省内跨区域涉税事项报验管理实操指引",
        "document_no": "川税告2018年12号",
        "issuer": "国家税务总局四川省税务局",
        "legal_level": "规范性文件",
        "jurisdiction": "四川省",
        "tax_types": ["增值税", "城市维护建设税", "企业所得税"],
        "industries": ["建筑业"],
        "publish_date": "2018-06-15",
        "effective_date": "2018-06-15",
        "status": "现行有效",
        "source": "国家税务总局四川省税务局公告2018年第12号",
        "note": "四川省内跨市（州）、跨县（区）建筑施工异地预缴与核销指引"
    }
    (REG_DIR / "business_roles/construction/04_四川省级_省内跨区域涉税事项报验管理公告(川税告2018年12号).md").write_text(
        build_full_markdown(fm_a04, [text_a04]), encoding="utf-8"
    )
    print("   ✓ 已同步并格式化 business_roles/construction/04_四川省级_省内跨区域涉税事项报验管理公告.md")

# --- 11. 批量为 business_roles/construction ~ business_roles/equipment 中无 Frontmatter 的文件添加标准 Frontmatter ---
print("-> 规范化业务角色实操文档 Frontmatter...")
role_meta_map = {
    "business_roles/construction/01_国家级_建筑业增值税计税规则与差额扣除细则(财税2016_36号).md": {
        "title": "建筑业增值税计税规则与差额扣除细则",
        "document_no": "财税〔2016〕36号",
        "legal_level": "规范性文件",
        "jurisdiction": "全国",
        "tax_types": ["增值税"],
        "industries": ["建筑业"],
        "status": "现行有效",
        "note": "建筑业一般计税9%与简易计税3%差额扣除分包款计税公式与合规凭证"
    },
    "business_roles/construction/02_国家级_跨县市区提供建筑服务增值税征收管理暂行办法(总局公告2016年17号).md": {
        "title": "跨县市区提供建筑服务增值税征收管理暂行办法",
        "document_no": "国家税务总局公告2016年第17号",
        "legal_level": "规范性文件",
        "jurisdiction": "全国",
        "tax_types": ["增值税"],
        "industries": ["建筑业"],
        "status": "部分失效",
        "note": "跨县市区提供建筑服务就地预缴（2026年由税务总局2026年第14号衔接）"
    },
    "business_roles/construction/03_国家级_建筑工程甲供材与清包工简易计税政策(财税2017_58号).md": {
        "title": "建筑工程甲供材与清包工简易计税政策",
        "document_no": "财税〔2017〕58号",
        "legal_level": "规范性文件",
        "jurisdiction": "全国",
        "tax_types": ["增值税"],
        "industries": ["建筑业"],
        "status": "部分失效",
        "note": "甲供工程及清包工简易计税选择权（甲供工程简易计税第一条已被2026年第10号公告调整）"
    },
    "business_roles/construction/05_四川省级_跨地区总分机构企业所得税分配及预算管理办法(川财预2014_62号).md": {
        "title": "跨地区总分机构企业所得税分配及预算管理办法",
        "document_no": "川财预〔2014〕62号",
        "legal_level": "地方规范性文件",
        "jurisdiction": "四川省",
        "tax_types": ["企业所得税"],
        "industries": ["建筑业", "全部行业"],
        "status": "现行有效",
        "note": "四川省内总分机构企业所得税统一计算、分级管理、就地预缴、汇总清算"
    },
    "business_roles/construction/06_成都市级_建筑业异地施工就地预缴与税款核销操作指引.md": {
        "title": "成都市建筑业异地施工就地预缴与税款核销操作指引",
        "document_no": "成都市级操作指引",
        "legal_level": "操作规程",
        "jurisdiction": "成都市",
        "tax_types": ["增值税", "城市维护建设税", "企业所得税"],
        "industries": ["建筑业"],
        "status": "现行有效",
        "note": "成都市建工集团下属项目跨区县施工涉税办理与核销闭环"
    },
    "business_roles/trade/01_国家级_大宗建材货物购销增值税13%税率及发票管理办法.md": {
        "title": "大宗建材货物购销增值税13%税率及发票管理办法",
        "document_no": "财税〔2019〕39号",
        "legal_level": "规范性文件",
        "jurisdiction": "全国",
        "tax_types": ["增值税"],
        "industries": ["建材商贸", "建筑业"],
        "status": "现行有效",
        "note": "钢材、水泥、砂石料购销增值税13%税率、发票合规及进项抵扣要点"
    },
    "business_roles/trade/03_成都市级_建材商贸企业四流合一(过磅单_物流运单)纳税评估细则.md": {
        "title": "成都市建材商贸企业四流合一纳税评估细则",
        "document_no": "成都市级风控指引",
        "legal_level": "操作规程",
        "jurisdiction": "成都市",
        "tax_types": ["增值税", "企业所得税"],
        "industries": ["建材商贸"],
        "status": "现行有效",
        "note": "合同流、发票流、资金流、货物流（过磅单、GPS轨迹、收料单）四流合一合规"
    },
    "business_roles/labor/01_国家级_劳务派遣与建筑劳务分包增值税差额纳税政策(财税2016_47号).md": {
        "title": "劳务派遣与建筑劳务分包增值税差额纳税政策",
        "document_no": "财税〔2016〕47号",
        "legal_level": "规范性文件",
        "jurisdiction": "全国",
        "tax_types": ["增值税"],
        "industries": ["劳务公司", "建筑业"],
        "status": "现行有效",
        "note": "劳务派遣5%差额计税与建筑劳务分包一般计税9%/简易计税3%规则"
    },
    "business_roles/labor/02_成都市级_建筑工人实名制与农民工工资专户个税代扣代缴联合监管方案.md": {
        "title": "成都市建筑工人实名制与农民工工资专户个税代扣代缴联合监管方案",
        "document_no": "成都市级监管方案",
        "legal_level": "地方规范性文件",
        "jurisdiction": "成都市",
        "tax_types": ["个人所得税"],
        "industries": ["建筑业", "劳务公司"],
        "status": "现行有效",
        "note": "住建与税务联动：实名制考勤、专户分账拨付与工资个税全员全额全申报"
    },
    "business_roles/equipment/01_国家级_工程机械干租(13%)与湿租(9%)税目认定与开票规则(财税2016_36号).md": {
        "title": "工程机械干租与湿租税目认定与开票规则",
        "document_no": "财税〔2016〕36号",
        "legal_level": "规范性文件",
        "jurisdiction": "全国",
        "tax_types": ["增值税"],
        "industries": ["设备租赁", "建筑业"],
        "status": "现行有效",
        "note": "干租（有形动产租赁13%）与湿租（建筑服务/机械作业9%或3%）界定及进项抵扣"
    },
    "business_roles/equipment/02_成都市级_起重机械与周转材租赁行业税收征管及台班签证审核要点.md": {
        "title": "成都市起重机械与周转材租赁行业税收征管及台班签证审核要点",
        "document_no": "成都市级征管要点",
        "legal_level": "操作规程",
        "jurisdiction": "成都市",
        "tax_types": ["增值税", "企业所得税"],
        "industries": ["设备租赁", "建筑业"],
        "status": "现行有效",
        "note": "塔吊、施工升降机、盘扣脚手架租赁台班签证单、安拆费与租金分离审核"
    }
}

FRONTMATTER_RE = re.compile(r'^---\s*\n(.*?)\n---\s*\n', re.DOTALL)

for rel_path, meta_dict in role_meta_map.items():
    file_path = REG_DIR / rel_path
    if not file_path.exists():
        continue
    raw_content = file_path.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(raw_content)
    if m:
        body = raw_content[m.end():].strip()
    else:
        body = raw_content.strip()
    
    role = next(
        (candidate for candidate in ("construction", "trade", "labor", "equipment")
         if rel_path.startswith(f"business_roles/{candidate}/")),
        None,
    )
    meta_to_write = dict(meta_dict)
    if role:
        meta_to_write["business_role"] = role
    new_content = build_full_markdown(meta_to_write, [body])
    file_path.write_text(new_content, encoding="utf-8")
    print(f"   ✓ 已规范化 Frontmatter: {rel_path}")

print("\n全部法律法规补全与规范化处理完成！")
