# -*- coding: utf-8 -*-
"""
routes/auth_bp.py - 认证相关路由
包含：注册、登录、刷新 token、用户信息、更新资料、统计
"""

import time as _time
import threading
from flask import Blueprint, jsonify, request

from config import REGISTER_MAX, REGISTER_WINDOW
from database import (
    get_user_by_username, get_user_by_id, create_user as db_create_user,
    update_last_login, get_user_count, get_db,
)
from auth import (
    login_required, _hash_password, _check_password, upgrade_password_hash,
    generate_token_pair, decode_refresh_token,
    revoke_refresh_token, is_refresh_token_revoked,
    get_current_user_id,
)

auth_bp = Blueprint("auth", __name__)

# [S-06] 注册速率限制
_register_attempts = {}
_register_lock = threading.Lock()


def _check_register_rate_limit():
    """[S-06] 检查注册速率限制，返回 True 表示允许，False 表示超限"""
    from flask import request as req
    client_ip = req.headers.get("X-Forwarded-For", req.remote_addr).split(",")[0].strip()
    now = _time.time()
    with _register_lock:
        if client_ip not in _register_attempts:
            _register_attempts[client_ip] = []
        _register_attempts[client_ip] = [t for t in _register_attempts[client_ip] if now - t < REGISTER_WINDOW]
        if len(_register_attempts[client_ip]) >= REGISTER_MAX:
            return False
        _register_attempts[client_ip].append(now)
        return True


@auth_bp.route("/api/auth/register", methods=["POST"])
def register():
    """用户注册"""
    if not _check_register_rate_limit():
        return jsonify({"error": "注册请求过于频繁，请稍后再试"}), 429

    body = request.get_json()
    if not body:
        return jsonify({"error": "请求数据不能为空"}), 400

    username = (body.get("username") or "").strip().lower()
    password = body.get("password") or ""
    nickname = (body.get("nickname") or "").strip()
    email = (body.get("email") or "").strip()

    if not username or len(username) < 3:
        return jsonify({"error": "用户名至少3个字符"}), 400
    if not password or len(password) < 6:
        return jsonify({"error": "密码至少6个字符"}), 400
    if len(username) > 20:
        return jsonify({"error": "用户名最多20个字符"}), 400

    existing = get_user_by_username(username)
    if existing:
        return jsonify({"error": "用户名已存在"}), 409

    password_hash = _hash_password(password)
    user_id = db_create_user(username, password_hash, nickname or username, email)
    if not user_id:
        return jsonify({"error": "注册失败，请稍后重试"}), 500

    access_token, refresh_token = generate_token_pair(user_id, username)
    update_last_login(user_id)

    return jsonify({
        "message": "注册成功",
        "user": {
            "id": user_id,
            "username": username,
            "nickname": nickname or username,
            "email": email,
        },
        "access_token": access_token,
        "refresh_token": refresh_token,
    }), 201


@auth_bp.route("/api/auth/login", methods=["POST"])
def login():
    """用户登录"""
    body = request.get_json()
    if not body:
        return jsonify({"error": "请求数据不能为空"}), 400

    username = (body.get("username") or "").strip().lower()
    password = body.get("password") or ""

    if not username or not password:
        return jsonify({"error": "用户名和密码不能为空"}), 400

    user = get_user_by_username(username)
    if not user:
        return jsonify({"error": "用户名或密码错误"}), 401

    if not _check_password(password, user["password_hash"]):
        return jsonify({"error": "用户名或密码错误"}), 401

    # [S-04] 密码哈希自动升级（100K → 310K）
    new_hash, upgraded = upgrade_password_hash(password, user["password_hash"])
    if upgraded:
        db = get_db()
        db.execute("UPDATE users SET password_hash=? WHERE id=?", (new_hash, user["id"]))
        db.commit()

    access_token, refresh_token = generate_token_pair(user["id"], user["username"])
    update_last_login(user["id"])

    return jsonify({
        "message": "登录成功",
        "user": {
            "id": user["id"],
            "username": user["username"],
            "nickname": user.get("nickname", user["username"]),
            "email": user.get("email", ""),
        },
        "access_token": access_token,
        "refresh_token": refresh_token,
    })


@auth_bp.route("/api/auth/refresh", methods=["POST"])
def refresh_token():
    """刷新 access token（含 refresh_token 轮换）"""
    body = request.get_json()
    if not body or not body.get("refresh_token"):
        return jsonify({"error": "缺少 refresh_token"}), 400

    payload = decode_refresh_token(body["refresh_token"])
    if not payload:
        return jsonify({"error": "refresh_token 无效或已过期"}), 401

    jti = payload.get("jti")
    if jti and is_refresh_token_revoked(jti):
        return jsonify({"error": "refresh_token 已被吊销，请重新登录"}), 401

    user_id = int(payload["sub"])
    username = payload["username"]

    user = get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "用户不存在"}), 401

    if jti:
        revoke_refresh_token(jti)

    access_token, new_refresh_token = generate_token_pair(user_id, username)
    return jsonify({
        "access_token": access_token,
        "refresh_token": new_refresh_token,
    })


@auth_bp.route("/api/auth/me")
@login_required
def get_current_user():
    """获取当前登录用户信息"""
    user_id = get_current_user_id()
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "用户不存在"}), 404
    return jsonify({
        "id": user["id"],
        "username": user["username"],
        "nickname": user.get("nickname", user["username"]),
        "email": user.get("email", ""),
        "created_at": user.get("created_at", ""),
        "last_login": user.get("last_login", ""),
    })


@auth_bp.route("/api/auth/update-profile", methods=["PUT"])
@login_required
def update_profile():
    """更新用户资料"""
    user_id = get_current_user_id()
    body = request.get_json() or {}
    db = get_db()
    with db:
        cur = db.cursor()
        updates = []
        values = []
        if "nickname" in body and body["nickname"]:
            updates.append("nickname=?")
            values.append(body["nickname"])
        if "email" in body:
            updates.append("email=?")
            values.append(body["email"])
        if updates:
            values.append(user_id)
            cur.execute(f"UPDATE users SET {','.join(updates)} WHERE id=?", values)
            db.commit()
    return jsonify({"message": "资料已更新"})


@auth_bp.route("/api/auth/stats")
def auth_stats():
    """获取注册统计（公开接口）"""
    count = get_user_count()
    return jsonify({"user_count": count})
