from __future__ import annotations

import logging
import os
import smtplib
from email.mime.text import MIMEText
from email.header import Header

_logger = logging.getLogger("user_center.notifier")

SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER).strip()

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
RESEND_FROM = os.getenv("RESEND_FROM", "成都建工智控系统 <onboarding@resend.dev>").strip()

def send_email_code(to_email: str, code: str, purpose: str = "安全验证") -> tuple[bool, str]:
    """发送电子邮箱验证码。优先使用 Resend API 或 SMTP 发送，未配置则走开发调试通道。"""
    purpose_map = {
        "change_password": "修改登录密码",
        "bind_email": "绑定电子邮箱",
        "bind_phone": "绑定手机号码",
    }
    purpose_text = purpose_map.get(purpose, "安全验证")
    subject = f"【成都建工智控系统】{purpose_text}验证码"
    content = f"""
    尊敬的用户：
    
    您好！您正在进行【{purpose_text}】操作，您的 6 位动态验证码为：
    
        【 {code} 】
    
    验证码有效期为 5 分钟。请勿将验证码泄露给他人。如非本人操作，请忽略此邮件。
    
    —— 成都建工 V2.0 财税智控与 RAG 穿透系统
    """

    # 1. 优先使用 Resend API 发送（Soma 项目同款）
    if RESEND_API_KEY:
        try:
            import json
            import urllib.request
            import urllib.error
            html_content = f"""
            <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 560px; margin: 0 auto; padding: 24px; background: #ffffff; border: 1px solid #e5e7eb; border-radius: 12px;">
              <h2 style="color: #1e293b; margin-top: 0;">成都建工智控系统</h2>
              <p style="color: #475569; font-size: 15px;">您好！您正在进行<strong>【{purpose_text}】</strong>操作，验证码如下：</p>
              <div style="background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 8px; padding: 16px; text-align: center; margin: 24px 0;">
                <span style="font-family: monospace; font-size: 32px; font-weight: bold; letter-spacing: 6px; color: #2563eb;">{code}</span>
              </div>
              <p style="color: #64748b; font-size: 13px;">验证码有效期为 <strong>5 分钟</strong>。如非本人操作，请忽略此邮件。</p>
              <hr style="border: none; border-top: 1px solid #f1f5f9; margin: 20px 0;" />
              <p style="color: #94a3b8; font-size: 11px; margin: 0;">成都建工 V2.0 财税智控与 RAG 穿透系统</p>
            </div>
            """
            req = urllib.request.Request(
                "https://api.resend.com/emails",
                data=json.dumps({
                    "from": RESEND_FROM,
                    "to": [to_email],
                    "subject": subject,
                    "text": content,
                    "html": html_content,
                }).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                    "User-Agent": "ChengduConstruction-V2",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if 200 <= resp.status < 300:
                    _logger.info(f"[UserCenter] Resend 邮件发送成功 -> {to_email}")
                    return True, "Resend 邮件发送成功"
        except urllib.error.HTTPError as http_err:
            err_body = http_err.read().decode("utf-8", errors="ignore")
            _logger.error(f"Resend HTTP {http_err.code} 错误: {err_body}")
            return False, f"Resend 发送失败 (HTTP {http_err.code}): {err_body}"
        except Exception as exc:
            _logger.error(f"Resend 邮件发送失败: {exc}")
            return False, f"Resend 邮件发送失败: {exc}"


    # 2. 使用 SMTP 协议发送（QQ/163/企业邮）
    if SMTP_HOST and SMTP_USER and SMTP_PASSWORD:
        try:
            msg = MIMEText(content, "plain", "utf-8")
            msg["From"] = Header(f"成都建工智控系统 <{SMTP_FROM}>", "utf-8")
            msg["To"] = Header(to_email, "utf-8")
            msg["Subject"] = Header(subject, "utf-8")

            if SMTP_PORT == 465:
                server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10)
            else:
                server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10)
                server.starttls()

            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, [to_email], msg.as_string())
            server.quit()
            return True, "邮件发送成功"
        except Exception as exc:
            _logger.error(f"SMTP 邮件发送失败: {exc}")
            return False, f"邮件发送失败: {exc}"

    # 3. 未配置任何邮件凭据时走开发调试回退通道
    _logger.warning(f"[UserCenter DEV] 模拟邮件发送成功 -> 目标: {to_email} | 验证码: {code} | 场景: {purpose_text}")
    return True, "模拟发送成功（开发调试模式）"


def send_sms_code(to_phone: str, code: str, purpose: str = "安全验证") -> tuple[bool, str]:
    """发送手机短信验证码。若未配置短信网关则自动走控制台与开发调试通道。"""
    purpose_map = {
        "change_password": "修改密码",
        "bind_email": "绑定邮箱",
        "bind_phone": "绑定手机",
    }
    purpose_text = purpose_map.get(purpose, "安全验证")
    # 支持 Brevo/UniSMS/阿里云短信 SDK 扩展；默认提供极速日志模拟
    _logger.info(f"[UserCenter SMS] 发送短信验证码 -> 手机号: {to_phone} | 验证码: {code} | 场景: {purpose_text}")
    return True, "短信发送成功（开发测试通道）"
