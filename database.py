"""
database.py - SQLite 数据持久化层
v3.0: SaaS 多租户改造，支持 user_id 数据隔离
"""
import os
import json
import sqlite3
import shutil
from datetime import datetime
from contextlib import contextmanager

# 数据库文件路径（与 JSON 数据同目录）
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_FILE = os.path.join(DATA_DIR, "portfolio.db")

# 全局连接管理
_conn = None


def get_db():
    """获取数据库连接（单例模式）"""
    global _conn
    if _conn is None:
        os.makedirs(DATA_DIR, exist_ok=True)
        _conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        init_tables()
    return _conn


@contextmanager
def get_cursor():
    """获取数据库游标上下文管理器"""
    db = get_db()
    cursor = db.cursor()
    try:
        yield cursor
        db.commit()
    except Exception:
        db.rollback()
        raise


# [S-03] SQL 表名白名单，防止潜在的 SQL 注入
_ALLOWED_TABLE_NAMES = frozenset({"users", "positions", "trades", "capital", "settings", "watch_list", "db_version"})


def _get_table_columns(cur, table_name):
    """获取表的列名列表（仅允许白名单内的表名）"""
    if table_name not in _ALLOWED_TABLE_NAMES:
        raise ValueError(f"Invalid table name: {table_name}")
    cur.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cur.fetchall()]


def _column_exists(cur, table_name, column_name):
    """检查列是否已存在"""
    return column_name in _get_table_columns(cur, table_name)


def _check_column_notnull(cur, table_name, column_name):
    """检查列是否已有 NOT NULL 约束"""
    if table_name not in _ALLOWED_TABLE_NAMES:
        return False
    cur.execute(f"PRAGMA table_info({table_name})")
    for row in cur.fetchall():
        if row[1] == column_name and row[3] == 1:  # row[3] is 'notnull' flag
            return True
    return False


def init_tables():
    """初始化数据库表结构（含 SaaS 多租户自动迁移）"""
    with get_cursor() as cur:
        # ============================================================
        # 用户表（v3.0 新增）
        # ============================================================
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                nickname TEXT DEFAULT '',
                email TEXT DEFAULT '',
                avatar TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                last_login TEXT
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")

        # ============================================================
        # 持仓表
        # ============================================================
        cur.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL DEFAULT 1,
                ts_code TEXT NOT NULL,
                name TEXT DEFAULT '',
                industry TEXT DEFAULT '',
                stop_loss REAL,
                stop_profit REAL,
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        if not _column_exists(cur, "positions", "user_id"):
            print("[MIGRATE] positions 表添加 user_id 列...")
            cur.execute("ALTER TABLE positions ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_positions_user ON positions(user_id)")
        # [D-03] 添加 (user_id, ts_code) 唯一索引，防止同一用户重复持有同一股票
        try:
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_user_code ON positions(user_id, ts_code)")
        except Exception:
            pass  # 索引创建失败（可能有重复数据），不阻塞启动

        # ============================================================
        # 交易记录表
        # ============================================================
        cur.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER NOT NULL,
                trade_type TEXT NOT NULL CHECK(trade_type IN ('buy', 'sell')),
                buy_date TEXT,
                sell_date TEXT,
                buy_price REAL DEFAULT 0,
                buy_volume INTEGER DEFAULT 0,
                sell_price REAL DEFAULT 0,
                sell_volume INTEGER DEFAULT 0,
                fee REAL DEFAULT 0,
                reason TEXT DEFAULT '',
                emotion TEXT DEFAULT '',
                note TEXT DEFAULT '',
                sell_profit REAL,
                created_at TEXT,
                FOREIGN KEY (position_id) REFERENCES positions(id) ON DELETE CASCADE
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_position ON trades(position_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(trade_type, buy_date, sell_date)")
        # [D-04] 确保 buy_date 有 NOT NULL 约束（通过迁移）
        if not _column_exists(cur, "trades", "buy_date") or _check_column_notnull(cur, "trades", "buy_date"):
            _ensure_trades_notnull(cur)

        # ============================================================
        # 资金表（每个用户一行）
        # ============================================================
        cur.execute("""
            CREATE TABLE IF NOT EXISTS capital (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL DEFAULT 1,
                initial REAL DEFAULT 0,
                cash REAL DEFAULT 0,
                updated_at TEXT,
                UNIQUE(user_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        if not _column_exists(cur, "capital", "user_id"):
            print("[MIGRATE] capital 表添加 user_id 列...")
            # 旧数据：移除 id=1 约束，添加 user_id
            cur.execute("ALTER TABLE capital ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
            # 删除旧的 CHECK 约束（SQLite 不支持 DROP CONSTRAINT，需要重建表）
            _rebuild_capital_table(cur)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_capital_user ON capital(user_id)")

        # ============================================================
        # 设置表（每个用户一行）
        # ============================================================
        cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL DEFAULT 1,
                refresh_interval INTEGER DEFAULT 10,
                theme TEXT DEFAULT 'dark',
                updated_at TEXT,
                UNIQUE(user_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        if not _column_exists(cur, "settings", "user_id"):
            print("[MIGRATE] settings 表添加 user_id 列...")
            cur.execute("ALTER TABLE settings ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
            _rebuild_settings_table(cur)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_settings_user ON settings(user_id)")

        # ============================================================
        # 观察池表（user_id + ts_code 联合唯一）
        # ============================================================
        cur.execute("""
            CREATE TABLE IF NOT EXISTS watch_list (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL DEFAULT 1,
                ts_code TEXT NOT NULL,
                name TEXT DEFAULT '',
                add_price REAL DEFAULT 0,
                add_date TEXT DEFAULT '',
                add_strategy TEXT DEFAULT '',
                add_score INTEGER DEFAULT 0,
                tag TEXT DEFAULT '',
                note TEXT DEFAULT '',
                created_at TEXT,
                UNIQUE(user_id, ts_code),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        if not _column_exists(cur, "watch_list", "user_id"):
            print("[MIGRATE] watch_list 表添加 user_id 列...")
            cur.execute("ALTER TABLE watch_list ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
            _rebuild_watch_list_table(cur)
        # [v3.6] 观察池新增match_audit列，用于存储选股审计轨迹，支持自选股查看筛选审计
        if not _column_exists(cur, "watch_list", "match_audit"):
            print("[MIGRATE] watch_list 表添加 match_audit 列...")
            cur.execute("ALTER TABLE watch_list ADD COLUMN match_audit TEXT DEFAULT ''")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_watch_user ON watch_list(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_watch_tag ON watch_list(tag)")

        # ============================================================
        # 选股历史记录（v3.1.1 从 JSON 迁移）
        # ============================================================
        cur.execute("""
            CREATE TABLE IF NOT EXISTS screen_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL DEFAULT 1,
                screen_date TEXT NOT NULL,
                screen_time TEXT NOT NULL DEFAULT '',
                strategy TEXT NOT NULL DEFAULT 'trend_break',
                market_status TEXT DEFAULT 'unknown',
                market_desc TEXT DEFAULT '',
                result_count INTEGER DEFAULT 0,
                top5 TEXT DEFAULT '[]',
                stats TEXT DEFAULT '{}',
                run_time REAL DEFAULT 0,
                created_at TEXT,
                UNIQUE(user_id, screen_date, screen_time, strategy),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sh_user ON screen_history(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sh_date ON screen_history(screen_date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sh_strategy ON screen_history(strategy)")

        # 迁移已有的 JSON 数据（一次性）
        _migrate_screen_history_json(cur)

        # ============================================================
        # 版本记录
        # ============================================================
        cur.execute("""
            CREATE TABLE IF NOT EXISTS db_version (
                id INTEGER PRIMARY KEY CHECK(id=1),
                version TEXT NOT NULL,
                migrated_at TEXT
            )
        """)

        # 初始化默认值
        cur.execute("INSERT OR IGNORE INTO db_version (id, version, migrated_at) VALUES (1, '3.0', ?)",
                     (datetime.now().isoformat(),))

        # 确保默认用户存在（用于旧数据归属）
        _ensure_default_user(cur)

        # 为默认用户创建 capital 和 settings 记录
        cur.execute("INSERT OR IGNORE INTO capital (user_id, initial, cash) VALUES (1, 0, 0)")
        cur.execute("INSERT OR IGNORE INTO settings (user_id) VALUES (1)")


def _rebuild_capital_table(cur):
    """重建 capital 表以移除旧的 CHECK(id=1) 约束"""
    try:
        cur.execute("CREATE TABLE IF NOT EXISTS capital_new (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL DEFAULT 1, initial REAL DEFAULT 0, cash REAL DEFAULT 0, updated_at TEXT, UNIQUE(user_id))")
        cur.execute("INSERT OR IGNORE INTO capital_new (user_id, initial, cash, updated_at) SELECT user_id, initial, cash, updated_at FROM capital")
        cur.execute("DROP TABLE capital")
        cur.execute("ALTER TABLE capital_new RENAME TO capital")
        print("[MIGRATE] capital 表重建完成")
    except Exception as e:
        print(f"[MIGRATE] capital 表重建失败（可忽略）: {e}")


def _rebuild_settings_table(cur):
    """重建 settings 表以移除旧的 CHECK(id=1) 约束"""
    try:
        cur.execute("CREATE TABLE IF NOT EXISTS settings_new (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL DEFAULT 1, refresh_interval INTEGER DEFAULT 10, theme TEXT DEFAULT 'dark', updated_at TEXT, UNIQUE(user_id))")
        cur.execute("INSERT OR IGNORE INTO settings_new (user_id, refresh_interval, theme, updated_at) SELECT user_id, refresh_interval, theme, updated_at FROM settings")
        cur.execute("DROP TABLE settings")
        cur.execute("ALTER TABLE settings_new RENAME TO settings")
        print("[MIGRATE] settings 表重建完成")
    except Exception as e:
        print(f"[MIGRATE] settings 表重建失败（可忽略）: {e}")


def _rebuild_watch_list_table(cur):
    """重建 watch_list 表以更新唯一约束为 (user_id, ts_code)"""
    try:
        cur.execute("CREATE TABLE IF NOT EXISTS watch_list_new (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL DEFAULT 1, ts_code TEXT NOT NULL, name TEXT DEFAULT '', add_price REAL DEFAULT 0, add_date TEXT DEFAULT '', add_strategy TEXT DEFAULT '', add_score INTEGER DEFAULT 0, tag TEXT DEFAULT '', note TEXT DEFAULT '', created_at TEXT, UNIQUE(user_id, ts_code))")
        cur.execute("INSERT OR IGNORE INTO watch_list_new (user_id, ts_code, name, add_price, add_date, add_strategy, add_score, tag, note, created_at) SELECT user_id, ts_code, name, add_price, add_date, add_strategy, add_score, tag, note, created_at FROM watch_list")
        cur.execute("DROP TABLE watch_list")
        cur.execute("ALTER TABLE watch_list_new RENAME TO watch_list")
        print("[MIGRATE] watch_list 表重建完成")
    except Exception as e:
        print(f"[MIGRATE] watch_list 表重建失败（可忽略）: {e}")


def _ensure_trades_notnull(cur):
    """[D-04] 确保 trades.buy_date 有 NOT NULL 约束"""
    if _check_column_notnull(cur, "trades", "buy_date"):
        return  # 已有 NOT NULL 约束
    # 将 NULL 值更新为默认日期，然后重建表
    try:
        cur.execute("UPDATE trades SET buy_date = created_at WHERE buy_date IS NULL AND created_at IS NOT NULL")
        cur.execute("UPDATE trades SET buy_date = ? WHERE buy_date IS NULL", (datetime.now().strftime("%Y-%m-%d"),))
        cur.execute("CREATE TABLE IF NOT EXISTS trades_new (id INTEGER PRIMARY KEY AUTOINCREMENT, position_id INTEGER NOT NULL, trade_type TEXT NOT NULL CHECK(trade_type IN ('buy', 'sell')), buy_date TEXT NOT NULL DEFAULT '', sell_date TEXT, buy_price REAL DEFAULT 0, buy_volume INTEGER DEFAULT 0, sell_price REAL DEFAULT 0, sell_volume INTEGER DEFAULT 0, fee REAL DEFAULT 0, reason TEXT DEFAULT '', emotion TEXT DEFAULT '', note TEXT DEFAULT '', sell_profit REAL, created_at TEXT, FOREIGN KEY (position_id) REFERENCES positions(id) ON DELETE CASCADE)")
        cur.execute("INSERT INTO trades_new SELECT * FROM trades")
        cur.execute("DROP TABLE trades")
        cur.execute("ALTER TABLE trades_new RENAME TO trades")
        print("[MIGRATE] trades 表 buy_date NOT NULL 约束已添加")
    except Exception as e:
        print(f"[MIGRATE] trades NOT NULL 迁移失败（可忽略）: {e}")


def _ensure_default_user(cur):
    """确保默认用户 (id=1) 存在"""
    cur.execute("SELECT id FROM users WHERE id=1")
    if not cur.fetchone():
        # [A-01 修复] 延迟导入避免循环依赖（database → auth → config → database）
        # 使用内联 PBKDF2 替代 auth._hash_password
        import hashlib, secrets
        salt = secrets.token_hex(16)
        dk = hashlib.pbkdf2_hmac('sha256', b"default123", salt.encode('utf-8'), 310000)
        password_hash = f"{salt}:{dk.hex()}"
        now = datetime.now().isoformat()
        cur.execute(
            "INSERT INTO users (id, username, password_hash, nickname, created_at) VALUES (1, ?, ?, ?, ?)",
            ("default", password_hash, "默认用户", now)
        )
        print("[MIGRATE] 已创建默认用户 (default / default123)")


# ============================================================
# 用户相关操作（v3.0 新增）
# ============================================================

def get_user_by_username(username):
    """按用户名查找用户"""
    with get_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE username=?", (username,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id):
    """按 ID 查找用户"""
    with get_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE id=?", (user_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def create_user(username, password_hash, nickname="", email=""):
    """创建用户，返回新用户 ID"""
    now = datetime.now().isoformat()
    with get_cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO users (username, password_hash, nickname, email, created_at) VALUES (?,?,?,?,?)",
                (username, password_hash, nickname or username, email, now)
            )
            user_id = cur.lastrowid
            # 为新用户初始化 capital 和 settings
            cur.execute("INSERT INTO capital (user_id, initial, cash) VALUES (?, 0, 0)", (user_id,))
            cur.execute("INSERT INTO settings (user_id) VALUES (?)", (user_id,))
            return user_id
        except sqlite3.IntegrityError:
            return None


def update_last_login(user_id):
    """更新最后登录时间"""
    with get_cursor() as cur:
        cur.execute("UPDATE users SET last_login=? WHERE id=?", (datetime.now().isoformat(), user_id))


def get_user_count():
    """获取用户总数"""
    with get_cursor() as cur:
        cur.execute("SELECT COUNT(*) as cnt FROM users")
        return cur.fetchone()["cnt"]


# ============================================================
# 持仓相关操作
# ============================================================

def get_all_positions(user_id=1):
    """获取所有持仓"""
    with get_cursor() as cur:
        cur.execute("""
            SELECT p.*, 
                (SELECT COUNT(*) FROM trades WHERE position_id=p.id AND trade_type='buy') as buy_count,
                (SELECT COUNT(*) FROM trades WHERE position_id=p.id AND trade_type='sell') as sell_count
            FROM positions p WHERE p.user_id=? ORDER BY p.id
        """, (user_id,))
        return [dict(row) for row in cur.fetchall()]


def get_position(position_id, user_id=None):
    """获取单个持仓"""
    with get_cursor() as cur:
        if user_id is not None:
            cur.execute("SELECT * FROM positions WHERE id=? AND user_id=?", (position_id, user_id))
        else:
            cur.execute("SELECT * FROM positions WHERE id=?", (position_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_position_by_code(ts_code, user_id=1):
    """按股票代码获取持仓"""
    with get_cursor() as cur:
        cur.execute("SELECT * FROM positions WHERE ts_code=? AND user_id=?", (ts_code, user_id))
        row = cur.fetchone()
        return dict(row) if row else None


def create_position(ts_code, name="", industry="", user_id=1):
    """创建持仓，返回ID"""
    now = datetime.now().isoformat()
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO positions (ts_code, name, industry, created_at, updated_at, user_id) VALUES (?,?,?,?,?,?)",
            (ts_code, name, industry, now, now, user_id)
        )
        return cur.lastrowid


def update_position(position_id, user_id=None, **kwargs):
    """更新持仓字段"""
    kwargs["updated_at"] = datetime.now().isoformat()
    fields = ", ".join(f"{k}=?" for k in kwargs)
    values = list(kwargs.values())
    if user_id is not None:
        values.append(position_id)
        values.append(user_id)
        with get_cursor() as cur:
            cur.execute(f"UPDATE positions SET {fields} WHERE id=? AND user_id=?", values)
    else:
        values.append(position_id)
        with get_cursor() as cur:
            cur.execute(f"UPDATE positions SET {fields} WHERE id=?", values)


def delete_position(position_id, user_id=None):
    """删除持仓（级联删除交易记录）"""
    with get_cursor() as cur:
        if user_id is not None:
            cur.execute("DELETE FROM trades WHERE position_id IN (SELECT id FROM positions WHERE id=? AND user_id=?)", (position_id, user_id))
            cur.execute("DELETE FROM positions WHERE id=? AND user_id=?", (position_id, user_id))
        else:
            cur.execute("DELETE FROM trades WHERE position_id=?", (position_id,))
            cur.execute("DELETE FROM positions WHERE id=?", (position_id,))


# ============================================================
# 交易记录相关操作
# ============================================================

def get_trades(position_id):
    """获取某持仓的所有交易记录"""
    with get_cursor() as cur:
        cur.execute("SELECT * FROM trades WHERE position_id=? ORDER BY created_at", (position_id,))
        return [dict(row) for row in cur.fetchall()]


def get_all_trades(user_id=1):
    """获取所有交易记录（跨持仓）"""
    with get_cursor() as cur:
        cur.execute("""
            SELECT t.*, p.ts_code, p.name as position_name
            FROM trades t JOIN positions p ON t.position_id = p.id
            WHERE p.user_id=?
            ORDER BY t.created_at
        """, (user_id,))
        return [dict(row) for row in cur.fetchall()]


def add_trade(position_id, trade_type, **kwargs):
    """添加交易记录"""
    now = datetime.now().isoformat()
    with get_cursor() as cur:
        if trade_type == "buy":
            cur.execute(
                """INSERT INTO trades (position_id, trade_type, buy_date, buy_price, buy_volume, fee, reason, emotion, note, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (position_id, "buy", kwargs.get("buy_date"), kwargs.get("buy_price", 0),
                 kwargs.get("buy_volume", 0), kwargs.get("fee", 0),
                 kwargs.get("reason", ""), kwargs.get("emotion", ""),
                 kwargs.get("note", ""), now))
        else:
            cur.execute(
                """INSERT INTO trades (position_id, trade_type, buy_date, sell_date, sell_price, sell_volume, fee, reason, note, sell_profit, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (position_id, "sell", kwargs.get("buy_date", ""),
                 kwargs.get("sell_date"), kwargs.get("sell_price", 0),
                 kwargs.get("sell_volume", 0), kwargs.get("fee", 0),
                 kwargs.get("reason", ""), kwargs.get("note", ""),
                 kwargs.get("sell_profit"), now))
        return cur.lastrowid


def delete_trade(trade_id):
    """删除交易记录"""
    with get_cursor() as cur:
        cur.execute("DELETE FROM trades WHERE id=?", (trade_id,))


def get_trade_stats(user_id=1):
    """获取交易统计"""
    with get_cursor() as cur:
        # 买入笔数
        cur.execute("""SELECT COUNT(*) as cnt FROM trades t 
                       JOIN positions p ON t.position_id=p.id WHERE t.trade_type='buy' AND p.user_id=?""", (user_id,))
        buy_count = cur.fetchone()["cnt"]
        # 卖出笔数
        cur.execute("""SELECT COUNT(*) as cnt FROM trades t 
                       JOIN positions p ON t.position_id=p.id WHERE t.trade_type='sell' AND p.user_id=?""", (user_id,))
        sell_count = cur.fetchone()["cnt"]
        # 胜率
        cur.execute("""SELECT COUNT(*) as cnt FROM trades t 
                       JOIN positions p ON t.position_id=p.id WHERE t.trade_type='sell' AND t.sell_profit>0 AND p.user_id=?""", (user_id,))
        win_count = cur.fetchone()["cnt"]
        win_rate = round(win_count / sell_count * 100, 1) if sell_count > 0 else 0
        # 总手续费
        cur.execute("""SELECT COALESCE(SUM(t.fee), 0) as total FROM trades t 
                       JOIN positions p ON t.position_id=p.id WHERE p.user_id=?""", (user_id,))
        total_fee = cur.fetchone()["total"]
        # 已实现盈亏
        cur.execute("""SELECT COALESCE(SUM(t.sell_profit), 0) as total FROM trades t 
                       JOIN positions p ON t.position_id=p.id WHERE t.trade_type='sell' AND p.user_id=?""", (user_id,))
        total_sell_profit = cur.fetchone()["total"]
        return {
            "buy_count": buy_count,
            "sell_count": sell_count,
            "win_rate": win_rate,
            "total_fee": round(total_fee, 2),
            "total_sell_profit": round(total_sell_profit, 2),
        }


# ============================================================
# 资金和设置
# ============================================================

def get_capital(user_id=1):
    """获取资金配置"""
    with get_cursor() as cur:
        cur.execute("SELECT initial, cash FROM capital WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        return {"initial": row["initial"] if row else 0, "cash": row["cash"] if row else 0}


def save_capital(initial, cash, user_id=1):
    """保存资金配置"""
    with get_cursor() as cur:
        cur.execute("INSERT INTO capital (user_id, initial, cash, updated_at) VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET initial=?, cash=?, updated_at=?",
                     (user_id, initial, cash, datetime.now().isoformat(), initial, cash, datetime.now().isoformat()))


def get_settings(user_id=1):
    """获取系统设置"""
    with get_cursor() as cur:
        cur.execute("SELECT * FROM settings WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        return dict(row) if row else {"refresh_interval": 10, "theme": "dark"}


# ============================================================
# 观察池相关操作
# ============================================================

def get_watch_list_items(user_id=1):
    """获取观察池列表（v3.6：match_audit字段反序列化为dict）"""
    with get_cursor() as cur:
        cur.execute("SELECT * FROM watch_list WHERE user_id=? ORDER BY created_at DESC", (user_id,))
        items = []
        for row in cur.fetchall():
            item = dict(row)
            # 解析 match_audit JSON 字段
            if item.get("match_audit"):
                try:
                    item["match_audit"] = json.loads(item["match_audit"])
                except (json.JSONDecodeError, TypeError):
                    item["match_audit"] = None
            else:
                item["match_audit"] = None
            items.append(item)
        return items


def get_watch_count(user_id=1):
    """获取观察池数量"""
    with get_cursor() as cur:
        cur.execute("SELECT COUNT(*) as cnt FROM watch_list WHERE user_id=?", (user_id,))
        return cur.fetchone()["cnt"]


def add_watch_item(ts_code, name="", add_price=0, add_strategy="", add_score=0, tag="", note="", user_id=1, match_audit=None):
    """添加到观察池（v3.6新增match_audit参数）"""
    now = datetime.now()
    audit_json = json.dumps(match_audit, ensure_ascii=False) if match_audit else ""
    with get_cursor() as cur:
        try:
            cur.execute(
                """INSERT OR IGNORE INTO watch_list (user_id, ts_code, name, add_price, add_date, add_strategy, add_score, tag, note, created_at, match_audit)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (user_id, ts_code, name, add_price, now.strftime("%Y-%m-%d %H:%M"), add_strategy, add_score, tag, note, now.isoformat(), audit_json)
            )
            return cur.rowcount > 0
        except sqlite3.IntegrityError:
            return False


def update_watch_item(ts_code, user_id=1, **kwargs):
    """更新观察池项"""
    fields = ", ".join(f"{k}=?" for k in kwargs)
    values = list(kwargs.values()) + [ts_code, user_id]
    with get_cursor() as cur:
        cur.execute(f"UPDATE watch_list SET {fields} WHERE ts_code=? AND user_id=?", values)


def remove_watch_item(ts_code, user_id=1):
    """移除观察池项"""
    with get_cursor() as cur:
        cur.execute("DELETE FROM watch_list WHERE ts_code=? AND user_id=?", (ts_code, user_id))


def clear_watch_list(user_id=1):
    """清空观察池"""
    with get_cursor() as cur:
        cur.execute("DELETE FROM watch_list WHERE user_id=?", (user_id,))


def get_watch_tags(user_id=1):
    """获取所有观察池标签"""
    with get_cursor() as cur:
        cur.execute("SELECT DISTINCT tag FROM watch_list WHERE user_id=? AND tag IS NOT NULL AND tag != '' ORDER BY tag", (user_id,))
        return [row["tag"] for row in cur.fetchall()]


# ============================================================
# JSON → SQLite 迁移
# ============================================================

def migrate_from_json():
    """从 portfolio.json 和 watch_list.json 迁移数据到 SQLite"""
    portfolio_file = os.path.join(DATA_DIR, "portfolio.json")
    watch_file = os.path.join(DATA_DIR, "watch_list.json")

    if not os.path.exists(portfolio_file):
        print("[MIGRATE] portfolio.json 不存在，跳过迁移")
        return

    # 检查是否已迁移
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT version FROM db_version WHERE id=1")
    row = cur.fetchone()
    if row and row["version"] == "2.4":
        # 检查是否有数据
        cur.execute("SELECT COUNT(*) as cnt FROM positions")
        if cur.fetchone()["cnt"] > 0:
            print("[MIGRATE] 数据库已有数据，跳过迁移")
            return

    print("[MIGRATE] 开始从 JSON 迁移到 SQLite...")

    # 迁移持仓和交易
    try:
        with open(portfolio_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[MIGRATE] 读取 portfolio.json 失败: {e}")
        return

    # 资金
    capital = data.get("capital", {})
    if capital:
        save_capital(capital.get("initial", 0), capital.get("cash", 0))

    # 持仓
    positions = data.get("positions", [])
    for pos in positions:
        ts_code = pos.get("ts_code", "")
        name = pos.get("name", "")
        industry = pos.get("industry", "")
        pos_id = create_position(ts_code, name, industry)
        update_position(pos_id, stop_loss=pos.get("stop_loss"), stop_profit=pos.get("stop_profit"))

        # 交易记录
        trades = pos.get("trades", [])
        for t in trades:
            if t.get("trade_type") == "sell":
                add_trade(pos_id, "sell",
                          sell_date=t.get("sell_date", ""), sell_price=t.get("sell_price", 0),
                          sell_volume=t.get("sell_volume", t.get("buy_volume", 0)),
                          fee=t.get("fee", 0), reason=t.get("reason", ""),
                          note=t.get("note", ""), sell_profit=t.get("sell_profit"))
            else:
                add_trade(pos_id, "buy",
                          buy_date=t.get("buy_date", ""), buy_price=t.get("buy_price", 0),
                          buy_volume=t.get("buy_volume", 0), fee=t.get("fee", 0),
                          reason=t.get("reason", ""), emotion=t.get("emotion", ""),
                          note=t.get("note", ""))

    print(f"[MIGRATE] 已迁移 {len(positions)} 个持仓")

    # 迁移观察池
    if os.path.exists(watch_file):
        try:
            with open(watch_file, "r", encoding="utf-8") as f:
                wdata = json.load(f)
            items = wdata.get("items", [])
            for item in items:
                add_watch_item(
                    ts_code=item.get("ts_code", ""),
                    name=item.get("name", ""),
                    add_price=item.get("add_price", 0),
                    add_strategy=item.get("add_strategy", ""),
                    add_score=item.get("add_score", 0),
                    tag=item.get("tag", ""),
                    note=item.get("note", ""),
                )
            print(f"[MIGRATE] 已迁移 {len(items)} 条观察池记录")
        except Exception as e:
            print(f"[MIGRATE] 迁移观察池失败: {e}")

    # 备份原 JSON 文件
    backup_dir = os.path.join(DATA_DIR, "json_backup")
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if os.path.exists(portfolio_file):
        shutil.copy2(portfolio_file, os.path.join(backup_dir, f"portfolio_{ts}.json"))
        print(f"[MIGRATE] 已备份 portfolio.json")
    if os.path.exists(watch_file):
        shutil.copy2(watch_file, os.path.join(backup_dir, f"watch_list_{ts}.json"))
        print(f"[MIGRATE] 已备份 watch_list.json")

    # 标记迁移完成
    cur.execute("UPDATE db_version SET version='2.4-migrated', migrated_at=? WHERE id=1",
                (datetime.now().isoformat(),))
    db.commit()
    print("[MIGRATE] 迁移完成!")


# ============================================================
# ============================================================
# 选股历史记录操作（v3.1.1）
# ============================================================

def _migrate_screen_history_json(cur):
    """一次性迁移 JSON 选股历史到 SQLite"""
    import json as _json
    history_file = os.path.join(DATA_DIR, "screen_history.json")
    if not os.path.exists(history_file):
        return
    try:
        with open(history_file, "r", encoding="utf-8") as f:
            records = _json.load(f)
        if not records:
            return
        migrated = 0
        for r in records:
            try:
                cur.execute(
                    """INSERT OR IGNORE INTO screen_history 
                       (user_id, screen_date, screen_time, strategy, market_status, market_desc, 
                        result_count, top5, stats, run_time, created_at)
                       VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (r.get("date", ""), r.get("time", ""), r.get("strategy", "trend_break"),
                     r.get("market_status", "unknown"), r.get("market_desc", ""),
                     r.get("result_count", 0), _json.dumps(r.get("top5", []), ensure_ascii=False),
                     _json.dumps(r.get("stats", {}), ensure_ascii=False),
                     r.get("run_time", 0), datetime.now().isoformat()))
                migrated += 1
            except Exception:
                continue
        if migrated > 0:
            print(f"[MIGRATE] 选股历史已迁移 {migrated} 条记录从 JSON 到 SQLite")
    except Exception as e:
        print(f"[MIGRATE] 选股历史迁移失败（可忽略）: {e}")


def save_screen_history(result, user_id=1):
    """保存选股结果到历史（去重：同 user+date+time+strategy 只保留一条）"""
    import json as _json
    screen_date = result.get("screen_date", datetime.now().strftime("%Y-%m-%d"))
    screen_time = result.get("screen_time", datetime.now().strftime("%H:%M"))
    strategy = result.get("strategy", "trend_break")
    market = result.get("market", {}) or {}
    top5 = [
        {"ts_code": r["ts_code"], "name": r["name"], "price": r["price"],
         "pct_chg": r["pct_chg"], "total_score": r["total_score"]}
        for r in result.get("results", [])[:5]
    ]
    with get_cursor() as cur:
        cur.execute(
            """INSERT OR REPLACE INTO screen_history 
               (user_id, screen_date, screen_time, strategy, market_status, market_desc,
                result_count, top5, stats, run_time, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, screen_date, screen_time, strategy,
             market.get("status", "unknown"), market.get("description", ""),
             len(result.get("results", [])),
             _json.dumps(top5, ensure_ascii=False),
             _json.dumps(result.get("stats", {}), ensure_ascii=False),
             result.get("run_time", 0),
             datetime.now().isoformat())
        )
        # 清理30天前的记录
        cutoff = (datetime.now() - __import__("datetime").timedelta(days=30)).strftime("%Y-%m-%d")
        cur.execute("DELETE FROM screen_history WHERE screen_date < ?", (cutoff,))


def load_screen_history(days=7, user_id=1):
    """加载历史选股记录"""
    import json as _json
    with get_cursor() as cur:
        cutoff = (datetime.now() - __import__("datetime").timedelta(days=days)).strftime("%Y-%m-%d")
        cur.execute(
            """SELECT * FROM screen_history 
               WHERE user_id=? AND screen_date >= ? 
               ORDER BY screen_date DESC, screen_time DESC""",
            (user_id, cutoff))
        rows = cur.fetchall()
    result = []
    for r in rows:
        try:
            top5 = _json.loads(r["top5"]) if r["top5"] else []
        except Exception:
            top5 = []
        try:
            stats = _json.loads(r["stats"]) if r["stats"] else {}
        except Exception:
            stats = {}
        result.append({
            "id": r["id"],
            "date": r["screen_date"],
            "time": r["screen_time"],
            "strategy": r["strategy"],
            "market_status": r["market_status"],
            "market_desc": r["market_desc"],
            "result_count": r["result_count"],
            "top5": top5,
            "stats": stats,
            "run_time": r["run_time"],
        })
    return result


# ============================================================
# 兼容层：提供与原 JSON 接口相同格式的函数
# ============================================================

def load_portfolio_compat(user_id=1):
    """
    兼容层：返回与原 load_portfolio() 相同格式的数据
    用于最小化 server.py 的改动
    """
    positions = get_all_positions(user_id)
    capital = get_capital(user_id)
    settings = get_settings(user_id)

    # 重建 position 结构（与原 JSON 格式一致）
    pos_list = []
    for p in positions:
        trades = get_trades(p["id"])
        pos_list.append({
            "id": p["id"],
            "ts_code": p["ts_code"],
            "name": p["name"],
            "industry": p["industry"],
            "stop_loss": p["stop_loss"],
            "stop_profit": p["stop_profit"],
            "trades": [
                {
                    "id": t["id"],
                    "trade_type": t["trade_type"],
                    "buy_date": t.get("buy_date", ""),
                    "sell_date": t.get("sell_date", ""),
                    "buy_price": t["buy_price"] if t["trade_type"] == "buy" else 0,
                    "buy_volume": t["buy_volume"] if t["trade_type"] == "buy" else 0,
                    "sell_price": t["sell_price"] if t["trade_type"] == "sell" else 0,
                    "sell_volume": t["sell_volume"] if t["trade_type"] == "sell" else 0,
                    "fee": t["fee"],
                    "reason": t["reason"],
                    "emotion": t.get("emotion", ""),
                    "note": t["note"],
                    "sell_profit": t.get("sell_profit"),
                    "trade_id": t["id"],
                }
                for t in trades
            ],
        })

    return {
        "version": "3.0",
        "positions": pos_list,
        "capital": capital,
        "settings": {
            "refresh_interval": settings.get("refresh_interval", 10),
            "theme": settings.get("theme", "dark"),
        }
    }


def save_portfolio_compat(data, user_id=1):
    """
    兼容层：将数据从 dict 格式同步到 SQLite
    注意：此函数主要用于导入场景，常规操作应直接使用 database API
    """
    # 资金
    capital = data.get("capital", {})
    if capital:
        save_capital(capital.get("initial", 0), capital.get("cash", 0), user_id)

    # 持仓和交易
    for pos in data.get("positions", []):
        existing = get_position(pos.get("id"), user_id)
        if existing:
            update_position(pos["id"], user_id,
                            stop_loss=pos.get("stop_loss"),
                            stop_profit=pos.get("stop_profit"),
                            name=pos.get("name", existing["name"]),
                            industry=pos.get("industry", existing["industry"]))
        else:
            pos_id = create_position(pos["ts_code"], pos.get("name", ""), pos.get("industry", ""), user_id)
            pos["id"] = pos_id

        # 交易记录（仅在导入时使用）
        for t in pos.get("trades", []):
            # 避免重复插入
            if t.get("trade_id"):
                continue
            if t.get("trade_type") == "sell":
                add_trade(pos["id"], "sell",
                          sell_date=t.get("sell_date", ""), sell_price=t.get("sell_price", 0),
                          sell_volume=t.get("sell_volume", 0), fee=t.get("fee", 0),
                          reason=t.get("reason", ""), note=t.get("note", ""))
            else:
                add_trade(pos["id"], "buy",
                          buy_date=t.get("buy_date", ""), buy_price=t.get("buy_price", 0),
                          buy_volume=t.get("buy_volume", 0), fee=t.get("fee", 0),
                          reason=t.get("reason", ""), emotion=t.get("emotion", ""),
                          note=t.get("note", ""))
