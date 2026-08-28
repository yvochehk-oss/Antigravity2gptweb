from app.services.metadata import refine_from_content, load_canonical_entity_cache
text = """成都建工集团内部档案管理系统·原始凭证扫描存档件
档案编号：TF-A08-D01
工程分项业务协议书（机械租赁）
合同编号
TF-A08-D01.
所属总包项目
成都天府国际金融中心二期大厦工程
发包/采购方
四川锐宝建设工程有限公司
承包/供应方
四川乾润和机械设备租赁有限公司
签约暂定金额
￥110.000,000.00元
发票开具税率
13%（物资/纯租赁）/9%（建筑施工劳务）/6%（技术服务）
结算支付进度
按月度核定形象进度80%支付，留存3%保修金"""

current = {
    "document_type": "other",
    "business_category": "",
    "tax_category": "",
}

res = refine_from_content(current, text, load_canonical_entity_cache())
print(res)
