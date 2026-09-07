# -*- coding: utf-8 -*-
"""
=============================================================================
成都建工 V3.0 · 02 与 03 标杆工程全套项目存档资料生成器
=============================================================================
全面遵循 01 项目的八大核心标准：
  1. 施工、劳务、材料、机械租赁全业务线条完整覆盖
  2. 系统内单位 (A/B/C/D 类法人) 与 系统外单位 (外部发包业主、外部材料商、外部特种分包、外部租赁) 双向交互
  3. 四流闭环：合同流、发票流、资金流 (已付款银行电子回单 / 未付款财务挂账审批单)、业务物资流 (地磅过磅验收单 / 工序验收与台班签认记录单)
  4. 全税种完税证明：增值税、企业所得税、印花税、环保税、个人所得税及社保公积金、跨区域就地预缴完税证明 (02 重庆项目)
  5. 真实双模态输出：矢量 PDF 电子凭证 + 带公章/印签/扫描质感的 JPG 原始影印件
  6. 现场实景照片：带今日工程相机水印的施工形象取证与物资进场过磅照片
  7. 模拟测试台账：10_模拟测试资料 (收入成本审核资料与工资社保台账)
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

PROJECT_ROOT = "/Users/yvoche/AI开发/073_成都建工/V3.0"
BASE_OUT_DIR = os.path.join(PROJECT_ROOT, "项目存档资料")

# 参建单位基础档案库
ENTITIES = {
    # A 类 施工总包/专业分包
    'A01': {'name': '中镌（湖北）建筑有限公司', 'tax_id': '91420105MA49M5UX1F', 'bank': '中国建设银行武汉江岸支行', 'acc': '42050123456789012345', 'authority': '国家税务总局武汉市江岸区税务局', 'treasury': '国家金库武汉市中心支库'},
    'A02': {'name': '四川中恒腾鸣建筑工程有限公司', 'tax_id': '91510107MA6C8XTY7B', 'bank': '中国工商银行成都武侯支行', 'acc': '51020198765432109876', 'authority': '国家税务总局成都市武侯区税务局', 'treasury': '国家金库成都市中心支库'},
    'A03': {'name': '四川屹明汇建设工程有限公司', 'tax_id': '91510104MA6CAD6R9K', 'bank': '招商银行成都锦江支行', 'acc': '51050111223344556677', 'authority': '国家税务总局成都市锦江区税务局第一税务所', 'treasury': '国家金库成都市中心支库'},
    'A04': {'name': '四川屹明汇建设工程有限公司重庆分公司', 'tax_id': '91500230MAD7T9Y43P', 'bank': '中国农业银行重庆江北支行', 'acc': '50010188776655443322', 'authority': '国家税务总局重庆市江北区税务局第一税务所', 'treasury': '国家金库重庆市江北区支库'},
    'A05': {'name': '四川帆亿通信科技有限公司', 'tax_id': '91510104MA6CYN4T2A', 'bank': '交通银行成都高新支行', 'acc': '51030133445566778899', 'authority': '国家税务总局成都市高新区税务局', 'treasury': '国家金库成都市中心支库'},
    'A06': {'name': '四川裕合荣建筑工程有限公司', 'tax_id': '91510105MA689P8W5E', 'bank': '中信银行成都青羊支行', 'acc': '51070199887766554433', 'authority': '国家税务总局成都市青羊区税务局', 'treasury': '国家金库成都市中心支库'},
    'A07': {'name': '四川铁安电力工程有限公司', 'tax_id': '91510107064438179R', 'bank': '中国银行成都金牛支行', 'acc': '51010155667788990011', 'authority': '国家税务总局成都市金牛区税务局', 'treasury': '国家金库成都市中心支库'},
    'A08': {'name': '四川锐宝建设工程有限公司', 'tax_id': '91510106MA61UEJ48K', 'bank': '成都银行科技支行', 'acc': '51090177889900112233', 'authority': '国家税务总局成都市金牛区税务局第一税务所', 'treasury': '国家金库成都市中心支库'},
    'A09': {'name': '四川顺程源建筑工程有限公司', 'tax_id': '91510105MABY7X5T3N', 'bank': '中国建设银行成都天府支行', 'acc': '51050144556677889900', 'authority': '国家税务总局四川天府新区成都管委会税务局', 'treasury': '国家金库成都市中心支库'},
    'A10': {'name': '四川鼎新源建筑工程有限公司', 'tax_id': '91510105MA6CBP5T9M', 'bank': '中国工商银行广元利州支行', 'acc': '51080166778899001122', 'authority': '国家税务总局广元市利州区税务局第一税务所', 'treasury': '国家金库广元市中心支库'},
    'A11': {'name': '成都巨邦建设工程有限公司', 'tax_id': '91510104MA6CM8P59N', 'bank': '中国农业银行成都锦江支行', 'acc': '51040188990011223344', 'authority': '国家税务总局成都市锦江区税务局', 'treasury': '国家金库成都市中心支库'},
    # B 类 物资贸易
    'B01': {'name': '四川乾润和贸易有限公司', 'tax_id': '91510106MA6D7H4N9A', 'bank': '招商银行成都金牛支行', 'acc': '51060122334455667788', 'authority': '国家税务总局成都市金牛区税务局', 'treasury': '国家金库成都市中心支库'},
    'B02': {'name': '四川兴誉诚商贸有限公司', 'tax_id': '91510185MA6CEK5T8W', 'bank': '中国银行成都简阳支行', 'acc': '51010199001122334455', 'authority': '国家税务总局简阳市税务局', 'treasury': '国家金库简阳市支库'},
    'B03': {'name': '四川坤珀贸易有限公司', 'tax_id': '91510106MAD04NX82T', 'bank': '中国工商银行成都金牛支行', 'acc': '51020133445566778811', 'authority': '国家税务总局成都市金牛区税务局', 'treasury': '国家金库成都市中心支库'},
    'B04': {'name': '四川矗佳商贸有限公司', 'tax_id': '91511526MA67UN7C2G', 'bank': '中国建设银行宜宾江安支行', 'acc': '51150155667788990022', 'authority': '国家税务总局江安县税务局', 'treasury': '国家金库江安县支库'},
    'B05': {'name': '广元玖硕商贸有限公司', 'tax_id': '91510802MA67Q84X1T', 'bank': '中国农业银行广元分行', 'acc': '51080177889900112244', 'authority': '国家税务总局广元市利州区税务局', 'treasury': '国家金库广元市中心支库'},
    'B06': {'name': '广州采云广告有限公司', 'tax_id': '91440101MA5CQ8N94Y', 'bank': '平安银行广州天河支行', 'acc': '44010188990011223355', 'authority': '国家税务总局广州市天河区税务局', 'treasury': '国家金库广州市中心支库'},
    'B07': {'name': '成都恒创嘉泰贸易有限公司', 'tax_id': '91510106MA6D2X5L8T', 'bank': '中信银行成都分行', 'acc': '51070111223344556688', 'authority': '国家税务总局成都市金牛区税务局', 'treasury': '国家金库成都市中心支库'},
    'B08': {'name': '成都鑫晨鼎升商贸有限公司', 'tax_id': '91510107MA6CK9P43X', 'bank': '交通银行成都武侯支行', 'acc': '51030144556677889922', 'authority': '国家税务总局成都市武侯区税务局', 'treasury': '国家金库成都市中心支库'},
    'B09': {'name': '格尔木青泽贸易有限公司', 'tax_id': '91632801MA758N3E2K', 'bank': '中国建设银行格尔木分行', 'acc': '63280166778899001133', 'authority': '国家税务总局格尔木市税务局', 'treasury': '国家金库格尔木市支库'},
    'B10': {'name': '重庆朗德乾润商贸有限公司', 'tax_id': '91500109MABY9K6W4L', 'bank': '招商银行重庆九龙坡支行', 'acc': '50010122334455667799', 'authority': '国家税务总局重庆市九龙坡区税务局', 'treasury': '国家金库重庆市九龙坡区支库'},
    # C 类 劳务分包
    'C01': {'name': '四川本盛劳务有限公司', 'tax_id': '91510107350645934C', 'bank': '成都农商银行成华支行', 'acc': '51010833445566778800', 'authority': '国家税务总局成都市成华区税务局', 'treasury': '国家金库成都市中心支库'},
    'C02': {'name': '四川灏琅建筑劳务有限公司', 'tax_id': '91510603MADH76KY3E', 'bank': '四川天府银行德阳支行', 'acc': '51060155667788990033', 'authority': '国家税务总局德阳市旌阳区税务局', 'treasury': '国家金库德阳市中心支库'},
    # D 类 机械设备租赁
    'D01': {'name': '四川乾润和机械设备租赁有限公司', 'tax_id': '91510106MA6D2K9L3E', 'bank': '中国民生银行成都金牛支行', 'acc': '51010188990011223366', 'authority': '国家税务总局成都市金牛区税务局', 'treasury': '国家金库成都市中心支库'},
    'D02': {'name': '四川乾诺机械租赁有限公司', 'tax_id': '91510100MA7G8T2E5H', 'bank': '中国光大银行成都分行', 'acc': '51010133445566778822', 'authority': '国家税务总局成都市青羊区税务局', 'treasury': '国家金库成都市中心支库'},
    'D03': {'name': '四川惠润农业设备有限公司', 'tax_id': '91510100MA6CP8W61L', 'bank': '四川银行成都高新支行', 'acc': '51010177889900112255', 'authority': '国家税务总局成都市高新区税务局', 'treasury': '国家金库成都市中心支库'},
    
    # 系统外发包业主
    'EXT-CY': {'name': '成渝高速公路开发投资集团有限公司', 'tax_id': '91510100MA61BBBB22', 'bank': '中国进出口银行成都分行', 'acc': '51000098765432102222', 'authority': '国家税务总局成都市高新区税务局', 'treasury': '国家金库成都市中心支库'},
    'EXT-GY': {'name': '广元市利州区水务发展投资集团有限公司', 'tax_id': '91510800MA61CCCC33', 'bank': '广元市农村商业银行利州支行', 'acc': '51080011223344553333', 'authority': '国家税务总局广元市利州区税务局', 'treasury': '国家金库广元市中心支库'},
    
    # 系统外第三方供货/分包/租赁/劳务合作单位
    'EXT-XN-CONCRETE': {'name': '西南特种商品混凝土直供配送有限公司', 'tax_id': '91500100MA61FFFF88', 'bank': '重庆农商行江北支行', 'acc': '50010822334455668888', 'authority': '国家税务总局重庆市江北区税务局', 'treasury': '国家金库重庆市江北区支库'},
    'EXT-CQ-HEAVY-CRANE': {'name': '重庆重交大件起重吊装工程有限公司', 'tax_id': '91500100MA61GGGG99', 'bank': '重庆银行江北支行', 'acc': '50010144556677889999', 'authority': '国家税务总局重庆市江北区税务局', 'treasury': '国家金库重庆市江北区支库'},
    'EXT-GY-FOREST': {'name': '广元利州生态园林绿化苗木专业合作社', 'tax_id': '93510800MA61HHHH00', 'bank': '广元市农村信用合作联社利州分社', 'acc': '51080133445566770000', 'authority': '国家税务总局广元市利州区税务局', 'treasury': '国家金库广元市中心支库'},
    'EXT-EXPERT-LABOR': {'name': '四川省建筑科学研究院特种技术服务中心', 'tax_id': '91510100MA61KKKK33', 'bank': '中国工商银行成都一环路支行', 'acc': '51010199001122333333', 'authority': '国家税务总局成都市金牛区税务局', 'treasury': '国家金库成都市中心支库'},
}

def get_entity_name(code):
    return ENTITIES.get(code, {}).get('name', f"合作单位({code})")

# ---------------------------------------------------------------------------
# 图像印章与扫描质感渲染 (PIL)
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

def draw_tax_oval_stamp(draw, top_title, entity_name, stamp_type="征税专用章", center=(150, 120), rx=130, ry=85, color=(210, 30, 30)):
    cx, cy = center
    draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], outline=color, width=3)
    draw.ellipse([cx - rx + 5, cy - ry + 5, cx + rx - 5, cy + ry - 5], outline=color, width=1)
    
    star_r = ry * 0.25
    points = []
    for i in range(5):
        angle = i * 4 * math.pi / 5 - math.pi / 2
        points.append((cx + star_r * math.cos(angle), cy - 10 + star_r * math.sin(angle)))
    draw.polygon(points, fill=color)
    
    sub_font = ImageFont.truetype(FONT_PATH, int(ry * 0.22))
    sw = draw.textlength(stamp_type, font=sub_font)
    draw.text((cx - sw / 2, cy + 12), stamp_type, font=sub_font, fill=color)
    
    top_font = ImageFont.truetype(FONT_PATH, int(ry * 0.18))
    tw = draw.textlength(top_title, font=top_font)
    draw.text((cx - tw / 2, cy - ry + 12), top_title, font=top_font, fill=color)
    
    ent_font = ImageFont.truetype(FONT_PATH, int(ry * 0.17))
    ew = draw.textlength(entity_name, font=ent_font)
    draw.text((cx - ew / 2, cy + ry - 28), entity_name, font=ent_font, fill=color)

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

def create_simulated_scan_jpg(filepath, title, key_rows, stamp_entity="四川屹明汇建设工程有限公司", stamp_type="合同专用章", doc_no="", extra_notes=None, archive_date_str=None):
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
            draw.text((left_x + 20, note_y + 40 + idx * 36), f"• {line}", font=text_font, fill=(70, 70, 70))
        note_y += 40 + len(extra_notes) * 36 + 40

    stamp_cx = w - 380
    stamp_cy = min(note_y + 130, h - 350)
    draw_circular_stamp(draw, stamp_entity, stamp_type, center=(stamp_cx, stamp_cy), radius=105, color=(210, 35, 35))

    arch_date = archive_date_str or "2024年09月15日"
    draw.text((stamp_cx - 100, stamp_cy + 130), f"归档日期：{arch_date}", font=bold_font, fill=(60, 60, 60))

    img = apply_scanner_photocopy_texture(img, is_color=True, noise_level=12)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    img.save(filepath, quality=92)

def create_simulated_tax_cert_jpg(filepath, title, cert_data, entity):
    w, h = 1600, 2260
    bg_color = (254, 253, 248)
    img = Image.new('RGB', (w, h), color=bg_color)
    draw = ImageDraw.Draw(img)
    
    title_font = ImageFont.truetype(FONT_PATH, 46)
    sub_font = ImageFont.truetype(FONT_PATH, 28)
    bold_font = ImageFont.truetype(FONT_PATH, 24)
    text_font = ImageFont.truetype(FONT_PATH, 21)
    small_font = ImageFont.truetype(FONT_PATH, 18)
    
    tw = draw.textlength("国家税务总局 电子税收完税证明", font=title_font)
    draw.text(((w - tw) / 2, 70), "国家税务总局 电子税收完税证明", font=title_font, fill=(180, 20, 20))
    sw = draw.textlength(f"（{title}）", font=sub_font)
    draw.text(((w - sw) / 2, 135), f"（{title}）", font=sub_font, fill=(60, 60, 60))
    
    left_x = 90
    table_w = w - 180
    
    draw.text((left_x, 195), f"填发日期: {cert_data['payment_date']}", font=bold_font, fill=(40, 40, 40))
    draw.text((left_x + 500, 195), f"税票号码: {cert_data['tax_ticket_no']}", font=bold_font, fill=(40, 40, 40))
    draw.text((left_x + 1050, 195), f"电子凭证号: {cert_data['receipt_no']}", font=bold_font, fill=(180, 20, 20))
    
    head_y = 235
    draw.rectangle([left_x, head_y, left_x + table_w, head_y + 110], fill=(245, 248, 252), outline=(180, 190, 200), width=1)
    draw.text((left_x + 20, head_y + 20), f"纳税人名称: {entity['name']}", font=bold_font, fill=(20, 20, 20))
    draw.text((left_x + 750, head_y + 20), f"纳税人识别号 (税号): {entity['tax_id']}", font=bold_font, fill=(20, 20, 20))
    draw.text((left_x + 20, head_y + 65), f"主管税务机关: {entity['authority']}", font=text_font, fill=(60, 60, 60))
    draw.text((left_x + 750, head_y + 65), f"开户银行及账号: {entity['bank']} ({entity['acc'][:6]}****{entity['acc'][-4:]})", font=text_font, fill=(60, 60, 60))
    
    items_top = head_y + 130
    item_row_h = 52
    cols = [
        ("原凭证号", 220), ("税种", 240), ("品目名称", 260),
        ("税款所属时期", 300), ("计税依据", 220), ("税率", 110), ("实缴金额(元)", 170)
    ]
    
    n_items = len(cert_data['items'])
    draw.rectangle([left_x, items_top, left_x + table_w, items_top + (n_items + 2) * item_row_h], outline=(180, 190, 200), width=1)
    draw.rectangle([left_x + 1, items_top + 1, left_x + table_w - 1, items_top + item_row_h - 1], fill=(230, 238, 248))
    
    c_start = left_x
    for name, cw in cols:
        draw.text((c_start + 10, items_top + 16), name, font=bold_font, fill=(30, 30, 30))
        c_start += cw
        if c_start < left_x + table_w:
            draw.line([(c_start, items_top), (c_start, items_top + (n_items + 2) * item_row_h)], fill=(200, 200, 200), width=1)
            
    total_tax = Decimal("0")
    for idx, itm in enumerate(cert_data['items']):
        row_y = items_top + (idx + 1) * item_row_h
        draw.line([(left_x, row_y), (left_x + table_w, row_y)], fill=(200, 200, 200), width=1)
        amt = Decimal(str(itm['amount']))
        total_tax += amt
        
        draw.text((left_x + 10, row_y + 16), str(itm['orig_no']), font=text_font, fill=(60, 60, 60))
        draw.text((left_x + 230, row_y + 16), str(itm['tax_type']), font=bold_font, fill=(20, 20, 20))
        draw.text((left_x + 470, row_y + 16), str(itm['category'])[:10], font=text_font, fill=(50, 50, 50))
        draw.text((left_x + 730, row_y + 16), str(itm['period_range']), font=text_font, fill=(50, 50, 50))
        draw.text((left_x + 1030, row_y + 16), f"¥{itm.get('tax_base', 0):,.0f}", font=text_font, fill=(50, 50, 50))
        draw.text((left_x + 1250, row_y + 16), str(itm['rate']), font=text_font, fill=(50, 50, 50))
        draw.text((left_x + 1360, row_y + 16), f"¥{amt:,.2f}", font=bold_font, fill=(160, 20, 20))
        
    tot_y = items_top + (n_items + 1) * item_row_h
    draw.line([(left_x, tot_y), (left_x + table_w, tot_y)], fill=(200, 200, 200), width=1)
    draw.rectangle([left_x + 1, tot_y + 1, left_x + table_w - 1, tot_y + item_row_h - 1], fill=(255, 250, 230))
    draw.text((left_x + 20, tot_y + 16), "合计金额 (大写):", font=bold_font, fill=(40, 40, 40))
    draw.text((left_x + 230, tot_y + 16), f"人民币 {cert_data['total_chinese']}", font=bold_font, fill=(160, 20, 20))
    draw.text((left_x + 1240, tot_y + 16), "小写合计:", font=bold_font, fill=(40, 40, 40))
    draw.text((left_x + 1360, tot_y + 16), f"¥{total_tax:,.2f}", font=bold_font, fill=(160, 20, 20))
    
    foot_y = tot_y + item_row_h + 35
    draw.rectangle([left_x, foot_y, left_x + table_w, foot_y + 240], fill=(245, 247, 250), outline=(180, 190, 200), width=1)
    draw.text((left_x + 30, foot_y + 25), f"收款国库: {entity['treasury']}", font=bold_font, fill=(30, 30, 30))
    draw.text((left_x + 30, foot_y + 65), "缴税方式: 财税库银横向联网系统 (TIPS) 实时扣缴清算入库", font=text_font, fill=(50, 50, 50))
    draw.text((left_x + 30, foot_y + 105), f"银行扣款流水号: {cert_data['bank_flow_no']}", font=text_font, fill=(50, 50, 50))
    draw.text((left_x + 30, foot_y + 145), f"电子印章防伪校验码: SHA256:{random.randint(10000000, 99999999)}FE8821{cert_data['entity_code']}", font=small_font, fill=(100, 100, 100))
    draw.text((left_x + 30, foot_y + 185), "国家税务总局电子税收票证查验真伪二维码 [✔ 已通过全国电子税务局验签]", font=bold_font, fill=(20, 130, 40))
    
    stamp_pos = (w - 360, foot_y + 110)
    auth_short = entity['authority'].split("税务局")[0].replace("国家税务总局", "") + "税务局"
    draw_tax_oval_stamp(draw, "国家税务总局", auth_short, "电子征税专用章", center=stamp_pos, rx=130, ry=85, color=(210, 30, 30))
    
    img = apply_scanner_photocopy_texture(img, is_color=True, noise_level=10)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    img.save(filepath, quality=92)

def create_simulated_site_photo_jpg(filepath, project_name, location_str, desc_str, time_str=None):
    w, h = 1200, 900
    img = Image.new('RGB', (w, h), color=(140, 160, 180))
    draw = ImageDraw.Draw(img)
    
    for y in range(h):
        grad = int(100 + (y / h) * 120)
        draw.line([(0, y), (w, y)], fill=(grad - 20, grad, grad + 20))
        
    draw.rectangle([100, 400, 1100, 850], fill=(80, 85, 90))
    draw.rectangle([200, 200, 400, 850], fill=(160, 165, 170))
    draw.rectangle([500, 150, 750, 850], fill=(130, 135, 140))
    draw.rectangle([800, 300, 1050, 850], fill=(150, 155, 160))
    
    draw.line([(300, 80), (300, 200)], fill=(220, 40, 40), width=8)
    draw.line([(150, 100), (450, 100)], fill=(220, 40, 40), width=6)
    
    watermark_box_w = 460
    watermark_box_h = 240
    wx = 40
    wy = h - watermark_box_h - 40
    
    draw.rectangle([wx, wy, wx + watermark_box_w, wy + watermark_box_h], fill=(0, 0, 0, 160))
    
    title_font = ImageFont.truetype(FONT_PATH, 26)
    t_font = ImageFont.truetype(FONT_PATH, 20)
    
    cur_time = time_str or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    draw.text((wx + 20, wy + 15), "今日水印相机 · 施工工程影像取证", font=title_font, fill=(255, 200, 0))
    draw.line([(wx + 20, wy + 52), (wx + watermark_box_w - 20, wy + 52)], fill=(255, 255, 255), width=1)
    
    draw.text((wx + 20, wy + 65), f"工程名称: {project_name[:16]}", font=t_font, fill=(255, 255, 255))
    draw.text((wx + 20, wy + 100), f"拍摄地点: {location_str[:16]}", font=t_font, fill=(255, 255, 255))
    draw.text((wx + 20, wy + 135), f"拍摄时间: {cur_time}", font=t_font, fill=(255, 255, 255))
    draw.text((wx + 20, wy + 170), f"现场纪要: {desc_str[:22]}", font=t_font, fill=(220, 220, 220))
    draw.text((wx + 20, wy + 205), "防篡改验证: GPS经纬度及时间戳已校验入库", font=ImageFont.truetype(FONT_PATH, 16), fill=(100, 220, 100))
    
    img = apply_scanner_photocopy_texture(img, is_color=True, noise_level=5, contrast_boost=1.05)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    img.save(filepath, quality=90)

# ---------------------------------------------------------------------------
# PDF 文档生成器 (ReportLab)
# ---------------------------------------------------------------------------

def create_pdf_contract(filepath, title, contract_no, party_a_code, party_b_code, amount, terms, contract_date="2023-03-15"):
    doc = SimpleDocTemplate(filepath, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('CTitle', fontName=FONT_NAME, fontSize=18, leading=24, alignment=TA_CENTER, textColor=colors.HexColor('#1A1A1A'))
    head_style = ParagraphStyle('CHead', fontName=FONT_NAME, fontSize=11, leading=16, alignment=TA_LEFT, textColor=colors.HexColor('#333333'))
    body_style = ParagraphStyle('CBody', fontName=FONT_NAME, fontSize=10, leading=15, alignment=TA_JUSTIFY, textColor=colors.HexColor('#222222'))
    bold_style = ParagraphStyle('CBold', fontName=FONT_NAME, fontSize=10, leading=15, alignment=TA_LEFT, textColor=colors.HexColor('#111111'))
    
    party_a = get_entity_name(party_a_code)
    party_b = get_entity_name(party_b_code)
    
    elements = []
    elements.append(Paragraph(title, title_style))
    elements.append(Spacer(1, 10))
    elements.append(Paragraph(f"合同统一编号：<b>{contract_no}</b>", ParagraphStyle('CNo', fontName=FONT_NAME, fontSize=10, leading=14, alignment=TA_RIGHT, textColor=colors.HexColor('#666666'))))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#1A1A1A'), spaceBefore=5, spaceAfter=15))
    
    info_data = [
        [Paragraph(f"<b>发包人 (甲方)：</b>{party_a}", head_style), Paragraph(f"<b>纳税人识别号：</b>{ENTITIES.get(party_a_code, {}).get('tax_id', '-')}", head_style)],
        [Paragraph(f"<b>承包人 (乙方)：</b>{party_b}", head_style), Paragraph(f"<b>纳税人识别号：</b>{ENTITIES.get(party_b_code, {}).get('tax_id', '-')}", head_style)],
        [Paragraph(f"<b>签约日期：</b>{contract_date}", head_style), Paragraph(f"<b>签约地点：</b>四川省成都市", head_style)],
        [Paragraph(f"<b>合同暂定总价：</b>¥ {amount:,.2f} 元", ParagraphStyle('CAmt', fontName=FONT_NAME, fontSize=11, leading=16, textColor=colors.HexColor('#B22222'))),
         Paragraph(f"<b>大写：</b>人民币", head_style)]
    ]
    t = Table(info_data, colWidths=[260, 255])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F8F9FA')),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#D0D7DE')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E1E4E8')),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 15))
    
    elements.append(Paragraph("<b>第一条 工程概况与承包范围</b>", bold_style))
    elements.append(Paragraph(f"依据《中华人民共和国民法典》、《中华人民共和国建筑法》及有关法律法规，遵循平等、自愿、公平和诚实信用的原则，双方就本工程建设承包事项协商一致，订立本合同。", body_style))
    for idx, term in enumerate(terms, 1):
        elements.append(Paragraph(f"{idx}. {term}；", body_style))
    elements.append(Spacer(1, 10))
    
    elements.append(Paragraph("<b>第二条 计价方式与款项结算</b>", bold_style))
    elements.append(Paragraph(f"本合同价款采用固定单价/总价包干形式，合同签约总金额（含增值税）为：<b>¥ {amount:,.2f} 元</b>。甲方按月根据监理审定工程量及合规增值税专用发票拨付进度款。", body_style))
    elements.append(Spacer(1, 10))
    
    elements.append(Paragraph("<b>第三条 税务合规与发票交付</b>", bold_style))
    elements.append(Paragraph(f"乙方承诺依法纳税，并在收到款项或纳税义务发生时，向甲方足额开具符合国家税收法律法规的增值税专用发票；严禁虚开发票、挂靠走账及无真实货物/劳务交易的发票流转。", body_style))
    elements.append(Spacer(1, 15))
    
    sign_data = [
        [Paragraph(f"<b>甲方 (盖章)：</b><br/><br/>{party_a}<br/>法定代表人或授权代表：周维国<br/>开户行：{ENTITIES.get(party_a_code, {}).get('bank', '-')}<br/>账号：{ENTITIES.get(party_a_code, {}).get('acc', '-')}", head_style),
         Paragraph(f"<b>乙方 (盖章)：</b><br/><br/>{party_b}<br/>法定代表人或授权代表：张明辉<br/>开户行：{ENTITIES.get(party_b_code, {}).get('bank', '-')}<br/>账号：{ENTITIES.get(party_b_code, {}).get('acc', '-')}", head_style)]
    ]
    st = Table(sign_data, colWidths=[260, 255])
    st.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#999999')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CCCCCC')),
        ('TOPPADDING', (0,0), (-1,-1), 10),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
    ]))
    elements.append(st)
    
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    doc.build(elements)

def create_pdf_invoice(filepath, invoice_no, buyer_code, seller_code, items, is_paid=True, is_tax_settled=True, invoice_date="2024-05-20"):
    doc = SimpleDocTemplate(filepath, pagesize=A4, leftMargin=35, rightMargin=35, topMargin=35, bottomMargin=35)
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('ITitle', fontName=FONT_NAME, fontSize=16, leading=22, alignment=TA_CENTER, textColor=colors.HexColor('#8B0000'))
    head_style = ParagraphStyle('IHead', fontName=FONT_NAME, fontSize=9, leading=13, alignment=TA_LEFT, textColor=colors.HexColor('#222222'))
    
    buyer = ENTITIES.get(buyer_code, {'name': buyer_code, 'tax_id': '-', 'bank': '-', 'acc': '-'})
    seller = ENTITIES.get(seller_code, {'name': seller_code, 'tax_id': '-', 'bank': '-', 'acc': '-'})
    
    elements = []
    elements.append(Paragraph("四川增值税专用发票 (电子化查验件)", title_style))
    elements.append(Spacer(1, 8))
    
    sub_head = [
        [Paragraph(f"发票代码：051002400111", head_style), Paragraph(f"发票号码：<b>{invoice_no}</b>", head_style),
         Paragraph(f"开票日期：{invoice_date}", head_style), Paragraph(f"校验码：89120 48291 00392", head_style)]
    ]
    sht = Table(sub_head, colWidths=[130, 130, 130, 135])
    sht.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'MIDDLE')]))
    elements.append(sht)
    elements.append(Spacer(1, 5))
    
    party_data = [
        [Paragraph("购买方", head_style),
         Paragraph(f"名称：{buyer['name']}<br/>纳税人识别号：{buyer['tax_id']}<br/>地址、电话：成都市高新区锦悦西路56号 028-85987123<br/>开户行及账号：{buyer['bank']} {buyer['acc']}", head_style),
         Paragraph("密码区", head_style),
         Paragraph("&lt;&gt;1892+48/921*&lt;&gt;<br/>489102-481920*881<br/>/9201948192004*81<br/>&lt;&gt;891240182901*&lt;&gt;", head_style)]
    ]
    pt = Table(party_data, colWidths=[45, 220, 45, 215])
    pt.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#8B0000')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#8B0000')),
        ('BACKGROUND', (0,0), (0,0), colors.HexColor('#FFF5F5')),
        ('BACKGROUND', (2,0), (2,0), colors.HexColor('#FFF5F5')),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    elements.append(pt)
    elements.append(Spacer(1, 5))
    
    item_rows = [[Paragraph("货物或应税劳务、服务名称", head_style), Paragraph("规格型号", head_style), Paragraph("单位", head_style), Paragraph("数量", head_style), Paragraph("单价(不含税)", head_style), Paragraph("金额(不含税)", head_style), Paragraph("税率", head_style), Paragraph("税额", head_style)]]
    total_amount = Decimal("0")
    total_tax = Decimal("0")
    
    for itm in items:
        name, spec, unit, qty, price, amt, rate, tax = itm
        total_amount += Decimal(str(amt))
        total_tax += Decimal(str(tax))
        item_rows.append([
            Paragraph(str(name), head_style), Paragraph(str(spec), head_style), Paragraph(str(unit), head_style),
            Paragraph(f"{qty:,.2f}" if isinstance(qty, (int, float)) else str(qty), head_style),
            Paragraph(f"¥{price:,.2f}" if isinstance(price, (int, float)) else str(price), head_style),
            Paragraph(f"¥{amt:,.2f}", head_style), Paragraph(f"{rate*100:.0f}%", head_style), Paragraph(f"¥{tax:,.2f}", head_style)
        ])
        
    it = Table(item_rows, colWidths=[125, 45, 30, 45, 70, 80, 40, 90])
    it.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#8B0000')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#8B0000')),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#FFF5F5')),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    elements.append(it)
    elements.append(Spacer(1, 5))
    
    total_all = total_amount + total_tax
    tot_data = [
        [Paragraph("价税合计 (大写)", head_style),
         Paragraph(f"人民币 <b>{total_all:,.2f} 元</b>", ParagraphStyle('TotB', fontName=FONT_NAME, fontSize=10, leading=14, textColor=colors.HexColor('#8B0000'))),
         Paragraph(f"(小写) ¥ {total_all:,.2f}", ParagraphStyle('TotS', fontName=FONT_NAME, fontSize=10, leading=14, textColor=colors.HexColor('#8B0000')))]
    ]
    tt = Table(tot_data, colWidths=[90, 275, 160])
    tt.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#8B0000')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#8B0000')),
        ('BACKGROUND', (0,0), (0,0), colors.HexColor('#FFF5F5')),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    elements.append(tt)
    elements.append(Spacer(1, 5))
    
    seller_data = [
        [Paragraph("销售方", head_style),
         Paragraph(f"名称：{seller['name']}<br/>纳税人识别号：{seller['tax_id']}<br/>地址、电话：成都市金牛区蜀西路48号 028-87512999<br/>开户行及账号：{seller['bank']} {seller['acc']}", head_style),
         Paragraph("备注", head_style),
         Paragraph(f"项目结算款发票。四流一致真实凭据。<br/>税控码: 510100981920839102<br/>付款状态: {'【已结算支付】' if is_paid else '【挂账应付中】'}<br/>完税抵扣: {'【已申报纳税与抵扣】' if is_tax_settled else '【待申报抵扣】'}", head_style)]
    ]
    st = Table(seller_data, colWidths=[45, 220, 45, 215])
    st.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#8B0000')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#8B0000')),
        ('BACKGROUND', (0,0), (0,0), colors.HexColor('#FFF5F5')),
        ('BACKGROUND', (2,0), (2,0), colors.HexColor('#FFF5F5')),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    elements.append(st)
    
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    doc.build(elements)

def create_pdf_tax_certificate(filepath, title, cert_data, entity):
    doc = SimpleDocTemplate(filepath, pagesize=A4, leftMargin=35, rightMargin=35, topMargin=35, bottomMargin=35)
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('TTitle', fontName=FONT_NAME, fontSize=18, leading=24, alignment=TA_CENTER, textColor=colors.HexColor('#8B0000'))
    sub_style = ParagraphStyle('TSub', fontName=FONT_NAME, fontSize=12, leading=16, alignment=TA_CENTER, textColor=colors.HexColor('#333333'))
    head_style = ParagraphStyle('THead', fontName=FONT_NAME, fontSize=9, leading=13, alignment=TA_LEFT, textColor=colors.HexColor('#222222'))
    bold_style = ParagraphStyle('TBold', fontName=FONT_NAME, fontSize=9, leading=13, alignment=TA_LEFT, textColor=colors.HexColor('#111111'))
    
    elements = []
    elements.append(Paragraph("国家税务总局 电子税收完税证明", title_style))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(f"（{title}）", sub_style))
    elements.append(Spacer(1, 10))
    
    meta_data = [
        [Paragraph(f"<b>填发日期：</b>{cert_data['payment_date']}", head_style),
         Paragraph(f"<b>税票号码：</b>{cert_data['tax_ticket_no']}", head_style),
         Paragraph(f"<b>电子凭证号：</b><font color='#8B0000'>{cert_data['receipt_no']}</font>", head_style)]
    ]
    mt = Table(meta_data, colWidths=[175, 175, 175])
    elements.append(mt)
    elements.append(Spacer(1, 6))
    
    taxpayer_data = [
        [Paragraph(f"<b>纳税人名称：</b>{entity['name']}", head_style), Paragraph(f"<b>纳税人识别号：</b>{entity['tax_id']}", head_style)],
        [Paragraph(f"<b>主管税务机关：</b>{entity['authority']}", head_style), Paragraph(f"<b>开户银行：</b>{entity['bank']} ({entity['acc'][:6]}****{entity['acc'][-4:]})", head_style)]
    ]
    tt = Table(taxpayer_data, colWidths=[260, 265])
    tt.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F4F7FB')),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#C0D0E0')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#D8E4F0')),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    elements.append(tt)
    elements.append(Spacer(1, 8))
    
    item_rows = [[Paragraph("原凭证号", bold_style), Paragraph("税种", bold_style), Paragraph("品目名称", bold_style), Paragraph("税款所属时期", bold_style), Paragraph("计税依据", bold_style), Paragraph("税率", bold_style), Paragraph("实缴金额(元)", bold_style)]]
    total_tax = Decimal("0")
    for itm in cert_data['items']:
        amt = Decimal(str(itm['amount']))
        total_tax += amt
        item_rows.append([
            Paragraph(str(itm['orig_no']), head_style), Paragraph(str(itm['tax_type']), bold_style),
            Paragraph(str(itm['category'])[:12], head_style), Paragraph(str(itm['period_range']), head_style),
            Paragraph(f"¥{itm.get('tax_base', 0):,.0f}", head_style), Paragraph(str(itm['rate']), head_style),
            Paragraph(f"¥{amt:,.2f}", bold_style)
        ])
        
    it = Table(item_rows, colWidths=[70, 75, 95, 115, 75, 40, 55])
    it.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#C0D0E0')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#D8E4F0')),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#EBF2F8')),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    elements.append(it)
    elements.append(Spacer(1, 6))
    
    tot_data = [
        [Paragraph("合计金额 (大写)", bold_style), Paragraph(f"人民币 <font color='#8B0000'><b>{cert_data['total_chinese']}</b></font>", bold_style), Paragraph(f"小写合计：<font color='#8B0000'><b>¥ {total_tax:,.2f}</b></font>", bold_style)]
    ]
    ttot = Table(tot_data, colWidths=[90, 275, 160])
    ttot.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#FFFDF0')),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#E8D880')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E8D880')),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    elements.append(ttot)
    elements.append(Spacer(1, 10))
    
    foot_data = [
        [Paragraph(f"<b>收款国库：</b>{entity['treasury']}<br/><b>缴税方式：</b>财税库银横向联网系统 (TIPS) 实时扣缴清算入库<br/><b>银行扣款流水号：</b>{cert_data['bank_flow_no']}<br/><b>电子印章校验：</b>国家税务总局全国统一电子税票验签一致 ✔", head_style)]
    ]
    ft = Table(foot_data, colWidths=[525])
    ft.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F8F9FA')),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#E0E0E0')),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    elements.append(ft)
    
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    doc.build(elements)

def create_pdf_bank_receipt(filepath, bank_no, payer_code, payee_code, amount, usage_desc, pay_date="2024-06-18"):
    doc = SimpleDocTemplate(filepath, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('BTitle', fontName=FONT_NAME, fontSize=16, leading=22, alignment=TA_CENTER, textColor=colors.HexColor('#003366'))
    head_style = ParagraphStyle('BHead', fontName=FONT_NAME, fontSize=9, leading=14, alignment=TA_LEFT, textColor=colors.HexColor('#222222'))
    
    payer = ENTITIES.get(payer_code, {'name': payer_code, 'bank': '招商银行成都分行', 'acc': '510000001'})
    payee = ENTITIES.get(payee_code, {'name': payee_code, 'bank': '中国建设银行成都分行', 'acc': '510000002'})
    
    elements = []
    elements.append(Paragraph("中国建设银行 / 招商银行 电子业务专用付款回单", title_style))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(f"回单编号：<b>{bank_no}</b> &nbsp;&nbsp;|&nbsp;&nbsp; 打印时间：{pay_date} 16:20:11 &nbsp;&nbsp;|&nbsp;&nbsp; 交易渠道：企业网银对公转账 (CNAPS)", ParagraphStyle('BSub', fontName=FONT_NAME, fontSize=9, leading=13, alignment=TA_CENTER, textColor=colors.HexColor('#666666'))))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#003366'), spaceBefore=5, spaceAfter=12))
    
    flow_data = [
        [Paragraph(f"<b>付款人全称：</b>{payer['name']}", head_style), Paragraph(f"<b>收款人全称：</b>{payee['name']}", head_style)],
        [Paragraph(f"<b>付款人账号：</b>{payer['acc']}", head_style), Paragraph(f"<b>收款人账号：</b>{payee['acc']}", head_style)],
        [Paragraph(f"<b>付款开户行：</b>{payer['bank']}", head_style), Paragraph(f"<b>收款开户行：</b>{payee['bank']}", head_style)],
        [Paragraph(f"<b>转账金额：</b><font color='#B22222'><b>¥ {amount:,.2f} 元</b></font>", head_style), Paragraph(f"<b>交易币种：</b>人民币 (CNY)", head_style)],
        [Paragraph(f"<b>款项用途/摘要：</b>{usage_desc}", head_style), Paragraph("<b>交易处理状态：</b><font color='#008000'><b>转账成功 · 已实时清算入账</b></font>", head_style)]
    ]
    bt = Table(flow_data, colWidths=[255, 260])
    bt.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F4F8FC')),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#B8D0E8')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#D0E0F0')),
        ('TOPPADDING', (0,0), (-1,-1), 7),
        ('BOTTOMPADDING', (0,0), (-1,-1), 7),
    ]))
    elements.append(bt)
    elements.append(Spacer(1, 15))
    elements.append(Paragraph("<b>经办说明：</b>本电子回单为网银系统直联开具，具备电子验签与防伪识别码；资金流直接由合同付款方对公电汇至合同收款方账号，与合同流、发票流、物流严格四流匹配。", ParagraphStyle('BNote', fontName=FONT_NAME, fontSize=8, leading=12, textColor=colors.HexColor('#888888'))))
    
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    doc.build(elements)

def create_pdf_weighbridge_logistics_sheet(filepath, project_name, supplier_code, receiver_code, mat_name, tonnage, truck_cnt, total_amount, logistics_date="2024-04-10"):
    doc = SimpleDocTemplate(filepath, pagesize=A4, leftMargin=35, rightMargin=35, topMargin=35, bottomMargin=35)
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('WTitle', fontName=FONT_NAME, fontSize=16, leading=22, alignment=TA_CENTER, textColor=colors.HexColor('#222222'))
    head_style = ParagraphStyle('WHead', fontName=FONT_NAME, fontSize=9, leading=13, alignment=TA_LEFT, textColor=colors.HexColor('#222222'))
    bold_style = ParagraphStyle('WBold', fontName=FONT_NAME, fontSize=9, leading=13, alignment=TA_LEFT, textColor=colors.HexColor('#111111'))
    
    supplier = ENTITIES.get(supplier_code, {'name': supplier_code})
    receiver = ENTITIES.get(receiver_code, {'name': receiver_code})
    
    elements = []
    elements.append(Paragraph("施工现场智能汽车衡 大宗物资过磅验收汇总单", title_style))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(f"项目名称：<b>{project_name}</b> &nbsp;&nbsp;|&nbsp;&nbsp; 统计周期：{logistics_date} 期间进场", ParagraphStyle('WSub', fontName=FONT_NAME, fontSize=9, leading=13, alignment=TA_CENTER, textColor=colors.HexColor('#555555'))))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#333333'), spaceBefore=5, spaceAfter=10))
    
    base_data = [
        [Paragraph(f"<b>供货单位：</b>{supplier['name']}", head_style), Paragraph(f"<b>收货料场：</b>{receiver['name']} 现场仓储区", head_style)],
        [Paragraph(f"<b>材料物资品名：</b>{mat_name}", head_style), Paragraph(f"<b>累计车次：</b>{truck_cnt} 车次 (重车/空车双向称重)", head_style)],
        [Paragraph(f"<b>累计净重总计：</b><font color='#B22222'><b>{tonnage:,.2f} 吨</b></font>", head_style), Paragraph(f"<b>核算货款总额：</b>¥ {total_amount:,.2f} 元", head_style)]
    ]
    t = Table(base_data, colWidths=[260, 265])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F8F9FA')),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#CCCCCC')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E0E0E0')),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 8))
    
    sample_rows = [[Paragraph("过磅单号", bold_style), Paragraph("车牌号码", bold_style), Paragraph("毛重(吨)", bold_style), Paragraph("皮重(吨)", bold_style), Paragraph("净重(吨)", bold_style), Paragraph("进场过磅时间", bold_style), Paragraph("质检结论", bold_style)]]
    avg_t = tonnage / max(truck_cnt, 1)
    for i in range(1, min(truck_cnt + 1, 9)):
        tare = random.uniform(12.5, 15.2)
        net = avg_t * random.uniform(0.92, 1.08)
        gross = tare + net
        sample_rows.append([
            Paragraph(f"PB-{logistics_date.replace('-','')}{i:03d}", head_style),
            Paragraph(f"川A·8{random.randint(100,999)}挂", head_style),
            Paragraph(f"{gross:.2f}", head_style), Paragraph(f"{tare:.2f}", head_style),
            Paragraph(f"{net:.2f}", bold_style), Paragraph(f"{logistics_date} {random.randint(8,17):02d}:{random.randint(10,55):02d}", head_style),
            Paragraph("合格 入库", ParagraphStyle('WPass', fontName=FONT_NAME, fontSize=8, leading=11, textColor=colors.HexColor('#008000')))
        ])
    if truck_cnt > 8:
        sample_rows.append([Paragraph("... (其余车次略)", head_style), Paragraph("...", head_style), Paragraph("...", head_style), Paragraph("...", head_style), Paragraph(f"累计 {tonnage:,.2f}", bold_style), Paragraph("全程红外抓拍", head_style), Paragraph("全部合格", head_style)])
        
    st = Table(sample_rows, colWidths=[90, 75, 60, 60, 60, 110, 70])
    st.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#CCCCCC')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E0E0E0')),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#EEEEEE')),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    elements.append(st)
    elements.append(Spacer(1, 10))
    elements.append(Paragraph("<b>地磅管理说明：</b>地磅系统与集团数字化供应链实时联网，具备车牌识别、红外定位防作弊及实时称重摄像功能，称重小票已全量归档。", ParagraphStyle('WNote', fontName=FONT_NAME, fontSize=8, leading=12, textColor=colors.HexColor('#777777'))))
    
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    doc.build(elements)


# ---------------------------------------------------------------------------
# 项目 02 与 03 全套配置
# ---------------------------------------------------------------------------

PROJECTS_DATA = [
    # =========================================================================
    # 02 项目: 成渝跨江特大桥及连接线 (CY-CQ-002)
    # =========================================================================
    {
        'dir_name': '02_成渝跨江特大桥及连接线_CY-CQ-002',
        'code': 'CY-CQ-002',
        'name': '成渝双城经济圈跨江特大桥及连接线工程',
        'main_client': 'EXT-CY',
        'main_contractor': 'A03',
        'main_amount': 880_000_000,
        'city': '重庆市',
        'main_contract_no': 'CYCQ-MAIN-2023-02',
        'contracts': [
            # 1. 施工总承包主合同
            {
                'title': '跨江特大桥基础设施工程施工总承包合同',
                'seller': 'A03', 'buyer': 'EXT-CY',
                'cat_dir': '01_主合同与发包批文',
                'cat': '建筑工程施工总承包',
                'amount': 880_000_000,
                'no': 'CYCQ-MAIN-2023-02',
                'date': '2023-03-20',
                'terms': [
                    '跨江特大桥双塔双索面斜拉桥主桥及跨江引桥工程施工',
                    '跨省跨区工程（重庆段就地预缴2%增值税，四川机构汇总申报）',
                    '川渝两地企业所得税按三因素法分摊测算，进度款按月申报80%支付'
                ]
            },
            # 2. 专业分包 (A04 屹明汇重庆分公司)
            {
                'title': '特大桥重庆江北侧引桥及互通立交分包工程合同',
                'seller': 'A04', 'buyer': 'A03',
                'cat_dir': '02_专业分包与施工合同',
                'cat': '专业分包',
                'amount': 120_000_000,
                'no': 'CY-A03-A04',
                'date': '2023-04-15',
                'terms': ['重庆江北侧引桥现浇箱梁与互通立交道路工程施工', '增值税税率9%']
            },
            # 3. 劳务用工 (C02 四川灏琅建筑劳务)
            {
                'title': '高空索塔及深水基础泥瓦特种作业劳务合同',
                'seller': 'C02', 'buyer': 'A03',
                'cat_dir': '03_劳务用工与用工结算',
                'cat': '劳务用工',
                'amount': 140_000_000,
                'no': 'CY-A03-C02',
                'date': '2023-04-18',
                'terms': ['特种高处作业及水上沉井施工班组实名制考勤代发', '税率9%']
            },
            # 4. 系统内材料采购 (B10 重庆朗德乾润商贸)
            {
                'title': '特种耐候桥梁高强厚板钢材供销合同',
                'seller': 'B10', 'buyer': 'A03',
                'cat_dir': '04_大宗材料采购与供销合同',
                'cat': '材料采购',
                'amount': 320_000_000,
                'no': 'CY-A03-B10',
                'date': '2023-04-25',
                'terms': ['Q345qD桥梁专用耐候钢板及预应力高强钢绞线供销', '税率13%']
            },
            # 5. 系统外材料直采 (EXT-XN-CONCRETE 西南特种商砼)
            {
                'title': '外部高强度水下抗冲刷特种混凝土直供合同',
                'seller': 'EXT-XN-CONCRETE', 'buyer': 'A03',
                'cat_dir': '04_大宗材料采购与供销合同',
                'cat': '材料采购',
                'amount': 60_000_000,
                'no': 'CY-A03-EXT-CONC',
                'date': '2023-05-10',
                'terms': ['C50水下自密实防腐抗冲刷特种商砼供货及泵送', '税率13%']
            },
            # 6. 系统内机械设备租赁 (D02 四川乾诺机械租赁)
            {
                'title': '重型水上浮吊与大直径旋挖桩机设备租赁合同',
                'seller': 'D02', 'buyer': 'A03',
                'cat_dir': '05_机械设备租赁与台班签证',
                'cat': '机械租赁',
                'amount': 80_000_000,
                'no': 'CY-A03-D02',
                'date': '2023-05-15',
                'terms': ['500吨级打桩浮吊及水上驳船作业组租用与维保', '纯租赁税率13%']
            },
            # 7. 系统外特种租赁 (EXT-CQ-HEAVY-CRANE 重庆重交大件起重)
            {
                'title': '外部深水作业工程潜水与水上航道保障租赁合同',
                'seller': 'EXT-CQ-HEAVY-CRANE', 'buyer': 'A03',
                'cat_dir': '05_机械设备租赁与台班签证',
                'cat': '机械租赁',
                'amount': 18_000_000,
                'no': 'CY-A03-EXT-SHIP',
                'date': '2023-06-05',
                'terms': ['长江主航道通航安全警示驳船与深水潜水机具租用', '税率13%']
            },
        ],
        # 完税证明与税收票证数据集
        'tax_certs': [
            {
                'entity_code': 'A03',
                'doc_name': 'TAX_CERT_CY-A03_2023Q1_跨江特大桥主合同印花税完税凭证',
                'receipt_no': '5101042300018920',
                'tax_ticket_no': '351014230100018920',
                'payment_date': '2023年03月28日',
                'period': '2023-03',
                'total_chinese': '贰拾陆万肆仟元整',
                'bank_flow_no': 'TIPS2023032888192001',
                'note': '成渝跨江特大桥施工总承包主合同印花税申报缴纳',
                'items': [
                    {'orig_no': '3510142301', 'tax_type': '印花税', 'category': '建设工程施工总承包合同', 'period_range': '2023-03-01 至 2023-03-31', 'tax_base': 880000000.00, 'rate': '0.03%', 'amount': 264000.00}
                ]
            },
            {
                'entity_code': 'A04',
                'doc_name': 'TAX_CERT_CY-A04_2023-2025_重庆段跨区域增值税就地预缴完税证明',
                'receipt_no': '5001052400088921',
                'tax_ticket_no': '350015240600028921',
                'payment_date': '2024年07月10日',
                'period': '2024-06',
                'total_chinese': '壹仟壹佰贰拾万元整',
                'bank_flow_no': 'TIPS2024071099281002',
                'note': '成渝特大桥重庆江北段跨区域施工就地预缴增值税及城建附加',
                'items': [
                    {'orig_no': '3500152401', 'tax_type': '增值税', 'category': '异地工程跨区就地预缴(2%)', 'period_range': '2024-01-01 至 2024-06-30', 'tax_base': 500000000.00, 'rate': '2%', 'amount': 10000000.00},
                    {'orig_no': '3500152402', 'tax_type': '城市维护建设税', 'category': '就地预缴城建税(7%)', 'period_range': '2024-01-01 至 2024-06-30', 'tax_base': 10000000.00, 'rate': '7%', 'amount': 700000.00},
                    {'orig_no': '3500152403', 'tax_type': '教育费附加', 'category': '增值税就地附加(3%)', 'period_range': '2024-01-01 至 2024-06-30', 'tax_base': 10000000.00, 'rate': '3%', 'amount': 300000.00},
                    {'orig_no': '3500152404', 'tax_type': '地方教育附加', 'category': '地方教育附加(2%)', 'period_range': '2024-01-01 至 2024-06-30', 'tax_base': 10000000.00, 'rate': '2%', 'amount': 200000.00},
                ]
            },
            {
                'entity_code': 'B10',
                'doc_name': 'TAX_CERT_CY-B10_2024Q3_桥梁耐候高强钢销售增值税完税证明',
                'receipt_no': '5001092400038923',
                'tax_ticket_no': '350019240900038923',
                'payment_date': '2024年10月15日',
                'period': '2024-09',
                'total_chinese': '肆佰壹拾陆万元整',
                'bank_flow_no': 'TIPS2024101588392003',
                'note': '特种耐候钢板供应结算销项税额申报纳税',
                'items': [
                    {'orig_no': '3500192401', 'tax_type': '增值税', 'category': '钢材物资销售(13%)', 'period_range': '2024-07-01 至 2024-09-30', 'tax_base': 32000000.00, 'rate': '13%', 'amount': 4160000.00}
                ]
            },
            {
                'entity_code': 'A03',
                'doc_name': 'TAX_CERT_CY-SOC-SEC_2023-2025_成渝特大桥社保公积金核定完税证明',
                'receipt_no': '5101042500099988',
                'tax_ticket_no': '351014251200099988',
                'payment_date': '2025年12月20日',
                'period': '2025-12',
                'total_chinese': '壹佰伍拾捌万元整',
                'bank_flow_no': 'TIPS2025122099381005',
                'note': '特大桥项目全员五险一金及劳务代扣社保统一征缴入库',
                'items': [
                    {'orig_no': '3510142501', 'tax_type': '社会保险费', 'category': '城镇职工基本养老保险', 'period_range': '2023-01-01 至 2025-12-31', 'tax_base': 6800000.00, 'rate': '16%', 'amount': 1088000.00},
                    {'orig_no': '3510142502', 'tax_type': '社会保险费', 'category': '城镇职工基本医疗保险', 'period_range': '2023-01-01 至 2025-12-31', 'tax_base': 6800000.00, 'rate': '7.5%', 'amount': 510000.00},
                ]
            },
            {
                'entity_code': 'A03',
                'doc_name': 'TAX_CERT_CY-MGT_2023-2025_参建单位管理员工资个税完税证明',
                'receipt_no': '5101042500077766',
                'tax_ticket_no': '351014251200077766',
                'payment_date': '2025年12月25日',
                'period': '2025-12',
                'total_chinese': '柒拾贰万元整',
                'bank_flow_no': 'TIPS2025122588192006',
                'note': '特大桥项目部管理层及技术骨干个人所得税代扣代缴',
                'items': [
                    {'orig_no': '3510142505', 'tax_type': '个人所得税', 'category': '工资薪金所得(累计代扣)', 'period_range': '2023-01-01 至 2025-12-31', 'tax_base': 7200000.00, 'rate': '超额累进', 'amount': 720000.00}
                ]
            }
        ]
    },

    # =========================================================================
    # 03 项目: 广元利州产城融合与河道治理 (GY-LZ-003)
    # =========================================================================
    {
        'dir_name': '03_广元利州产城融合与河道治理_GY-LZ-003',
        'code': 'GY-LZ-003',
        'name': '广元利州产城融合与生态河道综合治理工程',
        'main_client': 'EXT-GY',
        'main_contractor': 'A10',
        'main_amount': 360_000_000,
        'city': '广元市',
        'main_contract_no': 'GYLZ-MAIN-2023-03',
        'contracts': [
            # 1. 施工总承包主合同
            {
                'title': '水利水运与生态河道综合整治总承包合同',
                'seller': 'A10', 'buyer': 'EXT-GY',
                'cat_dir': '01_主合同与发包批文',
                'cat': '建筑工程施工总承包',
                'amount': 360_000_000,
                'no': 'GYLZ-MAIN-2023-03',
                'date': '2023-02-18',
                'terms': [
                    '利州区南河及嘉陵江支流河道清淤疏浚、防洪堤防工程与生态景观绿化',
                    '进度款按月计量审定80%支付，增值税税率9%'
                ]
            },
            # 2. 专业分包 (A09 顺程源土石方)
            {
                'title': '土石方开挖转运与边坡柔性防护专业分包合同',
                'seller': 'A09', 'buyer': 'A10',
                'cat_dir': '02_专业分包与施工合同',
                'cat': '专业分包',
                'amount': 60_000_000,
                'no': 'GY-A10-A09',
                'date': '2023-03-10',
                'terms': ['南河两岸边坡生态加固与35万方土石方挖填平衡转运', '税率9%']
            },
            # 3. 劳务用工 (C01 四川本盛劳务)
            {
                'title': '水利河道清淤开挖及边坡加固劳务用工合同',
                'seller': 'C01', 'buyer': 'A10',
                'cat_dir': '03_劳务用工与用工结算',
                'cat': '劳务用工',
                'amount': 45_000_000,
                'no': 'GY-A10-C01',
                'date': '2023-03-15',
                'terms': ['水利清淤及挡土墙砌筑班组实名代发工资', '税率9%']
            },
            # 4. 系统内材料采购 (B05 广元玖硕商贸)
            {
                'title': '水利工程专用级配砂石骨料地磅集采合同',
                'seller': 'B05', 'buyer': 'A10',
                'cat_dir': '04_大宗材料采购与供销合同',
                'cat': '材料采购',
                'amount': 130_000_000,
                'no': 'GY-A10-B05',
                'date': '2023-03-25',
                'terms': ['广元本地优质天然砂石、级配碎石供应，全车过磅验收', '税率13%']
            },
            # 5. 系统外材料采购 (EXT-GY-FOREST 广元利州林木合作社)
            {
                'title': '外部生态景观植被与水土保持苗木直采合同',
                'seller': 'EXT-GY-FOREST', 'buyer': 'A10',
                'cat_dir': '04_大宗材料采购与供销合同',
                'cat': '材料采购',
                'amount': 15_000_000,
                'no': 'GY-A10-EXT-TREE',
                'date': '2023-04-10',
                'terms': ['生态护坡水生植物及利州区本地景观绿化乔木苗木供销', '税率9%']
            },
            # 6. 系统外材料辅材 (B06 广州采云广告)
            {
                'title': '文明施工定型化标识标牌与围挡采购合同',
                'seller': 'B06', 'buyer': 'A10',
                'cat_dir': '04_大宗材料采购与供销合同',
                'cat': '材料采购',
                'amount': 8_000_000,
                'no': 'GY-A10-B06',
                'date': '2023-04-15',
                'terms': ['水利安全警示标牌、装配式连续围挡与文明施工宣传设施', '税率13%']
            },
            # 7. 系统内机械设备租赁 (D03 四川惠润农业/水利设备)
            {
                'title': '水利清淤抽沙绞吸船与大型挖掘机租赁合同',
                'seller': 'D03', 'buyer': 'A10',
                'cat_dir': '05_机械设备租赁与台班签证',
                'cat': '机械租赁',
                'amount': 38_000_000,
                'no': 'GY-A10-D03',
                'date': '2023-04-20',
                'terms': ['环保绞吸式清淤船、长臂水陆两用挖掘机及排泥管线租赁', '税率13%']
            },
        ],
        # 完税证明与税收票证数据集
        'tax_certs': [
            {
                'entity_code': 'A10',
                'doc_name': 'TAX_CERT_GY-A10_2023Q1_水利整治主合同印花税完税证明',
                'receipt_no': '5108022300018930',
                'tax_ticket_no': '351082230100018930',
                'payment_date': '2023年02月28日',
                'period': '2023-02',
                'total_chinese': '壹拾万零捌仟元整',
                'bank_flow_no': 'TIPS2023022888193001',
                'note': '广元河道水利整治总承包合同印花税申报缴纳',
                'items': [
                    {'orig_no': '3510822301', 'tax_type': '印花税', 'category': '建设工程施工总承包合同', 'period_range': '2023-02-01 至 2023-02-28', 'tax_base': 360000000.00, 'rate': '0.03%', 'amount': 108000.00}
                ]
            },
            {
                'entity_code': 'A10',
                'doc_name': 'TAX_CERT_GY-A10_2023Q3_河道清淤与扬尘环境保护税完税证明',
                'receipt_no': '5108022300048931',
                'tax_ticket_no': '351082230900048931',
                'payment_date': '2023年10月12日',
                'period': '2023-09',
                'total_chinese': '肆万伍仟元整',
                'bank_flow_no': 'TIPS2023101299283002',
                'note': '水利河道清淤现场扬尘与水污染物环境保护税季度申报',
                'items': [
                    {'orig_no': '3510822305', 'tax_type': '环境保护税', 'category': '施工扬尘与水利排污环保税', 'period_range': '2023-07-01 至 2023-09-30', 'tax_base': 45000.00, 'rate': '定额核定', 'amount': 45000.00}
                ]
            },
            {
                'entity_code': 'B05',
                'doc_name': 'TAX_CERT_GY-B05_2024Q2_级配砂石销售增值税完税证明',
                'receipt_no': '5108022400028932',
                'tax_ticket_no': '351082240600028932',
                'payment_date': '2024年07月15日',
                'period': '2024-06',
                'total_chinese': '壹佰陆拾玖万元整',
                'bank_flow_no': 'TIPS2024071588393003',
                'note': '天然砂石骨料大宗供销销项增值税及附加税费实缴',
                'items': [
                    {'orig_no': '3510822401', 'tax_type': '增值税', 'category': '砂石建材销售(13%)', 'period_range': '2024-04-01 至 2024-06-30', 'tax_base': 13000000.00, 'rate': '13%', 'amount': 1690000.00}
                ]
            },
            {
                'entity_code': 'A10',
                'doc_name': 'TAX_CERT_GY-SOC-SEC_2023-2025_广元利州项目社保公积金统缴完税证明',
                'receipt_no': '5108022500099977',
                'tax_ticket_no': '351082251200099977',
                'payment_date': '2025年12月20日',
                'period': '2025-12',
                'total_chinese': '捌拾伍万元整',
                'bank_flow_no': 'TIPS2025122099383005',
                'note': '广元水利项目现场管理及作业人员五险一金统一缴纳',
                'items': [
                    {'orig_no': '3510822501', 'tax_type': '社会保险费', 'category': '城镇职工基本养老及医疗保险', 'period_range': '2023-01-01 至 2025-12-31', 'tax_base': 3600000.00, 'rate': '23.5%', 'amount': 850000.00}
                ]
            },
            {
                'entity_code': 'A10',
                'doc_name': 'TAX_CERT_GY-MGT_2023-2025_广元项目部人员工资个税完税凭证',
                'receipt_no': '5108022500088866',
                'tax_ticket_no': '351082251200088866',
                'payment_date': '2025年12月25日',
                'period': '2025-12',
                'total_chinese': '肆拾贰万元整',
                'bank_flow_no': 'TIPS2025122588193006',
                'note': '水利治理工程管理人员工资薪金所得税代扣代缴',
                'items': [
                    {'orig_no': '3510822505', 'tax_type': '个人所得税', 'category': '工资薪金所得(代扣代缴)', 'period_range': '2023-01-01 至 2025-12-31', 'tax_base': 4200000.00, 'rate': '超额累进', 'amount': 420000.00}
                ]
            }
        ]
    }
]


# ---------------------------------------------------------------------------
# 执行全量生成
# ---------------------------------------------------------------------------

def generate_projects_02_and_03():
    print("=" * 80)
    print("  🚀 开始为【02 成渝跨江特大桥】与【03 广元利州水利工程】生成全套高保真归档资料...")
    print("=" * 80)
    
    total_files = 0
    
    for p in PROJECTS_DATA:
        p_dir = os.path.join(BASE_OUT_DIR, p['dir_name'])
        os.makedirs(p_dir, exist_ok=True)
        print(f"\n📂 正在生成项目: {p['name']} ({p['code']}) -> {p_dir}")
        
        # 确保 10 个标准目录就绪
        categories = [
            '01_主合同与发包批文',
            '02_专业分包与施工合同',
            '03_劳务用工与用工结算',
            '04_大宗材料采购与供销合同',
            '05_机械设备租赁与台班签证',
            '06_增值税发票与税务完税凭证',
            '07_资金结算与银行电子回单',
            '08_物资物流地磅单与入库验收',
            '09_现场实景照片与复印件影印本',
            '10_模拟测试资料'
        ]
        cat_dirs = {c: os.path.join(p_dir, c) for c in categories}
        for d in cat_dirs.values():
            os.makedirs(d, exist_ok=True)
            
        # 1. 生成主合同与中标通知书
        main_c = p['contracts'][0]
        main_pdf = os.path.join(cat_dirs['01_主合同与发包批文'], f"{main_c['no']}_建设工程施工总承包主合同.pdf")
        create_pdf_contract(
            main_pdf, main_c['title'], main_c['no'],
            main_c['buyer'], main_c['seller'],
            main_c['amount'], main_c['terms'], contract_date=main_c['date']
        )
        total_files += 1
        
        main_jpg = os.path.join(cat_dirs['01_主合同与发包批文'], f"{main_c['no']}_中标通知书与履约保函_复印扫描件.jpg")
        create_simulated_scan_jpg(
            main_jpg,
            "建设工程 中标通知书与银行履约保函 (核验备案件)",
            [
                ("工程项目名称", p['name']),
                ("招标人 / 发包方", get_entity_name(main_c['buyer'])),
                ("中标人 / 承包方", get_entity_name(main_c['seller'])),
                ("中标金额 (合同总价)", f"¥ {main_c['amount']:,.2f} 元"),
                ("中标工期", "720 日历天"),
                ("履约担保方式", "中国银行无条件见索即付银行保函 (担保金额10%)"),
                ("发包批文核准号", f"川发改投资[{main_c['date'][:4]}]第{random.randint(100,999)}号"),
            ],
            stamp_entity=get_entity_name(main_c['buyer']),
            stamp_type="招标发包专用章",
            doc_no=f"BID-{main_c['no']}",
            extra_notes=[
                "经依法公开招标，确定贵单位为本工程中标人；",
                "本通知书作为签订建设工程施工合同及办理施工许可证之法定依据。"
            ],
            archive_date_str=main_c['date'].replace("-", "年") + "日"
        )
        total_files += 1
        
        # 2. 生成各线条业务合同、发票、银行回单/挂账单、地磅单/工序验收单
        for c_idx, c in enumerate(p['contracts']):
            cat = c['cat']
            no = c['no']
            target_cat_dir = cat_dirs[c['cat_dir']]
            
            # (A) 合同 PDF
            c_pdf = os.path.join(target_cat_dir, f"{no}_{c['title']}.pdf")
            create_pdf_contract(
                c_pdf, c['title'], no, c['buyer'], c['seller'],
                c['amount'], c['terms'], contract_date=c['date']
            )
            total_files += 1
            
            # (B) 合同盖章扫描件 JPG
            c_jpg = os.path.join(target_cat_dir, f"{no}_{c['title']}_盖章原件影印本.jpg")
            create_simulated_scan_jpg(
                c_jpg,
                f"建设工程业务合同与技术协议书 (扫描归档原件)",
                [
                    ("合同编号", no),
                    ("合同名称", c['title']),
                    ("甲方 (发包/购买方)", get_entity_name(c['buyer'])),
                    ("乙方 (承包/销售方)", get_entity_name(c['seller'])),
                    ("合同签约价款", f"¥ {c['amount']:,.2f} 元"),
                    ("签订日期", c['date']),
                    ("履约地点", f"{p['city']} · 项目施工现场"),
                    ("增值税约定", "约定开具合法增值税专用发票，税款依法申报"),
                ],
                stamp_entity=get_entity_name(c['seller']),
                stamp_type="合同专用章",
                doc_no=no,
                extra_notes=[
                    "合同已通过集团法务及财税合规审核，备案印章真实有效；",
                    "严禁无真实业务背景的空转走账与虚开发票行为。"
                ],
                archive_date_str=c['date'].replace("-", "年") + "日"
            )
            total_files += 1
            
            # (C) 发票 PDF & JPG
            is_paid = (c_idx % 4 != 3)  # 75% 已付，25% 挂账中
            is_tax_settled = True
            inv_no = f"{random.randint(10000000, 99999999)}"
            tax_rate = 0.13 if '材料' in cat or '机械' in cat else (0.09 if '施工' in cat or '劳务' in cat or '专业分包' in cat else 0.06)
            amount = c['amount']
            amount_no_tax = round(amount / (1.0 + tax_rate), 2)
            tax_amt = round(amount - amount_no_tax, 2)
            
            inv_pdf = os.path.join(cat_dirs['06_增值税发票与税务完税凭证'], f"INVOICE_{no}_{inv_no}_增值税专票.pdf")
            create_pdf_invoice(
                inv_pdf, inv_no, c['buyer'], c['seller'],
                [[f"*{cat}*{c['title'][:16]}", "标段/批次", "项", 1, amount_no_tax, amount_no_tax, tax_rate, tax_amt]],
                is_paid=is_paid, is_tax_settled=is_tax_settled, invoice_date=c['date']
            )
            total_files += 1
            
            inv_jpg = os.path.join(cat_dirs['06_增值税发票与税务完税凭证'], f"INVOICE_{no}_{inv_no}_发票扫描件.jpg")
            create_simulated_scan_jpg(
                inv_jpg,
                "四川/重庆 增值税电子专用发票 (全国查验真伪一致件)",
                [
                    ("发票代码/号码", f"051002400111 / No.{inv_no}"),
                    ("开票日期", c['date']),
                    ("购买方 (付款人)", get_entity_name(c['buyer'])),
                    ("销售方 (收款人)", get_entity_name(c['seller'])),
                    ("金额 (不含税)", f"¥ {amount_no_tax:,.2f} 元"),
                    ("税额", f"¥ {tax_amt:,.2f} 元 (税率 {tax_rate*100:.0f}%)"),
                    ("价税合计 (小写)", f"¥ {amount:,.2f} 元"),
                    ("发票查验状态", "国家税务总局发票查验平台: 【一致 / 正常】"),
                ],
                stamp_entity=get_entity_name(c['seller']),
                stamp_type="发票专用章",
                doc_no=inv_no,
                extra_notes=[
                    "税务稽查核验：发票状态正常，未见作废或红冲记录；",
                    "进项税额已在增值税发票综合服务平台完成抵扣勾选认证。"
                ],
                archive_date_str=c['date'].replace("-", "年") + "日"
            )
            total_files += 1
            
            # (D) 资金流：已付款银行回单 OR 未付款审批单
            if is_paid:
                bank_date = c['date'].replace("-", "")
                bank_no = f"EBNK{bank_date}{random.randint(100000, 999999)}"
                b_pdf = os.path.join(cat_dirs['07_资金结算与银行电子回单'], f"BANK_{no}_{bank_no}_银行支付回单.pdf")
                create_pdf_bank_receipt(
                    b_pdf, bank_no, c['buyer'], c['seller'],
                    round(amount * 0.8, 2), f"支付【{p['name']}】项下【{c['title']}】核定工程/材料进度款",
                    pay_date=c['date']
                )
                total_files += 1
                
                b_jpg = os.path.join(cat_dirs['07_资金结算与银行电子回单'], f"BANK_{no}_{bank_no}_电子回单盖章原件.jpg")
                create_simulated_scan_jpg(
                    b_jpg,
                    "银行电子业务专用回单 (网银对公转账流水凭证)",
                    [
                        ("回单编号", bank_no),
                        ("付款账户名称", get_entity_name(c['buyer'])),
                        ("付款账号", ENTITIES.get(c['buyer'], {}).get('acc', '510000001')),
                        ("收款账户名称", get_entity_name(c['seller'])),
                        ("收款账号", ENTITIES.get(c['seller'], {}).get('acc', '510000002')),
                        ("交易金额", f"¥ {amount*0.8:,.2f} 元 (支付80%核定进度款)"),
                        ("记账时间", f"{c['date']} 15:10:35"),
                        ("清算状态", "转账成功 · 实时清算入账 (大额支付系统 CNAPS)"),
                        ("款项用途", f"工程进度款: {c['title']}"),
                    ],
                    stamp_entity="中国建设银行股份有限公司四川省分行",
                    stamp_type="电子回单业务专用章",
                    doc_no=bank_no,
                    extra_notes=[
                        "资金流核验：由合同甲方网银对公电汇至乙方合同备案账号；",
                        "无个人卡垫付或第三方过桥交易，资金链路真实完整。"
                    ],
                    archive_date_str=c['date'].replace("-", "年") + "日"
                )
                total_files += 1
            else:
                unpaid_jpg = os.path.join(cat_dirs['07_资金结算与银行电子回单'], f"UNPAID_{no}_财务挂账应付款确认审批表.jpg")
                create_simulated_scan_jpg(
                    unpaid_jpg,
                    "工程项目部 应付款项财务挂账与支付审批单",
                    [
                        ("业务合同编号", no),
                        ("应付收款单位", get_entity_name(c['seller'])),
                        ("合同总额", f"¥ {amount:,.2f} 元"),
                        ("本期结算申报金额", f"¥ {amount*0.75:,.2f} 元"),
                        ("实际支付金额", "¥ 0.00 元 (挂账应付中)"),
                        ("申报审批日期", c['date']),
                        ("支付审核状态", "⚠️ 业主回款进度滞后，产值已确认入账，待下期资金计划拨付"),
                        ("合规核查结论", "四流匹配核查：物资/工序已入库验收，挂账真实，无虚开风险"),
                    ],
                    stamp_entity=get_entity_name(c['buyer']),
                    stamp_type="财务专用章",
                    doc_no=f"AP-{no}",
                    extra_notes=[
                        "项目财务已录入应付账款明细台账；",
                        "待发包人对应工程结算款入账后，优先安排该分包单位款项支付。"
                    ],
                    archive_date_str=c['date'].replace("-", "年") + "日"
                )
                total_files += 1
                
            # (E) 物资流：地磅单 OR 工序验收记录
            if '材料' in cat:
                tonnage = round(amount / 4300.0, 1)
                truck_cnt = max(int(tonnage / 35), 4)
                w_pdf = os.path.join(cat_dirs['08_物资物流地磅单与入库验收'], f"LOGISTICS_{no}_地磅过磅验收单.pdf")
                create_pdf_weighbridge_logistics_sheet(
                    w_pdf, p['name'], c['seller'], c['buyer'],
                    c['title'], tonnage, truck_cnt, amount,
                    logistics_date=c['date']
                )
                total_files += 1
                
                w_jpg = os.path.join(cat_dirs['08_物资物流地磅单与入库验收'], f"LOGISTICS_{no}_地磅称重小票及现场签收单.jpg")
                create_simulated_scan_jpg(
                    w_jpg,
                    "施工现场智能汽车衡 称重过磅结算单",
                    [
                        ("过磅单流水号", f"PB-{random.randint(100000, 999999)}"),
                        ("工程项目名称", p['name']),
                        ("发货单位", get_entity_name(c['seller'])),
                        ("收货料场", f"{get_entity_name(c['buyer'])} · 现场物资总仓"),
                        ("累计进场车次", f"{truck_cnt} 车次 (重车进场 / 空车回皮)"),
                        ("累计净重总计", f"{tonnage:,.2f} 吨"),
                        ("现场材料员", "李建国 (现场检尺与材质报告核验合格)"),
                        ("过磅时间", f"{c['date']} 连续全量电子摄像记录"),
                    ],
                    stamp_entity=get_entity_name(c['buyer']),
                    stamp_type="项目物资材料专用章",
                    doc_no=f"DB-{no}",
                    extra_notes=[
                        "过磅系统具备红外防作弊与车牌自动识别功能；",
                        "每车称重数据均自动上传物资数字化中枢，数据真实闭环。"
                    ],
                    archive_date_str=c['date'].replace("-", "年") + "日"
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
                        ("施工部位/作业面", f"【{p['name']}】主施工作业标段"),
                        ("现场核验工程量", f"核定合格工程量达标率 100% (金额: ¥{amount*0.8:,.2f}元)"),
                        ("签证核准日期", c['date']),
                        ("安全与技术交底", "班前安全早会交底记录完整，特种作业人员持证上岗"),
                        ("监理旁站验收结论", "经现场实测实量与旁站核验，质量符合设计规范要求，准予计量"),
                    ],
                    stamp_entity="四川科诚建设监理咨询有限公司",
                    stamp_type="项目监理部业务专用章",
                    doc_no=f"YS-{no}",
                    extra_notes=[
                        "工序已通过建设、总包、分包、监理四方联合实地复验；",
                        "现场签认单据已归档至工程部数字化质量溯源系统。"
                    ],
                    archive_date_str=c['date'].replace("-", "年") + "日"
                )
                total_files += 1

        # 3. 生成全套完税证明 (PDF + JPG)
        for cert in p['tax_certs']:
            ent = ENTITIES.get(cert['entity_code'], {
                'name': cert['entity_code'], 'tax_id': '-',
                'authority': '国家税务总局成都市税务局', 'treasury': '国家金库成都市中心支库',
                'bank': '中国建设银行成都分行', 'acc': '510000001'
            })
            cert_pdf = os.path.join(cat_dirs['06_增值税发票与税务完税凭证'], f"{cert['doc_name']}.pdf")
            create_pdf_tax_certificate(cert_pdf, cert['note'], cert, ent)
            total_files += 1
            
            cert_jpg = os.path.join(cat_dirs['06_增值税发票与税务完税凭证'], f"{cert['doc_name']}_电子税票盖章原件.jpg")
            create_simulated_tax_cert_jpg(cert_jpg, cert['note'], cert, ent)
            total_files += 1

        # 4. 生成现场实景照片 (今日相机水印)
        photo1_jpg = os.path.join(cat_dirs['09_现场实景照片与复印件影印本'], f"PHOTO_01_施工现场实景取证_主体形象.jpg")
        create_simulated_site_photo_jpg(
            photo1_jpg, p['name'], "工程主施工作业标段及结构施工面",
            "现场塔吊运转正常，工人正在进行施工作业，监理旁站到位。",
            time_str="2024-06-18 10:30:15"
        )
        total_files += 1

        photo2_jpg = os.path.join(cat_dirs['09_现场实景照片与复印件影印本'], f"PHOTO_02_物资进场过磅与抽样检测.jpg")
        create_simulated_site_photo_jpg(
            photo2_jpg, p['name'], "物资地磅房及材料卸料堆场",
            "大宗物资重车过磅并抽检取样，物资入库单与送货单核对无误。",
            time_str="2024-09-20 14:45:20"
        )
        total_files += 1
        
        # 5. 生成 10_模拟测试资料
        test_md = os.path.join(cat_dirs['10_模拟测试资料'], f"TASK_SUMMARY_{p['code']}_法人月度收入成本审核资料.md")
        with open(test_md, "w", encoding="utf-8") as f:
            f.write(f"# 【{p['name']}】({p['code']}) 模拟测试与审计资料汇总\n\n")
            f.write(f"- **总承包合同金额**：¥ {p['main_amount']:,.2f} 元\n")
            f.write(f"- **建设单位（发包方）**：{get_entity_name(p['main_client'])}\n")
            f.write(f"- **总承包单位**：{get_entity_name(p['main_contractor'])}\n")
            f.write(f"- **合同线条总数**：{len(p['contracts'])} 条\n")
            f.write(f"- **完税证明总数**：{len(p['tax_certs'])} 份\n\n")
            f.write("## 审计与四流匹配结论\n")
            f.write("1. **合同流**：全部主合同、分包合同、材料采购与机械租赁合同均具备合法盖章。\n")
            f.write("2. **发票流**：增值税专票均完成全国发票查验平台真伪核验与进项勾选抵扣。\n")
            f.write("3. **资金流**：已付款项通过对公网银直联支付并保留电子回单；未付款项具备正规财务挂账审批单。\n")
            f.write("4. **业务物资流**：材料采购具备红外防作弊地磅过磅单，工程/劳务/机械具备四方联合签认验收记录。\n")
        total_files += 1

    print("\n" + "=" * 80)
    print(f"  🎉 恭喜！02 与 03 项目全套模拟资料制造完成！共计生成 {total_files} 份高保真凭证文件。")
    print(f"  📁 存档绝对路径: {BASE_OUT_DIR}")
    print("=" * 80)

if __name__ == '__main__':
    generate_projects_02_and_03()
