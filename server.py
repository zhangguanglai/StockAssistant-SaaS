# -*- coding: utf-8 -*-
"""
A股个人投资助手 - Flask 后端服务 v3.1
架构：Blueprint 模块化路由
  - routes/auth_bp.py      认证（注册/登录/token/用户信息）
  - routes/position_bp.py   持仓/资金/交易/行情/复盘/回测/预警
  - routes/screen_bp.py     选股引擎
  - routes/watch_bp.py      观察池/策略效果/股票对比
  - helpers.py              共享辅助函数（Tushare/东方财富行情/缓存等）
  - database.py             SQLite 数据持久化层
  - auth.py                 JWT 认证模块
  - config.py               配置
"""

import os
from flask import Flask, send_from_directory
from flask_cors import CORS
from flask_compress import Compress

from config import HOST, PORT, DEBUG, DATA_DIR, check_hardcoded_secrets
from database import migrate_from_json
from helpers import load_stock_list
from routes.auth_bp import auth_bp
from routes.position_bp import position_bp
from routes.screen_bp import screen_bp
from routes.watch_bp import watch_bp
from routes.data_bp import data_bp

# ============================================================
# 应用初始化
# ============================================================

app = Flask(__name__, static_folder="static")
CORS(app)

# Gzip 压缩（HTML/JSON/JS/CSS 自动压缩，约减少 70% 传输量）
app.config["COMPRESS_REGISTER"] = True
app.config["COMPRESS_LEVEL"] = 6
app.config["COMPRESS_MIN_SIZE"] = 500
Compress(app)

# 注册 Blueprint
app.register_blueprint(auth_bp)
app.register_blueprint(position_bp)
app.register_blueprint(screen_bp)
app.register_blueprint(watch_bp)
app.register_blueprint(data_bp)


# ============================================================
# 页面路由
# ============================================================

@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


@app.after_request
def add_cache_headers(response):
    """静态资源加缓存头，减少重复请求"""
    if response.content_type and any(t in response.content_type for t in ("javascript", "css", "font")):
        response.headers["Cache-Control"] = "public, max-age=3600"
    return response


# ============================================================
# 启动
# ============================================================

if __name__ == "__main__":
    # 安全检查
    for w in check_hardcoded_secrets():
        print(f"[WARNING] {w}")

    # 确保数据目录存在
    os.makedirs(DATA_DIR, exist_ok=True)

    # JSON → SQLite 迁移（如果需要）
    migrate_from_json()

    # 预加载股票列表
    print("[INFO] 正在加载股票列表...")
    stock_list = load_stock_list()
    print(f"[INFO] 已加载 {len(stock_list)} 只股票")
    print(f"[INFO] 数据目录: {os.path.abspath(DATA_DIR)}")
    
    # Railway 环境支持：从环境变量读取 PORT 和 HOST
    port = int(os.environ.get("PORT", PORT))
    host = os.environ.get("HOST", HOST)
    print(f"[INFO] 访问 http://{host}:{port}")
    app.run(host=host, port=port, debug=DEBUG)
