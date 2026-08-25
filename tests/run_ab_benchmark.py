#!/usr/bin/env python3
"""
Chengdu Construction V2.0 - Real Task A/B Benchmark (Ling-3.0-tiny vs Qwen3.5-2B)
Evaluates 60 real construction, tax, four-flow, facts review, and citation tasks across 10 dimensions.
"""

import json
import os
import re
import subprocess
import sys
import time
import threading
from typing import Any, Dict, List, Tuple
import httpx
import psutil

LLAMA_SERVER_BIN = "/tmp/llama_build/build/bin/llama-server"
LING_MODEL = "/Users/yvoche/AI开发/073_成都建工/V2.0/models/local-llm/Ling-3.0-tiny-Q4_K_M.gguf"
QWEN_MODEL = "/Users/yvoche/AI开发/073_成都建工/V2.0/models/local-llm/Qwen3.5-2B-Q4_K_M.gguf"
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8930
API_URL = f"http://{SERVER_HOST}:{SERVER_PORT}/v1/chat/completions"
HEALTH_URL = f"http://{SERVER_HOST}:{SERVER_PORT}/health"

# Build 60 authentic benchmark cases across 5 categories
BENCHMARK_CASES = [
    # -------------------------------------------------------------
    # 1. 合同与 RAG 问答 (Contract & RAG Q&A - 15 items)
    # -------------------------------------------------------------
    {
        "id": "CTR-01",
        "category": "合同/RAG问答",
        "prompt": "【证据材料】\n[EVD-01] 《成都天府新区项目施工主合同》第8.2条：工程质量保证金按工程结算总额的3%预留，缺陷责任期自实际竣工验收合格之日起满24个月后30日内无息结清。\n【问题】请依据证据说明天府新区项目的质量保证金比例是多少？退还条件和期限是什么？",
        "expected_keywords": ["3%", "24个月", "竣工验收合格", "30日", "无息"],
        "expected_numbers": [3, 24, 30],
        "evidence_ids": ["EVD-01"],
        "format": "text",
    },
    {
        "id": "CTR-02",
        "category": "合同/RAG问答",
        "prompt": "【证据材料】\n[EVD-02] 《成都高新产业园项目合同》第12.1条：逾期竣工违约金按每日合同总价的万分之三计算，违约金累计最高不超过合同结算总价的5%。\n【问题】该项目的逾期违约金每日计提比例是多少？最高违约金上限是多少？",
        "expected_keywords": ["万分之三", "5%"],
        "expected_numbers": [5],
        "evidence_ids": ["EVD-02"],
        "format": "text",
    },
    {
        "id": "CTR-03",
        "category": "合同/RAG问答",
        "prompt": "【证据材料】\n[EVD-03] 《成渝双城经济圈项目物资采购合同》第5.3条：甲方在收到符合国家税法规定的13%增值税专用发票及发货验收单后15个工作日内支付至合同到货款的80%。\n【问题】供应商申请支付至80%到货款需要提供哪两项核心单据？增值税发票税率是多少？",
        "expected_keywords": ["13%", "增值税专用发票", "发货验收单", "80%"],
        "expected_numbers": [13, 80, 15],
        "evidence_ids": ["EVD-03"],
        "format": "text",
    },
    {
        "id": "CTR-04",
        "category": "合同/RAG问答",
        "prompt": "【证据材料】\n[EVD-04] 《广元利州项目分包合同》第9条：严禁分包单位将承包工程转包或二次违法分包。一旦发现转包，总包方有权单方解除合同并扣除已缴纳履约保证金50万元。\n【问题】若分包单位发生转包行为，总包方享有哪些合同救济权利？涉及扣除金额是多少？",
        "expected_keywords": ["解除合同", "单方解除", "履约保证金", "50万"],
        "expected_numbers": [50],
        "evidence_ids": ["EVD-04"],
        "format": "text",
    },
    {
        "id": "CTR-05",
        "category": "合同/RAG问答",
        "prompt": "【证据材料】\n[EVD-05] 《青羊总部基地项目补充协议》：本工程工期顺延仅限不可抗力及发包方未按期提供施工图纸且导致关键线路延误超过7天的情况，且承包方须在事件发生后14天内书面申报。\n【问题】工期顺延需满足什么条件？承包方书面申报时限是几天？",
        "expected_keywords": ["不可抗力", "图纸", "关键线路", "14天", "7天"],
        "expected_numbers": [7, 14],
        "evidence_ids": ["EVD-05"],
        "format": "text",
    },
    {
        "id": "CTR-06",
        "category": "合同/RAG问答",
        "prompt": "【证据材料】\n[EVD-06] 《宜宾示范项目进度款结算细则》：当月产值核定后，发包人应在监理审核完成后10个工作日内向承包人支付当月核定产值的85%，剩余15%转入竣工结算支付。\n【问题】进度款支付比例是多少？剩余款项何时支付？",
        "expected_keywords": ["85%", "15%", "竣工结算", "10个工作日"],
        "expected_numbers": [85, 15, 10],
        "evidence_ids": ["EVD-06"],
        "format": "text",
    },
    {
        "id": "CTR-07",
        "category": "合同/RAG问答",
        "prompt": "【证据材料】\n[EVD-07] 《施工安全生产管理责任书》第4条：现场安全文明施工措施费总计120万元，发包人须在开工后28天内预付不低于该费用总额的50%，其余按工程进度拨付。\n【问题】安全文明施工措施费总额是多少？开工后预付比例和时限是多少？",
        "expected_keywords": ["120万", "28天", "50%"],
        "expected_numbers": [120, 28, 50],
        "evidence_ids": ["EVD-07"],
        "format": "text",
    },
    {
        "id": "CTR-08",
        "category": "合同/RAG问答",
        "prompt": "【证据材料】\n[EVD-08] 《天府新区合同争议解决条款》：因履行本合同引起的任何争议，双方应协商解决；协商不成的，应向项目所在地有管辖权的人民法院（即成都市双流区人民法院）提起诉讼。\n【问题】合同争议约定的管辖法院是哪一家？",
        "expected_keywords": ["成都市双流区人民法院", "诉讼", "管辖"],
        "evidence_ids": ["EVD-08"],
        "format": "text",
    },
    {
        "id": "CTR-09",
        "category": "合同/RAG问答",
        "prompt": "【证据材料】\n[EVD-09] 《钢筋集中采购补充协议》：当钢筋市场价格波动幅度在基准价±5%以内时，价格不予调整；超出±5%部分，超出部分由发承包双方按4:6比例分摊。\n【问题】钢筋调差的风险风险控制区间是多少？超出部分双方如何分摊？",
        "expected_keywords": ["5%", "4:6", "不予调整", "分摊"],
        "expected_numbers": [5, 4, 6],
        "evidence_ids": ["EVD-09"],
        "format": "text",
    },
    {
        "id": "CTR-10",
        "category": "合同/RAG问答",
        "prompt": "【证据材料】\n[EVD-10] 《天府新区商砼供货合同》：混凝土到场坍落度抽检不合格的，买方有权直接拒收并做退场处理，卖方须在2小时内补送合格商砼，造成停工窝工由卖方赔偿。\n【问题】抽检不合格商砼如何处理？补送响应时限是多久？",
        "expected_keywords": ["拒收", "退场", "2小时", "窝工", "赔偿"],
        "expected_numbers": [2],
        "evidence_ids": ["EVD-10"],
        "format": "text",
    },
    {
        "id": "CTR-11",
        "category": "合同/RAG问答",
        "prompt": "【证据材料】\n[EVD-11] 《劳务分包实名制管理细则》：劳务人员工资须通过专用账户直接发放至民工个人银行卡，每月20日前完成上月工资发放并报总包备案。\n【问题】民工工资发放通道与每月截止日期分别是什么？",
        "expected_keywords": ["专户", "个人银行卡", "20日", "实名制"],
        "expected_numbers": [20],
        "evidence_ids": ["EVD-11"],
        "format": "text",
    },
    {
        "id": "CTR-12",
        "category": "合同/RAG问答",
        "prompt": "【证据材料】\n[EVD-12] 《机电安装工程专业分包合同》：分包工程质保期自整体工程竣工验收合格之日起算，电气管线、给排水管道及设备安装工程质保期为2年，供热与供冷系统为2个采暖期/供冷期。\n【问题】电气与给排水质保期几年？暖通系统质保期多久？",
        "expected_keywords": ["2年", "2个采暖期", "供冷期"],
        "expected_numbers": [2],
        "evidence_ids": ["EVD-12"],
        "format": "text",
    },
    {
        "id": "CTR-13",
        "category": "合同/RAG问答",
        "prompt": "【证据材料】\n[EVD-13] 《工程签证与变更管理办法》：凡单项签证金额超过20万元的，必须附带由项目总监、业主代表及总包项目经理三方签字的现场影像资料。\n【问题】超过多少金额的签证需要三方签字及影像凭据？",
        "expected_keywords": ["20万", "影像", "三方签字"],
        "expected_numbers": [20],
        "evidence_ids": ["EVD-13"],
        "format": "text",
    },
    {
        "id": "CTR-14",
        "category": "合同/RAG问答",
        "prompt": "【证据材料】\n[EVD-14] 《临时用电与消防安全协议》：施工现场严禁私拉乱接电线，每发现一次违规操作处罚责任单位2000元，并责令1小时内整改完毕。\n【问题】私拉乱接电线单次处罚金额及整改时限是多少？",
        "expected_keywords": ["2000元", "1小时", "整改"],
        "expected_numbers": [2000, 1],
        "evidence_ids": ["EVD-14"],
        "format": "text",
    },
    {
        "id": "CTR-15",
        "category": "合同/RAG问答",
        "prompt": "【证据材料】\n[EVD-15] 《项目竣工结算送审管理制度》：承包人应在工程竣工验收合格后28天内向发包人递交竣工结算报告及完整的结算资料，发包人自收到起60天内审核完毕。\n【问题】竣工结算资料递交时限与发包人审核时限分别是多少天？",
        "expected_keywords": ["28天", "60天", "竣工结算"],
        "expected_numbers": [28, 60],
        "evidence_ids": ["EVD-15"],
        "format": "text",
    },

    # -------------------------------------------------------------
    # 2. 发票税务解释与计算 (Tax & Invoice Calculation - 15 items)
    # -------------------------------------------------------------
    {
        "id": "TAX-01",
        "category": "发票税务解释",
        "prompt": "某建筑工程施工项目采用一般计税方法，含税结算收入为 3270 万元，适用增值税税率 9%。请计算该笔收入对应的不含税销售额和增值税销项税额（单位：万元，保留两位小数）。",
        "expected_keywords": ["3000", "270"],
        "expected_numbers": [3000.0, 270.0],
        "format": "text",
    },
    {
        "id": "TAX-02",
        "category": "发票税务解释",
        "prompt": "项目部向设备供应商采购塔吊设备一批，取得增值税专用发票注明价税合计 226 万元，税率 13%。请计算该批设备的不含税金额以及可抵扣的进项税额（单位：万元，保留两位小数）。",
        "expected_keywords": ["200", "26"],
        "expected_numbers": [200.0, 26.0],
        "format": "text",
    },
    {
        "id": "TAX-03",
        "category": "发票税务解释",
        "prompt": "某异地老项目采用简易计税方法（征收率 3%），本月取得含税工程款 1030 万元，支付分包款 206 万元（取得合规分包发票）。请按差额征税计算预缴税款公式及应预缴税额（万元）。",
        "expected_keywords": ["差额", "24", "1030", "206", "3%"],
        "expected_numbers": [24.0, 824.0],
        "format": "text",
    },
    {
        "id": "TAX-04",
        "category": "发票税务解释",
        "prompt": "建筑企业异地提供建筑服务适用一般计税方法，取得含税工程款 2180 万元，支付分包款 545 万元。跨区域预缴增值税计算公式为：(含税价款-支付分包款)÷(1+9%)×2%。请计算本期应预缴增值税额（万元）。",
        "expected_keywords": ["30", "预缴", "2%"],
        "expected_numbers": [30.0, 1500.0],
        "format": "text",
    },
    {
        "id": "TAX-05",
        "category": "发票税务解释",
        "prompt": "请解释建筑施工企业取得的“增值税普通发票”与“增值税专用发票”在进项税额抵扣上的核心区别是什么？用于集体福利的材料专票是否允许抵扣？",
        "expected_keywords": ["普通发票不得抵扣", "专用发票", "进项税额", "集体福利不得抵扣", "转出"],
        "format": "text",
    },
    {
        "id": "TAX-06",
        "category": "发票税务解释",
        "prompt": "某项目发生混凝土材料采购含税金额 565 万元（税率 13%），租赁工程机械含税金额 109 万元（税率 9%）。请分别计算材料进项税额、机械租赁进项税额及合计可抵扣进项税额（万元）。",
        "expected_keywords": ["65", "9", "74"],
        "expected_numbers": [65.0, 9.0, 74.0],
        "format": "text",
    },
    {
        "id": "TAX-07",
        "category": "发票税务解释",
        "prompt": "某项目开具增值税专用发票后因工程量核减发生销售折让 109 万元（含 9% 增值税）。根据税法规定，开具红字增值税专用发票应冲减多少不含税销售额和多少销项税额？",
        "expected_keywords": ["100", "9", "红字发票", "冲减"],
        "expected_numbers": [100.0, 9.0],
        "format": "text",
    },
    {
        "id": "TAX-08",
        "category": "发票税务解释",
        "prompt": "项目部本月确认销项税额 450 万元，当月取得可抵扣进项税额 380 万元，上月留抵税额 20 万元。请计算本月一般计税下的应纳增值税额（万元）。",
        "expected_keywords": ["50", "销项税额", "进项税额", "留抵税额"],
        "expected_numbers": [50.0],
        "format": "text",
    },
    {
        "id": "TAX-09",
        "category": "发票税务解释",
        "prompt": "建筑企业将自产商品混凝土（适用 13% 或简易 3%）用于自身承包的建筑工程项目，在增值税上是否视同销售？请简述视同销售的计税原理。",
        "expected_keywords": ["视同销售", "增值税", "转移", "进项抵扣"],
        "format": "text",
    },
    {
        "id": "TAX-10",
        "category": "发票税务解释",
        "prompt": "甲供材模式下，发包人直接采购 300 万元钢筋并提供给施工单位使用。施工单位开具建筑服务发票时，是否可以将甲供材料金额从计税销售额中扣除？请说明税法依据。",
        "expected_keywords": ["不得扣除", "销售额", "全部价款和价外费用", "甲供工程", "简易计税"],
        "format": "text",
    },
    {
        "id": "TAX-11",
        "category": "发票税务解释",
        "prompt": "某项目收到业主支付的工程预付款 1090 万元（尚未开具发票也未达到纳税义务发生时间）。建筑企业是否需要在收到预收款时代扣代缴或预缴增值税？预征率是多少？",
        "expected_keywords": ["预收款", "预缴", "2%", "建筑服务"],
        "expected_numbers": [2],
        "format": "text",
    },
    {
        "id": "TAX-12",
        "category": "发票税务解释",
        "prompt": "某建筑劳务分包公司（小规模纳税人）为成都建工开具劳务分包发票，征收率为 3%（不考虑阶段性优惠）。结算含税金额为 515 万元，请计算分包公司开票的不含税价款与税额（万元）。",
        "expected_keywords": ["500", "15"],
        "expected_numbers": [500.0, 15.0],
        "format": "text",
    },
    {
        "id": "TAX-13",
        "category": "发票税务解释",
        "prompt": "建筑施工企业取得旅客运输服务电子普通发票（注明票价 1000 元，燃油附加 100 元，税额 99 元），是否可以作为进项税额抵扣？抵扣金额是多少？",
        "expected_keywords": ["可以抵扣", "99", "旅客运输"],
        "expected_numbers": [99],
        "format": "text",
    },
    {
        "id": "TAX-14",
        "category": "发票税务解释",
        "prompt": "某施工项目发生因管理不善导致的钢材被盗损失，损失账面成本为 100 万元（采购时已抵扣 13% 进项税）。请问按税法规定，应做进项税额转出多少万元？",
        "expected_keywords": ["进项税额转出", "13", "管理不善", "非正常损失"],
        "expected_numbers": [13.0],
        "format": "text",
    },
    {
        "id": "TAX-15",
        "category": "发票税务解释",
        "prompt": "某建筑工程总承包合同额为 10900 万元（含 9% 增值税），项目已竣工结算。销项税额已全部开具。经测算综合进项税率为 5.5%，请估算该项目的理论增值税税负率（增值税税负=应纳增值税÷不含税收入）。",
        "expected_keywords": ["3.5%", "税负率", "销项", "进项"],
        "expected_numbers": [3.5],
        "format": "text",
    },

    # -------------------------------------------------------------
    # 3. 四流一致性与异常解释 (Four-Flow Consistency - 10 items)
    # -------------------------------------------------------------
    {
        "id": "FF-01",
        "category": "四流异常解释",
        "prompt": "【业务场景】总包方成都建工（主体 A01）与劳务公司（B01）签订劳务分包合同，发票由劳务公司 B01 开具给 A01，但银行资金流水显示由项目经理个人账户转账给第三方自然人张某。请指出该业务在“四流”（合同流、发票流、资金流、货物流/业务流）中存在哪项严重异常，并说明税务风险。",
        "expected_keywords": ["资金流", "四流不一致", "公对私", "虚开", "不得抵扣", "个人账户"],
        "format": "text",
    },
    {
        "id": "FF-02",
        "category": "四流异常解释",
        "prompt": "【业务场景】物资采购合同由 A08（天府建设）与钢材供应商签订，钢材直接运抵天府新区施工现场并验收，但发票开票抬头写成了 A01（集团本部），付款由 A08 对公账户支付。请分析存在何种四流异常及合规整改建议。",
        "expected_keywords": ["发票流", "抬头不一致", "作废重开", "红冲", "受票方"],
        "format": "text",
    },
    {
        "id": "FF-03",
        "category": "四流异常解释",
        "prompt": "【业务场景】项目部租用机械设备，因出租方账户被冻结，出租方出具委托书要求将租金支付至其法人代表个人银行卡。该笔交易取得出租公司开具的增值税专票。请从四流合规角度评估是否存在资金流与发票流不符的风险，如何规避？",
        "expected_keywords": ["资金流不一致", "委托付款协议", "三方协议", "证明链条", "税务风险"],
        "format": "text",
    },
    {
        "id": "FF-04",
        "category": "四流异常解释",
        "prompt": "【业务场景】商砼站向项目供货 1000 方并完成浇筑（有供货单），但合同由另一家已被注销的旧商砼公司签署，发票由新商砼站开具，款项付给新商砼站。请指出其中的合同流异常并给出合规处理步骤。",
        "expected_keywords": ["合同流不一致", "主体不一致", "重新签订合同", "主体变更补充协议"],
        "format": "text",
    },
    {
        "id": "FF-05",
        "category": "四流异常解释",
        "prompt": "【业务场景】分包商 B05 承担土方工程，合同签订 200 万元，发票开具 200 万元，银行流水付了 200 万元，但现场工程进度报表与监理签证仅确认了 80 万元土方工程量。请分析该场景存在何种业务流/货物流异常与潜在虚开风险。",
        "expected_keywords": ["业务流", "货物流", "工程量不符", "虚开发票", "超进度开票", "真实性"],
        "format": "text",
    },
    {
        "id": "FF-06",
        "category": "四流异常解释",
        "prompt": "【业务场景】建设单位直接将工程款支付给总包指定的分包商 C01，总包 A02 未过账。总包对建设单位开具全额发票，分包商对总包开具分包发票。请指出三方支付中资金流跨主体的合规要件是什么？",
        "expected_keywords": ["三方代付协议", "委托划款", "债权债务", "抵消", "合规"],
        "format": "text",
    },
    {
        "id": "FF-07",
        "category": "四流异常解释",
        "prompt": "【业务场景】项目部采购零星五金材料 3 万元，向无实质经营的个体户采购并由其代开专票，无送货单及出入库记录。请从四流一致性角度解释为何缺少物流凭证会导致进项税被税务机关纳税调增及行政处罚？",
        "expected_keywords": ["货物流凭证缺失", "出入库单", "真实交易", "虚开风险", "补税罚款"],
        "format": "text",
    },
    {
        "id": "FF-08",
        "category": "四流异常解释",
        "prompt": "【业务场景】劳务公司开具 500 万元劳务发票，但其申报的个人所得税工资表仅有 100 万元，剩余 400 万元无法提供民工考勤与发放凭单。请分析该发票流与资金流背后的虚开与偷逃个税风险。",
        "expected_keywords": ["虚构人工", "个税不匹配", "发票与实际支出不符", "虚开发票", "补缴个税"],
        "format": "text",
    },
    {
        "id": "FF-09",
        "category": "四流异常解释",
        "prompt": "【业务场景】总包合同约定由 A07 签约承建，但开工后实际由未具备资质的挂靠团队以 A07 内部承包名义施工，收益全部转入挂靠人私户。请从四流一致性与法律合规角度分析实质挂靠的税务与法律定性。",
        "expected_keywords": ["挂靠", "非法分包", "转包", "私户流转", "四流不合规", "虚开"],
        "format": "text",
    },
    {
        "id": "FF-10",
        "category": "四流异常解释",
        "prompt": "【业务场景】某设备租赁公司为项目提供塔吊，按 9% 建筑机械租赁开具发票，但合同约定仅提供纯机械租赁不带操作人员（应为 13% 动产租赁）。请分析合同约定与发票税目税率不一致的税务风险及补税责任。",
        "expected_keywords": ["税目不符", "13%", "动产租赁", "低税率开具", "补税", "滞纳金"],
        "format": "text",
    },

    # -------------------------------------------------------------
    # 4. Facts 风险审查与指标解释 (Canonical Facts Review - 10 items)
    # -------------------------------------------------------------
    {
        "id": "FCT-01",
        "category": "Facts风险审查",
        "prompt": "【Canonical Facts 确定性指标】\n- 项目代码: CD-GX-004\n- 确认收入(recognized_revenue): 4500.00 万元\n- 实际真实利润(real_profit): -230.50 万元\n- EAC预计利润率(eac_margin): -4.2%\n- 30天现金缺口(cash_gap_30d): 820.00 万元\n- 回款率(collection_rate): 48.5%\n【规则约束】不得重新计算或修改 Facts 数值。请基于上述事实分析该项目目前面临的核心财务与经营风险，并给出针对性改善建议。",
        "expected_keywords": ["亏损", "-230.5", "现金缺口", "820", "回款率低", "48.5%"],
        "expected_numbers": [-230.5, 820.0, 48.5],
        "format": "text",
    },
    {
        "id": "FCT-02",
        "category": "Facts风险审查",
        "prompt": "【Canonical Facts 确定性指标】\n- 项目代码: CD-TF-001\n- 确认收入: 8200.00 万元\n- 真实利润: 780.00 万元\n- 回款率: 88.2%\n- 30天现金缺口: 0.00 万元\n【规则约束】不得伪造数值。请评估该项目的健康状态并给出运营结论。",
        "expected_keywords": ["健康", "良好", "回款率", "88.2%", "780", "现金流充裕"],
        "expected_numbers": [780.0, 88.2],
        "format": "text",
    },
    {
        "id": "FCT-03",
        "category": "Facts风险审查",
        "prompt": "【Canonical Facts 确定性指标】\n- 主体代码: A08（天府建设）\n- 实体映射状态: VALID\n- 项目未开票收入: 1200.00 万元\n- 应付暂估成本: 1500.00 万元\n【规则约束】请分析未开票收入与暂估成本长期挂账可能引发的所得税与增值税合规风险。",
        "expected_keywords": ["暂估成本", "未开票收入", "发票取得", "纳税义务", "企业所得税税前扣除"],
        "expected_numbers": [1200.0, 1500.0],
        "format": "text",
    },
    {
        "id": "FCT-04",
        "category": "Facts风险审查",
        "prompt": "【Canonical Facts 确定性指标】\n- 项目代码: CY-CQ-002\n- 结算总额: 6000.00 万元\n- 累计已付分包款: 5800.00 万元\n- 分包占比: 96.67%\n- 总包毛利率: 1.2%\n【规则约束】请审查该项目的分包占比异动情况，分析是否存在利润极薄甚至暗中转包的合规隐患。",
        "expected_keywords": ["分包比例过高", "96.67%", "转包风险", "利润过低", "1.2%"],
        "expected_numbers": [96.67, 1.2],
        "format": "text",
    },
    {
        "id": "FCT-05",
        "category": "Facts风险审查",
        "prompt": "【Canonical Facts 确定性指标】\n- 实体代码: B02\n- 经营角色: 内部劳务分包\n- 关联交易金额: 3400.00 万元\n- 定价公允性偏离度: +18.5%\n【规则约束】请从税法关联交易与反避税调查角度，评估 B02 劳务分包定价偏离可能面临的纳税调整风险。",
        "expected_keywords": ["关联交易", "独立交易原则", "反避税", "特别纳税调整", "18.5%"],
        "expected_numbers": [18.5],
        "format": "text",
    },
    {
        "id": "FCT-06",
        "category": "Facts风险审查",
        "prompt": "【Canonical Facts 确定性指标】\n- 项目代码: GY-LZ-003\n- 30天应收账款到期: 1250.00 万元\n- 30天应付账款到期: 1800.00 万元\n- 可用流动资金: 150.00 万元\n【规则约束】计算并说明该项目即期的净现金缺口是多少？若业主未按期回款将导致何种供应链兑付危机？",
        "expected_keywords": ["400", "现金缺口", "供应商挤兑", "民工工资", "违约"],
        "expected_numbers": [400.0, 1250.0, 1800.0],
        "format": "text",
    },
    {
        "id": "FCT-07",
        "category": "Facts风险审查",
        "prompt": "【Canonical Facts 确定性指标】\n- 项目代码: QY-GEM-005\n- 合同工期进度: 85%\n- 累计确认产值: 45%\n- 进度偏差: -40%\n【规则约束】请审查该工期与产值严重倒挂指标，指出可能隐含的工程索赔及发包人违约索赔风险。",
        "expected_keywords": ["进度严重滞后", "-40%", "工期索赔", "违约金", "产值倒挂"],
        "expected_numbers": [85, 45, -40],
        "format": "text",
    },
    {
        "id": "FCT-08",
        "category": "Facts风险审查",
        "prompt": "【Canonical Facts 确定性指标】\n- 实体代码: A07\n- 资产负债率: 86.4%\n- 现金流动负债比: 0.12\n- 涉诉保全金额: 640.00 万元\n【规则约束】请评估该法人主体的偿债能力与流动性风险级别，并说明对在建项目的连带影响。",
        "expected_keywords": ["资产负债率高", "86.4%", "流动性不足", "账户冻结", "高风险"],
        "expected_numbers": [86.4, 640.0],
        "format": "text",
    },
    {
        "id": "FCT-09",
        "category": "Facts风险审查",
        "prompt": "【Canonical Facts 确定性指标】\n- 项目代码: YB-DEMO-001\n- 预缴税款抵减率: 100%\n- 进项税额留抵: 350.00 万元\n- 连续留抵月数: 8个月\n【规则约束】请分析长期大额留抵税额可能引发的税务预警原因（如进项提前认证、销项延迟开具等）。",
        "expected_keywords": ["留抵税额", "进销不匹配", "滞后开票", "税务稽查预警", "资金占用"],
        "expected_numbers": [350.0, 8],
        "format": "text",
    },
    {
        "id": "FCT-10",
        "category": "Facts风险审查",
        "prompt": "【Canonical Facts 确定性指标】\n- 实体主数据: D01（内部专业设备租赁）\n- 设备原值: 2200.00 万元\n- 累计折旧: 1800.00 万元\n- 设备平均成新率: 18.2%\n【规则约束】请评估该设备实体的重资产老化风险，以及设备更新改造对未来项目施工效率的潜在影响。",
        "expected_keywords": ["成新率低", "18.2%", "设备老化", "维保成本增加", "安全隐患"],
        "expected_numbers": [18.2, 2200.0],
        "format": "text",
    },

    # -------------------------------------------------------------
    # 5. 结构化输出与证据引用约束 (JSON Structure & Citations - 10 items)
    # -------------------------------------------------------------
    {
        "id": "JSON-01",
        "category": "结构化输出/引用",
        "prompt": "【证据包】\n[EVD-A1] 成都高新项目7月结算单显示回款延迟45天，涉及逾期款520万元。\n[EVD-A2] 8月监理备忘录记录钢筋进场抽检合格率仅82%，存在二次返工风险。\n【要求】以纯 JSON 格式输出，不得包含 Markdown 围栏。包含字段：summary (string), risk_level (string: LOW|MEDIUM|HIGH|CRITICAL), evidence_refs (array of string), findings (array of object: category, description, evidence_refs, suggestion)。必须准确引用给定证据编号。",
        "evidence_ids": ["EVD-A1", "EVD-A2"],
        "format": "json",
    },
    {
        "id": "JSON-02",
        "category": "结构化输出/引用",
        "prompt": "【证据包】\n[EVD-B1] 天府新区项目因环保督查停工累计达18天，产生窝工损失约35万元。\n【要求】以纯 JSON 输出，字段：summary, risk_level, evidence_refs, suggestions (array of string)。引用 EVD-B1。",
        "evidence_ids": ["EVD-B1"],
        "format": "json",
    },
    {
        "id": "JSON-03",
        "category": "结构化输出/引用",
        "prompt": "【证据包】\n[EVD-C1] 劳务分包商 B03 银行专户被法院司法冻结 120 万元，影响当月民工工资准时发放。\n[EVD-C2] 项目部应急资金储备仅余 30 万元。\n【要求】以纯 JSON 输出，字段：summary, risk_level, evidence_refs, findings。严禁伪造不存在的证据编号。",
        "evidence_ids": ["EVD-C1", "EVD-C2"],
        "format": "json",
    },
    {
        "id": "JSON-04",
        "category": "结构化输出/引用",
        "prompt": "【证据包】\n[EVD-D1] 广元利州项目分包合同未约定人工费调差公式，钢材与水泥价格近期分别上涨 14% 与 9%。\n【要求】以纯 JSON 输出，包含字段：summary, risk_level, evidence_refs, material_risks (array of object: material_name, price_change, evidence_refs)。",
        "evidence_ids": ["EVD-D1"],
        "format": "json",
    },
    {
        "id": "JSON-05",
        "category": "结构化输出/引用",
        "prompt": "【证据包】\n[EVD-E1] 成渝项目混凝土供应商提供发票与送货单数量偏差 250 方，差额款项约 11.25 万元。\n【要求】以纯 JSON 输出，包含字段：summary, risk_level, evidence_refs, discrepancy_amount, resolution_plan。",
        "evidence_ids": ["EVD-E1"],
        "format": "json",
    },
    {
        "id": "JSON-06",
        "category": "结构化输出/引用",
        "prompt": "【证据包】\n[EVD-F1] 施工升降机特种设备检验合格证将于 2026 年 9 月 15 日到期，尚未提请年度复检。\n【要求】以纯 JSON 输出，字段：summary, risk_level, evidence_refs, expiry_date, urgent_action。",
        "evidence_ids": ["EVD-F1"],
        "format": "json",
    },
    {
        "id": "JSON-07",
        "category": "结构化输出/引用",
        "prompt": "【证据包】\n[EVD-G1] 青羊项目地下室防水隐蔽工程验收记录缺少勘察单位代表签字确认。\n[EVD-G2] 监理单位已下发工程暂停令，责令停工整改。\n【要求】以纯 JSON 输出，字段：summary, risk_level, evidence_refs, findings。引用 EVD-G1 和 EVD-G2。",
        "evidence_ids": ["EVD-G1", "EVD-G2"],
        "format": "json",
    },
    {
        "id": "JSON-08",
        "category": "结构化输出/引用",
        "prompt": "【证据包】\n[EVD-H1] 建设单位因资金链趋紧，向总包发函申请将后续进度款支付比例由 80% 暂调为 60%。\n【要求】以纯 JSON 输出，字段：summary, risk_level, evidence_refs, cash_impact_analysis, countermeasures。",
        "evidence_ids": ["EVD-H1"],
        "format": "json",
    },
    {
        "id": "JSON-09",
        "category": "结构化输出/引用",
        "prompt": "【证据包】\n[EVD-I1] 宜宾项目分包单位跨区开具增值税发票，未在项目所在地税务机关进行跨区域涉税事项报告。\n【要求】以纯 JSON 输出，字段：summary, risk_level, evidence_refs, tax_compliance_risk, required_procedures。",
        "evidence_ids": ["EVD-I1"],
        "format": "json",
    },
    {
        "id": "JSON-10",
        "category": "结构化输出/引用",
        "prompt": "【证据包】\n[EVD-J1] 项目夜间施工产生噪声投诉 3 起，市生态环境局下发责令改正通知书并拟处以 5 万元罚款。\n【要求】以纯 JSON 输出，字段：summary, risk_level, evidence_refs, penalty_amount, corrective_measures。",
        "evidence_ids": ["EVD-J1"],
        "format": "json",
    },
]


def stop_any_llama_server():
    """Ensure no previous llama-server is running."""
    subprocess.run(["pkill", "-9", "-f", "llama-server"], capture_output=True)
    time.sleep(1)


def start_server(model_path: str, alias: str) -> subprocess.Popen:
    """Start llama-server for the specified model."""
    stop_any_llama_server()
    cmd = [
        LLAMA_SERVER_BIN,
        "--model", model_path,
        "--host", SERVER_HOST,
        "--port", str(SERVER_PORT),
        "--alias", alias,
        "--ctx-size", "4096",
        "--threads", "4",
        "--threads-batch", "4",
        "--batch-size", "512",
        "--ubatch-size", "256",
        "-ngl", "99",
        "--reasoning", "off",
        "--parallel", "1",
        "--jinja",
    ]
    log_file = open(f"/tmp/server_{alias}.log", "w")
    proc = subprocess.Popen(cmd, stdout=log_file, stderr=log_file)
    # Wait for server readiness
    ready = False
    for _ in range(60):
        try:
            r = httpx.get(HEALTH_URL, timeout=1.0)
            if r.status_code == 200 and r.json().get("status") == "ok":
                ready = True
                break
        except Exception:
            pass
        time.sleep(0.5)
    if not ready:
        raise RuntimeError(f"Failed to start llama-server for model: {alias}")
    return proc


def get_process_memory_mb(pid: int) -> float:
    """Return current process RSS in MB including children."""
    try:
        main_proc = psutil.Process(pid)
        rss = main_proc.memory_info().rss
        for child in main_proc.children(recursive=True):
            try:
                rss += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return rss / (1024 * 1024)
    except Exception:
        return 0.0


def evaluate_single_case(client: httpx.Client, model_alias: str, case: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a single test case, measuring TTFT, generation speed, accuracy, citations, and JSON validity."""
    prompt = case["prompt"]
    is_json = case["format"] == "json"
    
    payload = {
        "model": model_alias,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一个专业的建筑工程与财税智能分析专家。"
                    + (" 必须以纯JSON输出，不要输出Markdown代码围栏。" if is_json else "")
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 512,
        "stream": True,
    }
    if is_json:
        payload["response_format"] = {"type": "json_object"}

    start_time = time.perf_counter()
    first_token_time = None
    generated_text = ""
    token_count = 0

    try:
        with client.stream("POST", API_URL, json=payload, timeout=60.0) as response:
            for line in response.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    delta = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if delta:
                        if first_token_time is None:
                            first_token_time = time.perf_counter()
                        generated_text += delta
                        token_count += 1
                except Exception:
                    pass
    except Exception as e:
        return {
            "id": case["id"],
            "category": case["category"],
            "success": False,
            "error": str(e),
            "ttft_ms": 0,
            "tokens_per_sec": 0,
            "total_ms": 0,
            "output": "",
            "json_valid": False,
            "citation_precision": 0.0,
            "citation_recall": 0.0,
            "hallucination": True,
            "business_correct": False,
        }

    total_time = time.perf_counter() - start_time
    ttft_ms = (first_token_time - start_time) * 1000 if first_token_time else total_time * 1000
    gen_time = total_time - (ttft_ms / 1000)
    tok_per_sec = token_count / gen_time if gen_time > 0 and token_count > 0 else 0

    # 1. JSON Validity Check
    json_valid = False
    parsed_json = None
    if is_json:
        clean_text = generated_text.strip()
        if clean_text.startswith("```"):
            clean_text = re.sub(r"^```[a-zA-Z]*\n?", "", clean_text)
            clean_text = re.sub(r"\n?```$", "", clean_text).strip()
        try:
            parsed_json = json.loads(clean_text)
            json_valid = isinstance(parsed_json, dict) and len(parsed_json) > 0
        except Exception:
            json_valid = False
    else:
        json_valid = True

    # 2. Citation Check
    expected_evds = set(case.get("evidence_ids", []))
    citation_precision = 1.0
    citation_recall = 1.0
    hallucinated_citation = False

    if expected_evds:
        # Find all cited evidence IDs in generated text
        found_evds = set(re.findall(r"EVD-[A-Za-z0-9]+", generated_text))
        if is_json and parsed_json and isinstance(parsed_json.get("evidence_refs"), list):
            found_evds.update([str(x) for x in parsed_json["evidence_refs"]])
        
        if found_evds:
            valid_cites = found_evds.intersection(expected_evds)
            invalid_cites = found_evds - expected_evds
            citation_precision = len(valid_cites) / len(found_evds) if found_evds else 0.0
            citation_recall = len(valid_cites) / len(expected_evds) if expected_evds else 1.0
            if invalid_cites:
                hallucinated_citation = True
        else:
            citation_precision = 0.0
            citation_recall = 0.0

    # 3. Business Correctness Check
    kw_hits = 0
    expected_kws = case.get("expected_keywords", [])
    for kw in expected_kws:
        # Check case-insensitive substring match
        if kw.lower() in generated_text.lower():
            kw_hits += 1
    kw_score = kw_hits / len(expected_kws) if expected_kws else 1.0

    num_hits = 0
    expected_nums = case.get("expected_numbers", [])
    for num in expected_nums:
        # Check both int and float representations
        num_str = str(num)
        int_str = str(int(num)) if isinstance(num, (int, float)) and num == int(num) else None
        if num_str in generated_text or (int_str and int_str in generated_text):
            num_hits += 1
    num_score = num_hits / len(expected_nums) if expected_nums else 1.0

    # Business is correct if key terms are recognized and numbers are accurate
    if expected_kws and expected_nums:
        business_correct = (kw_score >= 0.4) and (num_score >= 0.5)
    elif expected_kws:
        business_correct = kw_score >= 0.4
    elif expected_nums:
        business_correct = num_score >= 0.5
    else:
        business_correct = json_valid

    hallucination = hallucinated_citation or (not business_correct and kw_score < 0.2)

    return {
        "id": case["id"],
        "category": case["category"],
        "success": True,
        "ttft_ms": round(ttft_ms, 1),
        "tokens_per_sec": round(tok_per_sec, 1),
        "token_count": token_count,
        "total_ms": round(total_time * 1000, 1),
        "output_snippet": generated_text[:140].replace("\n", " "),
        "json_valid": json_valid,
        "citation_precision": round(citation_precision, 2),
        "citation_recall": round(citation_recall, 2),
        "hallucination": hallucination,
        "business_correct": business_correct,
        "kw_score": round(kw_score, 2),
        "num_score": round(num_score, 2),
    }


def run_benchmark_for_model(model_path: str, model_alias: str) -> Dict[str, Any]:
    """Runs the 60-case benchmark on a specific model, recording memory and per-test metrics."""
    print(f"\n=======================================================")
    print(f"🚀 Starting Benchmark for: {model_alias} ({os.path.basename(model_path)})")
    print(f"=======================================================")
    
    server_proc = start_server(model_path, model_alias)
    server_pid = server_proc.pid

    peak_ram_mb = 0.0
    mem_stop_flag = threading.Event()

    def track_memory():
        nonlocal peak_ram_mb
        while not mem_stop_flag.is_set():
            mem = get_process_memory_mb(server_pid)
            if mem > peak_ram_mb:
                peak_ram_mb = mem
            time.sleep(0.1)

    mem_thread = threading.Thread(target=track_memory, daemon=True)
    mem_thread.start()

    results = []
    with httpx.Client(timeout=90.0) as client:
        for idx, case in enumerate(BENCHMARK_CASES):
            sys.stdout.write(f"\r[{idx+1}/{len(BENCHMARK_CASES)}] Running {case['id']} ({case['category']})...")
            sys.stdout.flush()
            res = evaluate_single_case(client, model_alias, case)
            results.append(res)
            time.sleep(0.05)
    print("\n✅ All 60 cases completed.")

    mem_stop_flag.set()
    mem_thread.join(timeout=1.0)
    stop_any_llama_server()

    # Aggregate metrics
    valid_results = [r for r in results if r["success"]]
    total_cases = len(results)
    correct_count = sum(1 for r in valid_results if r["business_correct"])
    json_cases = [r for r in valid_results if "JSON" in r["id"]]
    json_valid_count = sum(1 for r in json_cases if r["json_valid"])
    hallucination_count = sum(1 for r in valid_results if r["hallucination"])

    avg_ttft = sum(r["ttft_ms"] for r in valid_results) / len(valid_results) if valid_results else 0
    avg_speed = sum(r["tokens_per_sec"] for r in valid_results) / len(valid_results) if valid_results else 0
    avg_cit_precision = sum(r["citation_precision"] for r in valid_results) / len(valid_results) if valid_results else 0
    avg_cit_recall = sum(r["citation_recall"] for r in valid_results) / len(valid_results) if valid_results else 0

    # Category breakdown
    categories = sorted(list(set(c["category"] for c in BENCHMARK_CASES)))
    cat_stats = {}
    for cat in categories:
        cat_items = [r for r in valid_results if r["category"] == cat]
        cat_correct = sum(1 for r in cat_items if r["business_correct"])
        cat_stats[cat] = {
            "total": len(cat_items),
            "correct": cat_correct,
            "accuracy_pct": round(cat_correct / len(cat_items) * 100, 1) if cat_items else 0.0,
            "avg_ttft_ms": round(sum(r["ttft_ms"] for r in cat_items) / len(cat_items), 1) if cat_items else 0,
            "avg_speed": round(sum(r["tokens_per_sec"] for r in cat_items) / len(cat_items), 1) if cat_items else 0,
        }

    return {
        "model_alias": model_alias,
        "model_path": model_path,
        "model_size_bytes": os.path.getsize(model_path),
        "peak_ram_mb": round(peak_ram_mb, 1),
        "total_cases": total_cases,
        "business_accuracy_pct": round(correct_count / total_cases * 100, 2),
        "json_valid_pct": round(json_valid_count / len(json_cases) * 100, 2) if json_cases else 100.0,
        "hallucination_rate_pct": round(hallucination_count / total_cases * 100, 2),
        "avg_ttft_ms": round(avg_ttft, 1),
        "avg_tokens_per_sec": round(avg_speed, 1),
        "citation_precision_pct": round(avg_cit_precision * 100, 2),
        "citation_recall_pct": round(avg_cit_recall * 100, 2),
        "category_stats": cat_stats,
        "details": results,
    }


def main():
    print("=== Chengdu Construction V2.0 LLM A/B Benchmark ===")
    ling_result = run_benchmark_for_model(LING_MODEL, "ling-3.0-tiny")
    qwen_result = run_benchmark_for_model(QWEN_MODEL, "local-qwen3.5-2b")

    report = {
        "benchmark_date": "2026-08-25",
        "ling_3_0_tiny": ling_result,
        "qwen_3_5_2b": qwen_result,
        "comparison": {
            "accuracy_ratio": round((ling_result["business_accuracy_pct"] / qwen_result["business_accuracy_pct"]) * 100, 2) if qwen_result["business_accuracy_pct"] > 0 else 100.0,
            "speed_multiplier": round(ling_result["avg_tokens_per_sec"] / qwen_result["avg_tokens_per_sec"], 2) if qwen_result["avg_tokens_per_sec"] > 0 else 1.0,
            "ram_delta_mb": round(ling_result["peak_ram_mb"] - qwen_result["peak_ram_mb"], 1),
            "ttft_speedup_ms": round(qwen_result["avg_ttft_ms"] - ling_result["avg_ttft_ms"], 1),
        },
    }

    report_path = "/Users/yvoche/AI开发/073_成都建工/V2.0/tests/ab_benchmark_results.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n\n=======================================================")
    print("📊 A/B BENCHMARK FINAL SUMMARY REPORT")
    print("=======================================================")
    print(f"Total Cases Evaluated: {len(BENCHMARK_CASES)}")
    print(f"Ling-3.0-tiny Business Accuracy: {ling_result['business_accuracy_pct']}%")
    print(f"Qwen3.5-2B    Business Accuracy: {qwen_result['business_accuracy_pct']}%")
    print(f"Accuracy Retention (Ling / Qwen): {report['comparison']['accuracy_ratio']}% (Threshold: >= 95.0%)")
    print(f"Ling-3.0-tiny Gen Speed: {ling_result['avg_tokens_per_sec']} tok/s (vs Qwen {qwen_result['avg_tokens_per_sec']} tok/s, {report['comparison']['speed_multiplier']}x)")
    print(f"Ling-3.0-tiny TTFT: {ling_result['avg_ttft_ms']} ms (vs Qwen {qwen_result['avg_ttft_ms']} ms)")
    print(f"Ling-3.0-tiny Peak RAM: {ling_result['peak_ram_mb']} MB (vs Qwen {qwen_result['peak_ram_mb']} MB)")
    print(f"Ling-3.0-tiny JSON Compliance: {ling_result['json_valid_pct']}%")
    print(f"Ling-3.0-tiny Hallucination Rate: {ling_result['hallucination_rate_pct']}% (vs Qwen {qwen_result['hallucination_rate_pct']}%)")
    print(f"Saved full JSON results to: {report_path}")


if __name__ == "__main__":
    main()
