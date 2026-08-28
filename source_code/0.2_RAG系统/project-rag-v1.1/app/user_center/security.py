from __future__ import annotations

import hashlib
import os
import secrets

def hash_password(password: str) -> str:
    """使用带有随机盐的 PBKDF2-HMAC-SHA256 对密码进行加密哈希。"""
    salt = os.urandom(16).hex()
    hashed = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100000,
    ).hex()
    return f"pbkdf2_sha256${salt}${hashed}"

def verify_password(plain_password: str, stored_hash: str) -> bool:
    """校验明文密码与存储的哈希是否一致。"""
    try:
        parts = stored_hash.split("$")
        if len(parts) != 3 or parts[0] != "pbkdf2_sha256":
            # 兼容普通 bcrypt 或旧版哈希（如果存在）
            return False
        salt = parts[1]
        expected_hash = parts[2]
        computed_hash = hashlib.pbkdf2_hmac(
            "sha256",
            plain_password.encode("utf-8"),
            salt.encode("utf-8"),
            100000,
        ).hex()
        return secrets.compare_digest(computed_hash, expected_hash)
    except Exception:
        return False

def generate_otp_code() -> str:
    """生成 6 位安全动态数字验证码 (000000 - 999999)。"""
    return f"{secrets.randbelow(1000000):06d}"
