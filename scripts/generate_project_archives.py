# -*- coding: utf-8 -*-
"""
=============================================================================
成都建工 V2.0 · 全套工程项目存档资料与四流证据链生成器 (PDF & JPG 仿复印件/照片)
=============================================================================
全面保证每个项目均具备：
  1. 施工、劳务、材料、机械租赁全业务线条
  2. 系统内单位 (A/B/C/D 类法人) 与 系统外单位 (外部业主、外部材料商、外部特种分包、外部租赁)
  3. 已付款 (银行电子回单) 与 未付款/挂账中 (应付审批单)
  4. 已开票完税 (增值税专票、跨区预缴完税证明) 与 待开票/待申报 (结算挂账)
  5. 现场物流与履约闭环 (电子地磅小票、材料验收入库单、隐蔽工程验收单)
  6. 现场实景照片 (带今日水印相机工程实名水印) 与 盖章复印件影印本 (JPG)
=============================================================================
"""
import os
import sys
import math
import random
from datetime import datetime, date, timedelta
from decimal import Decimal
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

import reportlab
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether, PageBreak
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

BASE_OUT_DIR = "/Users/yvoche/AI开发/073_成都建工/V2.0/项目存档资料"
os.makedirs(BASE_OUT_DIR, exist_ok=True)

# 实体全称对照表 (系统内 26 家 + 系统外代表性合作单位)
ENTITIES = {
    # A 类 施工总包/专业分包
    'A01': {'name': '中镌（湖北）建筑有限公司', 'tax_id': '91420105MA49M5UX1F', 'bank': '中国建设银行武汉江岸支行', 'acc': '42050123456789012345'},
    'A02': {'name': '四川中恒腾鸣建筑工程有限公司', 'tax_id': '91510107MA6C8XTY7B', 'bank': '中国工商银行成都武侯支行', 'acc': '51020198765432109876'},
    'A03': {'name': '四川屹明汇建设工程有限公司', 'tax_id': '91510104MA6CAD6R9K', 'bank': '招商银行成都锦江支行', 'acc': '51050111223344556677'},
    'A04': {'name': '四川屹明汇建设工程有限公司重庆分公司', 'tax_id': '91500230MAD7T9Y43P', 'bank': '中国农业银行重庆江北支行', 'acc': '50010188776655443322'},
    'A05': {'name': '四川帆亿通信科技有限公司', 'tax_id': '91510104MA6CYN4T2A', 'bank': '交通银行成都高新支行', 'acc': '51030133445566778899'},
    'A06': {'name': '四川裕合荣建筑工程有限公司', 'tax_id': '91510105MA689P8W5E', 'bank': '中信银行成都青羊支行', 'acc': '51070199887766554433'},
    'A07': {'name': '四川铁安电力工程有限公司', 'tax_id': '91510107064438179R', 'bank': '中国银行成都金牛支行', 'acc': '51010155667788990011'},
    'A08': {'name': '四川锐宝建设工程有限公司', 'tax_id': '91510106MA61UEJ48K', 'bank': '成都银行科技支行', 'acc': '51090177889900112233'},
    'A09': {'name': '四川顺程源建筑工程有限公司', 'tax_id': '91510105MABY7X5T3N', 'bank': '中国建设银行成都天府支行', 'acc': '51050144556677889900'},
    'A10': {'name': '四川鼎新源建筑工程有限公司', 'tax_id': '91510105MA6CBP5T9M', 'bank': '中国工商银行广元利州支行', 'acc': '51080166778899001122'},
    'A11': {'name': '成都巨邦建设工程有限公司', 'tax_id': '91510104MA6CM8P59N', 'bank': '中国农业银行成都锦江支行', 'acc': '51040188990011223344'},
    # B 类 物资贸易
    'B01': {'name': '四川乾润和贸易有限公司', 'tax_id': '91510106MA6D7H4N9A', 'bank': '招商银行成都金牛支行', 'acc': '51060122334455667788'},
    'B02': {'name': '四川兴誉诚商贸有限公司', 'tax_id': '91510185MA6CEK5T8W', 'bank': '中国银行成都简阳支行', 'acc': '51010199001122334455'},
    'B03': {'name': '四川坤珀贸易有限公司', 'tax_id': '91510106MAD04NX82T', 'bank': '中国工商银行成都金牛支行', 'acc': '51020133445566778811'},
    'B04': {'name': '四川矗佳商贸有限公司', 'tax_id': '91511526MA67UN7C2G', 'bank': '中国建设银行宜宾江安支行', 'acc': '51150155667788990022'},
    'B05': {'name': '广元玖硕商贸有限公司', 'tax_id': '91510802MA67Q84X1T', 'bank': '中国农业银行广元分行', 'acc': '51080177889900112244'},
    'B06': {'name': '广州采云广告有限公司', 'tax_id': '91440101MA5CQ8N94Y', 'bank': '平安银行广州天河支行', 'acc': '44010188990011223355'},
    'B07': {'name': '成都恒创嘉泰贸易有限公司', 'tax_id': '91510106MA6D2X5L8T', 'bank': '中信银行成都分行', 'acc': '51070111223344556688'},
    'B08': {'name': '成都鑫晨鼎升商贸有限公司', 'tax_id': '91510107MA6CK9P43X', 'bank': '交通银行成都武侯支行', 'acc': '51030144556677889922'},
    'B09': {'name': '格尔木青泽贸易有限公司', 'tax_id': '91632801MA758N3E2K', 'bank': '中国建设银行格尔木分行', 'acc': '63280166778899001133'},
    'B10': {'name': '重庆朗德乾润商贸有限公司', 'tax_id': '91500109MABY9K6W4L', 'bank': '招商银行重庆九龙坡支行', 'acc': '50010122334455667799'},
    # C 类 劳务分包
    'C01': {'name': '四川本盛劳务有限公司', 'tax_id': '91510107350645934C', 'bank': '成都农商银行成华支行', 'acc': '51010833445566778800'},
    'C02': {'name': '四川灏琅建筑劳务有限公司', 'tax_id': '91510603MADH76KY3E', 'bank': '四川天府银行德阳支行', 'acc': '51060155667788990033'},
    # D 类 机械设备租赁
    'D01': {'name': '四川乾润和机械设备租赁有限公司', 'tax_id': '91510106MA6D2K9L3E', 'bank': '中国民生银行成都金牛支行', 'acc': '51010188990011223366'},
    'D02': {'name': '四川乾诺机械租赁有限公司', 'tax_id': '91510100MA7G8T2E5H', 'bank': '中国光大银行成都分行', 'acc': '51010133445566778822'},
    'D03': {'name': '四川惠润农业设备有限公司', 'tax_id': '91510100MA6CP8W61L', 'bank': '四川银行成都高新支行', 'acc': '51010177889900112255'},
    
    # 系统外发包业主
    'EXT-TF': {'name': '成都市天府新区金融城投公司', 'tax_id': '91510100MA61AAAA11', 'bank': '国家开发银行四川省分行', 'acc': '51000012345678901111'},
    'EXT-CY': {'name': '成渝高速公路开发投资集团', 'tax_id': '91510100MA61BBBB22', 'bank': '中国进出口银行成都分行', 'acc': '51000098765432102222'},
    'EXT-GY': {'name': '广元市利州区水务局城投平台', 'tax_id': '91510800MA61CCCC33', 'bank': '广元市农村商业银行', 'acc': '51080011223344553333'},
    'EXT-GX': {'name': '国家电网四川省电力公司成都供电公司', 'tax_id': '91510100064438179G', 'bank': '中国工商银行成都市分行营业部', 'acc': '51010155667788994444'},
    'EXT-GEM': {'name': '青海盐湖工业股份有限公司', 'tax_id': '91630000226590001X', 'bank': '中国银行青海省分行', 'acc': '63000177889900115555'},
    'EXT-YB': {'name': '宜宾市三江新区开发建设投资集团', 'tax_id': '91511500MA61DDDD66', 'bank': '宜宾市商业银行临港支行', 'acc': '51150199001122336666'},
    
    # 系统外第三方供货/分包/租赁/劳务合作单位
    'EXT-PG-STEEL': {'name': '攀钢集团攀枝花钢钒物资销售有限公司', 'tax_id': '91510400MA61EEEE77', 'bank': '中国建设银行攀枝花分行', 'acc': '51040188990011227777'},
    'EXT-XN-CONCRETE': {'name': '西南商品混凝土直供配送服务有限公司', 'tax_id': '91510100MA61FFFF88', 'bank': '成都农商银行高新支行', 'acc': '51010822334455668888'},
    'EXT-CQ-HEAVY-CRANE': {'name': '重庆重交大件起重吊装工程有限公司', 'tax_id': '91500100MA61GGGG99', 'bank': '重庆银行江北支行', 'acc': '50010144556677889999'},
    'EXT-GY-FOREST': {'name': '广元利州生态园林绿化苗木专业合作社', 'tax_id': '93510800MA61HHHH00', 'bank': '广元市农村信用合作联社', 'acc': '51080133445566770000'},
    'EXT-ABB-ELECTRIC': {'name': '国网南瑞继保电气成套设备销售有限公司', 'tax_id': '91320100MA61IIII11', 'bank': '中国银行南京江宁支行', 'acc': '32010155667788991111'},
    'EXT-QH-RAILWAY': {'name': '中铁青海特种铁路工程装备工程部', 'tax_id': '91630100MA61JJJJ22', 'bank': '中国建设银行西宁城东支行', 'acc': '63010177889900112222'},
    'EXT-EXPERT-LABOR': {'name': '四川省建筑科学研究院特种技术服务中心', 'tax_id': '91510100MA61KKKK33', 'bank': '中国工商银行成都一环路支行', 'acc': '51010199001122333333'},
}

def get_entity_name(code):
    return ENTITIES.get(code, {}).get('name', f"外部协作单位({code})")

# ---------------------------------------------------------------------------
# 图像与印章生成工具 (PIL)
# ---------------------------------------------------------------------------

def draw_circular_stamp(draw, text_circle, text_star_sub="专用章", center=(150, 150), radius=90, color=(220, 30, 30)):
    cx, cy = center
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], outline=color, width=3)
    star_r = radius * 0.32
    points = []
    for i in range(5):
        angle = i * 4 * math.pi / 5 - math.pi / 2
        points.append((cx + star_r * math.cos(angle), cy + star_r * math.sin(angle)))
    draw.polygon(points, fill=color)
    
    sub_font = ImageFont.truetype(FONT_PATH, int(radius * 0.22))
    sub_w = draw.textlength(text_star_sub, font=sub_font)
    draw.text((cx - sub_w / 2, cy + radius * 0.38), text_star_sub, font=sub_font, fill=color)

    n = len(text_circle)
    if n > 0:
        angle_span = math.pi * 1.3
        start_angle = -math.pi / 2 - angle_span / 2
        char_step = angle_span / (n - 1) if n > 1 else 0
        char_font = ImageFont.truetype(FONT_PATH, int(radius * 0.19))
        for i, ch in enumerate(text_circle):
            ang = start_angle + i * char_step
            tx = cx + (radius * 0.72) * math.cos(ang)
            ty = cy + (radius * 0.72) * math.sin(ang)
            draw.text((tx - 8, ty - 8), ch, font=char_font, fill=color)


def apply_scanner_photocopy_texture(img, is_color=True, noise_level=12, contrast_boost=1.15):
    w, h = img.size
    if not is_color:
        img = img.convert('L').convert('RGB')
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(contrast_boost)
    pixels = img.load()
    for _ in range(int(w * h * (noise_level / 1000.0))):
        rx = random.randint(0, w - 1)
        ry = random.randint(0, h - 1)
        gray = random.randint(30, 200)
        pixels[rx, ry] = (gray, gray, gray)
    return img


def create_simulated_scan_jpg(filepath, title, key_rows, stamp_entity="四川锐宝建设工程有限公司", stamp_type="合同专用章", doc_no="", extra_notes=None):
    w, h = 1600, 2260
    bg_color = (252, 252, 250)
    img = Image.new('RGB', (w, h), color=bg_color)
    draw = ImageDraw.Draw(img)
    
    title_font = ImageFont.truetype(FONT_PATH, 44)
    text_font = ImageFont.truetype(FONT_PATH, 26)
    bold_font = ImageFont.truetype(FONT_PATH, 28)
    small_font = ImageFont.truetype(FONT_PATH, 20)
    
    draw.text((80, 70), "成都建工集团内部档案管理系统 · 原始凭证扫描存档件", font=small_font, fill=(120, 120, 120))
    if doc_no:
        draw.text((w - 550, 70), f"档案编号: {doc_no}", font=small_font, fill=(120, 120, 120))
    draw.line([(80, 105), (w - 80, 105)], fill=(180, 180, 180), width=2)
    
    tw = draw.textlength(title, font=title_font)
    draw.text(((w - tw) / 2, 160), title, font=title_font, fill=(20, 20, 20))
    draw.line([((w - tw) / 2 - 40, 230), ((w + tw) / 2 + 40, 230)], fill=(40, 40, 40), width=3)
    
    start_y = 280
    row_height = 65
    left_x = 100
    table_w = w - 200
    
    n_rows = len(key_rows)
    draw.rectangle([left_x, start_y, left_x + table_w, start_y + n_rows * row_height], outline=(60, 60, 60), width=2)
    
    for i, (k, v) in enumerate(key_rows):
        cur_y = start_y + i * row_height
        draw.line([(left_x, cur_y), (left_x + table_w, cur_y)], fill=(120, 120, 120), width=1)
        split_x = left_x + 360
        draw.line([(split_x, cur_y), (split_x, cur_y + row_height)], fill=(120, 120, 120), width=1)
        draw.rectangle([left_x + 1, cur_y + 1, split_x - 1, cur_y + row_height - 1], fill=(240, 242, 245))
        draw.text((left_x + 25, cur_y + 18), str(k), font=bold_font, fill=(40, 40, 40))
        draw.text((split_x + 25, cur_y + 18), str(v), font=text_font, fill=(20, 20, 20))

    note_y = start_y + n_rows * row_height + 50
    if extra_notes:
        draw.text((left_x, note_y), "【经办与履约审查签注】:", font=bold_font, fill=(50, 50, 50))
        for idx, line in enumerate(extra_notes):
            draw.text((left_x + 20, note_y + 40 + idx * 36), f"• {line}", font=text_font, fill=(60, 60, 60))
            
    stamp_y = h - 420
    draw.text((left_x + 50, stamp_y), "经办人签字:  陈建国 (已核)", font=bold_font, fill=(40, 40, 40))
    draw.text((left_x + 50, stamp_y + 50), "项目财务主管: 王晓敏 (已审)", font=bold_font, fill=(40, 40, 40))
    draw.text((left_x + 50, stamp_y + 100), f"归档日期:     {date.today().strftime('%Y年%m月%d日')}", font=text_font, fill=(80, 80, 80))
    
    stamp_center = (w - 380, stamp_y + 80)
    draw_circular_stamp(draw, stamp_entity, stamp_type, center=stamp_center, radius=130, color=(210, 40, 40))
    draw.text((w - 500, h - 90), "★ 电子档案系统防篡改真实性核验通过 ★", font=small_font, fill=(150, 150, 150))
    
    img = apply_scanner_photocopy_texture(img, is_color=True, noise_level=10)
    img.save(filepath, quality=90)


def create_simulated_site_photo_jpg(filepath, project_name, location_title, detail_text, time_str=None):
    w, h = 1920, 1080
    img = Image.new('RGB', (w, h), color=(55, 65, 75))
    draw = ImageDraw.Draw(img)
    
    for i in range(15):
        y_pos = 100 + i * 60
        draw.line([(0, y_pos), (w, y_pos + 120)], fill=(70, 80, 95), width=2)
        draw.line([(i * 140, 0), (i * 140 + 300, h)], fill=(65, 75, 90), width=2)
    
    draw.rectangle([200, 300, 700, 850], fill=(45, 55, 65), outline=(90, 105, 125), width=4)
    draw.rectangle([800, 250, 1500, 900], fill=(40, 50, 60), outline=(90, 105, 125), width=4)
    
    board_font = ImageFont.truetype(FONT_PATH, 32)
    draw.rectangle([300, 400, 1300, 680], fill=(245, 245, 240), outline=(200, 40, 40), width=5)
    draw.text((340, 430), f"工程项目: {project_name}", font=board_font, fill=(30, 30, 30))
    draw.text((340, 490), f"施工部位: {location_title}", font=board_font, fill=(30, 30, 30))
    draw.text((340, 550), f"质检状态: 隐蔽工程联合验收合格 / 物资过磅清点完毕", font=board_font, fill=(10, 140, 40))
    draw.text((340, 610), f"旁站监理: 四川科诚工程监理咨询有限公司 (已确认)", font=board_font, fill=(80, 80, 80))

    if not time_str:
        time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    wm_font_large = ImageFont.truetype(FONT_PATH, 42)
    wm_font_mid = ImageFont.truetype(FONT_PATH, 24)
    wm_font_small = ImageFont.truetype(FONT_PATH, 20)
    
    overlay = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    ol_draw = ImageDraw.Draw(overlay)
    ol_draw.rectangle([50, h - 300, 850, h - 40], fill=(0, 0, 0, 180))
    img.paste(Image.alpha_composite(img.convert('RGBA'), overlay).convert('RGB'))
    
    draw = ImageDraw.Draw(img)
    draw.text((80, h - 280), "📷 今日水印相机 · 工程工程实名认证", font=wm_font_mid, fill=(255, 200, 50))
    draw.text((80, h - 235), f"时间: {time_str}", font=wm_font_large, fill=(255, 255, 255))
    draw.text((80, h - 175), f"项目: {project_name}", font=wm_font_mid, fill=(240, 240, 240))
    draw.text((80, h - 135), f"点位: {location_title}", font=wm_font_mid, fill=(240, 240, 240))
    draw.text((80, h - 95), f"备注: {detail_text}", font=wm_font_small, fill=(200, 200, 200))
    draw.text((80, h - 68), "防伪验证码: SCDJ-2026-REAL-EVIDENCE-PASS", font=wm_font_small, fill=(150, 220, 150))
    
    img = apply_scanner_photocopy_texture(img, is_color=True, noise_level=5)
    img.save(filepath, quality=92)


# ---------------------------------------------------------------------------
# PDF 文档生成辅助类
# ---------------------------------------------------------------------------

class NumberedCanvas(canvas.Canvas):
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
            self.draw_header_footer(num_pages)
            super().showPage()
        super().save()

    def draw_header_footer(self, page_count):
        self.saveState()
        self.setFont(FONT_NAME, 8)
        self.setFillColor(colors.HexColor('#666666'))
        self.drawString(54, 800, "成都建工集团内部工程全流程档案管理系统 · 财税与履约证据链存档")
        self.setStrokeColor(colors.HexColor('#CCCCCC'))
        self.setLineWidth(0.5)
        self.line(54, 792, 540, 792)
        self.line(54, 45, 540, 45)
        page_str = f"第 {self._pageNumber} 页 / 共 {page_count} 页"
        self.drawRightString(540, 32, page_str)
        self.drawString(54, 32, "密级：集团内部商密 · 凭证链真实性校验通过")
        self.restoreState()


def get_doc_styles():
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName=FONT_NAME,
        fontSize=18,
        leading=24,
        alignment=TA_CENTER,
        textColor=colors.HexColor('#1A1A1A'),
        spaceAfter=15,
        fontStyle='bold'
    )
    h2_style = ParagraphStyle(
        'DocH2',
        parent=styles['Normal'],
        fontName=FONT_NAME,
        fontSize=13,
        leading=18,
        textColor=colors.HexColor('#B71C1C'),
        spaceBefore=12,
        spaceAfter=6,
        fontStyle='bold'
    )
    body_style = ParagraphStyle(
        'DocBody',
        parent=styles['Normal'],
        fontName=FONT_NAME,
        fontSize=9.5,
        leading=14,
        textColor=colors.HexColor('#212121'),
        alignment=TA_JUSTIFY,
        firstLineIndent=20,
        spaceAfter=6
    )
    table_cell = ParagraphStyle(
        'TableCell',
        parent=styles['Normal'],
        fontName=FONT_NAME,
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor('#212121')
    )
    table_cell_bold = ParagraphStyle(
        'TableCellBold',
        parent=table_cell,
        fontStyle='bold',
        textColor=colors.HexColor('#111111')
    )
    return {
        'title': title_style,
        'h2': h2_style,
        'body': body_style,
        'cell': table_cell,
        'cell_bold': table_cell_bold
    }


def create_pdf_contract(filepath, title, party_a_code, party_b_code, project_name, contract_no, amount_yuan, category, key_terms):
    doc = SimpleDocTemplate(
        filepath,
        pagesize=A4,
        leftMargin=54, rightMargin=54,
        topMargin=54, bottomMargin=54
    )
    styles = get_doc_styles()
    story = []
    
    party_a = get_entity_name(party_a_code)
    party_b = get_entity_name(party_b_code)
    
    story.append(Paragraph(title, styles['title']))
    story.append(Paragraph(f"<b>合同编号：</b>{contract_no} &nbsp;&nbsp;&nbsp;&nbsp; <b>签约地点：</b>四川省成都市", styles['cell_bold']))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#B71C1C'), spaceBefore=6, spaceAfter=12))
    
    info_table = [
        [Paragraph("<b>发包方 (甲方)：</b>", styles['cell_bold']), Paragraph(party_a, styles['cell'])],
        [Paragraph("<b>统一社会信用代码：</b>", styles['cell_bold']), Paragraph(ENTITIES.get(party_a_code, {}).get('tax_id', '91510100MAXXXXXX01'), styles['cell'])],
        [Paragraph("<b>承包方 (乙方)：</b>", styles['cell_bold']), Paragraph(party_b, styles['cell'])],
        [Paragraph("<b>统一社会信用代码：</b>", styles['cell_bold']), Paragraph(ENTITIES.get(party_b_code, {}).get('tax_id', '91510100MAXXXXXX02'), styles['cell'])],
        [Paragraph("<b>工程项目名称：</b>", styles['cell_bold']), Paragraph(project_name, styles['cell'])],
        [Paragraph("<b>合同签约含税总价：</b>", styles['cell_bold']), Paragraph(f"<b>¥ {amount_yuan:,.2f} 元</b> (大写：人民币 {amount_yuan/10000:,.2f} 万元整)", styles['cell_bold'])],
        [Paragraph("<b>业务分类与税率：</b>", styles['cell_bold']), Paragraph(f"{category} (增值税法定适用税率开票)", styles['cell'])],
    ]
    t = Table(info_table, colWidths=[120, 366])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#F5F5F5')),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#CCCCCC')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E0E0E0')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(t)
    story.append(Spacer(1, 10))
    
    story.append(Paragraph("第一条 工程范围与承包形式", styles['h2']))
    story.append(Paragraph(f"甲乙双方根据《中华人民共和国民法典》、《建筑法》等法律法规，就【{project_name}】项目的【{category}】工程承包事宜达成一致。乙方严格按照国家规范、设计图纸及甲方的技术交底要求组织履约施工或供货。", styles['body']))
    
    story.append(Paragraph("第二条 合同价款与结算方式", styles['h2']))
    story.append(Paragraph(f"本合同采用固定综合单价与实际工程量结合结算。合同签约暂定价款为人民币 <b>{amount_yuan:,.2f}</b> 元。工程款按月度核定进度款的 80% 支付，完工验收并提供完整合规增值税专用发票后支付至 97%，留存 3% 工程质量保修金。", styles['body']))
    
    story.append(Paragraph("第三条 税务合规与发票交付特别约定 (四流一致)", styles['h2']))
    story.append(Paragraph("1. 乙方承诺向甲方开具合法有效的增值税专用发票，发票开具主体必须与本合同乙方、银行结算账户收款方及现场实际履约方完全保持一致（严格执行‘合同流、发票流、资金流、货物流/劳务流’四流一致原则）。", styles['body']))
    story.append(Paragraph("2. 乙方如涉及大宗物资采购或设备进场，必须同步提供真实完整的物流电子地磅单、出厂合格证、入库验收签收单作为支付前置要件；若缺少有效物流凭据链，甲方有权暂停相应款项支付直至证据补齐。", styles['body']))
    
    story.append(Paragraph("第四条 关键履约与技术条款", styles['h2']))
    for term in key_terms:
        story.append(Paragraph(f"• {term}", styles['body']))
        
    story.append(Spacer(1, 15))
    story.append(Paragraph("<b>签约双方盖章与法人签字（原件存档）：</b>", styles['cell_bold']))
    sign_table = [
        [Paragraph(f"<b>发包人 (甲方)：</b>{party_a}<br/>法定代表人 (或委托代理人)：陈建国<br/>开户行：{ENTITIES.get(party_a_code, {}).get('bank', '建设银行')}<br/>账号：{ENTITIES.get(party_a_code, {}).get('acc', '5100000000000001')}<br/>日期：2026年01月15日", styles['cell']),
         Paragraph(f"<b>承包人 (乙方)：</b>{party_b}<br/>法定代表人 (或委托代理人)：李德海<br/>开户行：{ENTITIES.get(party_b_code, {}).get('bank', '工商银行')}<br/>账号：{ENTITIES.get(party_b_code, {}).get('acc', '5100000000000002')}<br/>日期：2026年01月15日", styles['cell'])]
    ]
    st = Table(sign_table, colWidths=[243, 243])
    st.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#999999')),
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#FAFAFA')),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(st)
    
    doc.build(story, canvasmaker=NumberedCanvas)


def create_pdf_tax_invoice_sheet(filepath, invoice_no, seller_code, buyer_code, project_name, amount_no_tax, tax_amt, tax_rate, items_list, is_paid=True, is_tax_settled=True):
    doc = SimpleDocTemplate(filepath, pagesize=A4, leftMargin=54, rightMargin=54, topMargin=54, bottomMargin=54)
    styles = get_doc_styles()
    story = []
    
    seller = get_entity_name(seller_code)
    buyer = get_entity_name(buyer_code)
    total_val = amount_no_tax + tax_amt
    
    story.append(Paragraph(f"四川增值税专用发票结算与完税明细表", styles['title']))
    story.append(Paragraph(f"<b>发票号码：</b>{invoice_no} &nbsp;&nbsp;&nbsp;&nbsp; <b>开票日期：</b>2026-03-20 &nbsp;&nbsp;&nbsp;&nbsp; <b>校验码：</b>98234 10293 84728 19284", styles['cell_bold']))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#1565C0'), spaceBefore=4, spaceAfter=10))
    
    head_table = [
        [Paragraph("<b>购买方名称：</b>", styles['cell_bold']), Paragraph(buyer, styles['cell']), Paragraph("<b>销售方名称：</b>", styles['cell_bold']), Paragraph(seller, styles['cell'])],
        [Paragraph("<b>纳税人识别号：</b>", styles['cell_bold']), Paragraph(ENTITIES.get(buyer_code, {}).get('tax_id', '91510100MAXXXXXX'), styles['cell']), Paragraph("<b>纳税人识别号：</b>", styles['cell_bold']), Paragraph(ENTITIES.get(seller_code, {}).get('tax_id', '91510100MAYYYYYY'), styles['cell'])],
        [Paragraph("<b>开户行及账号：</b>", styles['cell_bold']), Paragraph(ENTITIES.get(buyer_code, {}).get('bank', '银行') + " " + ENTITIES.get(buyer_code, {}).get('acc', '***')[:10] + "...", styles['cell']),
         Paragraph("<b>开户行及账号：</b>", styles['cell_bold']), Paragraph(ENTITIES.get(seller_code, {}).get('bank', '银行') + " " + ENTITIES.get(seller_code, {}).get('acc', '***')[:10] + "...", styles['cell'])],
    ]
    ht = Table(head_table, colWidths=[80, 163, 80, 163])
    ht.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#BBDEFB')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E3F2FD')),
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#F1F8E9')),
        ('BACKGROUND', (2,0), (2,-1), colors.HexColor('#E1F5FE')),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(ht)
    story.append(Spacer(1, 8))
    
    item_rows = [[Paragraph("<b>货物或应税劳务、服务名称</b>", styles['cell_bold']),
                  Paragraph("<b>规格型号/单位</b>", styles['cell_bold']),
                  Paragraph("<b>数量</b>", styles['cell_bold']),
                  Paragraph("<b>单价(不含税)</b>", styles['cell_bold']),
                  Paragraph("<b>金额(元)</b>", styles['cell_bold']),
                  Paragraph("<b>税率</b>", styles['cell_bold']),
                  Paragraph("<b>税额(元)</b>", styles['cell_bold'])]]
    for itm in items_list:
        item_rows.append([
            Paragraph(itm[0], styles['cell']),
            Paragraph(itm[1], styles['cell']),
            Paragraph(str(itm[2]), styles['cell']),
            Paragraph(f"¥ {itm[3]:,.2f}", styles['cell']),
            Paragraph(f"¥ {itm[4]:,.2f}", styles['cell']),
            Paragraph(f"{tax_rate*100:.0f}%", styles['cell']),
            Paragraph(f"¥ {itm[5]:,.2f}", styles['cell']),
        ])
    it = Table(item_rows, colWidths=[130, 66, 40, 65, 75, 40, 70])
    it.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#64B5F6')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E0E0E0')),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#E3F2FD')),
        ('ALIGN', (2,1), (-1,-1), 'RIGHT'),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(it)
    story.append(Spacer(1, 6))
    
    sum_table = [
        [Paragraph("<b>价税合计 (大写)：</b>", styles['cell_bold']), Paragraph(f"人民币 {total_val/10000:,.2f} 万元整", styles['cell']),
         Paragraph("<b>(小写)：</b>", styles['cell_bold']), Paragraph(f"<b>¥ {total_val:,.2f} 元</b>", styles['cell_bold'])]
    ]
    st = Table(sum_table, colWidths=[110, 200, 50, 126])
    st.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#1565C0')),
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#E8EAF6')),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(st)
    story.append(Spacer(1, 10))
    
    status_bg = '#E8F5E9' if (is_paid and is_tax_settled) else '#FFF3E0'
    pay_status = "✅ 已通过银行网银电子转账结清全额" if is_paid else "⚠️ 处于财务挂账待支付状态 (应付账款)"
    tax_status = "✅ 已在属地税务主管机关申报并缴纳增值税/预缴核销" if is_tax_settled else "⚠️ 待月度报税期统一汇总清缴"
    
    audit_table = [
        [Paragraph("<b>【四流一致性核查状态】</b>", styles['cell_bold']), Paragraph(f"<b>资金流状态：</b>{pay_status}<br/><b>税收申报状态：</b>{tax_status}<br/><b>合同履约匹配：</b>与【{project_name}】对应批次结算单一致", styles['cell'])],
    ]
    at = Table(audit_table, colWidths=[140, 346])
    at.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#4CAF50' if is_paid else '#FF9800')),
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor(status_bg)),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(at)
    
    doc.build(story, canvasmaker=NumberedCanvas)


def create_pdf_bank_receipt(filepath, flow_no, payer_code, receiver_code, amount_yuan, usage_desc, pay_date="2026-03-22"):
    doc = SimpleDocTemplate(filepath, pagesize=A4, leftMargin=54, rightMargin=54, topMargin=54, bottomMargin=54)
    styles = get_doc_styles()
    story = []
    
    payer = get_entity_name(payer_code)
    receiver = get_entity_name(receiver_code)
    
    story.append(Paragraph("中国建设银行 / 招商银行 电子回单 (专用支付凭据)", styles['title']))
    story.append(Paragraph(f"<b>流水编号：</b>{flow_no} &nbsp;&nbsp;&nbsp;&nbsp; <b>记账日期：</b>{pay_date} 14:35:22 &nbsp;&nbsp;&nbsp;&nbsp; <b>回单状态：</b>交易成功 / 已入账", styles['cell_bold']))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#2E7D32'), spaceBefore=4, spaceAfter=10))
    
    b_table = [
        [Paragraph("<b>付款人全称：</b>", styles['cell_bold']), Paragraph(payer, styles['cell']), Paragraph("<b>收款人全称：</b>", styles['cell_bold']), Paragraph(receiver, styles['cell'])],
        [Paragraph("<b>付款人账号：</b>", styles['cell_bold']), Paragraph(ENTITIES.get(payer_code, {}).get('acc', '5100000001'), styles['cell']), Paragraph("<b>收款人账号：</b>", styles['cell_bold']), Paragraph(ENTITIES.get(receiver_code, {}).get('acc', '5100000002'), styles['cell'])],
        [Paragraph("<b>付款人开户行：</b>", styles['cell_bold']), Paragraph(ENTITIES.get(payer_code, {}).get('bank', '中国建设银行'), styles['cell']), Paragraph("<b>收款人开户行：</b>", styles['cell_bold']), Paragraph(ENTITIES.get(receiver_code, {}).get('bank', '招商银行'), styles['cell'])],
        [Paragraph("<b>交易币种及金额：</b>", styles['cell_bold']), Paragraph(f"<b>RMB ¥ {amount_yuan:,.2f} 元</b>", styles['cell_bold']), Paragraph("<b>金额大写：</b>", styles['cell_bold']), Paragraph(f"人民币 {amount_yuan/10000:,.2f} 万元整", styles['cell'])],
        [Paragraph("<b>业务款项用途：</b>", styles['cell_bold']), Paragraph(usage_desc, styles['cell']), Paragraph("<b>结算交易方式：</b>", styles['cell_bold']), Paragraph("大额网银实时电汇 (CNAPS)", styles['cell'])],
    ]
    bt = Table(b_table, colWidths=[95, 148, 95, 148])
    bt.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1.5, colors.HexColor('#2E7D32')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#C8E6C9')),
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#E8F5E9')),
        ('BACKGROUND', (2,0), (2,-1), colors.HexColor('#E8F5E9')),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(bt)
    story.append(Spacer(1, 15))
    story.append(Paragraph("<b>【银行系统电子验签说明】：</b>", styles['cell_bold']))
    story.append(Paragraph("本回单系通过中国建设银行网络金融服务系统生成，电子印章与实体印章具备同等法律效力。校验码：9871 2049 1827 3019 4827。", styles['body']))
    
    doc.build(story, canvasmaker=NumberedCanvas)


def create_pdf_weighbridge_logistics_sheet(filepath, project_name, supplier_code, receiver_code, material_name, total_tonnage, truck_count, amount_yuan):
    doc = SimpleDocTemplate(filepath, pagesize=A4, leftMargin=54, rightMargin=54, topMargin=54, bottomMargin=54)
    styles = get_doc_styles()
    story = []
    
    supplier = get_entity_name(supplier_code)
    receiver = get_entity_name(receiver_code)
    
    story.append(Paragraph(f"建设工程大宗物资电子地磅计量与入库验收单", styles['title']))
    story.append(Paragraph(f"<b>工程项目：</b>{project_name} &nbsp;&nbsp;&nbsp;&nbsp; <b>验收批次：</b>PZ-{date.today().strftime('%Y%m%d')}-01", styles['cell_bold']))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#E65100'), spaceBefore=4, spaceAfter=8))
    
    info = [
        [Paragraph("<b>供应发货单位：</b>", styles['cell_bold']), Paragraph(supplier, styles['cell']), Paragraph("<b>收货使用单位：</b>", styles['cell_bold']), Paragraph(receiver, styles['cell'])],
        [Paragraph("<b>物资名称规格：</b>", styles['cell_bold']), Paragraph(material_name, styles['cell']), Paragraph("<b>总过磅净重：</b>", styles['cell_bold']), Paragraph(f"<b>{total_tonnage:,.2f} 吨</b> (共计 {truck_count} 车次)", styles['cell_bold'])],
        [Paragraph("<b>物资结算金额：</b>", styles['cell_bold']), Paragraph(f"¥ {amount_yuan:,.2f} 元", styles['cell_bold']), Paragraph("<b>质检与抽检：</b>", styles['cell_bold']), Paragraph("力学性能与出厂合格证核验合格", styles['cell'])],
    ]
    it = Table(info, colWidths=[90, 153, 90, 153])
    it.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#FFE0B2')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#FFE0B2')),
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#FFF3E0')),
        ('BACKGROUND', (2,0), (2,-1), colors.HexColor('#FFF3E0')),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(it)
    story.append(Spacer(1, 8))
    
    story.append(Paragraph("<b>地磅过磅明细车次抽样记录表（现场自动记录）：</b>", styles['cell_bold']))
    
    truck_rows = [[Paragraph("<b>车牌号码</b>", styles['cell_bold']),
                   Paragraph("<b>毛重(吨)</b>", styles['cell_bold']),
                   Paragraph("<b>皮重(吨)</b>", styles['cell_bold']),
                   Paragraph("<b>净重(吨)</b>", styles['cell_bold']),
                   Paragraph("<b>过磅时间</b>", styles['cell_bold']),
                   Paragraph("<b>司磅员/材料员</b>", styles['cell_bold']),
                   Paragraph("<b>入库料场</b>", styles['cell_bold'])]]
    
    truck_prefix = ["川A·", "川G·", "渝B·", "青H·"]
    for idx in range(1, min(truck_count + 1, 9)):
        plate = f"{random.choice(truck_prefix)}{random.randint(10000, 99999)}"
        gross = random.uniform(45.0, 52.0)
        tare = random.uniform(14.0, 16.5)
        net = gross - tare
        truck_rows.append([
            Paragraph(plate, styles['cell']),
            Paragraph(f"{gross:.2f}", styles['cell']),
            Paragraph(f"{tare:.2f}", styles['cell']),
            Paragraph(f"{net:.2f}", styles['cell']),
            Paragraph(f"2026-03-{random.randint(10,25):02d} {random.randint(8,17):02d}:{random.randint(10,59):02d}", styles['cell']),
            Paragraph("李司磅 (签)", styles['cell']),
            Paragraph("1号主料场", styles['cell']),
        ])
        
    tt = Table(truck_rows, colWidths=[80, 60, 60, 60, 100, 66, 60])
    tt.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#BDBDBD')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#EEEEEE')),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F5F5F5')),
        ('ALIGN', (1,1), (3,-1), 'RIGHT'),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ]))
    story.append(tt)
    
    doc.build(story, canvasmaker=NumberedCanvas)


# ---------------------------------------------------------------------------
# 6 大项目全量档案数据定义 (严格包含 系统内单位 + 系统外单位 + 各业务形态)
# ---------------------------------------------------------------------------

PROJECTS_CONFIG = [
    {
        'dir_name': '01_天府国际金融中心二期_CD-TF-001',
        'code': 'CD-TF-001',
        'name': '成都天府国际金融中心二期大厦工程',
        'main_client': 'EXT-TF',
        'main_contractor': 'A08',
        'main_amount': 1_450_000_000,
        'city': '成都市',
        'contracts': [
            # 1. 施工总包主合同 (系统外业主 -> 系统内总包)
            {'title': '建设工程施工总承包主合同', 'seller': 'A08', 'buyer': 'EXT-TF', 'cat': '建筑工程施工总承包', 'amount': 1_450_000_000, 'no': 'CDTF-MAIN-2026-01', 'terms': ['工期720日历天，争创天府杯金奖', '进度款按月申报80%支付，增值税税率9%']},
            # 2. 系统内 材料 (B01)
            {'title': '大宗高强抗震钢材集采供销合同', 'seller': 'B01', 'buyer': 'A08', 'cat': '材料采购', 'amount': 450_000_000, 'no': 'TF-A08-B01', 'terms': ['HRB400E高强抗震螺纹钢集采', '全量提供电子地磅单与过磅小票，税率13%']},
            # 3. 系统外 外部材料直采 (攀钢集团物资销售)
            {'title': '外部特种高强合金钢直采供货合同', 'seller': 'EXT-PG-STEEL', 'buyer': 'A08', 'cat': '材料采购', 'amount': 80_000_000, 'no': 'TF-A08-EXT-PG', 'terms': ['攀钢直供Q420高强厚板特种钢材', '税率13%']},
            # 4. 系统内 劳务 (C01)
            {'title': '建筑主体结构劳务用工分包合同', 'seller': 'C01', 'buyer': 'A08', 'cat': '劳务用工', 'amount': 260_000_000, 'no': 'TF-A08-C01', 'terms': ['严格落实四川省建筑工人实名制考勤与银行代发工资', '税率9%']},
            # 5. 系统内 机械租赁 (D01)
            {'title': '重型塔式起重机及附着式升降脚手架租赁合同', 'seller': 'D01', 'buyer': 'A08', 'cat': '机械租赁', 'amount': 110_000_000, 'no': 'TF-A08-D01', 'terms': ['含中联重科8台重型自升式塔吊租赁与维保', '纯租赁税率13%']},
            # 6. 系统外 外部特种大件起重租赁
            {'title': '外部500吨级超重型履带吊租赁与吊装合同', 'seller': 'EXT-CQ-HEAVY-CRANE', 'buyer': 'A08', 'cat': '机械租赁', 'amount': 25_000_000, 'no': 'TF-A08-EXT-CRANE', 'terms': ['用于顶层连廊大跨度特种大件高空吊装', '税率13%']},
            # 7. 系统内 专业分包 (A11 钢结构)
            {'title': '超高层大跨度钢结构专业分包工程合同', 'seller': 'A11', 'buyer': 'A08', 'cat': '专业分包', 'amount': 280_000_000, 'no': 'TF-A08-A11', 'terms': ['大跨度钢桁架连廊加工与高空液压同步提升', '税率9%']},
            # 8. 系统内 专业分包 (A05 弱电智能)
            {'title': '建筑弱电智能化及BIM数字微网工程分包合同', 'seller': 'A05', 'buyer': 'A08', 'cat': '专业分包', 'amount': 40_000_000, 'no': 'TF-A08-A05', 'terms': ['智慧建筑楼宇自控系统与弱电系统集成', '税率9%']},
            # 9. 系统外 外部技术专家服务
            {'title': '外部超高层深基坑地质监测与技术咨询服务合同', 'seller': 'EXT-EXPERT-LABOR', 'buyer': 'A08', 'cat': '劳务用工', 'amount': 12_000_000, 'no': 'TF-A08-EXT-EXP', 'terms': ['三维激光深基坑变形监测与专家论证', '税率6%']},
        ]
    },
    {
        'dir_name': '02_成渝跨江特大桥及连接线_CY-CQ-002',
        'code': 'CY-CQ-002',
        'name': '成渝双城经济圈跨江特大桥及连接线工程',
        'main_client': 'EXT-CY',
        'main_contractor': 'A03',
        'main_amount': 880_000_000,
        'city': '重庆市',
        'contracts': [
            {'title': '跨江特大桥基础设施工程施工总承包合同', 'seller': 'A03', 'buyer': 'EXT-CY', 'cat': '建筑工程施工总承包', 'amount': 880_000_000, 'no': 'CYCQ-MAIN-2026-02', 'terms': ['跨省跨区工程（重庆段就地预缴2%增值税）', '川渝两地企业所得税三因素法分摊申报']},
            {'title': '特种耐候桥梁高强厚板钢材供销合同', 'seller': 'B10', 'buyer': 'A03', 'cat': '材料采购', 'amount': 320_000_000, 'no': 'CY-A03-B10', 'terms': ['Q345qD桥梁专用钢板供应', '税率13%']},
            {'title': '外部高强度水下抗冲刷特种混凝土直供合同', 'seller': 'EXT-XN-CONCRETE', 'buyer': 'A03', 'cat': '材料采购', 'amount': 60_000_000, 'no': 'CY-A03-EXT-CONC', 'terms': ['C50水下自密实防腐特种商砼供货', '税率13%']},
            {'title': '高空索塔及深水基础泥瓦特种作业劳务合同', 'seller': 'C02', 'buyer': 'A03', 'cat': '劳务用工', 'amount': 140_000_000, 'no': 'CY-A03-C02', 'terms': ['特种高处作业及水上沉井施工班组实名代发', '税率9%']},
            {'title': '重型水上浮吊与大直径旋挖桩机设备租赁合同', 'seller': 'D02', 'buyer': 'A03', 'cat': '机械租赁', 'amount': 80_000_000, 'no': 'CY-A03-D02', 'terms': ['配置500吨级打桩浮吊及水上驳船作业组', '设备租金税率13%']},
            {'title': '外部深水作业工程潜水与水上航道保障租赁合同', 'seller': 'EXT-CQ-HEAVY-CRANE', 'buyer': 'A03', 'cat': '机械租赁', 'amount': 18_000_000, 'no': 'CY-A03-EXT-SHIP', 'terms': ['长江主航道通航安全警示驳船与深水潜水机具', '税率13%']},
            {'title': '特大桥重庆江北侧引桥及互通立交分包工程合同', 'seller': 'A04', 'buyer': 'A03', 'cat': '专业分包', 'amount': 120_000_000, 'no': 'CY-A03-A04', 'terms': ['屹明汇重庆分公司属地化施工执行', '税率9%']},
        ]
    },
    {
        'dir_name': '03_广元利州产城融合与河道治理_GY-LZ-003',
        'code': 'GY-LZ-003',
        'name': '广元利州产城融合与生态河道综合治理工程',
        'main_client': 'EXT-GY',
        'main_contractor': 'A10',
        'main_amount': 360_000_000,
        'city': '广元市',
        'contracts': [
            {'title': '水利水运与生态河道综合整治总承包合同', 'seller': 'A10', 'buyer': 'EXT-GY', 'cat': '建筑工程施工总承包', 'amount': 360_000_000, 'no': 'GYLZ-MAIN-2026-03', 'terms': ['河道疏浚、防洪堤防工程与生态景观绿化', '税率9%']},
            {'title': '水利工程专用级配砂石骨料地磅集采合同', 'seller': 'B05', 'buyer': 'A10', 'cat': '材料采购', 'amount': 130_000_000, 'no': 'GY-A10-B05', 'terms': ['广元本地天然砂石骨料供应，全车过磅验收', '税率13%']},
            {'title': '外部生态景观植被与水土保持苗木直采合同', 'seller': 'EXT-GY-FOREST', 'buyer': 'A10', 'cat': '材料采购', 'amount': 15_000_000, 'no': 'GY-A10-EXT-TREE', 'terms': ['生态护坡水生植物与景观绿化林木供销', '税率9%']},
            {'title': '水利河道清淤开挖及边坡加固劳务用工合同', 'seller': 'C01', 'buyer': 'A10', 'cat': '劳务用工', 'amount': 45_000_000, 'no': 'GY-A10-C01', 'terms': ['清淤班组实名代发', '税率9%']},
            {'title': '水利清淤抽沙绞吸船与大型挖掘机租赁合同', 'seller': 'D03', 'buyer': 'A10', 'cat': '机械租赁', 'amount': 38_000_000, 'no': 'GY-A10-D03', 'terms': ['含清淤机具与农田排灌设施设备租用', '税率13%']},
            {'title': '土石方开挖转运与边坡柔性防护专业分包合同', 'seller': 'A09', 'buyer': 'A10', 'cat': '专业分包', 'amount': 60_000_000, 'no': 'GY-A10-A09', 'terms': ['山体边坡加固与35万方土石方挖填平衡', '税率9%']},
            {'title': '文明施工定型化标识标牌与围挡采购合同', 'seller': 'B06', 'buyer': 'A10', 'cat': '材料采购', 'amount': 8_000_000, 'no': 'GY-A10-B06', 'terms': ['全线定型化安全防护网与警示标识', '税率13%']},
        ]
    },
    {
        'dir_name': '04_成都高新西区微电网变电站_CD-GX-004',
        'code': 'CD-GX-004',
        'name': '成都高新西区绿色低碳微电网与变电站工程',
        'main_client': 'EXT-GX',
        'main_contractor': 'A07',
        'main_amount': 180_000_000,
        'city': '成都市',
        'contracts': [
            {'title': '110kV变电站及配电微网工程总承包合同', 'seller': 'A07', 'buyer': 'EXT-GX', 'cat': '建筑工程施工总承包', 'amount': 180_000_000, 'no': 'CDGX-MAIN-2026-04', 'terms': ['110kV智能变电站设备安装与电缆敷设', '税率9%']},
            {'title': '高低压交联电力电缆与成套配电柜集采合同', 'seller': 'B08', 'buyer': 'A07', 'cat': '材料采购', 'amount': 65_000_000, 'no': 'GX-A07-B08', 'terms': ['特种阻燃铜芯电缆及高压GIS开关柜', '税率13%']},
            {'title': '外部高压微网继电保护成套智能装置供销合同', 'seller': 'EXT-ABB-ELECTRIC', 'buyer': 'A07', 'cat': '材料采购', 'amount': 28_000_000, 'no': 'GX-A07-EXT-NARI', 'terms': ['特种数字微网变流变压智能装置', '税率13%']},
            {'title': '电气安装五金工具与接地防雷辅材供应合同', 'seller': 'B02', 'buyer': 'A07', 'cat': '材料采购', 'amount': 20_000_000, 'no': 'GX-A07-B02', 'terms': ['铜排、接地极及绝缘辅材供销', '税率13%']},
            {'title': '高压电力电缆敷设与接线特种劳务分包合同', 'seller': 'C01', 'buyer': 'A07', 'cat': '劳务用工', 'amount': 25_000_000, 'no': 'GX-A07-C01', 'terms': ['高压电工持证作业班组', '税率9%']},
            {'title': '重型高压试验车与大吨位随车起重机租赁合同', 'seller': 'D01', 'buyer': 'A07', 'cat': '机械租赁', 'amount': 12_000_000, 'no': 'GX-A07-D01', 'terms': ['特种电力起重与带电作业机械', '税率13%']},
            {'title': '智能变电站主控楼及防火隔墙土建分包合同', 'seller': 'A02', 'buyer': 'A07', 'cat': '专业分包', 'amount': 22_000_000, 'no': 'GX-A07-A02', 'terms': ['变电站站房清水混凝土结构施工', '税率9%']},
        ]
    },
    {
        'dir_name': '05_格尔木特种仓储综合配套_QY-GEM-005',
        'code': 'QY-GEM-005',
        'name': '格尔木盐湖工业园区特种仓储与综合配套工程',
        'main_client': 'EXT-GEM',
        'main_contractor': 'A01',
        'main_amount': 250_000_000,
        'city': '格尔木市',
        'contracts': [
            {'title': '盐湖工业园区特种耐腐仓储设施总承包合同', 'seller': 'A01', 'buyer': 'EXT-GEM', 'cat': '建筑工程施工总承包', 'amount': 250_000_000, 'no': 'QYGEM-MAIN-2026-05', 'terms': ['高原高寒盐雾腐蚀特种防腐仓储厂房', '税率9%']},
            {'title': '高寒特种耐低温保温材料直采供销合同', 'seller': 'B09', 'buyer': 'A01', 'cat': '材料采购', 'amount': 55_000_000, 'no': 'GEM-A01-B09', 'terms': ['聚氨酯低温保温板与密封结构胶', '税率13%']},
            {'title': '耐腐蚀耐候特种合金钢构件采购合同', 'seller': 'B03', 'buyer': 'A01', 'cat': '材料采购', 'amount': 35_000_000, 'no': 'GEM-A01-B03', 'terms': ['防盐雾耐酸特种涂层钢结构构件', '税率13%']},
            {'title': '外部重型铁路专用卸货线路与轨道接轨分包合同', 'seller': 'EXT-QH-RAILWAY', 'buyer': 'A01', 'cat': '专业分包', 'amount': 30_000_000, 'no': 'GEM-A01-EXT-RAIL', 'terms': ['盐湖铁路专线接入与装卸站台施工', '税率9%']},
            {'title': '高原集采供应链大宗建材供销合同', 'seller': 'B07', 'buyer': 'A01', 'cat': '材料采购', 'amount': 20_000_000, 'no': 'GEM-A01-B07', 'terms': ['集采高强度水泥与抗硫酸盐外加剂', '税率13%']},
            {'title': '干混预拌特种砂浆及添加剂采购合同', 'seller': 'B04', 'buyer': 'A01', 'cat': '材料采购', 'amount': 15_000_000, 'no': 'GEM-A01-B04', 'terms': ['特种防冻防裂灌浆料供应', '税率13%']},
            {'title': '高原高寒防腐作业与保温安装劳务分包合同', 'seller': 'C02', 'buyer': 'A01', 'cat': '劳务用工', 'amount': 32_000_000, 'no': 'GEM-A01-C02', 'terms': ['高寒特种作业劳务班组', '税率9%']},
            {'title': '高原特种大臂履带吊与极寒发电机组租赁合同', 'seller': 'D02', 'buyer': 'A01', 'cat': '机械租赁', 'amount': 18_000_000, 'no': 'GEM-A01-D02', 'terms': ['适应海拔3000米特种起重设备', '税率13%']},
            {'title': '大型重型特种钢结构仓储厂房制作吊装分包合同', 'seller': 'A06', 'buyer': 'A01', 'cat': '专业分包', 'amount': 45_000_000, 'no': 'GEM-A01-A06', 'terms': ['高寒高原大跨度门式刚架吊装施工', '税率9%']},
        ]
    },
    {
        'dir_name': '06_宜宾示范工业项目_YB-DEMO-001',
        'code': 'YB-DEMO-001',
        'name': '宜宾示范工业项目',
        'main_client': 'EXT-YB',
        'main_contractor': 'A08',
        'main_amount': 100_000_000,
        'city': '宜宾市',
        'contracts': [
            {'title': '宜宾三江示范工业园厂房总承包合同', 'seller': 'A08', 'buyer': 'EXT-YB', 'cat': '建筑工程施工总承包', 'amount': 100_000_000, 'no': 'YBDEMO-MAIN-2026-06', 'terms': ['标准轻钢厂房与配套综合用房', '税率9%']},
            {'title': '主体工程建筑劳务用工分包合同', 'seller': 'C01', 'buyer': 'A08', 'cat': '劳务用工', 'amount': 8_000_000, 'no': 'YB-B-001', 'terms': ['劳务班组实名代发', '税率9%']},
            {'title': '商品混凝土及砌体材料集采合同', 'seller': 'B01', 'buyer': 'A08', 'cat': '材料采购', 'amount': 12_000_000, 'no': 'YB-C-001', 'terms': ['预拌混凝土C30/C35供应', '税率13%']},
            {'title': '起重运输及土方施工机械租赁合同', 'seller': 'D01', 'buyer': 'A08', 'cat': '机械租赁', 'amount': 3_000_000, 'no': 'YB-D-001', 'terms': ['挖掘机及装载机设备台班', '税率13%']},
            {'title': '外部专业劳务协作与技术支持合同', 'seller': 'EXT-CY', 'buyer': 'A08', 'cat': '劳务用工', 'amount': 2_000_000, 'no': 'YB-EXT-CY-001', 'terms': ['系统外专家技术服务与劳务配合', '税率6%']},
            {'title': '特种电力配套调试与试验配合合同', 'seller': 'EXT-GX', 'buyer': 'A08', 'cat': '专业分包', 'amount': 1_500_000, 'no': 'YB-EXT-GX-001', 'terms': ['高压配电试验与保护定值整定', '税率6%']},
            {'title': '特种防腐耐磨地坪材料直销合同', 'seller': 'EXT-GEM', 'buyer': 'A08', 'cat': '材料采购', 'amount': 5_000_000, 'no': 'YB-EXT-GEM-001', 'terms': ['环氧自流平耐磨骨料直供', '税率13%']},
            {'title': '厂区雨污水管网生态开挖分包合同', 'seller': 'EXT-GY', 'buyer': 'A08', 'cat': '专业分包', 'amount': 3_000_000, 'no': 'YB-EXT-GY-001', 'terms': ['市政管网顶管与沉井施工', '税率9%']},
        ]
    }
]

def generate_all_archives():
    print("=" * 70)
    print("  🚀 开始为成都建工 6 大标杆工程全覆盖生成全套真实归档资料与四流凭证...")
    print("=" * 70)
    
    total_files = 0
    
    for p_idx, p in enumerate(PROJECTS_CONFIG, 1):
        p_dir = os.path.join(BASE_OUT_DIR, p['dir_name'])
        
        cat_dirs = {
            '01_主合同与发包批文': os.path.join(p_dir, '01_主合同与发包批文'),
            '02_专业分包与施工合同': os.path.join(p_dir, '02_专业分包与施工合同'),
            '03_劳务用工与用工结算': os.path.join(p_dir, '03_劳务用工与用工结算'),
            '04_大宗材料采购与供销合同': os.path.join(p_dir, '04_大宗材料采购与供销合同'),
            '05_机械设备租赁与台班签证': os.path.join(p_dir, '05_机械设备租赁与台班签证'),
            '06_增值税发票与税务完税凭证': os.path.join(p_dir, '06_增值税发票与税务完税凭证'),
            '07_资金结算与银行电子回单': os.path.join(p_dir, '07_资金结算与银行电子回单'),
            '08_物资物流地磅单与入库验收': os.path.join(p_dir, '08_物资物流地磅单与入库验收'),
            '09_现场实景照片与复印件影印本': os.path.join(p_dir, '09_现场实景照片与复印件影印本'),
        }
        for d in cat_dirs.values():
            os.makedirs(d, exist_ok=True)
            
        print(f"\n[{p_idx}/6] 正在构建项目档案: {p['name']} ({p['code']})...")
        
        main_c = p['contracts'][0]
        main_pdf = os.path.join(cat_dirs['01_主合同与发包批文'], f"{main_c['no']}_建设工程施工总承包主合同.pdf")
        create_pdf_contract(
            main_pdf, main_c['title'], main_c['buyer'], main_c['seller'],
            p['name'], main_c['no'], main_c['amount'], main_c['cat'], main_c['terms']
        )
        total_files += 1
        
        main_scan_jpg = os.path.join(cat_dirs['01_主合同与发包批文'], f"{main_c['no']}_中标通知书与履约保函_复印扫描件.jpg")
        create_simulated_scan_jpg(
            main_scan_jpg,
            "建设工程中标通知书与履约保证金确认函",
            [
                ("工程项目名称", p['name']),
                ("工程立项编码", p['code']),
                ("建设发包单位", get_entity_name(main_c['buyer'])),
                ("中标总包单位", get_entity_name(main_c['seller'])),
                ("中标合同金额", f"¥ {main_c['amount']:,.2f} 元"),
                ("履约担保方式", "银行见索即付不可撤销保函 (保函金额 10%)"),
                ("计划建设工期", "2026年01月15日 - 2028年01月15日"),
                ("工程质量目标", "四川省建设工程‘天府杯’金奖 / 鲁班奖参评"),
            ],
            stamp_entity=get_entity_name(main_c['buyer']),
            stamp_type="发包招投标专用章",
            doc_no=f"ZB-{p['code']}-2026",
            extra_notes=[
                "发包人确认：招标程序合规，中标通知书具有完全法律效力；",
                "总承包人已提交足额履约担保，准予进场开工并办理施工许可证。"
            ]
        )
        total_files += 1

        for c_idx, c in enumerate(p['contracts'][1:], 1):
            cat = c['cat']
            amount = c['amount']
            no = c['no']
            
            if '劳务' in cat:
                target_dir = cat_dirs['03_劳务用工与用工结算']
            elif '材料' in cat or '供销' in cat:
                target_dir = cat_dirs['04_大宗材料采购与供销合同']
            elif '机械' in cat or '租赁' in cat or '设备' in cat:
                target_dir = cat_dirs['05_机械设备租赁与台班签证']
            else:
                target_dir = cat_dirs['02_专业分包与施工合同']
                
            c_pdf = os.path.join(target_dir, f"{no}_{c['title']}.pdf")
            create_pdf_contract(
                c_pdf, c['title'], c['buyer'], c['seller'],
                p['name'], no, amount, cat, c['terms']
            )
            total_files += 1
            
            c_jpg = os.path.join(target_dir, f"{no}_{c['title']}_盖章原件影印本.jpg")
            create_simulated_scan_jpg(
                c_jpg,
                f"工程分项业务协议书 ({cat})",
                [
                    ("合同编号", no),
                    ("所属总包项目", p['name']),
                    ("发包/采购方", get_entity_name(c['buyer'])),
                    ("承包/供应方", get_entity_name(c['seller'])),
                    ("签约暂定金额", f"¥ {amount:,.2f} 元"),
                    ("发票开具税率", "13% (物资/纯租赁) / 9% (建筑施工劳务) / 6% (技术服务)"),
                    ("结算支付进度", "按月度核定形象进度 80% 支付，留存3%保修金"),
                ],
                stamp_entity=get_entity_name(c['seller']),
                stamp_type="业务合同专用章",
                doc_no=no,
                extra_notes=[
                    "严格执行合同流、资金流、发票流、货物流/劳务流四流一致性审核；",
                    "开票单位与收款银行账户必须与本合同签署主体完全一致。"
                ]
            )
            total_files += 1

            inv_no = f"2651{random.randint(10000000, 99999999)}"
            tax_rate = 0.13 if ('材料' in cat or '租赁' in cat) else (0.06 if '服务' in c['title'] else 0.09)
            amount_no_tax = round(amount / (1 + tax_rate), 2)
            tax_amt = round(amount - amount_no_tax, 2)
            
            is_paid = (c_idx % 4 != 0)
            is_tax_settled = (c_idx % 3 != 0)
            
            inv_pdf = os.path.join(cat_dirs['06_增值税发票与税务完税凭证'], f"INVOICE_{no}_{inv_no}_增值税专票.pdf")
            create_pdf_tax_invoice_sheet(
                inv_pdf, inv_no, c['seller'], c['buyer'], p['name'],
                amount_no_tax, tax_amt, tax_rate,
                [[f"*{cat}*{c['title'][:16]}", "批/项", 1, amount_no_tax, amount_no_tax, tax_amt]],
                is_paid=is_paid, is_tax_settled=is_tax_settled
            )
            total_files += 1
            
            inv_jpg = os.path.join(cat_dirs['06_增值税发票与税务完税凭证'], f"INVOICE_{no}_{inv_no}_发票扫描件.jpg")
            create_simulated_scan_jpg(
                inv_jpg,
                "四川增值税电子专用发票 (全国统一查验平台验证件)",
                [
                    ("发票代码/号码", f"051002300111 / No.{inv_no}"),
                    ("开票日期", "2026年03月20日"),
                    ("购买方 (付款人)", get_entity_name(c['buyer'])),
                    ("销售方 (收款人)", get_entity_name(c['seller'])),
                    ("金额 (不含税)", f"¥ {amount_no_tax:,.2f} 元"),
                    ("税额", f"¥ {tax_amt:,.2f} 元 (税率 {tax_rate*100:.0f}%)"),
                    ("价税合计 (小写)", f"¥ {amount:,.2f} 元"),
                    ("发票查验结果", "国家税务总局发票查验平台: 【一致 / 正常】"),
                ],
                stamp_entity=get_entity_name(c['seller']),
                stamp_type="发票专用章",
                doc_no=inv_no,
                extra_notes=[
                    "税务稽查核验：发票状态正常，未见作废或红冲记录；",
                    "进项税额已在增值税发票综合服务平台完成抵扣勾选认证。"
                ]
            )
            total_files += 1

            if is_paid:
                bank_no = f"EBNK20260322{random.randint(100000, 999999)}"
                b_pdf = os.path.join(cat_dirs['07_资金结算与银行电子回单'], f"BANK_{no}_{bank_no}_银行支付回单.pdf")
                create_pdf_bank_receipt(
                    b_pdf, bank_no, c['buyer'], c['seller'],
                    round(amount * 0.8, 2), f"支付【{p['name']}】项下【{c['title']}】第2期工程进度款",
                    pay_date=f"2026-03-{random.randint(15, 26):02d}"
                )
                total_files += 1
                
                b_jpg = os.path.join(cat_dirs['07_资金结算与银行电子回单'], f"BANK_{no}_{bank_no}_电子回单盖章原件.jpg")
                create_simulated_scan_jpg(
                    b_jpg,
                    "中国建设银行 电子业务专用回单 (网银支付证实)",
                    [
                        ("回单编号", bank_no),
                        ("付款账户名称", get_entity_name(c['buyer'])),
                        ("付款账号", ENTITIES.get(c['buyer'], {}).get('acc', '510000001')),
                        ("收款账户名称", get_entity_name(c['seller'])),
                        ("收款账号", ENTITIES.get(c['seller'], {}).get('acc', '510000002')),
                        ("交易金额", f"¥ {amount*0.8:,.2f} 元 (支付80%核定进度款)"),
                        ("交易状态", "转账成功 · 实时清算入账 (CNAPS)"),
                        ("款项用途", f"工程款/材料款: {c['title']}"),
                    ],
                    stamp_entity="中国建设银行股份有限公司成都分行",
                    stamp_type="电子回单业务专用章",
                    doc_no=bank_no,
                    extra_notes=[
                        "资金流路径验证：款项直接由合同甲方网银对公电汇至乙方合同备案账号；",
                        "无个人卡垫付或第三方过桥交易，资金链路清晰闭环。"
                    ]
                )
                total_files += 1
            else:
                unpaid_jpg = os.path.join(cat_dirs['07_资金结算与银行电子回单'], f"UNPAID_{no}_财务挂账应付款确认审批表.jpg")
                create_simulated_scan_jpg(
                    unpaid_jpg,
                    "工程项目部 应付款项财务挂账与资金支付审批单",
                    [
                        ("业务合同编号", no),
                        ("应付收款单位", get_entity_name(c['seller'])),
                        ("合同总额", f"¥ {amount:,.2f} 元"),
                        ("本期结算申报金额", f"¥ {amount*0.75:,.2f} 元"),
                        ("目前实际支付金额", "¥ 0.00 元 (挂账应付中)"),
                        ("支付审核状态", "⚠️ 业主回款进度滞后，已完成产值确认，待下批资金计划拨付"),
                        ("风险与合规结论", "四流匹配核查：劳务/材料验收已入库，挂账真实，无虚开发票风险"),
                    ],
                    stamp_entity=get_entity_name(c['buyer']),
                    stamp_type="财务专用章",
                    doc_no=f"AP-{no}",
                    extra_notes=[
                        "项目财务已录入应付账款明细台账；",
                        "待发包人对应工程结算款入账后，优先安排该分包单位款项支付。"
                    ]
                )
                total_files += 1

            if '材料' in cat:
                tonnage = round(amount / 4200.0, 1)
                truck_cnt = max(int(tonnage / 35), 4)
                w_pdf = os.path.join(cat_dirs['08_物资物流地磅单与入库验收'], f"LOGISTICS_{no}_地磅过磅验收单.pdf")
                create_pdf_weighbridge_logistics_sheet(
                    w_pdf, p['name'], c['seller'], c['buyer'],
                    c['title'], tonnage, truck_cnt, amount
                )
                total_files += 1
                
                w_jpg = os.path.join(cat_dirs['08_物资物流地磅单与入库验收'], f"LOGISTICS_{no}_地磅称重小票及现场签收单.jpg")
                create_simulated_scan_jpg(
                    w_jpg,
                    "施工现场智能电子汽车衡 称重过磅结算单",
                    [
                        ("过磅单流水号", f"PB-{random.randint(100000, 999999)}"),
                        ("工程项目名称", p['name']),
                        ("发货单位", get_entity_name(c['seller'])),
                        ("收货单位及料场", f"{get_entity_name(c['buyer'])} · 现场1号材料仓"),
                        ("累计过磅车次", f"{truck_cnt} 车次 (重车进场 / 空车回皮)"),
                        ("累计净重总计", f"{tonnage:,.2f} 吨"),
                        ("现场材料员签字", "周大伟 (现场检尺与外观质量核验合格)"),
                        ("过磅时间区间", "2026-03-01 至 2026-03-20 全程电子摄像记录"),
                    ],
                    stamp_entity=get_entity_name(c['buyer']),
                    stamp_type="项目物资材料专用章",
                    doc_no=f"DB-{no}",
                    extra_notes=[
                        "过磅称重系统具备红外防作弊与车牌自动识别功能；",
                        "每车称重数据均自动上传集团物资数字化中枢，数据真实闭环。"
                    ]
                )
                total_files += 1
            else:
                acc_jpg = os.path.join(cat_dirs['08_物资物流地磅单与入库验收'], f"ACCEPTANCE_{no}_工序验收与台班签认记录单.jpg")
                create_simulated_scan_jpg(
                    acc_jpg,
                    "建设工程 隐蔽工序验收与设备台班签证核定表",
                    [
                        ("合同编号与名称", f"{no} · {c['title']}"),
                        ("承包执行主体", get_entity_name(c['seller'])),
                        ("施工部位/作业面", f"【{p['name']}】主体作业标段"),
                        ("现场核验工程量", f"核定合格工程量达标率 100% (金额: ¥{amount*0.8:,.2f}元)"),
                        ("安全与技术交底", "班前安全早会交底记录完整，特种作业人员持证上岗"),
                        ("监理旁站验收结论", "经现场实测实量与旁站核验，质量符合设计规范要求，准予计量"),
                    ],
                    stamp_entity="四川科诚建设监理咨询有限公司",
                    stamp_type="项目监理部业务专用章",
                    doc_no=f"YS-{no}",
                    extra_notes=[
                        "专业工序已通过建设、总包、分包、监理四方联合实地复验；",
                        "现场签认单据已归档至工程部数字化质量溯源系统。"
                    ]
                )
                total_files += 1

        photo1_jpg = os.path.join(cat_dirs['09_现场实景照片与复印件影印本'], f"PHOTO_01_施工现场实景取证_主体形象.jpg")
        create_simulated_site_photo_jpg(
            photo1_jpg, p['name'], "主标段主体结构及施工作业面",
            "现场塔吊运转正常，工人正在进行钢筋绑扎与模板支设，监理旁站到位。"
        )
        total_files += 1

        photo2_jpg = os.path.join(cat_dirs['09_现场实景照片与复印件影印本'], f"PHOTO_02_物资进场过磅与抽样检测.jpg")
        create_simulated_site_photo_jpg(
            photo2_jpg, p['name'], "物资地磅房及大宗材料卸料堆场",
            "大宗抗震钢材重车过磅并抽检取样，物资入库单与送货单核对无误。"
        )
        total_files += 1

        if p['code'] == 'CY-CQ-002':
            cq_tax_jpg = os.path.join(cat_dirs['06_增值税发票与税务完税凭证'], "CROSS_REGION_跨区域涉税事项报告及重庆就地预缴税收完税证明.jpg")
            create_simulated_scan_jpg(
                cq_tax_jpg,
                "国家税务总局 重庆市江北区税务局 跨区税收完税证明",
                [
                    ("跨区域涉税事项报告编号", "510104-2026-000128"),
                    ("异地施工项目名称", "成渝双城经济圈跨江特大桥及连接线工程 (重庆段)"),
                    ("纳税人名称", "四川屹明汇建设工程有限公司重庆分公司 (A04)"),
                    ("计税销售额", "¥ 500,000,000.00 元"),
                    ("跨区就地预缴增值税 (2%)", "¥ 10,000,000.00 元 (已就地实缴国库)"),
                    ("城建及附加税费 (就地缴纳)", "¥ 1,200,000.00 元"),
                    ("企业所得税分摊测算", "已按川渝三因素法（资产/收入/人员）计入总分机构汇总申报"),
                    ("征税税务机关", "国家税务总局重庆市江北区税务局第一税务所"),
                ],
                stamp_entity="国家税务总局重庆市江北区税务局征税专用章",
                stamp_type="征税专用章",
                doc_no="WS-2026-CQ-009182",
                extra_notes=[
                    "预缴税款已通过全国财税库银横向联网系统 (TIPS) 实缴入库；",
                    "已生成《跨区域涉税事项反馈表》交回主管税务机关（锦江区税务局）核销抵减。"
                ]
            )
            total_files += 1

    print("\n" + "=" * 70)
    print(f"  🎉 恭喜！全部 6 大工程项目全套存档资料生成完毕！共计生成 {total_files} 份文件。")
    print(f"  📁 存档绝对路径: {BASE_OUT_DIR}")
    print("=" * 70)

if __name__ == '__main__':
    generate_all_archives()
