# -*- coding: utf-8 -*-
"""
helpers.py - 共享辅助函数
从 server.py 提取出来的公共工具函数，供各 Blueprint 模块复用
"""

import json
import os
import threading
import time as _time  # 系统time模块，用于time.time()等
from datetime import datetime, timedelta, time as dtime
from functools import lru_cache

from config import (
    TUSHARE_TOKEN,
    TRADE_MORNING_START, TRADE_MORNING_END,
    TRADE_AFTERNOON_START, TRADE_AFTERNOON_END,
)

# ============================================================
# 延迟导入：pandas / requests / tushare 在首次使用时才加载
# 目的：将启动时间从 ~0.69s 降低到 <0.1s（跳过重型依赖）
# ============================================================

_pd = None
_requests = None
_ts = None
_pro = None


def _get_pd():
    """延迟获取 pandas"""
    global _pd
    if _pd is None:
        import pandas as pd
        _pd = pd
    return _pd


def _get_requests():
    """延迟获取 requests"""
    global _requests
    if _requests is None:
        import requests
        _requests = requests
    return _requests


def _get_pro():
    """延迟初始化 Tushare Pro API 实例（仅首次调用时连接）"""
    global _ts, _pro
    if _pro is None:
        import tushare as ts
        _ts = ts
        ts.set_token(TUSHARE_TOKEN)
        _pro = ts.pro_api()
    return _pro


class _TushareProxy:
    """延迟代理：允许 from helpers import pro，实际使用时才初始化 Tushare"""
    def __getattr__(self, name):
        return getattr(_get_pro(), name)

    def __call__(self, *args, **kwargs):
        return _get_pro()(*args, **kwargs)


# 模块级兼容属性：from helpers import pro 可用，首次访问时自动初始化
pro = _TushareProxy()

# ============================================================
# 内存缓存（TTL Cache）
# 减少对新浪/Tushare 的重复请求，提升 API 响应速度
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
    morning = dtime.fromisoformat(TRADE_MORNING_START)
    morning_end = dtime.fromisoformat(TRADE_MORNING_END)
    afternoon = dtime.fromisoformat(TRADE_AFTERNOON_START)
    afternoon_end = dtime.fromisoformat(TRADE_AFTERNOON_END)
    return (morning <= t <= morning_end) or (afternoon <= t <= afternoon_end)


def should_use_realtime_source():
    """判断是否应该使用实时数据源（新浪财经）。"""
    return True


STOCK_LIST_CACHE_FILE = os.path.join(os.path.dirname(__file__), "data", "stock_list_cache.json")
STOCK_LIST_CACHE_TTL = 24 * 3600  # 缓存24小时

def load_stock_list():
    """加载并缓存股票列表（Tushare stock_basic），优先使用本地缓存"""
    global _stock_list_cache, _stock_list_loaded
    if _stock_list_loaded:
        return _stock_list_cache
    with _stock_list_lock:
        if _stock_list_loaded:
            return _stock_list_cache
        
        # 1. 尝试从本地缓存文件加载
        try:
            if os.path.exists(STOCK_LIST_CACHE_FILE):
                cache_mtime = os.path.getmtime(STOCK_LIST_CACHE_FILE)
                if _time.time() - cache_mtime < STOCK_LIST_CACHE_TTL:
                    with open(STOCK_LIST_CACHE_FILE, 'r', encoding='utf-8') as f:
                        _stock_list_cache = json.load(f)
                        _stock_list_loaded = True
                        print(f"[INFO] 从本地缓存加载股票列表: {len(_stock_list_cache)} 只")
                        return _stock_list_cache
        except Exception as e:
            print(f"[WARN] 读取本地缓存失败: {e}")
        
        # 2. 从Tushare API获取
        try:
            print("[INFO] 从Tushare API获取股票列表...")
            start = _time.time()

            df = _get_pro().stock_basic(exchange="", list_status="L",
                                 fields="ts_code,symbol,name,area,industry,market,list_date")
            _stock_list_cache = df.to_dict("records")
            _stock_list_loaded = True
            print(f"[INFO] 成功加载 {len(_stock_list_cache)} 只股票，耗时 {_time.time()-start:.2f}s")
            
            # 3. 保存到本地缓存
            try:
                os.makedirs(os.path.dirname(STOCK_LIST_CACHE_FILE), exist_ok=True)
                with open(STOCK_LIST_CACHE_FILE, 'w', encoding='utf-8') as f:
                    json.dump(_stock_list_cache, f, ensure_ascii=False)
                print(f"[INFO] 股票列表已缓存到本地")
            except Exception as e:
                print(f"[WARN] 保存本地缓存失败: {e}")
            
            return _stock_list_cache
        except Exception as e:
            print(f"[ERROR] 加载股票列表失败: {e}")
            # 4. 如果API失败但有旧缓存，使用旧缓存
            try:
                if os.path.exists(STOCK_LIST_CACHE_FILE):
                    with open(STOCK_LIST_CACHE_FILE, 'r', encoding='utf-8') as f:
                        _stock_list_cache = json.load(f)
                        _stock_list_loaded = True
                        print(f"[WARN] 使用过期缓存: {len(_stock_list_cache)} 只")
                        return _stock_list_cache
            except:
                pass
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
        # 持仓天数：取最早一笔买入日期
        "first_buy_date": None,
        "hold_days": 0,
    }
    # 计算首次买入日期和持有天数
    buy_trades = [t for t in trades if t.get("trade_type") == "buy"]
    if buy_trades:
        dates = [t.get("buy_date", "") for t in buy_trades if t.get("buy_date")]
        if dates:
            # 取最早的买入日期
            first_date = min(dates)
            try:
                from datetime import datetime as _dt
                first_dt = _dt.strptime(first_date[:10], "%Y-%m-%d") if len(first_date) >= 10 else _dt.strptime(first_date, "%Y-%m-%d")
                position["meta"]["first_buy_date"] = first_dt.strftime("%Y-%m-%d")
                position["meta"]["hold_days"] = max(0, (_dt.now() - first_dt).days)
            except (ValueError, TypeError):
                position["meta"]["first_buy_date"] = dates[0][:10] if len(dates[0]) >= 10 else ""


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
        resp = _get_requests().get(url, params=params, timeout=8)
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
        resp = _get_requests().get(url, headers=headers, timeout=8)
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
    获取实时行情（数据源降级链）
    优先级：新浪财经 → Tushare日线
    codes: Tushare 格式代码列表
    """
    codes = [c.upper() for c in codes]
    if not codes:
        return {}

    # 第一优先：新浪财经
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
    """获取单只股票实时行情（新浪 → Tushare 降级）"""
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
        df = _get_pro().daily(**params)
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

    # 多数据源降级链：新浪财经 → Tushare日线
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

        # ── 获取技术面数据（用于放量破位等高级预警）──
        ma20_val = None
        ma60_val = None
        vol_ratio_val = None
        sector_name = info.get("industry", "")
        sector_chg = 0
        try:
            ts_code = p["ts_code"]
            end_dt = datetime.now().strftime("%Y%m%d")
            df_tech = pro.daily(ts_code=ts_code, end_date=end_dt, limit=65)
            if len(df_tech) >= 20:
                closes = df_tech["close"].tolist()
                vols = df_tech["vol"].tolist()
                ma20_val = round(sum(closes[-20:]) / 20, 3)
                if len(closes) >= 60:
                    ma60_val = round(sum(closes[-60:]) / 60, 3)
                # 量比: 当日成交量 / 近5日均量
                if len(vols) >= 6:
                    ma5_vol = sum(vols[-6:-1]) / 5
                    vol_ratio_val = round(vols[-1] / ma5_vol, 2) if ma5_vol > 0 else 1.0

            # ── 板块涨跌幅（申万行业）注意字段名是 pct_change 而非 pct_chg ──
            try:
                df_sw = pro.sw_daily(trade_date=end_dt, fields="ts_code,name,pct_change")
                # 当日数据未入库，降级到上一交易日
                if df_sw.empty:
                    last_td = _get_last_trade_date()
                    df_sw = pro.sw_daily(trade_date=last_td, fields="ts_code,name,pct_change")
                if not df_sw.empty:
                    sw_name = info.get("industry", "")
                    sw_match = df_sw[df_sw["name"].str.contains(sw_name, na=False)]
                    if not sw_match.empty:
                        sector_chg = float(sw_match.iloc[0]["pct_change"])
                        sector_name = str(sw_match.iloc[0]["name"])
            except Exception:
                pass
        except Exception as e_tech:
            print(f"[WARN] 预警技术数据获取失败 {p['ts_code']}: {e_tech}")

        alerts = _generate_smart_alerts(
            position=p,
            current_price=current_price,
            avg_cost=avg_cost,
            total_volume=total_volume,
            profit_pct=profit_pct,
            ma20=ma20_val,
            ma60=ma60_val,
            vol_ratio=vol_ratio_val,
            stop_loss=p.get("stop_loss"),
            stop_profit=p.get("stop_profit"),
            sector_name=sector_name,
            sector_pct_chg=sector_chg,
        )

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
            # 持仓天数（从 meta 中取）
            "hold_days": meta.get("hold_days", 0),
            "first_buy_date": meta.get("first_buy_date"),
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


def _generate_smart_alerts(position, current_price, avg_cost, total_volume, profit_pct,
                            ma20=None, ma60=None, vol_ratio=None,
                            stop_loss=None, stop_profit=None,
                            sector_name="", sector_pct_chg=0):
    """
    智能预警生成器 — 基于多维度数据自动产生投资建议级预警
    返回 alert 字典列表: [{type, message, level, detail}]
    最多返回3条（按优先级排序：critical > danger > warning > caution > info）

    v3.4.1 新增参数：
      ma20/ma60: 均线值（用于放量破位检测）
      vol_ratio: 量比（当日成交量/MA5成交量）
      stop_loss/stop_profit: 用户设定的止盈止损价
      sector_name/sector_pct_chg: 所属板块及涨跌幅（用于板块异动）
    """
    alerts = []
    if not current_price or current_price <= 0:
        return []

    # ── 0. 止损/止盈触发（最高优先级）──
    if stop_loss and current_price <= stop_loss:
        loss_pct = round((current_price - avg_cost) / avg_cost * 100, 1) if avg_cost else 0
        alerts.append({
            "type": "stop_loss_triggered",
            "message": f"🚨 已跌破止损价 ¥{stop_loss}",
            "level": "critical",
            "detail": f"当前价¥{current_price}已低于止损线，浮亏{loss_pct}%。建议立即执行卖出或确认是否调整策略"
        })
    elif stop_profit and current_price >= stop_profit:
        alerts.append({
            "type": "stop_profit_reached",
            "message": f"已触达止盈价 ¥{stop_profit} 🎯",
            "level": "info",
            "detail": "达到目标价位，可根据趋势决定全部兑现或分批减仓"
        })
    elif stop_loss and stop_loss > 0:
        distance = (current_price - stop_loss) / stop_loss * 100
        if distance <= 10:
            alerts.append({
                "type": "near_stop_loss",
                "message": f"距止损价仅 {distance:.1f}%",
                "level": "caution",
                "detail": f"当前距止损位 ¥{stop_loss} 很近，关注后续走势"
            })

    # ── 1. 深度亏损警报 ─
    if profit_pct <= -15:
        alerts.append({
            "type": "deep_loss",
            "message": f"深度套牢 {profit_pct:.1f}%（¥{abs(round(profit_pct/100*avg_cost*total_volume,0)):,.0f}）",
            "level": "danger",
            "detail": "建议评估是否止损截断或逢低补仓摊低成本"
        })
    elif profit_pct <= -8:
        alerts.append({
            "type": "moderate_loss",
            "message": f"中度亏损 {abs(profit_pct):.1f}%，接近止损线",
            "level": "warning",
            "detail": "若跌破支撑位可考虑止损"
        })

    # ── 2. 大幅盈利提醒（提示止盈）─
    if profit_pct >= 20:
        alerts.append({
            "type": "big_profit",
            "message": f"丰厚盈利 +{profit_pct:.1f}% 🎉，考虑分批止盈",
            "level": "info",
            "detail": "落袋为安，可先卖50%锁定利润"
        })
    elif profit_pct >= 10:
        alerts.append({
            "type": "good_profit",
            "message": f"稳健盈利 +{profit_pct:.1f}%，关注趋势变化",
            "level": "info",
            "detail": "盈利达标，可根据技术面决定持有或减仓"
        })

    # ── 3. 放量破位检测（新增 v3.4.1）──
    if ma20 and vol_ratio and current_price < ma20 and vol_ratio > 1.5:
        below_ma = round((ma20 - current_price) / ma20 * 100, 1)
        alerts.append({
            "type": "volume_breakdown",
            "message": f"⚡ 放量破位！跌穿MA20({below_ma}%↓) 量比{vol_ratio:.1f}",
            "level": "danger",
            "detail": f"价格跌破中期均线MA20(¥{ma20})且放量(量比>1.5)，主力出货信号强烈。若不能快速收回需果断离场"
        })
    elif ma20 and current_price < ma20 and vol_ratio and vol_ratio > 1.2:
        below_ma = round((ma20 - current_price) / ma20 * 100, 1)
        alerts.append({
            "type": "volume_weak_break",
            "message": f"温和放量破MA20 ({below_ma}%↓)",
            "level": "warning",
            "detail": f"价格在均线下方且有一定放量，趋势转弱信号，建议减仓观察"
        })
    elif ma60 and current_price < ma60:
        below_ma = round((ma60 - current_price) / ma60 * 100, 1)
        alerts.append({
            "type": "break_ma60",
            "message": f"跌破长期均线MA60 ({below_ma}%↓)",
            "level": "warning",
            "detail": "价格跌破长期趋势线MA60，中期格局可能转弱"
        })

    # ── 4. 长期持仓检查 ─
    meta = position.get("meta", {}) if isinstance(position.get("meta"), dict) else {}
    hold_days = meta.get("hold_days", 0)
    if hold_days > 90:
        alerts.append({
            "type": "long_hold",
            "message": f"已持有{hold_days}天（{hold_days//30}个月+）",
            "level": "info",
            "detail": "长期持仓需定期复盘基本面和趋势"
        })
    elif hold_days > 30 and profit_pct < -5:
        alerts.append({
            "type": "stagnant_loss",
            "message": f"持{hold_days}天仍亏{abs(profit_pct):.0f}%，需审视逻辑",
            "level": "warning",
            "detail": "买入逻辑是否仍然有效？"
        })

    # ── 5. 单日异动检测 ─
    today_chg = position.get("pct_chg") or 0
    if today_chg is not None:
        if today_chg <= -5:
            alerts.append({
                "type": "big_drop",
                "message": f"今日大跌 {today_chg:.1f}% ⚠️",
                "level": "warning",
                "detail": "单日暴跌需确认是否有利空消息"
            })
        elif today_chg >= 9.5:
            alerts.append({
                "type": "big_rally",
                "message": f"大涨接近涨停 +{today_chg:.1f}% 🔥",
                "level": "info",
                "detail": "强势拉升注意是否放量"
            })
        elif today_chg <= -3:
            alerts.append({
                "type": "noticeable_drop",
                "message": f"今日下跌 {today_chg:.1f}%",
                "level": "caution",
                "detail": "短期回调信号，关注支撑位"
            })

    # ── 6. 板块异动检测（新增 v3.4.1）──
    if sector_name and abs(sector_pct_chg) >= 3:
        direction = "暴涨" if sector_pct_chg > 0 else "暴跌"
        emoji = "🔥" if sector_pct_chg > 0 else "💧"
        alerts.append({
            "type": "sector_anomaly",
            "message": f"{emoji} 板块异动：{sector_name}{direction}{abs(sector_pct_chg):.1f}%",
            "level": "caution" if sector_pct_chg < 0 else "info",
            "detail": f"所属板块今日{'大涨' if sector_pct_chg > 0 else '大跌'}{abs(sector_pct_chg):.1f}%，注意板块轮动风险/机会"
        })

    # 按优先级排序+限制3条
    priority_order = {"critical": 0, "danger": 1, "warning": 2, "caution": 3, "info": 4}
    alerts.sort(key=lambda a: priority_order.get(a["level"], 5))
    return alerts[:3]


# ============================================================
# 大盘指数行情
# ============================================================

INDEX_CODES = {
    "000001.SH": {"name": "上证指数"},
    "399001.SZ": {"name": "深证成指"},
    "399006.SZ": {"name": "创业板指"},
}


def get_index_quotes():
    """获取大盘指数实时行情（数据源：新浪财经 → Tushare）"""
    cache_key = "index_quotes"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    index_codes = list(INDEX_CODES.keys())

    # 第一优先：新浪财经
    try:
        sina_codes = [f"{c.split('.')[1].lower()}{c[:6]}" for c in index_codes]
        sina_url = f"http://hq.sinajs.cn/list={','.join(sina_codes)}"
        headers = {"Referer": "https://finance.sina.com.cn"}
        resp = _get_requests().get(sina_url, headers=headers, timeout=8)
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
        df = _get_pro().adj_factor(**params)
        if df.empty:
            return _get_pd().DataFrame()
        df = df.sort_values("trade_date")
        return df[["trade_date", "adj_factor"]]
    except Exception as e:
        print(f"[ERROR] 获取复权因子失败({ts_code}): {e}")
        return _get_pd().DataFrame()


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
        df = _get_pro().stock_st(fields="ts_code,trade_date")
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
        df = _get_pro().suspend_d(trade_date=trade_date, fields="ts_code,trade_date")
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
    返回: [dict, ...] 按期倒序，百分比字段已转换为小数
    """
    cache_key = f"fina_indicator_{ts_code}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        df = _get_pro().fina_indicator(
            ts_code=ts_code,
            fields="ts_code,ann_date,end_date,roe,roa,grossprofit_margin,netprofit_margin,debt_to_assets,"
                   "eps,yoy_eps,yoy_sales,yoy_equity,yoy_asset,yoy_profit,or_yoy,q_sales,q_profit"
        )
        if df.empty:
            return []
        df = df.sort_values("end_date", ascending=False).head(periods)
        
        # Tushare返回的百分比字段需要转换为小数（如79.0 -> 0.79）
        pct_fields = ['roe', 'roa', 'grossprofit_margin', 'netprofit_margin', 'debt_to_assets',
                      'yoy_eps', 'yoy_sales', 'yoy_equity', 'yoy_asset', 'yoy_profit', 'or_yoy']
        for field in pct_fields:
            if field in df.columns:
                df[field] = df[field] / 100.0
        
        result = _df_to_records(df)
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
        df = _get_pro().income(
            ts_code=ts_code,
            fields="ts_code,ann_date,end_date,revenue,total_profit,n_income,n_income_attr_p,yoy_revenue,yoy_net_profit"
        )
        if df.empty:
            return []
        df = df.sort_values("end_date", ascending=False).head(periods)
        result = _df_to_records(df)
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
        df = _get_pro().forecast(
            ts_code=ts_code,
            fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max,"
                   "summary,exp_date"
        )
        if df.empty:
            return []
        df = df.sort_values("ann_date", ascending=False).head(4)
        result = _df_to_records(df)
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
        df = _get_pro().repurchase(
            ts_code=ts_code,
            fields="ts_code,ann_date,end_date,proposer,amount,high_price,low_price,status,close_date"
        )
        if df.empty:
            return []
        df = df.sort_values("ann_date", ascending=False).head(4)
        result = _df_to_records(df)
        cache_set(cache_key, result, 3600)
        return result
    except Exception as e:
        print(f"[ERROR] 获取回购记录失败({ts_code}): {e}")
        return []


# ============================================================
# P1: 涨停数据接口
# ============================================================

def _df_to_records(df):
    """
    DataFrame 转 records 列表，同时处理 NaN/None（替换为 None 以确保 JSON 合法）
    JSON 标准不含 NaN，pandas 默认 to_dict 会保留 float('nan') 导致序列化失败
    """
    import math
    records = df.to_dict("records")
    cleaned = []
    for row in records:
        cleaned.append({
            k: (None if (v is not None and isinstance(v, float) and math.isnan(v)) else v)
            for k, v in row.items()
        })
    return cleaned


def _get_last_trade_date(fallback_days=5):
    """
    获取上一个交易日（今日之前）。
    涨停/申万板块等统计类数据通常在收盘后约16-17点才完整入库，
    因此取"今日之前的最近交易日"作为降级目标。
    """
    try:
        today = datetime.now().strftime("%Y%m%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=fallback_days + 2)).strftime("%Y%m%d")
        df = _get_pro().trade_cal(exchange="SSE", start_date=start_date, end_date=yesterday)
        df = df[df["is_open"] == 1].sort_values("cal_date", ascending=False)
        if not df.empty:
            return str(df.iloc[0]["cal_date"])
    except Exception:
        pass
    # 保底：取昨天
    return (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")


def get_limit_list(trade_date=None):
    """
    获取涨停股票列表（limit_list_d）
    返回: [dict, ...] 含涨停股信息
    当日无数据时自动降级到上一交易日
    """
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y%m%d")
    cache_key = f"limit_list_{trade_date}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        df = _get_pro().limit_list_d(
            trade_date=trade_date,
            fields="ts_code,trade_date,name,close,pct_chg,amount,limit_amount,fund,limit,fd_amount"
        )
        # 当日交易时段中 Tushare 数据延迟，自动降级到上一交易日
        if df.empty:
            last_td = _get_last_trade_date()
            if last_td != trade_date:
                df = _get_pro().limit_list_d(
                    trade_date=last_td,
                    fields="ts_code,trade_date,name,close,pct_chg,amount,limit_amount,fund,limit,fd_amount"
                )
        if df.empty:
            return []
        result = _df_to_records(df)
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
        df = _get_pro().limit_step(
            trade_date=trade_date,
            fields="ts_code,trade_date,name,close,pct_chg,amount,limit_amount,fund,limit,days,first_time"
        )
        if df.empty:
            last_td = _get_last_trade_date()
            if last_td != trade_date:
                df = _get_pro().limit_step(
                    trade_date=last_td,
                    fields="ts_code,trade_date,name,close,pct_chg,amount,limit_amount,fund,limit,days,first_time"
                )
        if df.empty:
            return []
        result = _df_to_records(df)
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
        df = _get_pro().limit_cpt_list(
            trade_date=trade_date,
            fields="ts_code,trade_date,name,close,pct_chg,amount,limit_amount,fund,limit,concept"
        )
        if df.empty:
            last_td = _get_last_trade_date()
            if last_td != trade_date:
                df = _get_pro().limit_cpt_list(
                    trade_date=last_td,
                    fields="ts_code,trade_date,name,close,pct_chg,amount,limit_amount,fund,limit,concept"
                )
        if df.empty:
            return []
        result = _df_to_records(df)
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
        df = _get_pro().ths_member(
            ts_code=ts_code,
            fields="ts_code,code,name,in_date,out_date"
        )
        if df.empty:
            return []
        result = _df_to_records(df)
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
        df = _get_pro().moneyflow_ind_ths(
            trade_date=trade_date,
            fields="ts_code,trade_date,name,close,pct_chg,buy_sm_amount,sell_sm_amount,buy_md_amount,"
                   "sell_md_amount,buy_lg_amount,sell_lg_amount,buy_elg_amount,sell_elg_amount,net_mf_amount"
        )
        if df.empty:
            return []
        df = df.sort_values("net_mf_amount", ascending=False)
        result = _df_to_records(df)
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
        df = _get_pro().moneyflow_ind_dc(
            trade_date=trade_date,
            fields="ts_code,trade_date,name,close,pct_chg,buy_sm_amount,sell_sm_amount,buy_md_amount,"
                   "sell_md_amount,buy_lg_amount,sell_lg_amount,buy_elg_amount,sell_elg_amount,net_mf_amount"
        )
        if df.empty:
            return []
        df = df.sort_values("net_mf_amount", ascending=False)
        result = _df_to_records(df)
        cache_set(cache_key, result, 600)
        return result
    except Exception as e:
        print(f"[ERROR] 获取东财行业资金流失败: {e}")
        return []


# ============================================================
# 高低点结构分析
# ============================================================

def get_hl_structure(ts_code, n=5, lookback=60):
    """
    获取股票/指数的高低点结构分析（基于Tushare日线数据）
    统一调用 analyze_hl_points() 进行核心分析。
    
    Args:
        ts_code: 股票代码
        n: 极值识别窗口大小（前后各n天）
        lookback: 回溯天数
    
    Returns:
        dict: {structure, score, signal, high_trend, low_trend}
        （兼容旧接口，不含具体点位，仅返回趋势判断）
    """
    try:
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=lookback + 30)).strftime('%Y%m%d')
        
        df = _get_pro().daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is None or len(df) < 20:
            return {"structure": "未知", "score": 50, "signal": "观望", "high_trend": "走平", "low_trend": "走平"}
        
        df = df.sort_values('trade_date')
        
        # 调用统一分析函数
        hl = analyze_hl_points(highs=df['high'].values, lows=df['low'].values, n=n)
        
        # 兼容旧接口格式映射
        trend_map = {"up": "上升", "down": "下降", "flat": "走平"}
        structure_map = {
            "uptrend": ("上升趋势", 70, "买入"),
            "downtrend": ("下降趋势", 30, "卖出"),
            "weak_uptrend": ("偏强震荡", 55, "观望"),
            "weak_downtrend": ("偏弱震荡", 45, "观望"),
            "bottoming": ("底部构建", 60, "关注"),
            "topping": ("顶部构筑", 40, "谨慎"),
            "sideways": ("震荡整理", 50, "观望"),
            "unknown": ("未知", 50, "观望"),
        }
        
        struct_info = structure_map.get(hl["structure"], ("未知", 50, "观望"))
        
        return {
            "structure": struct_info[0],
            "score": struct_info[1],
            "signal": struct_info[2],
            "high_trend": trend_map.get(hl["high_trend"], "走平"),
            "low_trend": trend_map.get(hl["low_trend"], "走平"),
            # 额外返回完整点位数据供前端使用
            "_full": hl,
        }
    except Exception as e:
        print(f"[ERROR] 高低点结构分析失败({ts_code}): {e}")
        return {"structure": "未知", "score": 50, "signal": "观望", "high_trend": "走平", "low_trend": "走平"}


def analyze_hl_points(highs=None, lows=None, n=5):
    """
    统一的高低点结构分析函数（v3.5 统一重构）

    接收数组形式的高/低点数据，返回完整的高低点结构分析结果。
    所有高低点相关逻辑统一收敛到此函数。

    Args:
        highs: 高价数组 (list/np.array)
        lows:  低价数组 (list/np.array)
        n:     极值识别窗口大小（前后各n天），默认5

    Returns:
        dict: {
            "structure": "uptrend" / "downtrend" / "sideways" / "unknown",
            "score": int (-15 ~ 10),
            "signal": str (自然语言描述),
            "high_trend": "up" / "down" / "flat",
            "low_trend":  "up" / "down" / "flat",
            "recent_highs": [(index, price), ...],   # 最近3个高点
            "recent_lows":  [(index, price), ...],   # 最近3个低点
            "hh_count": int,  # 高点创新高次数
            "hl_count": int,  # 低点创新高次数(低点抬高)
            "lh_count": int,  # 高点创新低次数
            "ll_count": int,  # 低点创新低次数
        }

    使用场景：
      - get_hl_structure() 内部调用（大盘指数）
      - get_position_advice() 调用（持仓建议）
      - screener 选股引擎内联调用
      - check_three_views() 三看检查
    """
    result = {
        "structure": "unknown",
        "score": 0,
        "signal": "无法分析",
        "high_trend": "flat",
        "low_trend": "flat",
        "recent_highs": [],
        "recent_lows": [],
        "hh_count": 0, "hl_count": 0, "lh_count": 0, "ll_count": 0,
    }
    
    if highs is None or lows is None:
        return result
    
    try:
        high_arr = list(highs) if not hasattr(highs, '__len__') or isinstance(highs, (list, tuple)) else list(highs)
        low_arr = list(lows) if not hasattr(lows, '__len__') or isinstance(lows, (list, tuple)) else list(lows)
        
        length = len(high_arr)
        if length < 10 or length != len(low_arr):
            result["signal"] = f"数据不足({length}条)"
            return result
        
        # ---- 识别局部极值点：窗口极值法 ----
        high_points = []
        low_points = []
        
        for i in range(n, length - n):
            is_high = all(high_arr[i] > high_arr[i-j] for j in range(1, n+1)) and \
                      all(high_arr[i] > high_arr[i+j] for j in range(1, n+1))
            if is_high:
                high_points.append((i, float(high_arr[i])))
            
            is_low = all(low_arr[i] < low_arr[i-j] for j in range(1, n+1)) and \
                    all(low_arr[i] < low_arr[i+j] for j in range(1, n+1))
            if is_low:
                low_points.append((i, float(low_arr[i])))
        
        # 取最近3个高低点
        recent_highs = high_points[-3:] if len(high_points) >= 3 else high_points
        recent_lows = low_points[-3:] if len(low_points) >= 3 else low_points
        
        result["recent_highs"] = [(idx, round(p, 3)) for idx, p in recent_highs]
        result["recent_lows"] = [(idx, round(p, 3)) for idx, p in recent_lows]
        
        # ---- 判断趋势方向 ----
        high_trend = "flat"
        low_trend = "flat"
        trend_threshold = 0.02  # 2% 阈值
        
        if len(recent_highs) >= 2:
            h_first = recent_highs[0][1]
            h_last = recent_highs[-1][1]
            if h_last > h_first * (1 + trend_threshold):
                high_trend = "up"
            elif h_last < h_first * (1 - trend_threshold):
                high_trend = "down"
        
        if len(recent_lows) >= 2:
            l_first = recent_lows[0][1]
            l_last = recent_lows[-1][1]
            if l_last > l_first * (1 + trend_threshold):
                low_trend = "up"
            elif l_last < l_first * (1 - trend_threshold):
                low_trend = "down"
        
        result["high_trend"] = high_trend
        result["low_trend"] = low_trend
        
        # ---- HH/HL/LH/LL 统计 ----
        hh = sum(1 for i in range(1, len(recent_highs)) if recent_highs[i][1] > recent_highs[i-1][1])
        hl = sum(1 for i in range(1, len(recent_highs)) if recent_highs[i][1] < recent_highs[i-1][1])
        lh = sum(1 for i in range(1, len(recent_lows)) if recent_lows[i][1] > recent_lows[i-1][1])  # 低点抬高
        ll = sum(1 for i in range(1, len(recent_lows)) if recent_lows[i][1] < recent_lows[i-1][1])  # 低点下移
        
        result["hh_count"] = hh
        result["hl_count"] = hl
        result["lh_count"] = lh
        result["ll_count"] = ll
        
        # ---- 结构判定与评分 ----
        if high_trend == "up" and low_trend == "up":
            result["structure"] = "uptrend"
            result["score"] = 10
            result["signal"] = "上升趋势：低点持续抬高，高点突破向上"
        elif high_trend == "down" and low_trend == "down":
            result["structure"] = "downtrend"
            result["score"] = -15
            result["signal"] = "下降趋势：低点不断下移，风险加大"
        elif high_trend == "up" and low_trend == "flat":
            result["structure"] = "weak_uptrend"
            result["score"] = 5
            result["signal"] = "偏强震荡：高点突破但低点待确认"
        elif high_trend == "down" and low_trend == "flat":
            result["structure"] = "weak_downtrend"
            result["score"] = -8
            result["signal"] = "偏弱震荡：高点下移需警惕"
        elif high_trend == "flat" and low_trend == "up":
            result["structure"] = "bottoming"
            result["score"] = 6
            result["signal"] = "底部构建中：低点已抬高，等待高点突破"
        elif high_trend == "flat" and low_trend == "down":
            result["structure"] = "topping"
            result["score"] = -10
            result["signal"] = "顶部构筑中：低点开始下移，注意风险"
        else:
            result["structure"] = "sideways"
            result["score"] = 2
            result["signal"] = "震荡整理：方向不明，等待突破"
            
    except Exception as e:
        print(f"[WARN] analyze_hl_points 分析异常: {e}")
        result["signal"] = f"分析异常: {e}"
    
    return result


# ============================================================
# P2-7: 全面波动率自适应 — ATR 公共计算引擎 (v4.2)
# ============================================================

def calc_atr_profile(closes=None, highs=None, lows=None, latest_price=None):
    """
    [v4.2 P2-7] 统一的ATR波动率分析引擎
    
    计算个股的ATR值和波动率分档，为所有价格锚点参数提供自适应依据。
    替代原来分散在各处的硬编码百分比参数。
    
    Args:
        closes: 收盘价列表 (至少21条)
        highs:  最高价列表
        lows:   最低价列表
        latest_price: 可选实时价格（用于ATR%归一化）
    
    Returns:
        dict: {
            "atr": float,              # 20日平均真实波幅(绝对值)
            "atr_pct": float,          # ATR/现价的百分比
            "tier": str,               # "low" / "medium" / "high" / "extreme"
            "tier_label": str,         # 中文标签如"中波动"
            "stop_loss_pct": float,    # 推荐止损幅度(5%~18%)
            "pressure_offset": float,  # 压力位偏离(+8%~+20%)
            "support_tolerance": float,# 支撑位容忍度(±3%~±12%)
            "target_offset": float,    # 目标位偏离(±8%~±25%)
            "buy_callback": float,     # 买入回调幅度(2%~6%)
            "raw_tr_list": [float],    # 原始TR序列(供高级分析用)
        }
    """
    default = {
        "atr": 0, "atr_pct": 0.03, "tier": "medium", "tier_label": "默认",
        "stop_loss_pct": 0.08, "pressure_offset": 0.12,
        "support_tolerance": 0.05, "target_offset": 0.15,
        "buy_callback": 0.03, "raw_tr_list": [],
    }
    
    if not closes or len(closes) < 21 or not highs or not lows:
        return default
    
    try:
        # 计算20日TR序列
        tr_list = []
        for i in range(max(1, len(closes) - 20), len(closes)):
            h = float(highs[i]) if i < len(highs) else 0
            l = float(lows[i]) if i < len(lows) else 0
            prev_close = float(closes[i - 1]) if i > 0 else closes[i]
            tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
            tr_list.append(tr)
        
        if not tr_list:
            return default
        
        atr = sum(tr_list) / len(tr_list)
        price_ref = latest_price or closes[-1]
        atr_pct = atr / price_ref if price_ref > 0 else 0.03
        
        # ── 四档波动率分类 ──
        if atr_pct <= 0.02:
            tier = "low"
            tier_label = "低波动"
            stop_loss_pct = 0.05
            pressure_offset = 0.08
            support_tolerance = 0.03
            target_offset = 0.08
            buy_callback = 0.02
        elif atr_pct <= 0.04:
            tier = "medium"
            tier_label = "中波动"
            stop_loss_pct = 0.08
            pressure_offset = 0.12
            support_tolerance = 0.05
            target_offset = 0.15
            buy_callback = 0.03
        elif atr_pct <= 0.06:
            tier = "high"
            tier_label = "高波动"
            stop_loss_pct = 0.13
            pressure_offset = 0.16
            support_tolerance = 0.08
            target_offset = 0.22
            buy_callback = 0.05
        else:
            tier = "extreme"
            tier_label = "极端波动"
            stop_loss_pct = 0.18
            pressure_offset = 0.20
            support_tolerance = 0.12
            target_offset = 0.30
            buy_callback = 0.06
        
        return {
            "atr": round(atr, 3),
            "atr_pct": round(atr_pct, 4),
            "tier": tier,
            "tier_label": tier_label,
            "stop_loss_pct": stop_loss_pct,
            "pressure_offset": pressure_offset,
            "support_tolerance": support_tolerance,
            "target_offset": target_offset,
            "buy_callback": buy_callback,
            "raw_tr_list": [round(t, 3) for t in tr_list],
        }
    except Exception as e:
        print(f"[WARN] calc_atr_profile 异常: {e}")
        return default


def calc_price_time_prob(current_price, target_price, atr_profile, direction="up",
                         closes=None, highs=None, lows=None):
    """
    [v4.2 P2-8] 价格位时间预期与达成概率计算引擎
    
    基于ATR波动率、距离百分比、历史K线统计，估算：
    - expected_days: 预计到达目标价的天数（基于日均波幅推算）
    - probability: 达成概率(0~100)，综合距离/趋势/波动率
    
    Args:
        current_price: 当前价格
        target_price: 目标价位
        atr_profile: calc_atr_profile() 返回的字典
        direction: "up"(向上突破) 或 "down"(向下测试支撑)
        closes/highs/lows: K线数据(用于高级概率修正)
    
    Returns:
        dict: {"expected_days": int, "probability": float, "confidence": str}
              confidence: "high" | "medium" | "low"
    """
    if not current_price or not target_price or target_price == 0:
        return {"expected_days": None, "probability": None, "confidence": "none", "distance_pct": None}
    
    try:
        distance_pct = abs(target_price - current_price) / current_price * 100
        atr_pct = atr_profile.get("atr_pct", 0.03)
        tier = atr_profile.get("tier", "medium")
        atr_val = atr_profile.get("atr", 1)
        
        # ── 预期天数：基于ATR日均值推算 ──
        # 基本假设：每天平均移动约0.5*ATR（有方向性的净移动）
        daily_move = max(atr_val * 0.4, current_price * 0.005)  # 日均净移动
        price_gap = abs(target_price - current_price)
        base_days = int(price_gap / daily_move) if daily_move > 0 else 30
        
        # 波动率档位调整：高波动股更快到达但更不确定
        tier_multiplier = {"low": 2.0, "medium": 1.5, "high": 1.0, "extreme": 0.7}.get(tier, 1.5)
        expected_days = max(1, round(base_days * tier_multiplier))
        
        # ── 达成概率计算 ──
        # 核心逻辑：距离越近概率越高，方向越明确概率越高
        if distance_pct <= 3:
            prob = 85  # 很近，大概率触及
        elif distance_pct <= 6:
            prob = 70
        elif distance_pct <= 10:
            prob = 55
        elif distance_pct <= 15:
            prob = 40
        elif distance_pct <= 25:
            prob = 25
        else:
            prob = 15  # 太远，不确定性大
        
        # 方向修正：上升趋势中向上目标加分，下降趋势中向下支撑加分
        if len(closes) and len(closes) >= 10:
            recent_trend = (closes[-1] - closes[-10]) / closes[-10] * 100 if closes[-10] > 0 else 0
            if direction == "up" and recent_trend > 3:
                prob = min(95, prob + 15)
            elif direction == "up" and recent_trend < -5:
                prob = max(5, prob - 20)
            elif direction == "down" and recent_trend < -3:
                prob = min(95, prob + 15)
            elif direction == "down" and recent_trend > 5:
                prob = max(5, prob - 20)
        
        # 波动率修正：极端波动降低确定性
        if tier == "extreme":
            prob = int(prob * 0.75)
        elif tier == "high":
            prob = int(prob * 0.88)
        
        prob = max(5, min(95, prob))
        
        # 置信度分级
        if prob >= 65 and expected_days <= 10:
            confidence = "high"
        elif prob >= 35 and expected_days <= 20:
            confidence = "medium"
        else:
            confidence = "low"
        
        return {
            "expected_days": expected_days,
            "probability": prob,
            "confidence": confidence,
            "distance_pct": round(distance_pct, 1),
            "logic": f"距{distance_pct:.1f}%|ATR{atr_profile['tier_label']}|预计{expected_days}天",
        }
    except Exception as e:
        print(f"[WARN] calc_price_time_prob 异常: {e}")
        return {"expected_days": None, "probability": None, "confidence": "error",
                "distance_pct": None, "logic": f"计算异常: {e}"}


def calc_price_hit_rate(df, support_prices=None, resistance_prices=None):
    """
    [v4.2 P2-9] 历史价位命中率统计引擎
    
    回测近120日K线，统计触及各价位后的反弹/突破率。
    
    核心逻辑：
    - 支撑位命中：价格接近(±2%)该价位后，N日内是否出现上涨反弹
    - 阻力位命中：价格接近(±2%)该价位后，N日内是否被压制或突破
    
    Args:
        df: Tushare日线DataFrame（建议120行以上）
        support_prices: [float, ...] 支撑价列表（如[S1, S2]）
        resistance_prices: [float, ...] 阻力价列表（如[R1, R2]）
    
    Returns:
        dict: {
            "supports": [{"price", "hit_count", "total_touches", "bounce_rate", "avg_bounce_pct"}],
            "resistances": [{"price", "hit_count", "total_touches", "break_rate", "avg_break_pct"}],
            "overall_reliability": float (0~100),
        }
    """
    result = {
        "supports": [],
        "resistances": [],
        "overall_reliability": 60,
        "sample_size": 0,
        "lookback_days": 0,
    }
    
    try:
        if df is None or len(df) < 20:
            return result
        
        df = df.sort_values("trade_date").reset_index(drop=True)
        closes = df["close"].tolist()
        highs = df["high"].tolist()
        lows = df["low"].tolist()
        
        result["sample_size"] = len(closes)
        # 只用最近120天数据做回测
        max_lookback = min(len(closes), 120)
        result["lookback_days"] = max_lookback
        
        window = 5   # 触及判定窗口
        fwd_window = 10  # 前瞻窗口（看未来几天走势）
        tolerance = 0.02  # 价位容忍度±2%
        
        def _analyze_level(target_price, is_support=True):
            """分析单个价位的命中率"""
            touches = []
            
            for i in range(window, max_lookback - fwd_window):
                if is_support:
                    # 支撑位检测：最低价接近目标价
                    day_low = lows[i]
                    if abs(day_low - target_price) / target_price <= tolerance:
                        # 看后续fwd_window天内是否有反弹
                        future_high = max(highs[i+1:i+fwd_window+1]) if i + fwd_window < len(highs) else highs[-1]
                        bounce_pct = (future_high - day_low) / day_low * 100
                        touches.append({
                            "date_index": i,
                            "touch_price": round(day_low, 2),
                            "bounced": bounce_pct > 1.5,  # 反弹>1.5%算有效
                            "bounce_pct": round(bounce_pct, 2),
                        })
                else:
                    # 阻力位检测：最高价接近目标价
                    day_high = highs[i]
                    if abs(day_high - target_price) / target_price <= tolerance:
                        # 看后续fwd_window天内是否突破或回落
                        future_low = min(lows[i+1:i+fwd_window+1]) if i + fwd_window < len(lows) else lows[-1]
                        drop_pct = (day_high - future_low) / day_high * 100
                        touches.append({
                            "date_index": i,
                            "touch_price": round(day_high, 2),
                            "broken": drop_pct <= 1.0,  # 回落<1%算突破成功
                            "drop_pct": round(drop_pct, 2),
                        })
            
            total = len(touches)
            if total == 0:
                return {
                    "price": target_price,
                    "hit_count": 0,
                    "total_touches": 0,
                    "rate": 0,
                    "avg_move_pct": None,
                    "note": f"未触及",
                }
            
            if is_support:
                hit_count = sum(1 for t in touches if t.get("bounced"))
                avg_bounce = sum(t["bounce_pct"] for t in touches) / total
                rate = round(hit_count / total * 100, 1)
                return {
                    "price": target_price,
                    "hit_count": hit_count,
                    "total_touches": total,
                    "bounce_rate": rate,
                    "avg_bounce_pct": round(avg_bounce, 2),
                    "note": f"{hit_count}/{total}次触及后有反弹",
                }
            else:
                broken_count = sum(1 for t in touches if t.get("broken"))
                avg_drop = sum(t["drop_pct"] for t in touches) / total
                rate = round(broken_count / total * 100, 1)
                return {
                    "price": target_price,
                    "hit_count": broken_count,
                    "total_touches": total,
                    "break_rate": rate,
                    "avg_drop_pct": round(avg_drop, 2),
                    "note": f"{broken_count}/{total}次触及后被突破",
                }
        
        # 分析所有传入的价位
        if support_prices:
            for sp in support_prices:
                if sp and sp > 0:
                    result["supports"].append(_analyze_level(sp, is_support=True))
        
        if resistance_prices:
            for rp in resistance_prices:
                if rp and rp > 0:
                    result["resistances"].append(_analyze_level(rp, is_support=False))
        
        # 综合可靠性评分
        all_rates = []
        for s in result["supports"]:
            if s.get("total_touches", 0) > 0:
                all_rates.append(s.get("bounce_rate", 50))
        for r in result["resistances"]:
            if r.get("total_touches", 0) > 0:
                all_rates.append(r.get("break_rate", 50))
        
        if all_rates:
            result["overall_reliability"] = round(sum(all_rates) / len(all_rates), 1)
        
    except Exception as e:
        print(f"[WARN] calc_price_hit_rate 异常: {e}")
    
    return result


def calc_buy_price_anchors(df, latest_realtime=None):
    """
    [P1-6] 买入场景专用价格锚点 — 不依赖avg_cost，纯市场数据驱动
    
    适用场景：选股结果页、自选股详情、策略建议
    特点：
      - 无成本价基准（用户尚未买入）
      - 使用K线极值+均线作为参考
      - 返回4个价位：2个买入区间 + 安全支撑 + 止损底线

    Args:
        df: Tushare日线DataFrame(至少20行), 含 open/high/low/close/vol 列
        latest_realtime: 可选实时价格（优先使用）

    Returns:
        dict: {
            "buy_zone": {"low": float, "high": str, "desc": "回调介入区 ~ 突破确认区"},
            "safety_support": {"price": float, "desc": "安全垫支撑"},
            "stop_loss_line": {"price": float, "desc": "最大亏损容忍"},
            "target_profit": {"price": float, "desc": "第一目标"},
            "latest": float,
            "ma20": float,
        }
    """
    result = {
        "buy_zone": {}, "safety_support": {},
        "stop_loss_line": {}, "target_profit": {},
        "latest": None, "ma20": None, "atr_profile": {},
    }

    try:
        df = df.sort_values("trade_date")
        closes = df["close"].tolist()
        highs = df["high"].tolist()
        lows = df["low"].tolist()

        # [v4.2 P2-7] ATR波动率自适应
        atr = calc_atr_profile(closes=closes, highs=highs, lows=lows,
                               latest_price=latest_realtime or closes[-1])
        result["atr_profile"] = {k: v for k, v in atr.items() if k != "raw_tr_list"}

        # 最新价：实时优先，否则用K线收盘价
        latest = latest_realtime if latest_realtime else closes[-1]
        result["latest"] = round(latest, 2) if latest else None

        # MA20
        ma20 = round(sum(closes[-20:]) / 20, 2) if len(closes) >= 20 else closes[-1]
        result["ma20"] = ma20

        # 近N日极值
        lookback = min(30, len(lows))
        low_n = round(min(lows[-lookback:]), 2)
        high_n = round(max(highs[-lookback:]), 2)

        # [v4.2 P2-7] 买入下沿：近期低点 + ATR回调容忍度（替代硬编码2%）
        cb = atr.get("buy_callback", 0.03)
        buy_low = round(low_n * (1 + cb), 2)
        # 买入上沿：MA20 + ATR压力偏移的一半 或 现价+回调容忍度，取较低者
        po_half = atr.get("pressure_offset", 0.12) * 0.5
        buy_from_ma = round(ma20 * (1 + po_half), 2)
        buy_from_latest = round(latest * (1 + cb), 2) if latest else buy_from_ma
        buy_high = min(buy_from_ma, buy_from_latest)

        result["buy_zone"] = {
            "low": buy_low,
            "high": buy_high,
            "desc": f"回调至¥{buy_low}可轻仓试探 / 突破¥{buy_high}可跟进",
        }

        # 安全垫支撑：近30日最低
        st = atr.get("support_tolerance", 0.05)
        result["safety_support"] = {
            "price": low_n,
            "desc": f"近{lookback}日强支撑 ¥{low_n}，跌破说明判断失误",
        }

        # 止损底线：安全垫下方 - ATR止损幅度（给假突破留余量）
        sl_pct = atr.get("stop_loss_pct", 0.08)
        result["stop_loss_line"] = {
            "price": round(low_n * (1 - sl_pct), 2),
            "desc": f"无条件离场线(ATR{atr['tier_label']},止损{sl_pct*100:.0f}%)",
        }

        # 第一目标：近30日高点 或 MA20+ATR目标偏移
        to_pct = atr.get("target_offset", 0.15)
        target = max(high_n, round(ma20 * (1 + to_pct), 2))
        result["target_profit"] = {
            "price": round(target, 2),
            "desc": f"第一止盈目标(ATR{atr['tier_label']},目标+{to_pct*100:.0f}%)",
        }

    except Exception as e:
        print(f"[WARN] calc_buy_price_anchors 计算失败: {e}")

    return result
