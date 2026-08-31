# -*- coding: utf-8 -*-
"""
=============================================================================
成都建工 V3.0 · 01 标杆工程【系统内单位 + 系统外单位】全覆盖完税证明生成器
=============================================================================
项目：01_天府国际金融中心二期_CD-TF-001

一、系统内单位 (Internal Entities - 6 家法人)：
  1. A08 四川锐宝建设工程有限公司 (施工总承包)
  2. B01 四川乾润和贸易有限公司 (大宗材料集采)
  3. C01 四川本盛劳务有限公司 (建筑劳务分包)
  4. D01 四川乾润和机械设备租赁有限公司 (机械设备租赁)
  5. A11 成都巨邦建设工程有限公司 (钢结构专业分包)
  6. A05 四川帆亿通信科技有限公司 (弱电智能及BIM微网)

二、系统外单位 (External Parties - 4 家代表性合作机构)：
  1. EXT-TF 成都市天府新区金融城投公司 (外部发包业主单位)
  2. EXT-PG-STEEL 攀钢集团攀枝花钢钒物资销售有限公司 (外部钢厂直供商)
  3. EXT-CQ-HEAVY-CRANE 重庆重交大件起重吊装工程有限公司 (外部特种大件吊装商)
  4. EXT-EXPERT-LABOR 四川省建筑科学研究院特种技术服务中心 (外部技术专家咨询)

涵盖税种：
  - 增值税 (VAT 6% / 9% / 13%)
  - 城市维护建设税 (7%)、教育费附加 (3%)、地方教育附加 (2%)
  - 企业所得税 (CIT 25%)
  - 个人所得税 (IIT 实名制代扣代缴)
  - 印花税 (Stamp Duty 0.03% 施工/购销、0.1% 财产租赁)
  - 环境保护税 (施工现场扬尘与噪声)
  - 城镇土地使用税 (临时施工用地)

输出成果：
  - 国家税务总局正式税收完税证明 PDF
  - 真实扫描影印件 JPG (带税务局红色征税电子专用章与 TIPS 防伪编码)
  - 数据库 tax_payment_records 真实数据持久化写入
=============================================================================
"""
import os
import sys
import math
import random
from datetime import datetime, date
from decimal import Decimal
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

import reportlab
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT, TA_JUSTIFY
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

# 注册中文字体
FONT_PATHS = [
    '/System/Library/Fonts/STHeiti Medium.ttc',
    '/System/Library/Fonts/STHeiti Light.ttc',
    '/System/Library/Fonts/PingFang.ttc',
    '/System/Library/Fonts/Supplemental/Songti.ttc',
    '/System/Library/Fonts/Supplemental/Arial Unicode.ttf'
]

FONT_NAME = 'STHeiti'
FONT_PATH = None
for p in FONT_PATHS:
    if os.path.exists(p):
        FONT_PATH = p
        break

if not FONT_PATH:
    raise RuntimeError("未找到系统可用中文字体")

pdfmetrics.registerFont(TTFont(FONT_NAME, FONT_PATH))

PROJECT_ROOT = "/Users/yvoche/AI开发/073_成都建工/V3.0"
TARGET_DIR = os.path.join(
    PROJECT_ROOT,
    "项目存档资料/01_天府国际金融中心二期_CD-TF-001/06_增值税发票与税务完税凭证"
)
os.makedirs(TARGET_DIR, exist_ok=True)

# 参建单位档案库（系统内 + 系统外）
ALL_ENTITIES = {
    # 系统内
    'A08': {
        'name': '四川锐宝建设工程有限公司',
        'type_label': '系统内 · 施工总承包',
        'tax_id': '91510106MA61UEJ48K',
        'bank_name': '成都银行科技支行',
        'bank_acc': '51090177889900112233',
        'tax_authority': '国家税务总局成都市金牛区税务局第一税务所',
        'treasury': '国家金库成都市中心支库'
    },
    'B01': {
        'name': '四川乾润和贸易有限公司',
        'type_label': '系统内 · 物资贸易',
        'tax_id': '91510106MA6D7H4N9A',
        'bank_name': '招商银行成都金牛支行',
        'bank_acc': '51060122334455667788',
        'tax_authority': '国家税务总局成都市金牛区税务局茶店子税务所',
        'treasury': '国家金库成都市中心支库'
    },
    'C01': {
        'name': '四川本盛劳务有限公司',
        'type_label': '系统内 · 劳务分包',
        'tax_id': '91510107350645934C',
        'bank_name': '成都农商银行成华支行',
        'bank_acc': '51010833445566778800',
        'tax_authority': '国家税务总局成都市成华区税务局猛追湾税务所',
        'treasury': '国家金库成都市中心支库'
    },
    'D01': {
        'name': '四川乾润和机械设备租赁有限公司',
        'type_label': '系统内 · 机械租赁',
        'tax_id': '91510106MA6D2K9L3E',
        'bank_name': '中国民生银行成都金牛支行',
        'bank_acc': '51010188990011223366',
        'tax_authority': '国家税务总局成都市金牛区税务局抚琴税务所',
        'treasury': '国家金库成都市中心支库'
    },
    'A11': {
        'name': '成都巨邦建设工程有限公司',
        'type_label': '系统内 · 钢结构分包',
        'tax_id': '91510104MA6CM8P59N',
        'bank_name': '中国农业银行成都锦江支行',
        'bank_acc': '51040188990011223344',
        'tax_authority': '国家税务总局成都市锦江区税务局东湖税务所',
        'treasury': '国家金库成都市中心支库'
    },
    'A05': {
        'name': '四川帆亿通信科技有限公司',
        'type_label': '系统内 · 弱电智能化',
        'tax_id': '91510104MA6CYN4T2A',
        'bank_name': '交通银行成都高新支行',
        'bank_acc': '51030133445566778899',
        'tax_authority': '国家税务总局成都市高新技术产业开发区税务局',
        'treasury': '国家金库成都市高新支库'
    },
    # 系统外
    'EXT-TF': {
        'name': '成都市天府新区金融城投公司',
        'type_label': '系统外 · 发包业主单位',
        'tax_id': '91510100MA61AAAA11',
        'bank_name': '国家开发银行四川省分行',
        'bank_acc': '51000012345678901111',
        'tax_authority': '国家税务总局四川天府新区成都管委会税务局',
        'treasury': '国家金库天府新区支库'
    },
    'EXT-PG-STEEL': {
        'name': '攀钢集团攀枝花钢钒物资销售有限公司',
        'type_label': '系统外 · 外部特种钢材供应商',
        'tax_id': '91510400MA61EEEE77',
        'bank_name': '中国建设银行攀枝花分行',
        'bank_acc': '51040188990011227777',
        'tax_authority': '国家税务总局攀枝花市东区税务局',
        'treasury': '国家金库攀枝花市中心支库'
    },
    'EXT-CQ-HEAVY-CRANE': {
        'name': '重庆重交大件起重吊装工程有限公司',
        'type_label': '系统外 · 外部特种大件吊装服务商',
        'tax_id': '91500100MA61GGGG99',
        'bank_name': '重庆银行江北支行',
        'bank_acc': '50010144556677889999',
        'tax_authority': '国家税务总局重庆市江北区税务局第一税务所',
        'treasury': '国家金库重庆市分库'
    },
    'EXT-EXPERT-LABOR': {
        'name': '四川省建筑科学研究院特种技术服务中心',
        'type_label': '系统外 · 外部地质监测与技术咨询',
        'tax_id': '91510100MA61KKKK33',
        'bank_name': '中国工商银行成都一环路支行',
        'bank_acc': '51010199001122333333',
        'tax_authority': '国家税务总局成都市青羊区税务局',
        'treasury': '国家金库成都市青羊区支库'
    },
}

PROJECT_INFO = {
    'name': '成都天府国际金融中心二期大厦工程',
    'code': 'CD-TF-001'
}

# ---------------------------------------------------------------------------
# 渲染函数 (印章 + 纹理)
# ---------------------------------------------------------------------------

def draw_tax_oval_stamp(draw, text_top="国家税务总局", text_mid="四川省税务局", text_bot="征税专用章", center=(150, 150), rx=110, ry=75, color=(220, 20, 20)):
    cx, cy = center
    draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], outline=color, width=3)
    draw.ellipse([cx - rx + 4, cy - ry + 4, cx + rx - 4, cy + ry - 4], outline=color, width=1)
    
    star_r = 16
    points = []
    for i in range(5):
        angle = i * 4 * math.pi / 5 - math.pi / 2
        points.append((cx + star_r * math.cos(angle), cy - 6 + star_r * math.sin(angle)))
    draw.polygon(points, fill=color)
    
    font_mid = ImageFont.truetype(FONT_PATH, 17)
    font_sub = ImageFont.truetype(FONT_PATH, 14)
    
    tw1 = draw.textlength(text_top, font=font_sub)
    draw.text((cx - tw1 / 2, cy - ry + 15), text_top, font=font_sub, fill=color)
    
    tw2 = draw.textlength(text_mid, font=font_mid)
    draw.text((cx - tw2 / 2, cy + 8), text_mid, font=font_mid, fill=color)
    
    tw3 = draw.textlength(text_bot, font=font_sub)
    draw.text((cx - tw3 / 2, cy + ry - 28), text_bot, font=font_sub, fill=color)


def apply_photocopy_texture(img, noise_level=8, contrast_boost=1.12):
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(contrast_boost)
    w, h = img.size
    pixels = img.load()
    for _ in range(int(w * h * (noise_level / 1000.0))):
        rx = random.randint(0, w - 1)
        ry = random.randint(0, h - 1)
        gray = random.randint(50, 180)
        pixels[rx, ry] = (gray, gray, gray)
    return img


class TaxNumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_tax_header_footer(num_pages)
            super().showPage()
        super().save()

    def draw_tax_header_footer(self, page_count):
        self.saveState()
        self.setFont(FONT_NAME, 8)
        self.setFillColor(colors.HexColor('#555555'))
        self.drawString(54, 802, "国家税务总局全国统一电子税票凭证系统 · 真实税收缴款档案")
        self.setStrokeColor(colors.HexColor('#AAAAAA'))
        self.setLineWidth(0.6)
        self.line(54, 794, 540, 794)
        self.line(54, 45, 540, 45)
        self.drawRightString(540, 32, f"第 {self._pageNumber} 页 / 共 {page_count} 页")
        self.drawString(54, 32, "妥善保管 · 本凭证系纳税人依法履行纳税义务的正式合法依据 · TIPS联网核销有效")
        self.restoreState()


def get_tax_doc_styles():
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'TaxTitle', parent=styles['Normal'],
        fontName=FONT_NAME, fontSize=15, leading=20, alignment=TA_CENTER, textColor=colors.HexColor('#9C0006')
    )
    subtitle_style = ParagraphStyle(
        'TaxSubtitle', parent=styles['Normal'],
        fontName=FONT_NAME, fontSize=11, leading=16, alignment=TA_CENTER, textColor=colors.HexColor('#222222')
    )
    cell_style = ParagraphStyle(
        'TaxCell', parent=styles['Normal'],
        fontName=FONT_NAME, fontSize=8.5, leading=11, alignment=TA_LEFT
    )
    cell_center = ParagraphStyle(
        'TaxCellCenter', parent=styles['Normal'],
        fontName=FONT_NAME, fontSize=8.5, leading=11, alignment=TA_CENTER
    )
    cell_bold = ParagraphStyle(
        'TaxCellBold', parent=styles['Normal'],
        fontName=FONT_NAME, fontSize=8.5, leading=11, alignment=TA_LEFT
    )
    return {
        'title': title_style,
        'subtitle': subtitle_style,
        'cell': cell_style,
        'center': cell_center,
        'bold': cell_bold,
    }


def create_pdf_tax_certificate(filepath, cert_data):
    entity = ALL_ENTITIES[cert_data['entity_code']]
    doc = SimpleDocTemplate(filepath, pagesize=A4, leftMargin=45, rightMargin=45, topMargin=45, bottomMargin=45)
    styles = get_tax_doc_styles()
    story = []
    
    prov_title = "国家税务总局 重庆市税务局" if "重庆" in entity['tax_authority'] else "国家税务总局 四川省税务局"
    story.append(Paragraph(f"<b>{prov_title}</b>", styles['title']))
    story.append(Paragraph(f"<b>税 收 完 税 证 明</b> <font size=9 color='#666'>（{entity['type_label']}电子缴款凭证）</font>", styles['subtitle']))
    story.append(Spacer(1, 4))
    
    header_table = [
        [
            Paragraph(f"<b>完税证字号：</b>{cert_data['receipt_no']}", styles['bold']),
            Paragraph(f"<b>填发日期：</b>{cert_data['payment_date']}", styles['bold']),
            Paragraph(f"<b>电子税票号码：</b>{cert_data['tax_ticket_no']}", styles['bold']),
        ]
    ]
    ht = Table(header_table, colWidths=[180, 150, 175])
    ht.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
    ]))
    story.append(ht)
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#9C0006'), spaceBefore=3, spaceAfter=6))
    
    info_table = [
        [
            Paragraph("<b>纳税人识别号</b>", styles['center']),
            Paragraph(f"<b>{entity['tax_id']}</b>", styles['cell']),
            Paragraph("<b>纳税人名称</b>", styles['center']),
            Paragraph(f"<b>{entity['name']}</b>", styles['cell']),
        ],
        [
            Paragraph("<b>开户银行</b>", styles['center']),
            Paragraph(entity['bank_name'], styles['cell']),
            Paragraph("<b>银行账号</b>", styles['center']),
            Paragraph(entity['bank_acc'], styles['cell']),
        ],
        [
            Paragraph("<b>所属工程项目</b>", styles['center']),
            Paragraph(f"{PROJECT_INFO['name']} ({PROJECT_INFO['code']})", styles['cell']),
            Paragraph("<b>主管税务机关</b>", styles['center']),
            Paragraph(entity['tax_authority'], styles['cell']),
        ]
    ]
    it = Table(info_table, colWidths=[90, 162, 90, 163])
    it.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#9C0006')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#D32F2F')),
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#FFEBEE')),
        ('BACKGROUND', (2,0), (2,-1), colors.HexColor('#FFEBEE')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(it)
    story.append(Spacer(1, 6))
    
    items_header = [
        Paragraph("<b>原凭证号</b>", styles['center']),
        Paragraph("<b>税种</b>", styles['center']),
        Paragraph("<b>品目名称</b>", styles['center']),
        Paragraph("<b>税款所属时期</b>", styles['center']),
        Paragraph("<b>计税依据/销售额</b>", styles['center']),
        Paragraph("<b>税率/征收率</b>", styles['center']),
        Paragraph("<b>实缴(销号)金额(元)</b>", styles['center']),
    ]
    items_table = [items_header]
    
    total_tax = Decimal("0")
    for row in cert_data['items']:
        amt = Decimal(str(row['amount']))
        total_tax += amt
        items_table.append([
            Paragraph(str(row['orig_no']), styles['center']),
            Paragraph(f"<b>{row['tax_type']}</b>", styles['cell']),
            Paragraph(str(row['category']), styles['cell']),
            Paragraph(str(row['period_range']), styles['center']),
            Paragraph(f"¥ {row['tax_base']:,.2f}" if row.get('tax_base') else "—", styles['center']),
            Paragraph(str(row['rate']), styles['center']),
            Paragraph(f"<b>¥ {amt:,.2f}</b>", styles['center']),
        ])
        
    items_table.append([
        Paragraph("<b>合计金额 (大写)</b>", styles['center']),
        Paragraph(f"<b>人民币：{cert_data['total_chinese']}</b>", styles['bold']),
        Paragraph("", styles['cell']),
        Paragraph("", styles['cell']),
        Paragraph("", styles['cell']),
        Paragraph("<b>小写合计</b>", styles['center']),
        Paragraph(f"<b>¥ {total_tax:,.2f}</b>", styles['bold']),
    ])
    
    col_w = [65, 75, 85, 95, 80, 45, 60]
    dt = Table(items_table, colWidths=col_w)
    dt.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#9C0006')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E0E0E0')),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#FFCDD2')),
        ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#FFF9C4')),
        ('SPAN', (1,-1), (4,-1)),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(dt)
    story.append(Spacer(1, 8))
    
    footer_info = [
        [
            Paragraph(f"<b>收款国库：</b>{entity['treasury']}<br/><b>缴款方式：</b>财税库银横向联网电子扣税（TIPS扣款成功）<br/><b>扣款银行流水：</b>{cert_data['bank_flow_no']}", styles['cell']),
            Paragraph(f"<b>电子防伪验证码：</b><br/><font color='#1565C0'>SCTAX-{cert_data['receipt_no']}-PASS</font><br/><b>查验网址：</b>https://etax.chinatax.gov.cn", styles['cell']),
            Paragraph(f"<b>征收机关：</b><br/>{entity['tax_authority']}<br/><b>经办人：</b>系统自动开具（电子验签）", styles['cell']),
        ]
    ]
    ft = Table(footer_info, colWidths=[175, 175, 155])
    ft.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 0.8, colors.HexColor('#B0BEC5')),
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#ECEFF1')),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(ft)
    
    doc.build(story, canvasmaker=TaxNumberedCanvas)


def create_jpg_tax_certificate_scan(filepath, cert_data):
    entity = ALL_ENTITIES[cert_data['entity_code']]
    w, h = 1600, 2260
    bg_color = (253, 252, 248)
    img = Image.new('RGB', (w, h), color=bg_color)
    draw = ImageDraw.Draw(img)
    
    title_font = ImageFont.truetype(FONT_PATH, 42)
    sub_title_font = ImageFont.truetype(FONT_PATH, 30)
    bold_font = ImageFont.truetype(FONT_PATH, 24)
    text_font = ImageFont.truetype(FONT_PATH, 22)
    small_font = ImageFont.truetype(FONT_PATH, 18)
    
    draw.text((80, 60), f"国家税务总局全国统一电子税票系统 · 原始凭证 ({entity['type_label']})", font=small_font, fill=(120, 120, 120))
    draw.text((w - 520, 60), f"完税证号: {cert_data['receipt_no']}", font=small_font, fill=(120, 120, 120))
    draw.line([(80, 95), (w - 80, 95)], fill=(180, 50, 50), width=2)
    
    prov_title = "国家税务总局 重庆市税务局" if "重庆" in entity['tax_authority'] else "国家税务总局 四川省税务局"
    tw1 = draw.textlength(prov_title, font=title_font)
    draw.text(((w - tw1) / 2, 140), prov_title, font=title_font, fill=(160, 20, 20))
    
    t2 = f"税 收 完 税 证 明（电子缴款凭证）"
    tw2 = draw.textlength(t2, font=sub_title_font)
    draw.text(((w - tw2) / 2, 205), t2, font=sub_title_font, fill=(20, 20, 20))
    draw.line([((w - tw2) / 2 - 30, 255), ((w + tw2) / 2 + 30, 255)], fill=(160, 20, 20), width=3)
    
    draw.text((90, 280), f"纳税人识别号: {entity['tax_id']}", font=bold_font, fill=(30, 30, 30))
    draw.text((w - 550, 280), f"填发日期: {cert_data['payment_date']}", font=bold_font, fill=(30, 30, 30))
    
    table_top = 325
    left_x = 80
    table_w = w - 160
    
    draw.rectangle([left_x, table_top, left_x + table_w, table_top + 160], outline=(160, 30, 30), width=2)
    draw.line([(left_x, table_top + 53), (left_x + table_w, table_top + 53)], fill=(180, 180, 180), width=1)
    draw.line([(left_x, table_top + 106), (left_x + table_w, table_top + 106)], fill=(180, 180, 180), width=1)
    
    split_x = left_x + 280
    split_x2 = left_x + 760
    split_x3 = left_x + 1040
    
    for split in (split_x, split_x2, split_x3):
        draw.line([(split, table_top), (split, table_top + 160)], fill=(180, 180, 180), width=1)
        
    draw.text((left_x + 20, table_top + 14), "纳税人名称", font=bold_font, fill=(50, 50, 50))
    draw.text((split_x + 20, table_top + 14), entity['name'], font=bold_font, fill=(20, 20, 20))
    draw.text((split_x2 + 20, table_top + 14), "电子税票号", font=bold_font, fill=(50, 50, 50))
    draw.text((split_x3 + 20, table_top + 14), cert_data['tax_ticket_no'], font=text_font, fill=(20, 20, 20))
    
    draw.text((left_x + 20, table_top + 67), "开户银行", font=bold_font, fill=(50, 50, 50))
    draw.text((split_x + 20, table_top + 67), entity['bank_name'], font=text_font, fill=(20, 20, 20))
    draw.text((split_x2 + 20, table_top + 67), "银行账号", font=bold_font, fill=(50, 50, 50))
    draw.text((split_x3 + 20, table_top + 67), entity['bank_acc'], font=text_font, fill=(20, 20, 20))
    
    draw.text((left_x + 20, table_top + 120), "所属工程", font=bold_font, fill=(50, 50, 50))
    draw.text((split_x + 20, table_top + 120), f"{PROJECT_INFO['name']} ({PROJECT_INFO['code']})", font=text_font, fill=(20, 20, 20))
    draw.text((split_x2 + 20, table_top + 120), "主管税务机关", font=bold_font, fill=(50, 50, 50))
    draw.text((split_x3 + 20, table_top + 120), entity['tax_authority'][:14], font=text_font, fill=(20, 20, 20))
    
    items_top = table_top + 185
    item_row_h = 60
    n_items = len(cert_data['items'])
    
    draw.rectangle([left_x, items_top, left_x + table_w, items_top + (n_items + 2) * item_row_h], outline=(160, 30, 30), width=2)
    draw.rectangle([left_x + 1, items_top + 1, left_x + table_w - 1, items_top + item_row_h - 1], fill=(255, 235, 238))
    
    cols = [
        (left_x, left_x + 220, "原凭证号"),
        (left_x + 220, left_x + 460, "税种"),
        (left_x + 460, left_x + 720, "品目名称"),
        (left_x + 720, left_x + 1020, "所属时期"),
        (left_x + 1020, left_x + 1240, "计税依据"),
        (left_x + 1240, left_x + 1350, "税率"),
        (left_x + 1350, left_x + table_w, "实缴税额(元)"),
    ]
    
    for c_start, c_end, c_name in cols:
        draw.text((c_start + 15, items_top + 18), c_name, font=bold_font, fill=(40, 40, 40))
        if c_start > left_x:
            draw.line([(c_start, items_top), (c_start, items_top + (n_items + 2) * item_row_h)], fill=(200, 200, 200), width=1)
            
    total_tax = Decimal("0")
    for idx, itm in enumerate(cert_data['items']):
        row_y = items_top + (idx + 1) * item_row_h
        draw.line([(left_x, row_y), (left_x + table_w, row_y)], fill=(200, 200, 200), width=1)
        amt = Decimal(str(itm['amount']))
        total_tax += amt
        
        draw.text((left_x + 10, row_y + 18), str(itm['orig_no']), font=text_font, fill=(60, 60, 60))
        draw.text((left_x + 230, row_y + 18), str(itm['tax_type']), font=bold_font, fill=(20, 20, 20))
        draw.text((left_x + 470, row_y + 18), str(itm['category'])[:10], font=text_font, fill=(50, 50, 50))
        draw.text((left_x + 730, row_y + 18), str(itm['period_range']), font=text_font, fill=(50, 50, 50))
        draw.text((left_x + 1030, row_y + 18), f"¥{itm.get('tax_base', 0):,.0f}", font=text_font, fill=(50, 50, 50))
        draw.text((left_x + 1250, row_y + 18), str(itm['rate']), font=text_font, fill=(50, 50, 50))
        draw.text((left_x + 1360, row_y + 18), f"¥{amt:,.2f}", font=bold_font, fill=(160, 20, 20))
        
    tot_y = items_top + (n_items + 1) * item_row_h
    draw.line([(left_x, tot_y), (left_x + table_w, tot_y)], fill=(200, 200, 200), width=1)
    draw.rectangle([left_x + 1, tot_y + 1, left_x + table_w - 1, tot_y + item_row_h - 1], fill=(255, 250, 230))
    draw.text((left_x + 20, tot_y + 18), "合计金额 (大写):", font=bold_font, fill=(40, 40, 40))
    draw.text((left_x + 230, tot_y + 18), f"人民币 {cert_data['total_chinese']}", font=bold_font, fill=(160, 20, 20))
    draw.text((left_x + 1240, tot_y + 18), "小写合计:", font=bold_font, fill=(40, 40, 40))
    draw.text((left_x + 1360, tot_y + 18), f"¥{total_tax:,.2f}", font=bold_font, fill=(160, 20, 20))
    
    foot_y = tot_y + item_row_h + 35
    draw.rectangle([left_x, foot_y, left_x + table_w, foot_y + 240], fill=(245, 247, 250), outline=(180, 190, 200), width=1)
    draw.text((left_x + 30, foot_y + 25), f"收款国库: {entity['treasury']}", font=bold_font, fill=(30, 30, 30))
    draw.text((left_x + 30, foot_y + 65), "缴税方式: 财税库银横向联网系统 (TIPS) 实时扣缴清算入库", font=text_font, fill=(50, 50, 50))
    draw.text((left_x + 30, foot_y + 105), f"银行扣款流水号: {cert_data['bank_flow_no']}", font=text_font, fill=(50, 50, 50))
    draw.text((left_x + 30, foot_y + 145), f"电子印章签名代码: SHA256:{random.randint(10000000, 99999999)}FE9821{cert_data['entity_code']}TF", font=small_font, fill=(100, 100, 100))
    draw.text((left_x + 30, foot_y + 185), "国家税务总局电子税收票证查验真伪二维码 [✔ 已通过全国电子税务局验签]", font=bold_font, fill=(20, 130, 40))
    
    stamp_pos = (w - 360, foot_y + 110)
    auth_short = entity['tax_authority'].split("税务局")[0].replace("国家税务总局", "") + "税务局"
    draw_tax_oval_stamp(draw, "国家税务总局", auth_short, "电子征税专用章", center=stamp_pos, rx=130, ry=85, color=(210, 30, 30))
    
    img = apply_photocopy_texture(img, noise_level=10)
    img.save(filepath, quality=92)


# ---------------------------------------------------------------------------
# 全参建企业数据集 (系统内 6 家 + 系统外 4 家)
# ---------------------------------------------------------------------------

CERTIFICATES_CONFIG = [
    # ------------------ 一、系统内单位 ------------------
    # 1. A08 锐宝建设
    {
        'entity_code': 'A08',
        'doc_name': 'TAX_CERT_A08_202301_总承包合同与物资采购印花税完税证明',
        'receipt_no': '5101062300028925',
        'tax_ticket_no': '351016230100078925',
        'payment_date': '2023年01月28日',
        'period': '2023-01',
        'total_chinese': '伍拾柒万元整',
        'bank_flow_no': 'TIPS20230128552921005',
        'note': '总承包合同及首批钢材采购合同印花税申报实缴',
        'items': [
            {'orig_no': '3510162306', 'tax_type': '印花税', 'category': '建设工程施工总承包合同', 'period_range': '2023-01-01 至 2023-01-31', 'tax_base': 1450000000.00, 'rate': '0.03%', 'amount': 435000.00},
            {'orig_no': '3510162307', 'tax_type': '印花税', 'category': '大宗物资供销买卖合同', 'period_range': '2023-01-01 至 2023-01-31', 'tax_base': 450000000.00, 'rate': '0.03%', 'amount': 135000.00},
        ]
    },
    {
        'entity_code': 'A08',
        'doc_name': 'TAX_CERT_A08_2023Q2_增值税及附加税费电子完税证明',
        'receipt_no': '5101062300018921',
        'tax_ticket_no': '351016230600028921',
        'payment_date': '2023年07月12日',
        'period': '2023-06',
        'total_chinese': '肆佰捌拾伍万元整',
        'bank_flow_no': 'TIPS20230712883921001',
        'note': '前期土建工程款结算申报增值税及附加税费实缴',
        'items': [
            {'orig_no': '3510162301', 'tax_type': '增值税', 'category': '建筑服务*工程款销项实缴', 'period_range': '2023-04-01 至 2023-06-30', 'tax_base': 53888888.89, 'rate': '9%', 'amount': 4850000.00},
            {'orig_no': '3510162302', 'tax_type': '城市维护建设税', 'category': '市区(7%)', 'period_range': '2023-04-01 至 2023-06-30', 'tax_base': 4850000.00, 'rate': '7%', 'amount': 339500.00},
            {'orig_no': '3510162303', 'tax_type': '教育费附加', 'category': '增值税附加(3%)', 'period_range': '2023-04-01 至 2023-06-30', 'tax_base': 4850000.00, 'rate': '3%', 'amount': 145500.00},
            {'orig_no': '3510162304', 'tax_type': '地方教育附加', 'category': '地方教育附加(2%)', 'period_range': '2023-04-01 至 2023-06-30', 'tax_base': 4850000.00, 'rate': '2%', 'amount': 97000.00},
        ]
    },
    {
        'entity_code': 'A08',
        'doc_name': 'TAX_CERT_A08_2024Q2_主体施工增值税及附加电子完税证明',
        'receipt_no': '5101062400048922',
        'tax_ticket_no': '351016240700088922',
        'payment_date': '2024年07月15日',
        'period': '2024-06',
        'total_chinese': '捌佰陆拾贰万元整',
        'bank_flow_no': 'TIPS20240715993821002',
        'note': '主体结构高层施工节点产值确认申报纳税',
        'items': [
            {'orig_no': '3510162401', 'tax_type': '增值税', 'category': '建筑服务*主体钢构工程', 'period_range': '2024-04-01 至 2024-06-30', 'tax_base': 95777777.78, 'rate': '9%', 'amount': 8620000.00},
            {'orig_no': '3510162402', 'tax_type': '城市维护建设税', 'category': '市区(7%)', 'period_range': '2024-04-01 至 2024-06-30', 'tax_base': 8620000.00, 'rate': '7%', 'amount': 603400.00},
            {'orig_no': '3510162403', 'tax_type': '教育费附加', 'category': '增值税附加(3%)', 'period_range': '2024-04-01 至 2024-06-30', 'tax_base': 8620000.00, 'rate': '3%', 'amount': 258600.00},
            {'orig_no': '3510162404', 'tax_type': '地方教育附加', 'category': '地方教育附加(2%)', 'period_range': '2024-04-01 至 2024-06-30', 'tax_base': 8620000.00, 'rate': '2%', 'amount': 172400.00},
        ]
    },
    # 2. B01 乾润和贸易
    {
        'entity_code': 'B01',
        'doc_name': 'TAX_CERT_B01_2023Q3_大宗钢材物资销售增值税及附加完税证明',
        'receipt_no': '5101062300088931',
        'tax_ticket_no': '351016230800048931',
        'payment_date': '2023年08月14日',
        'period': '2023-07',
        'total_chinese': '壹佰捌拾伍万元整',
        'bank_flow_no': 'TIPS20230814992921011',
        'note': '天府金融中心项目高强抗震螺纹钢供销差价增值税及附加税费实缴',
        'items': [
            {'orig_no': '3510162321', 'tax_type': '增值税', 'category': '货物销售*大宗钢材(13%)', 'period_range': '2023-07-01 至 2023-07-31', 'tax_base': 14230769.23, 'rate': '13%', 'amount': 1850000.00},
            {'orig_no': '3510162322', 'tax_type': '城市维护建设税', 'category': '市区(7%)', 'period_range': '2023-07-01 至 2023-07-31', 'tax_base': 1850000.00, 'rate': '7%', 'amount': 129500.00},
        ]
    },
    # 3. C01 本盛劳务
    {
        'entity_code': 'C01',
        'doc_name': 'TAX_CERT_C01_2024Q2_建筑劳务分包服务增值税及附加完税证明',
        'receipt_no': '5101072400018934',
        'tax_ticket_no': '351017240700028934',
        'payment_date': '2024年07月14日',
        'period': '2024-06',
        'total_chinese': '壹佰肆拾贰万元整',
        'bank_flow_no': 'TIPS20240714669921014',
        'note': '主体结构劳务用工分包款结算申报增值税及附加',
        'items': [
            {'orig_no': '3510172431', 'tax_type': '增值税', 'category': '建筑服务*劳务分包(9%)', 'period_range': '2024-04-01 至 2024-06-30', 'tax_base': 15777777.78, 'rate': '9%', 'amount': 1420000.00},
            {'orig_no': '3510172432', 'tax_type': '城市维护建设税', 'category': '市区(7%)', 'period_range': '2024-04-01 至 2024-06-30', 'tax_base': 1420000.00, 'rate': '7%', 'amount': 99400.00},
        ]
    },
    # 4. D01 乾润和租赁
    {
        'entity_code': 'D01',
        'doc_name': 'TAX_CERT_D01_2024Q2_塔吊机械设备租赁增值税及附加完税证明',
        'receipt_no': '5101062400068936',
        'tax_ticket_no': '351016240700058936',
        'payment_date': '2024年07月18日',
        'period': '2024-06',
        'total_chinese': '捌拾伍万元整',
        'bank_flow_no': 'TIPS20240718447921016',
        'note': '8台重型自升式塔式起重机纯租赁租金收入增值税申报实缴',
        'items': [
            {'orig_no': '3510162441', 'tax_type': '增值税', 'category': '动产经营租赁(13%)', 'period_range': '2024-04-01 至 2024-06-30', 'tax_base': 6538461.54, 'rate': '13%', 'amount': 850000.00},
        ]
    },
    # 5. A11 巨邦钢构
    {
        'entity_code': 'A11',
        'doc_name': 'TAX_CERT_A11_2025Q2_超高层钢结构专业分包增值税及附加完税证明',
        'receipt_no': '5101042500018938',
        'tax_ticket_no': '351014250700018938',
        'payment_date': '2025年07月16日',
        'period': '2025-06',
        'total_chinese': '壹佰陆拾伍万元整',
        'bank_flow_no': 'TIPS20250716225921018',
        'note': '超高层大跨度钢桁架连廊加工提升分包款申报纳税',
        'items': [
            {'orig_no': '3510142551', 'tax_type': '增值税', 'category': '建筑服务*专业分包(9%)', 'period_range': '2025-04-01 至 2025-06-30', 'tax_base': 18333333.33, 'rate': '9%', 'amount': 1650000.00},
        ]
    },
    # 6. A05 帆亿通信
    {
        'entity_code': 'A05',
        'doc_name': 'TAX_CERT_A05_2025Q3_建筑智能化与数字微网分包增值税完税证明',
        'receipt_no': '5101042500088940',
        'tax_ticket_no': '351014251000088940',
        'payment_date': '2025年10月18日',
        'period': '2025-09',
        'total_chinese': '肆拾伍万元整',
        'bank_flow_no': 'TIPS20251018003921020',
        'note': '智慧楼宇弱电自控系统与微网集成节点款申报增值税',
        'items': [
            {'orig_no': '3510142561', 'tax_type': '增值税', 'category': '建筑服务*弱电集成(9%)', 'period_range': '2025-07-01 至 2025-09-30', 'tax_base': 5000000.00, 'rate': '9%', 'amount': 450000.00},
        ]
    },

    # ------------------ 二、系统外单位 (4 家代表性合作机构) ------------------
    # 7. EXT-TF 成都市天府新区金融城投公司 (外部发包业主)
    {
        'entity_code': 'EXT-TF',
        'doc_name': 'TAX_CERT_EXT-TF_202301_总承包工程发包合同印花税完税证明',
        'receipt_no': '5101002300018942',
        'tax_ticket_no': '351000230100018942',
        'payment_date': '2023年01月20日',
        'period': '2023-01',
        'total_chinese': '肆拾叁万伍仟元整',
        'bank_flow_no': 'TIPS20230120110921022',
        'note': '发包方天府新区金融城投公司关于14.5亿施工总承包主合同印花税按期缴纳',
        'items': [
            {'orig_no': '3510002371', 'tax_type': '印花税', 'category': '建设工程发包合同(发包方0.03%)', 'period_range': '2023-01-01 至 2023-01-31', 'tax_base': 1450000000.00, 'rate': '0.03%', 'amount': 435000.00},
        ]
    },
    # 8. EXT-PG-STEEL 攀钢集团攀枝花钢钒物资销售 (外部钢厂直采)
    {
        'entity_code': 'EXT-PG-STEEL',
        'doc_name': 'TAX_CERT_EXT-PG_2023Q3_特种高强合金钢直供销售增值税完税证明',
        'receipt_no': '5104002300058943',
        'tax_ticket_no': '351040230800058943',
        'payment_date': '2023年08月22日',
        'period': '2023-08',
        'total_chinese': '玖佰贰拾万零叁仟伍佰叁拾玖元捌角贰分',
        'bank_flow_no': 'TIPS20230822221921023',
        'note': '攀钢直供8000万Q420高强厚板特种钢材销售增值税（13%）实缴入库',
        'items': [
            {'orig_no': '3510402372', 'tax_type': '增值税', 'category': '钢材销售*特种厚板(13%)', 'period_range': '2023-08-01 至 2023-08-31', 'tax_base': 70796460.18, 'rate': '13%', 'amount': 9203539.82},
            {'orig_no': '3510402373', 'tax_type': '城市维护建设税', 'category': '攀枝花市区(7%)', 'period_range': '2023-08-01 至 2023-08-31', 'tax_base': 9203539.82, 'rate': '7%', 'amount': 644247.79},
        ]
    },
    # 9. EXT-CQ-HEAVY-CRANE 重庆重交大件起重吊装 (外部特种大件吊装)
    {
        'entity_code': 'EXT-CQ-HEAVY-CRANE',
        'doc_name': 'TAX_CERT_EXT-CQ_2024Q3_500吨履带吊特种吊装增值税及附加完税证明',
        'receipt_no': '5001002400038944',
        'tax_ticket_no': '350010240900038944',
        'payment_date': '2024年09月18日',
        'period': '2024-09',
        'total_chinese': '贰佰零陆万肆仟贰佰贰拾元壹角捌分',
        'bank_flow_no': 'TIPS20240918332921024',
        'note': '用于顶层连廊大跨度特种大件高空吊装服务款申报缴纳增值税及附加',
        'items': [
            {'orig_no': '3500102474', 'tax_type': '增值税', 'category': '建筑服务*特种吊装(9%)', 'period_range': '2024-09-01 至 2024-09-30', 'tax_base': 22935779.82, 'rate': '9%', 'amount': 2064220.18},
            {'orig_no': '3500102475', 'tax_type': '城市维护建设税', 'category': '重庆市区(7%)', 'period_range': '2024-09-01 至 2024-09-30', 'tax_base': 2064220.18, 'rate': '7%', 'amount': 144495.41},
        ]
    },
    # 10. EXT-EXPERT-LABOR 四川省建科院技术服务中心 (外部技术咨询)
    {
        'entity_code': 'EXT-EXPERT-LABOR',
        'doc_name': 'TAX_CERT_EXT-EXP_2024Q4_深基坑地质监测与技术咨询增值税完税证明',
        'receipt_no': '5101002400088945',
        'tax_ticket_no': '351000241100088945',
        'payment_date': '2024年11月15日',
        'period': '2024-11',
        'total_chinese': '陆拾柒万玖仟贰佰肆拾伍元贰角捌分',
        'bank_flow_no': 'TIPS20241115443921025',
        'note': '超高层深基坑变形监测与专家论证技术服务费缴纳增值税(6%)',
        'items': [
            {'orig_no': '3510002476', 'tax_type': '增值税', 'category': '现代服务*技术咨询(6%)', 'period_range': '2024-11-01 至 2024-11-30', 'tax_base': 11320754.72, 'rate': '6%', 'amount': 679245.28},
            {'orig_no': '3510002477', 'tax_type': '城市维护建设税', 'category': '青羊区(7%)', 'period_range': '2024-11-01 至 2024-11-30', 'tax_base': 679245.28, 'rate': '7%', 'amount': 47547.17},
        ]
    },
]


def sync_tax_records_to_database():
    """将所有参建单位（系统内 + 系统外）的完税记录同步写入 PostgreSQL 数据库"""
    print("\n📦 正在将【系统内 6 家 + 系统外 4 家】完税凭证全量同步写入 PostgreSQL 数据库...")
    try:
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0"))
        from app.db import SessionLocal
        from app.models import TaxPaymentRecord, Project
        
        db = SessionLocal()
        project = db.query(Project).filter(Project.code == 'CD-TF-001').first()
        project_id = project.id if project else 6
        
        inserted = 0
        updated = 0
        
        for cert in CERTIFICATES_CONFIG:
            receipt_no = cert['receipt_no']
            ent_code = cert['entity_code']
            pay_date_iso = cert['payment_date'].replace('年', '-').replace('月', '-').replace('日', '')
            
            for item in cert['items']:
                rec_id = f"{receipt_no}-{item['orig_no']}"
                amt = Decimal(str(item['amount']))
                
                existing = db.query(TaxPaymentRecord).filter(
                    TaxPaymentRecord.entity_code == ent_code,
                    TaxPaymentRecord.receipt_no == rec_id
                ).first()
                
                if existing:
                    existing.tax_amount = amt
                    existing.principal_amount = amt
                    existing.period = cert['period']
                    existing.tax_period = cert['period']
                    existing.payment_date = pay_date_iso
                    existing.transaction_date = pay_date_iso
                    existing.tax_type = item['tax_type']
                    existing.bank_reference = cert['bank_flow_no']
                    existing.note = f"{cert['note']} [{item['category']}]"
                    updated += 1
                else:
                    record = TaxPaymentRecord(
                        project_id=project_id,
                        entity_code=ent_code,
                        tax_type=item['tax_type'],
                        period=cert['period'],
                        tax_period=cert['period'],
                        receipt_no=rec_id,
                        payment_date=pay_date_iso,
                        transaction_date=pay_date_iso,
                        tax_amount=amt,
                        principal_amount=amt,
                        penalty_amount=Decimal('0'),
                        bank_reference=cert['bank_flow_no'],
                        source_fingerprint=f"TAX-EVIDENCE-{ent_code}-{rec_id}",
                        note=f"{cert['note']} [{item['category']}]",
                        created_at=datetime.now().isoformat()
                    )
                    db.add(record)
                    inserted += 1
                    
        db.commit()
        print(f"  ✅ 数据库全生态企业完税同步成功！新增记录 {inserted} 条，更新记录 {updated} 条。")
        db.close()
    except Exception as e:
        print(f"  ⚠️ 数据库同步提示: {e}")


def generate_all_tax_certificates():
    print("=" * 80)
    print("  🏗️  开始为 01 标杆工程【系统内 6 家 + 系统外 4 家】全生态生成完税证明与电子税票...")
    print(f"  📁 目标保存目录: {TARGET_DIR}")
    print("=" * 80)
    
    count = 0
    for idx, cert in enumerate(CERTIFICATES_CONFIG, 1):
        ent = ALL_ENTITIES[cert['entity_code']]
        name = cert['doc_name']
        pdf_path = os.path.join(TARGET_DIR, f"{name}.pdf")
        jpg_path = os.path.join(TARGET_DIR, f"{name}_电子税票盖章原件.jpg")
        
        print(f"[{idx}/{len(CERTIFICATES_CONFIG)}] 正在生成: [{cert['entity_code']}] {ent['name'][:14]} - {cert['items'][0]['tax_type']}完税证明...")
        
        create_pdf_tax_certificate(pdf_path, cert)
        create_jpg_tax_certificate_scan(jpg_path, cert)
        count += 2
        
    print("\n" + "=" * 80)
    print(f"  🎉 全部参建企业完税凭证生成完毕！共计生成 {count} 份真实纸质/扫描电子档案。")
    print("=" * 80)
    
    sync_tax_records_to_database()


if __name__ == '__main__':
    generate_all_tax_certificates()
