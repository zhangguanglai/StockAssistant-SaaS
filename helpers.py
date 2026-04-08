# -*- coding: utf-8 -*-
"""
helpers.py - 共享辅助函数
从 server.py 提取出来的公共工具函数，供各 Blueprint 模块复用
"""

import threading
from datetime import datetime, time, timedelta
from functools import lru_cache

import pandas as pd
import requests
import tushare as ts

from config import (
    TUSHARE_TOKEN,
    TRADE_MORNING_START, TRADE_MORNING_END,
    TRADE_AFTERNOON_START, TRADE_AFTERNOON_END,
)

# ============================================================
# Tushare API 实例
# ============================================================

ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# ============================================================
# 内存缓存（TTL Cache）
# 减少对东方财富/Tushare 的重复请求，提升 API 响应速度
# ============================================================

_cache_store = {}  # key -> (data, expire_ts)
_cache_lock = threading.Lock()


def cache_get(key):
    """获取缓存，过期或不存在返回 None"""
    with _cache_lock:
        entry = _cache_store.get(key)
        if entry and entry[1] > datetime.now().timestamp():
            return entry[0]
    return None


def cache_set(key, data, ttl_seconds=60):
    """写入缓存，ttl_seconds 为过期时间（秒）"""
    with _cache_lock:
        _cache_store[key] = (data, datetime.now().timestamp() + ttl_seconds)


def cache_clear(key=None):
    """清空全部缓存或指定 key"""
    with _cache_lock:
        if key:
            _cache_store.pop(key, None)
        else:
            _cache_store.clear()


# ============================================================
# 股票列表缓存
# ============================================================

_stock_list_cache = []
_stock_list_loaded = False
_stock_list_lock = threading.Lock()


def safe_float(val, default=0):
    """安全转换为 float，失败返回默认值"""
    try:
        if val is None or val == "" or val == "-":
            return default
        return float(val)
    except (ValueError, TypeError):
        return default


def is_trade_time():
    """判断当前是否为交易时段"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    morning = time.fromisoformat(TRADE_MORNING_START)
    morning_end = time.fromisoformat(TRADE_MORNING_END)
    afternoon = time.fromisoformat(TRADE_AFTERNOON_START)
    afternoon_end = time.fromisoformat(TRADE_AFTERNOON_END)
    return (morning <= t <= morning_end) or (afternoon <= t <= afternoon_end)


def should_use_realtime_source():
    """
    判断是否应该使用东方财富实时数据源。
    在交易时段（9:15-15:00）和收盘后当日（15:00-23:59，工作日）都优先使用东方财富。
    东方财富在收盘后仍返回当天收盘数据，比 Tushare（延迟1-2天）更及时。
    """
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    market_open = time.fromisoformat(TRADE_MORNING_START)
    return t >= market_open  # 9:15 ~ 23:59 工作日都走东方财富


def load_stock_list():
    """加载并缓存股票列表（Tushare stock_basic）"""
    global _stock_list_cache, _stock_list_loaded
    if _stock_list_loaded:
        return _stock_list_cache
    with _stock_list_lock:
        if _stock_list_loaded:
            return _stock_list_cache
        try:
            df = pro.stock_basic(exchange="", list_status="L",
                                 fields="ts_code,symbol,name,area,industry,market,list_date")
            _stock_list_cache = df.to_dict("records")
            _stock_list_loaded = True
            return _stock_list_cache
        except Exception as e:
            print(f"[ERROR] 加载股票列表失败: {e}")
            return []


def get_stock_info(ts_code):
    """从缓存获取股票基本信息"""
    stocks = load_stock_list()
    for s in stocks:
        if s["ts_code"] == ts_code:
            return {"name": s["name"], "industry": s.get("industry", ""), "area": s.get("area", "")}
    return {"name": ts_code, "industry": "", "area": ""}


def calc_position_meta(position):
    """计算持仓的加权成本价等元数据"""
    trades = position.get("trades", [])
    if not trades:
        position["meta"] = {"avg_cost": 0, "total_volume": 0, "total_cost": 0}
        return
    total_cost = sum(t["buy_price"] * t["buy_volume"] + t.get("fee", 0) for t in trades if t.get("trade_type") != "sell")
    total_volume = sum(t["buy_volume"] for t in trades if t.get("trade_type") != "sell")
    sell_volume = sum(t.get("sell_volume", t.get("buy_volume", 0)) for t in trades if t.get("trade_type") == "sell")
    total_volume = max(0, total_volume - sell_volume)
    avg_cost = round(total_cost / (total_volume + sell_volume), 3) if (total_volume + sell_volume) > 0 else 0
    position["meta"] = {
        "avg_cost": avg_cost,
        "total_volume": total_volume,
        "total_cost": round(total_cost, 2),
        "sell_volume": sell_volume,
    }


# ============================================================
# 东方财富实时行情（后端代理）
# ============================================================

def get_realtime_quotes_eastmoney(codes, use_cache=True):
    """
    通过东方财富接口获取实时行情（带 TTL 缓存）
    codes: Tushare 格式代码列表，如 ["000001.SZ", "600519.SH"]
    返回: {ts_code: {name, price, open, high, low, pre_close, vol, amount, pct_chg, change}}
    缓存策略：交易时段 30s，非交易时段 5min
    """
    codes = [c.upper() for c in codes]
    cache_key = "em_quotes_" + ",".join(sorted(codes))

    if use_cache:
        cached = cache_get(cache_key)
        if cached is not None:
            return cached

    secids = []
    for code in codes:
        if code.endswith(".SZ"):
            secids.append(f"0.{code[:6]}")
        elif code.endswith(".SH"):
            secids.append(f"1.{code[:6]}")
        elif code.endswith(".BJ"):
            secids.append(f"0.{code[:6]}")
        else:
            secids.append(f"0.{code[:6]}")

    if not secids:
        return {}

    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    params = {
        "fltt": "2",
        "fields": "f2,f3,f4,f5,f6,f7,f12,f14,f15,f16,f17,f18",
        "secids": ",".join(secids),
    }

    try:
        resp = requests.get(url, params=params, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        result = {}
        items = data.get("data", {}).get("diff", [])
        for item in items:
            code_raw = str(item.get("f12", ""))
            if code_raw.startswith("6"):
                ts_code = f"{code_raw}.SH"
            elif code_raw.startswith("8") or code_raw.startswith("4"):
                ts_code = f"{code_raw}.BJ"
            else:
                ts_code = f"{code_raw}.SZ"

            result[ts_code] = {
                "price": item.get("f2"),
                "pct_chg": item.get("f3"),
                "change": item.get("f4"),
                "vol": item.get("f5"),
                "amount": item.get("f6"),
                "amplitude": item.get("f7"),
                "name": item.get("f14"),
                "high": item.get("f15"),
                "low": item.get("f16"),
                "open": item.get("f17"),
                "pre_close": item.get("f18"),
            }
        # 缓存：交易时段30s刷新，非交易时段5min
        ttl = 30 if is_trade_time() else 300
        cache_set(cache_key, result, ttl)
        return result
    except Exception as e:
        print(f"[ERROR] 东方财富行情获取失败: {e}")
        return {}


def get_realtime_quotes_sina(codes, use_cache=True):
    """
    通过新浪财经接口获取实时行情（带 TTL 缓存）
    codes: Tushare 格式代码列表，如 ["000001.SZ", "600519.SH"]
    返回: {ts_code: {name, price, open, high, low, pre_close, vol, amount, pct_chg, change, time}}
    缓存策略：交易时段 15s，非交易时段 5min（新浪更轻量，刷新更频繁）
    """
    codes = [c.upper() for c in codes]
    cache_key = "sina_quotes_" + ",".join(sorted(codes))

    if use_cache:
        cached = cache_get(cache_key)
        if cached is not None:
            return cached

    # 转换为新浪代码格式：600519.SH → sh600519, 000001.SZ → sz000001
    sina_codes = []
    for code in codes:
        parts = code.split(".")
        if len(parts) == 2:
            sina_codes.append(f"{parts[1].lower()}{parts[0]}")
        else:
            sina_codes.append(code)

    if not sina_codes:
        return {}

    url = f"http://hq.sinajs.cn/list={','.join(sina_codes)}"
    headers = {"Referer": "https://finance.sina.com.cn"}

    try:
        resp = requests.get(url, headers=headers, timeout=8)
        resp.raise_for_status()
        text = resp.text

        result = {}
        for line in text.strip().split("\n"):
            line = line.strip()
            if not line or '=' not in line:
                continue
            # 提取数据部分：var hq_str_sh600519="贵州茅台,...";
            data_str = line.split('"')[1] if '"' in line else ""
            if not data_str:
                continue

            # 新浪数据字段说明（个股）：
            # 0:名称, 1:今开, 2:昨收, 3:现价, 4:最高, 5:最低,
            # 6:买一价, 7:卖一价, 8:成交量(股), 9:成交额(元),
            # 30:日期+时间(YYYY-MM-DD HH:MM:SS), 31:未知
            fields = data_str.split(",")
            if len(fields) < 10:
                continue

            name = fields[0]
            open_price = safe_float(fields[1])
            pre_close = safe_float(fields[2])
            price = safe_float(fields[3])
            high = safe_float(fields[4])
            low = safe_float(fields[5])
            vol = safe_float(fields[8])
            amount = safe_float(fields[9])

            # 计算涨跌和涨幅
            change = round(price - pre_close, 2) if (price and pre_close) else 0
            pct_chg = round(change / pre_close * 100, 2) if pre_close else 0

            # 解析时间
            time_str = ""
            if len(fields) > 30 and fields[30]:
                time_str = fields[30].strip()

            # 从新浪代码反推 Tushare 代码
            code_hint = line.split("hq_str_")[1].split("=")[0] if "hq_str_" in line else ""
            if code_hint.startswith("sh"):
                ts_code = f"{code_hint[2:]}.SH"
            elif code_hint.startswith("sz"):
                ts_code = f"{code_hint[2:]}.SZ"
            elif code_hint.startswith("bj"):
                ts_code = f"{code_hint[2:]}.BJ"
            else:
                continue

            # 只返回请求的代码
            if ts_code in codes:
                result[ts_code] = {
                    "name": name,
                    "price": price,
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "pre_close": pre_close,
                    "vol": vol,
                    "amount": amount,
                    "pct_chg": pct_chg,
                    "change": change,
                    "time": time_str,
                    "_source": "sina",
                }

        if result:
            ttl = 15 if is_trade_time() else 300
            cache_set(cache_key, result, ttl)
        return result
    except Exception as e:
        print(f"[ERROR] 新浪行情获取失败: {e}")
        return {}


def get_realtime_quotes(codes, use_cache=True):
    """
    获取实时行情（多数据源降级链）
    优先级：东方财富 → 新浪财经 → Tushare日线
    codes: Tushare 格式代码列表
    """
    codes = [c.upper() for c in codes]
    if not codes:
        return {}

    if should_use_realtime_source():
        # 第一优先：东方财富
        quotes = get_realtime_quotes_eastmoney(codes, use_cache=use_cache)
        if quotes:
            # 标记数据源
            for v in quotes.values():
                v.setdefault("_source", "eastmoney")
            return quotes

        # 第二优先：新浪财经
        quotes = get_realtime_quotes_sina(codes, use_cache=use_cache)
        if quotes:
            return quotes

    # 最终降级：Tushare 日线
    result = {}
    for code in codes:
        daily = get_tushare_daily(code)
        if daily:
            daily["_source"] = "tushare"
            result[code] = daily
    return result


def get_realtime_quote(ts_code):
    """获取单只股票实时行情（东方财富 → 新浪 → Tushare 降级）"""
    ts_code = ts_code.upper()
    quotes = get_realtime_quotes([ts_code])
    return quotes.get(ts_code)


def get_tushare_daily(ts_code, trade_date=None):
    """从 Tushare 获取日K线数据（收盘后使用）"""
    try:
        params = {"ts_code": ts_code}
        if trade_date:
            params["trade_date"] = trade_date
        else:
            params["end_date"] = datetime.now().strftime("%Y%m%d")
        df = pro.daily(**params)
        if df.empty:
            return None
        row = df.iloc[0]
        return {
            "price": float(row["close"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "pre_close": float(row["pre_close"]),
            "vol": float(row["vol"]),
            "amount": float(row["amount"]),
            "pct_chg": float(row["pct_chg"]),
            "change": float(row["change"]),
            "trade_date": str(row["trade_date"]),
            "name": "",
        }
    except Exception as e:
        print(f"[ERROR] Tushare日K获取失败({ts_code}): {e}")
        return None


def enrich_positions(positions):
    """为持仓列表附加实时行情和盈亏计算（带容错）"""
    if not positions:
        return positions

    codes = [p["ts_code"] for p in positions]

    # 多数据源降级链：东方财富 → 新浪财经 → Tushare日线
    quotes = get_realtime_quotes(codes)

    result = []
    for p in positions:
        calc_position_meta(p)
        meta = p["meta"]
        info = get_stock_info(p["ts_code"])
        quote = quotes.get(p["ts_code"], {})

        current_price = quote.get("price") or 0
        pre_close = quote.get("pre_close") or 0
        name = quote.get("name") or info["name"] or p["ts_code"]
        pct_chg = quote.get("pct_chg")

        total_volume = meta["total_volume"]
        avg_cost = meta["avg_cost"]
        total_cost = meta["total_cost"]

        market_value = round(current_price * total_volume, 2) if current_price else 0
        profit = round(market_value - avg_cost * total_volume, 2) if current_price else 0
        profit_pct = round(profit / (avg_cost * total_volume) * 100, 2) if (avg_cost * total_volume) > 0 else 0

        today_profit = round((current_price - pre_close) * total_volume, 2) if (current_price and pre_close) else 0

        alerts = []
        stop_loss = p.get("stop_loss")
        stop_profit = p.get("stop_profit")
        if stop_loss and current_price > 0 and current_price <= stop_loss:
            alerts.append({"type": "stop_loss", "message": f"已触发止损价 ¥{stop_loss}", "level": "danger"})
        if stop_profit and current_price > 0 and current_price >= stop_profit:
            alerts.append({"type": "stop_profit", "message": f"已触达止盈价 ¥{stop_profit}", "level": "warning"})
        if stop_loss and current_price > 0 and current_price > stop_loss:
            distance = (current_price - stop_loss) / current_price * 100
            if distance <= 10:
                alerts.append({"type": "near_stop_loss", "message": f"距止损价 ¥{stop_loss} 仅 {distance:.1f}%", "level": "caution"})

        result.append({
            "id": p["id"],
            "ts_code": p["ts_code"],
            "name": name,
            "industry": info.get("industry", ""),
            "total_volume": total_volume,
            "avg_cost": avg_cost,
            "total_cost": total_cost,
            "current_price": current_price,
            "pre_close": pre_close,
            "market_value": market_value,
            "profit": profit,
            "profit_pct": profit_pct,
            "today_profit": today_profit,
            "pct_chg": pct_chg,
            "open": quote.get("open"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "vol": quote.get("vol"),
            "amount": quote.get("amount"),
            "trades": p.get("trades", []),
            "stop_loss": p.get("stop_loss"),
            "stop_profit": p.get("stop_profit"),
            "alerts": alerts,
        })

    return result


# ============================================================
# 大盘指数行情
# ============================================================

INDEX_CODES = {
    "000001.SH": {"name": "上证指数", "secid": "1.000001"},
    "399001.SZ": {"name": "深证成指", "secid": "0.399001"},
    "399006.SZ": {"name": "创业板指", "secid": "0.399006"},
}


def get_index_quotes():
    """获取大盘指数实时行情（降级链：东方财富 → 新浪财经 → Tushare）"""
    cache_key = "index_quotes"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    index_codes = list(INDEX_CODES.keys())

    # 第一优先：东方财富
    secids = [v["secid"] for v in INDEX_CODES.values()]
    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    params = {
        "fltt": "2",
        "fields": "f2,f3,f4,f5,f6,f7,f12,f14,f15,f16,f17,f18",
        "secids": ",".join(secids),
    }
    try:
        resp = requests.get(url, params=params, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", {}).get("diff", [])
        result = {}
        for item in items:
            code_raw = str(item.get("f12", ""))
            ts_code = f"{code_raw}.SH" if code_raw.startswith("000001") else f"{code_raw}.SZ"
            if ts_code in INDEX_CODES:
                result[ts_code] = {
                    "name": item.get("f14"),
                    "price": item.get("f2"),
                    "pct_chg": item.get("f3"),
                    "change": item.get("f4"),
                    "vol": item.get("f5"),
                    "amount": item.get("f6"),
                    "high": item.get("f15"),
                    "low": item.get("f16"),
                    "open": item.get("f17"),
                    "pre_close": item.get("f18"),
                    "_source": "eastmoney",
                }
        if result:
            ttl = 15 if is_trade_time() else 300
            cache_set(cache_key, result, ttl)
            return result
    except Exception as e:
        print(f"[WARN] 东方财富指数获取失败，尝试新浪: {e}")

    # 第二优先：新浪财经
    try:
        sina_codes = [f"{c.split('.')[1].lower()}{c[:6]}" for c in index_codes]
        sina_url = f"http://hq.sinajs.cn/list={','.join(sina_codes)}"
        headers = {"Referer": "https://finance.sina.com.cn"}
        resp = requests.get(sina_url, headers=headers, timeout=8)
        resp.raise_for_status()

        result = {}
        for line in resp.text.strip().split("\n"):
            line = line.strip()
            if not line or '=' not in line:
                continue
            data_str = line.split('"')[1] if '"' in line else ""
            if not data_str:
                continue
            fields = data_str.split(",")
            if len(fields) < 10:
                continue

            name = fields[0]
            open_p = safe_float(fields[1])
            pre_close = safe_float(fields[2])
            price = safe_float(fields[3])
            high = safe_float(fields[4])
            low = safe_float(fields[5])
            vol = safe_float(fields[8])
            amount = safe_float(fields[9])
            change = round(price - pre_close, 2) if (price and pre_close) else 0
            pct_chg = round(change / pre_close * 100, 2) if pre_close else 0
            time_str = fields[30].strip() if len(fields) > 30 else ""

            code_hint = line.split("hq_str_")[1].split("=")[0] if "hq_str_" in line else ""
            if code_hint.startswith("sh"):
                ts_code = f"{code_hint[2:]}.SH"
            elif code_hint.startswith("sz"):
                ts_code = f"{code_hint[2:]}.SZ"
            else:
                continue

            if ts_code in INDEX_CODES:
                result[ts_code] = {
                    "name": name,
                    "price": price,
                    "pct_chg": pct_chg,
                    "change": change,
                    "vol": vol,
                    "amount": amount,
                    "high": high,
                    "low": low,
                    "open": open_p,
                    "pre_close": pre_close,
                    "time": time_str,
                    "_source": "sina",
                }

        if result:
            ttl = 15 if is_trade_time() else 300
            cache_set(cache_key, result, ttl)
            return result
    except Exception as e:
        print(f"[WARN] 新浪指数获取失败: {e}")

    return {}


# ============================================================
# P0: 复权因子（adj_factor）
# ============================================================

def get_adj_factor(ts_code, start_date=None, end_date=None):
    """
    获取个股复权因子，用于K线前复权计算
    返回: DataFrame {trade_date, adj_factor}
    """
    try:
        params = {"ts_code": ts_code}
        if start_date:
            params["start_date"] = start_date
        else:
            params["start_date"] = (datetime.now() - timedelta(days=120)).strftime("%Y%m%d")
        if end_date:
            params["end_date"] = end_date
        else:
            params["end_date"] = datetime.now().strftime("%Y%m%d")
        df = pro.adj_factor(**params)
        if df.empty:
            return pd.DataFrame()
        df = df.sort_values("trade_date")
        return df[["trade_date", "adj_factor"]]
    except Exception as e:
        print(f"[ERROR] 获取复权因子失败({ts_code}): {e}")
        return pd.DataFrame()


def adjust_kline_by_adj_factor(kline_df, adj_df):
    """
    使用复权因子对K线数据进行前复权处理
    kline_df: 含 trade_date, open, high, low, close 的 DataFrame
    adj_df: 含 trade_date, adj_factor 的 DataFrame
    返回: 前复权后的 DataFrame（原价 * adj_factor / 最新adj_factor）
    """
    if kline_df.empty or adj_df.empty:
        return kline_df
    merged = kline_df.merge(adj_df, on="trade_date", how="left")
    if "adj_factor" not in merged.columns:
        return kline_df
    # 最新复权因子
    latest_adj = adj_df.iloc[-1]["adj_factor"]
    if latest_adj and latest_adj > 0:
        for col in ["open", "high", "low", "close"]:
            if col in merged.columns:
                merged[col] = (merged[col] * merged["adj_factor"] / latest_adj).round(3)
    return merged.drop(columns=["adj_factor"], errors="ignore")


# ============================================================
# P0: ST股票缓存
# ============================================================

_st_stock_cache = {}  # {"YYYYMMDD": set("000001.SZ", ...)}
_st_cache_lock = threading.Lock()


def get_st_stocks(trade_date=None):
    """
    获取指定日期处于ST状态的股票集合
    使用 stock_st 接口精确查询，替代名称匹配
    返回: set of ts_code
    """
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y%m%d")

    with _st_cache_lock:
        if trade_date in _st_stock_cache:
            return _st_stock_cache[trade_date]

    cache_key = f"st_stocks_{trade_date}"
    cached = cache_get(cache_key)
    if cached is not None:
        with _st_cache_lock:
            _st_stock_cache[trade_date] = cached
        return cached

    try:
        # 查询所有ST记录，过滤到指定日期有效
        df = pro.stock_st(fields="ts_code,trade_date")
        if df.empty:
            return set()
        # 只取 trade_date <= 目标日期 的最新状态
        df = df[df["trade_date"] <= trade_date].sort_values("trade_date", ascending=False)
        # 每个 ts_code 取最近的一条记录
        st_codes = set()
        seen = set()
        for _, row in df.iterrows():
            tc = row["ts_code"]
            if tc not in seen:
                seen.add(tc)
                st_codes.add(tc)

        # 缓存（5分钟TTL）
        cache_set(cache_key, st_codes, 300)
        with _st_cache_lock:
            _st_stock_cache[trade_date] = st_codes
        return st_codes
    except Exception as e:
        print(f"[ERROR] 获取ST股票列表失败: {e}")
        return set()


# ============================================================
# P0: 停牌股票缓存
# ============================================================

_suspended_stock_cache = {}
_suspended_cache_lock = threading.Lock()


def get_suspended_stocks(trade_date=None):
    """
    获取指定日期停牌的股票集合
    返回: set of ts_code
    """
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y%m%d")

    with _suspended_cache_lock:
        if trade_date in _suspended_stock_cache:
            return _suspended_stock_cache[trade_date]

    cache_key = f"suspended_stocks_{trade_date}"
    cached = cache_get(cache_key)
    if cached is not None:
        with _suspended_cache_lock:
            _suspended_stock_cache[trade_date] = cached
        return cached

    try:
        df = pro.suspend_d(trade_date=trade_date, fields="ts_code,trade_date")
        if df.empty:
            return set()
        suspended = set(df["ts_code"].tolist())

        cache_set(cache_key, suspended, 300)
        with _suspended_cache_lock:
            _suspended_stock_cache[trade_date] = suspended
        return suspended
    except Exception as e:
        print(f"[ERROR] 获取停牌股票列表失败: {e}")
        return set()


# ============================================================
# P1: 财务数据接口
# ============================================================

def get_fina_indicator(ts_code, periods=4):
    """
    获取个股财务指标（fina_indicator）
    包含：ROE、ROA、毛利率、净利率、资产负债率、EPS、营收增长等
    返回: [dict, ...] 按期倒序
    """
    cache_key = f"fina_indicator_{ts_code}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        df = pro.fina_indicator(
            ts_code=ts_code,
            fields="ts_code,ann_date,end_date,roe,roa,grossprofit_margin,netprofit_margin,debt_to_assets,"
                   "eps,yoy_eps,yoy_sales,yoy_equity,yoy_asset,yoy_profit,or_yoy,q_sales,q_profit"
        )
        if df.empty:
            return []
        df = df.sort_values("end_date", ascending=False).head(periods)
        result = df.to_dict("records")
        cache_set(cache_key, result, 3600)  # 1小时缓存
        return result
    except Exception as e:
        print(f"[ERROR] 获取财务指标失败({ts_code}): {e}")
        return []


def get_income_trend(ts_code, periods=8):
    """
    获取个股营收趋势（income）
    返回: [dict, ...] 按报告期倒序，含营收、净利润、同比增长率
    """
    cache_key = f"income_trend_{ts_code}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        df = pro.income(
            ts_code=ts_code,
            fields="ts_code,ann_date,end_date,revenue,total_profit,n_income,n_income_attr_p,yoy_revenue,yoy_net_profit"
        )
        if df.empty:
            return []
        df = df.sort_values("end_date", ascending=False).head(periods)
        result = df.to_dict("records")
        cache_set(cache_key, result, 3600)
        return result
    except Exception as e:
        print(f"[ERROR] 获取营收趋势失败({ts_code}): {e}")
        return []


def get_forecast(ts_code):
    """
    获取个股业绩预告（forecast）
    返回: [dict, ...] 按公告日期倒序
    """
    cache_key = f"forecast_{ts_code}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        df = pro.forecast(
            ts_code=ts_code,
            fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max,"
                   "summary,exp_date"
        )
        if df.empty:
            return []
        df = df.sort_values("ann_date", ascending=False).head(4)
        result = df.to_dict("records")
        cache_set(cache_key, result, 1800)  # 30分钟缓存
        return result
    except Exception as e:
        print(f"[ERROR] 获取业绩预告失败({ts_code}): {e}")
        return []


def get_repurchase(ts_code):
    """
    获取个股回购记录（repurchase）
    返回: [dict, ...] 按公告日期倒序
    """
    cache_key = f"repurchase_{ts_code}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        df = pro.repurchase(
            ts_code=ts_code,
            fields="ts_code,ann_date,end_date,proposer,amount,high_price,low_price,status,close_date"
        )
        if df.empty:
            return []
        df = df.sort_values("ann_date", ascending=False).head(4)
        result = df.to_dict("records")
        cache_set(cache_key, result, 3600)
        return result
    except Exception as e:
        print(f"[ERROR] 获取回购记录失败({ts_code}): {e}")
        return []


# ============================================================
# P1: 涨停数据接口
# ============================================================

def get_limit_list(trade_date=None):
    """
    获取涨停股票列表（limit_list_d）
    返回: [dict, ...] 含涨停股信息
    """
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y%m%d")
    cache_key = f"limit_list_{trade_date}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        df = pro.limit_list_d(
            trade_date=trade_date,
            fields="ts_code,trade_date,name,close,pct_chg,amount,limit_amount,fund,limit,fd_amount"
        )
        if df.empty:
            return []
        result = df.to_dict("records")
        cache_set(cache_key, result, 600)
        return result
    except Exception as e:
        print(f"[ERROR] 获取涨停列表失败({trade_date}): {e}")
        return []


def get_limit_step(trade_date=None):
    """
    获取连板股票信息（limit_step）
    返回: [dict, ...] 含连板梯队信息
    """
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y%m%d")
    cache_key = f"limit_step_{trade_date}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        df = pro.limit_step(
            trade_date=trade_date,
            fields="ts_code,trade_date,name,close,pct_chg,amount,limit_amount,fund,limit,days,first_time"
        )
        if df.empty:
            return []
        result = df.to_dict("records")
        cache_set(cache_key, result, 600)
        return result
    except Exception as e:
        print(f"[ERROR] 获取连板信息失败({trade_date}): {e}")
        return []


def get_limit_cpt_list(trade_date=None):
    """
    获取涨停概念分布（limit_cpt_list）
    返回: [dict, ...] 含概念涨停家数
    """
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y%m%d")
    cache_key = f"limit_cpt_{trade_date}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        df = pro.limit_cpt_list(
            trade_date=trade_date,
            fields="ts_code,trade_date,name,close,pct_chg,amount,limit_amount,fund,limit,concept"
        )
        if df.empty:
            return []
        result = df.to_dict("records")
        cache_set(cache_key, result, 600)
        return result
    except Exception as e:
        print(f"[ERROR] 获取涨停概念分布失败({trade_date}): {e}")
        return []


# ============================================================
# P1: 板块资金流接口
# ============================================================

def get_ths_members(ts_code):
    """
    获取同花顺概念板块成分股（ths_member）
    ts_code: 概念板块代码，如 "885711.TI"
    返回: [dict, ...]
    """
    cache_key = f"ths_members_{ts_code}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        df = pro.ths_member(
            ts_code=ts_code,
            fields="ts_code,code,name,in_date,out_date"
        )
        if df.empty:
            return []
        result = df.to_dict("records")
        cache_set(cache_key, result, 7200)  # 2小时缓存
        return result
    except Exception as e:
        print(f"[ERROR] 获取概念成分股失败({ts_code}): {e}")
        return []


def get_moneyflow_ind_ths(trade_date=None):
    """
    获取同花顺行业资金流（moneyflow_ind_ths）
    返回: [dict, ...] 按净流入降序
    """
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y%m%d")
    cache_key = f"moneyflow_ths_{trade_date}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        df = pro.moneyflow_ind_ths(
            trade_date=trade_date,
            fields="ts_code,trade_date,name,close,pct_chg,buy_sm_amount,sell_sm_amount,buy_md_amount,"
                   "sell_md_amount,buy_lg_amount,sell_lg_amount,buy_elg_amount,sell_elg_amount,net_mf_amount"
        )
        if df.empty:
            return []
        df = df.sort_values("net_mf_amount", ascending=False)
        result = df.to_dict("records")
        cache_set(cache_key, result, 600)
        return result
    except Exception as e:
        print(f"[ERROR] 获取同花顺行业资金流失败: {e}")
        return []


def get_moneyflow_ind_dc(trade_date=None):
    """
    获取东财行业资金流（moneyflow_ind_dc）
    返回: [dict, ...] 按净流入降序
    """
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y%m%d")
    cache_key = f"moneyflow_dc_{trade_date}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        df = pro.moneyflow_ind_dc(
            trade_date=trade_date,
            fields="ts_code,trade_date,name,close,pct_chg,buy_sm_amount,sell_sm_amount,buy_md_amount,"
                   "sell_md_amount,buy_lg_amount,sell_lg_amount,buy_elg_amount,sell_elg_amount,net_mf_amount"
        )
        if df.empty:
            return []
        df = df.sort_values("net_mf_amount", ascending=False)
        result = df.to_dict("records")
        cache_set(cache_key, result, 600)
        return result
    except Exception as e:
        print(f"[ERROR] 获取东财行业资金流失败: {e}")
        return []
