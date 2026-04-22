# -*- coding: utf-8 -*-
"""A股持仓管家 - 配置文件"""

import os
import secrets

# Tushare Pro Token（优先从环境变量读取，避免密钥硬编码）
_DEFAULT_TUSHARE_TOKEN = "e424926aa68fda7ad44af3af6e0357f1e156615563adbce0a16ac758"
TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", _DEFAULT_TUSHARE_TOKEN)
_TUSHARE_FROM_ENV = os.environ.get("TUSHARE_TOKEN") is not None

# SaaS 认证配置（优先从环境变量读取，否则自动生成）
_DEFAULT_SECRET_KEY = "a-stock-portfolio-saas-secret-2026"  # 开发用，生产环境务必设置环境变量
SECRET_KEY = os.environ.get("SECRET_KEY", _DEFAULT_SECRET_KEY)
_SECRET_FROM_ENV = os.environ.get("SECRET_KEY") is not None


def check_hardcoded_secrets():
    """检查是否存在硬编码密钥，返回警告列表"""
    warnings = []
    if not _TUSHARE_FROM_ENV:
        warnings.append("TUSHARE_TOKEN 使用硬编码值，生产环境请设置环境变量 TUSHARE_TOKEN")
    if not _SECRET_FROM_ENV:
        warnings.append("SECRET_KEY 使用默认值，生产环境请设置环境变量 SECRET_KEY")
    return warnings

# 服务配置
HOST = "127.0.0.1"
PORT = 5000
DEBUG = os.environ.get("FLASK_DEBUG", "1") == "1"

# 数据目录（支持 Railway Volume 持久化：设置环境变量 VOLUME_PATH 即可）
_VOLUME_DIR = os.environ.get("VOLUME_PATH")
DATA_DIR = _VOLUME_DIR if _VOLUME_DIR else "data"
PORTFOLIO_FILE = os.path.join(DATA_DIR, "portfolio.json")

# 行情刷新间隔（秒）
REFRESH_INTERVAL = 10

# 交易时段
TRADE_MORNING_START = "09:15"
TRADE_MORNING_END = "11:30"
TRADE_AFTERNOON_START = "13:00"
TRADE_AFTERNOON_END = "15:00"
JWT_EXPIRY_HOURS = 72  # JWT 过期时间（小时）
JWT_REFRESH_DAYS = 7   # 刷新令牌有效期（天）

# 注册速率限制（每个 IP 每 REGISTER_WINDOW 秒内最多 REGISTER_MAX 次）
REGISTER_MAX = 10
REGISTER_WINDOW = 3600  # 1小时
