# -*- coding: utf-8 -*-
"""
auth.py - 用户认证模块
v3.1 SaaS: 安全加固 — JWT alg 校验、PBKDF2 310K 迭代、refresh_token 轮换
"""

import hashlib
import hmac
import json
import os
import time
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from config import SECRET_KEY, JWT_EXPIRY_HOURS, JWT_REFRESH_DAYS


# ============================================================
# 密码哈希（使用 PBKDF2-HMAC-SHA256，OWASP 2023 推荐 310K 迭代）
# ============================================================

# 迭代次数：旧密码用 100K，新密码用 310K，登录时自动升级
PBKDF2_ITERATIONS_OLD = 100000
PBKDF2_ITERATIONS_NEW = 310000


def _hash_password(password, salt=None):
    """PBKDF2 哈希密码，返回 salt:hash 格式（使用 310K 迭代）"""
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), PBKDF2_ITERATIONS_NEW)
    return f"{salt}:{dk.hex()}"


def _check_password(password, stored_hash):
    """验证密码，兼容旧 100K 和新 310K 迭代"""
    try:
        salt, hash_hex = stored_hash.split(':', 1)
        hash_hex = hash_hex.lstrip('$')
        # 优先用旧迭代次数验证，兼容已存储的旧密码
        dk_old = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), PBKDF2_ITERATIONS_OLD)
        if hmac.compare_digest(dk_old.hex(), hash_hex):
            return True
        # 再用新迭代次数验证
        dk_new = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), PBKDF2_ITERATIONS_NEW)
        if hmac.compare_digest(dk_new.hex(), hash_hex):
            return True
        return False
    except (ValueError, AttributeError):
        return False


def needs_rehash(stored_hash):
    """检查密码哈希是否需要升级（从 100K → 310K）"""
    try:
        salt, hash_hex = stored_hash.split(':', 1)
        hash_hex = hash_hex.lstrip('$')
        dk_new = hashlib.pbkdf2_hmac('sha256', password=None, salt=None, iterations=1)
        # 用旧迭代次数重新验证
        return True  # 无法在不知道密码的情况下判断，登录成功后统一升级
    except Exception:
        return False


def upgrade_password_hash(password, stored_hash):
    """如果密码哈希使用旧迭代次数，则升级"""
    try:
        salt, hash_hex = stored_hash.split(':', 1)
        hash_hex = hash_hex.lstrip('$')
        # 用旧迭代次数验证
        dk_old = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), PBKDF2_ITERATIONS_OLD)
        if hmac.compare_digest(dk_old.hex(), hash_hex):
            # 确认是旧哈希，升级
            return _hash_password(password), True
        return stored_hash, False
    except Exception:
        return stored_hash, False


# ============================================================
# JWT 令牌（纯手写实现，无需 PyJWT 依赖）
# ============================================================

def _base64url_encode(data):
    """Base64URL 编码"""
    if isinstance(data, str):
        data = data.encode('utf-8')
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('utf-8')


def _base64url_decode(s):
    """Base64URL 解码"""
    import base64
    padding = 4 - len(s) % 4
    if padding != 4:
        s += '=' * padding
    return base64.urlsafe_b64decode(s)


def _jwt_encode(payload, secret=None):
    """生成 JWT token"""
    if secret is None:
        secret = SECRET_KEY
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _base64url_encode(json.dumps(header, separators=(',', ':')))
    payload_b64 = _base64url_encode(json.dumps(payload, separators=(',', ':')))
    message = f"{header_b64}.{payload_b64}"
    signature = hmac.new(secret.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()
    return f"{message}.{_base64url_encode(signature)}"


def _jwt_decode(token, secret=None):
    """解析并验证 JWT token，返回 payload 或 None"""
    if secret is None:
        secret = SECRET_KEY
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return None
        header_b64, payload_b64, sig_b64 = parts

        # [S-02 安全修复] 严格校验 alg 字段，防止算法混淆攻击
        try:
            header = json.loads(_base64url_decode(header_b64))
        except Exception:
            return None
        if header.get("alg") != "HS256":
            return None

        # 验证签名
        message = f"{header_b64}.{payload_b64}"
        expected_sig = hmac.new(secret.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()
        actual_sig = _base64url_decode(sig_b64).decode('utf-8')
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None
        # 解析 payload
        payload = json.loads(_base64url_decode(payload_b64))
        # 检查过期
        if payload.get('exp', 0) < time.time():
            return None
        return payload
    except Exception:
        return None


def generate_token_pair(user_id, username):
    """生成 access_token + refresh_token 对"""
    now = datetime.now(timezone.utc)
    # Access token (短期)
    access_payload = {
        "sub": str(user_id),
        "username": username,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=JWT_EXPIRY_HOURS)).timestamp()),
    }
    access_token = _jwt_encode(access_payload)
    # Refresh token (长期)
    refresh_payload = {
        "sub": str(user_id),
        "username": username,
        "type": "refresh",
        "jti": secrets.token_hex(16),  # [S-05] JWT ID 用于吊销追踪
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=JWT_REFRESH_DAYS)).timestamp()),
    }
    refresh_token = _jwt_encode(refresh_payload)
    return access_token, refresh_token


def decode_access_token(token):
    """解析 access token，返回 payload 或 None"""
    payload = _jwt_decode(token)
    if payload and payload.get("type") == "access":
        return payload
    return None


def decode_refresh_token(token):
    """解析 refresh token，返回 payload 或 None"""
    payload = _jwt_decode(token)
    if payload and payload.get("type") == "refresh":
        return payload
    return None


# [S-05] Refresh token 黑名单（内存存储，重启清空——适合单实例部署）
_refresh_token_blacklist = set()


def revoke_refresh_token(jti):
    """吊销 refresh token（通过 jti）"""
    _refresh_token_blacklist.add(jti)


def is_refresh_token_revoked(jti):
    """检查 refresh token 是否已吊销"""
    return jti in _refresh_token_blacklist


# ============================================================
# Flask 装饰器：登录验证
# ============================================================

def login_required(f):
    """API 认证装饰器：验证 Authorization: Bearer <token>"""
    @wraps(f)
    def decorated(*args, **kwargs):
        from flask import request, jsonify
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({"error": "未登录，请先登录", "code": 401}), 401
        token = auth_header[7:]
        payload = decode_access_token(token)
        if payload is None:
            return jsonify({"error": "登录已过期，请重新登录", "code": 401}), 401
        # 将用户信息注入 request
        request.user_id = int(payload["sub"])
        request.username = payload["username"]
        return f(*args, **kwargs)
    return decorated


def get_current_user_id():
    """从 request 获取当前用户 ID（需在 login_required 路由内调用）"""
    from flask import request
    return getattr(request, 'user_id', None)


def get_current_username():
    """从 request 获取当前用户名（需在 login_required 路由内调用）"""
    from flask import request
    return getattr(request, 'username', None)
