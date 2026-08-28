with open("source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/app/seed.py", "r") as f:
    content = f.read()

new_parties = """EXTERNAL_PARTIES = [
    ("EXT-TF", "成都市天府新区金融城投公司", "天府金融城投", "construction"),
    ("EXT-CY", "成渝高速公路开发投资集团", "成渝高速投资", "construction"),
    ("EXT-GY", "广元市利州区水务局城投平台", "广元利州水务", "construction"),
    ("EXT-GX", "国家电网四川省电力公司成都供电公司", "国网成都供电", "construction"),
    ("EXT-GEM", "青海盐湖工业股份有限公司", "青海盐湖工业", "industrial"),
    ("EXT-YB", "宜宾市三江新区开发集团", "宜宾三江开发", "construction"),
    ("EXT-ABB-ELECTRIC", "ABB(中国)特种电气设备公司", "ABB电气", "supplier"),
    ("EXT-NARI", "南京南瑞继保电气有限公司", "南瑞继保", "supplier"),
    ("EXT-QH-RAILWAY", "青海地方铁路建设投资有限公司", "青海铁投", "construction"),
    ("EXT-RAIL", "中铁特种轨道工程有限公司", "特种轨道", "construction"),
    ("EXT-SHIP", "长航特种工程潜水与打捞公司", "长航潜水", "construction"),
    ("EXT-CONC", "西南特种混凝土骨料直供站", "特种商砼", "supplier"),
    ("EXT-XN-CONCRETE", "西南特种混凝土骨料直供站", "特种商砼", "supplier"),
    ("EXT-CQ-HEAVY-CRANE", "重庆巨力重型起重设备吊装公司", "重庆巨力吊装", "construction"),
    ("EXT-CRANE", "重庆巨力重型起重设备吊装公司", "重庆巨力吊装", "construction"),
    ("EXT-EXP", "中建西南地勘院技术专家组", "西南地勘", "service"),
    ("EXT-TREE", "四川省生态林业苗木繁育中心", "生态林业", "supplier"),
    ("EXT-PG", "攀钢集团特种钢材直销部", "攀钢直销", "supplier"),
    ("EXT-PG-STEEL", "攀钢集团特种钢材直销部", "攀钢直销", "supplier"),
    ("EXT-EXPERT-LABOR", "特种作业专家劳务派遣中心", "特种劳务", "service"),
    ("EXT-CY-001", "外部专业劳务协作与技术支持", "劳务协作", "service"),
    ("EXT-GX-001", "特种电力配套调试与试验配合", "特种电力", "service"),
    ("EXT-GEM-001", "特种防腐耐磨地坪材料直销", "特种地坪", "supplier"),
    ("EXT-GY-001", "厂区雨污水管网生态开挖分包", "管网开挖", "construction"),
    ("EXT-GY-FOREST", "国家天然林保护工程林木中心", "国家天然林", "supplier")
]"""

import re
content = re.sub(r'EXTERNAL_PARTIES = \[[^\]]+\]', new_parties, content)

with open("source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/app/seed.py", "w") as f:
    f.write(content)
