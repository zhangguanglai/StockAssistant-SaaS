# -*- coding: utf-8 -*-
"""A股短线选股引擎 - 基于6步筛选流程 + 100分评分体系"""

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
import tushare as ts

from config import TUSHARE_TOKEN

ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# ============================================================
# 常量
# ============================================================

INDEX_CODE = "000001.SH"  # 上证指数

# 热门政策概念关键词（用于加分项匹配）
HOT_CONCEPTS = [
    "人工智能", "新能源", "半导体", "芯片", "机器人", "数字经济",
    "华为", "特斯拉", "锂电池", "储能", "光伏", "风电",
    "军工", "医药", "创新药", "数据要素", "算力", "CPO",
    "低空经济", "卫星互联网", "量子计算", "鸿蒙", "AIGC",
]


# ============================================================
# 交易日数据缓存机制
# ============================================================

_CACHE_DIR = Path(__file__).parent / "data" / "cache"
_cache_latest_trade_date = None  # 内存缓存：最近交易日日期字符串 YYYYMMDD


def _get_cache_dir():
    """确保缓存目录存在"""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR


def get_latest_trade_date():
    """
    获取最近的交易日日期（缓存结果，每个进程只查一次）
    返回: "YYYYMMDD" 格式字符串
    """
    global _cache_latest_trade_date
    if _cache_latest_trade_date is not None:
        return _cache_latest_trade_date

    today = datetime.now().strftime("%Y%m%d")
    for i in range(10):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
        try:
            df = pro.trade_cal(exchange="SSE", start_date=d, end_date=d, is_open="1")
            if not df.empty:
                _cache_latest_trade_date = d
                return d
        except Exception:
            continue

    # fallback: 用今天
    _cache_latest_trade_date = today
    return today


def is_trading_hours():
    """
    判断当前是否在A股交易时段内（周一~周五 9:15~15:00）
    非交易时段时应从缓存读取上一交易日数据
    """
    now = datetime.now()
    # 周末
    if now.weekday() >= 5:
        return False
    # 非交易时段（9:15之前，15:00之后）
    h, m = now.hour, now.minute
    if h < 9 or (h == 9 and m < 15) or h >= 15:
        return False
    # 再验证今天是否交易日（排除节假日）
    today_str = now.strftime("%Y%m%d")
    try:
        df = pro.trade_cal(exchange="SSE", start_date=today_str, end_date=today_str, is_open="1")
        return not df.empty
    except Exception:
        return True  # 查询失败时假定为交易时段


def get_recent_trade_dates(days=5):
    """
    获取最近N个交易日日期（降序排列）
    返回: ["YYYYMMDD", ...]
    """
    dates = []
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days * 3)).strftime("%Y%m%d")
    try:
        df = pro.trade_cal(exchange="SSE", start_date=start_date, end_date=end_date, is_open="1")
        if not df.empty:
            dates = df.sort_values("cal_date", ascending=False)["cal_date"].tolist()[:days]
    except Exception:
        pass
    return dates


def _load_cache(cache_key):
    """
    从缓存目录加载最近的缓存文件
    cache_key: 如 "concept_boards", "money_flow"
    返回: (data, trade_date) 或 (None, None)
    """
    cache_dir = _get_cache_dir()
    prefix = f"{cache_key}_"
    # 找到所有匹配文件，按日期降序排列
    matched = sorted(
        [f for f in cache_dir.iterdir() if f.name.startswith(prefix) and f.suffix == ".json"],
        key=lambda f: f.stem.split("_")[-1],
        reverse=True,
    )
    if not matched:
        return None, None
    try:
        filepath = matched[0]
        trade_date = filepath.stem.split("_")[-1]
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data, trade_date
    except Exception as e:
        print(f"[WARN] 读取缓存失败({cache_key}): {e}")
        return None, None


def _save_cache(cache_key, data, trade_date):
    """
    保存数据到缓存目录
    cache_key: 如 "concept_boards", "money_flow"
    data: 要缓存的数据（需可JSON序列化）
    trade_date: "YYYYMMDD" 格式
    """
    try:
        cache_dir = _get_cache_dir()
        filepath = cache_dir / f"{cache_key}_{trade_date}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        print(f"[CACHE] 已缓存 {cache_key} → {trade_date}")
    except Exception as e:
        print(f"[WARN] 写入缓存失败({cache_key}): {e}")


def _cleanup_old_cache(cache_key, keep_days=5):
    """清理过期缓存，只保留最近 N 天的"""
    try:
        cache_dir = _get_cache_dir()
        prefix = f"{cache_key}_"
        matched = sorted(
            [f for f in cache_dir.iterdir() if f.name.startswith(prefix) and f.suffix == ".json"],
            key=lambda f: f.stem.split("_")[-1],
            reverse=True,
        )
        for old_file in matched[keep_days:]:
            old_file.unlink()
    except Exception:
        pass


# ============================================================
# 数据获取层
# ============================================================

def get_stock_list():
    """获取全部A股列表"""
    df = pro.stock_basic(exchange="", list_status="L",
                         fields="ts_code,symbol,name,area,industry,market,list_date")
    return df.to_dict("records")


def get_daily(ts_code, days=30):
    """获取个股日K线（最近N天，按日期升序），带重试"""
    end_date = datetime.now().strftime("%Y%m%d")
    for attempt in range(3):
        try:
            if attempt > 0:
                time.sleep(1 * attempt)
            df = pro.daily(ts_code=ts_code, end_date=end_date, limit=days)
            if df.empty:
                return []
            df = df.sort_values("trade_date")
            return df.to_dict("records")
        except Exception as e:
            if attempt == 2:
                print(f"[WARN] get_daily失败({ts_code}): {e}")
                return []
    return []


def get_daily_basic(ts_code, days=30):
    """获取每日基本面指标（换手率、流通市值等），带重试"""
    end_date = datetime.now().strftime("%Y%m%d")
    for attempt in range(3):
        try:
            if attempt > 0:
                time.sleep(1 * attempt)
            df = pro.daily_basic(ts_code=ts_code, end_date=end_date, limit=days,
                                 fields="ts_code,trade_date,turnover_rate,circ_mv")
            if df.empty:
                return []
            df = df.sort_values("trade_date")
            return df.to_dict("records")
        except Exception as e:
            if attempt == 2:
                print(f"[WARN] get_daily_basic失败({ts_code}): {e}")
                return []
    return []


def get_index_daily(index_code="000001.SH", days=30):
    """获取大盘指数日K"""
    end_date = datetime.now().strftime("%Y%m%d")
    df = pro.index_daily(ts_code=index_code, end_date=end_date, limit=days)
    if df.empty:
        return []
    df = df.sort_values("trade_date")
    return df.to_dict("records")


_ths_index_cache = {}  # ts_code -> name 的映射缓存


def _build_ths_index_map():
    """
    构建 ths_index 的 ts_code -> name 映射（只加载一次）
    """
    global _ths_index_cache
    if _ths_index_cache:
        return _ths_index_cache
    try:
        df = pro.ths_index(fields="ts_code,name")
        if not df.empty:
            _ths_index_cache = dict(zip(df["ts_code"], df["name"]))
    except Exception as e:
        print(f"[WARN] 获取概念板块名称映射失败: {e}")
    return _ths_index_cache


def get_concept_board_data():
    """
    获取概念板块涨幅排行（Tushare ths_daily 接口）
    交易时段实时获取并缓存，非交易时段从上一交易日缓存读取
    返回: [{concept_code, concept_name, change_pct, ...}, ...]
    """
    trade_date = get_latest_trade_date()

    # 非交易时段：优先从缓存读取，但检查是否过期（超过1个交易日）
    if not is_trading_hours():
        cached, cached_date = _load_cache("concept_boards")
        if cached:
            # 检查缓存是否过期（超过1个交易日）
            if cached_date and trade_date:
                try:
                    from datetime import datetime
                    cache_dt = datetime.strptime(cached_date, "%Y%m%d")
                    trade_dt = datetime.strptime(trade_date, "%Y%m%d")
                    days_diff = (trade_dt - cache_dt).days
                    if days_diff > 1:
                        print(f"[CACHE] 板块缓存已过期（{cached_date} vs 最新交易日{trade_date}），尝试获取最新数据...")
                        # 尝试获取最新数据，失败再回退到缓存
                    else:
                        print(f"[CACHE] 非交易时段，使用 {cached_date} 的板块缓存数据")
                        return cached
                except Exception:
                    print(f"[CACHE] 非交易时段，使用 {cached_date} 的板块缓存数据")
                    return cached
            else:
                print(f"[CACHE] 非交易时段，使用 {cached_date} 的板块缓存数据")
                return cached
        print(f"[CACHE] 未找到板块缓存，尝试实时获取...")

    try:
        df = pro.ths_daily(trade_date=trade_date,
                           fields="ts_code,trade_date,pct_change,close,vol,turnover_rate")
        if df.empty:
            print(f"[WARN] ths_daily 返回空数据 ({trade_date})")
            cached, cached_date = _load_cache("concept_boards")
            if cached:
                print(f"[CACHE] 降级使用 {cached_date} 的板块缓存")
                return cached
            return []

        # 构建 ts_code -> name 映射
        name_map = _build_ths_index_map()

        boards = []
        for _, row in df.iterrows():
            ts_code = row["ts_code"]
            boards.append({
                "concept_code": ts_code,
                "concept_name": name_map.get(ts_code, ts_code),
                "change_pct": float(row["pct_change"]),
                "vol": float(row.get("vol", 0)),
                "turnover_rate": float(row.get("turnover_rate", 0)),
            })

        # 按涨幅降序排列
        boards.sort(key=lambda x: x["change_pct"], reverse=True)

        if boards:
            _save_cache("concept_boards", boards, trade_date)
            _cleanup_old_cache("concept_boards", keep_days=5)
        return boards
    except Exception as e:
        print(f"[ERROR] Tushare ths_daily 获取概念板块失败: {e}")
        # 降级到缓存
        cached, cached_date = _load_cache("concept_boards")
        if cached:
            print(f"[CACHE] 降级使用 {cached_date} 的板块缓存")
            return cached
        return []


def get_batch_daily_basic(trade_date=None):
    """
    Tushare 按交易日批量获取全市场当日基本面指标
    返回 DataFrame 或空: ts_code, close, pct_chg, turnover_rate, circ_mv, vol, amount
    一次 API 调用替代 ~5000 次逐只查询
    """
    if not trade_date:
        # 尝试最近交易日
        end_date = datetime.now().strftime("%Y%m%d")
        for i in range(7):
            d = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
            try:
                df = pro.daily_basic(trade_date=d,
                                     fields="ts_code,close,pct_chg,turnover_rate,circ_mv,vol,amount,pe,pb")
                if not df.empty:
                    return df
            except Exception:
                continue
        return None
    try:
        df = pro.daily_basic(trade_date=trade_date,
                             fields="ts_code,close,pct_chg,turnover_rate,circ_mv,vol,amount,pe,pb")
        return df
    except Exception as e:
        print(f"[WARN] get_batch_daily_basic失败: {e}")
        return None


def get_batch_daily(trade_date=None):
    """
    Tushare 按交易日批量获取全市场当日行情
    返回 DataFrame: ts_code, open, high, low, close, vol, pct_chg
    """
    if not trade_date:
        end_date = datetime.now().strftime("%Y%m%d")
        for i in range(7):
            d = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
            try:
                df = pro.daily(trade_date=d,
                               fields="ts_code,open,high,low,close,vol,pct_chg,amount")
                if not df.empty:
                    return df
            except Exception:
                continue
        return None
    try:
        df = pro.daily(trade_date=trade_date,
                       fields="ts_code,open,high,low,close,vol,pct_chg,amount")
        return df
    except Exception as e:
        print(f"[WARN] get_batch_daily失败: {e}")
        return None


def get_batch_daily_multi(days=45):
    """
    批量获取近N个交易日全市场日线数据，用于超跌反弹策略性能优化
    替代逐只 get_daily(ts_code, days=40)，从 O(N*API) → O(days*API)
    返回: {ts_code: [{"trade_date","open","high","low","close","vol","pct_chg"}, ...]} (按日期升序)
    """
    trade_dates = get_recent_trade_dates(days)
    if not trade_dates:
        return {}

    result = {}  # ts_code -> list of records (升序)

    for i, td in enumerate(trade_dates):
        cache_key = f"daily_batch_{td}"
        cached, _ = _load_cache(cache_key)
        df = None
        if cached:
            import pandas as pd
            df = pd.DataFrame(cached)
        else:
            try:
                df = pro.daily(trade_date=td,
                               fields="ts_code,trade_date,open,high,low,close,vol,pct_chg")
                if df is not None and not df.empty:
                    _save_cache(cache_key, df.to_dict("records"), td)
                    _cleanup_old_cache(cache_key, keep_days=3)
            except Exception as e:
                print(f"[WARN] get_batch_daily_multi ({td}) 失败: {e}")
                continue

        if df is None or df.empty:
            continue

        for _, row in df.iterrows():
            ts_code = row["ts_code"]
            rec = {
                "trade_date": str(row["trade_date"]),
                "open": float(row.get("open", 0) or 0),
                "high": float(row.get("high", 0) or 0),
                "low": float(row.get("low", 0) or 0),
                "close": float(row.get("close", 0) or 0),
                "vol": float(row.get("vol", 0) or 0),
                "pct_chg": float(row.get("pct_chg", 0) or 0),
            }
            if ts_code not in result:
                result[ts_code] = []
            result[ts_code].append(rec)

    # 按日期升序排列（trade_dates 是降序的，所以 append 后是降序，需要 reverse）
    for ts_code in result:
        result[ts_code].sort(key=lambda r: r["trade_date"])

    return result


def get_hot_board_constituents(concept_boards, threshold_pct=0.5, max_boards=10):
    """
    获取热门板块的成分股列表（反向筛选：先确定板块，再从板块中选股）
    使用 Tushare concept_detail 获取板块成分股
    返回: {ts_code: {"concepts": [...], "max_board_pct": float, "max_board_name": str}, ...}
    """
    # 筛出热门板块
    hot_boards = [b for b in concept_boards if b["change_pct"] >= threshold_pct]
    hot_boards.sort(key=lambda x: x["change_pct"], reverse=True)
    hot_boards = hot_boards[:max_boards]

    if not hot_boards:
        return {}

    # 通过 Tushare concept_detail 获取每个热门板块的成分股
    stock_board_map = {}  # ts_code -> {"concepts": [...], "max_board_pct": float, "max_board_name": str}
    name_map = _build_ths_index_map()
    # 反向映射：name -> concept_code（concept_detail 用的是板块名称的 ts_code）
    code_map = {v: k for k, v in name_map.items()}

    for board in hot_boards:
        board_name = board["concept_name"]
        board_code = board["concept_code"]
        if not board_code:
            continue
        try:
            # Tushare concept_detail: 用板块 ts_code 查成分股
            # 注意：concept_detail 用的是概念 ts_code（如 885562.TI），不是 700xxx
            # 但实际 concept_detail 的 id 字段使用的是另一种编码，需要尝试
            df = pro.concept_detail(id=board_code.replace(".TI", ""),
                                     fields="ts_code,name,concepts")
            if df is None or df.empty:
                # 尝试直接用原始 code
                continue

            for _, row in df.iterrows():
                ts_code = row["ts_code"]
                if ts_code not in stock_board_map:
                    stock_board_map[ts_code] = {
                        "concepts": [],
                        "max_board_pct": 0,
                        "max_board_name": "",
                    }
                stock_board_map[ts_code]["concepts"].append(board_name)
                if board["change_pct"] > stock_board_map[ts_code]["max_board_pct"]:
                    stock_board_map[ts_code]["max_board_pct"] = board["change_pct"]
                    stock_board_map[ts_code]["max_board_name"] = board_name
        except Exception as e:
            print(f"[WARN] 获取板块{board_name}成分股失败: {e}")
            # 降级：尝试东方财富接口
            try:
                url = "https://push2.eastmoney.com/api/qt/clist/get"
                params = {
                    "pn": "1", "pz": "500", "po": "1", "np": "1",
                    "fltt": "2", "invt": "2", "fid": "f3",
                    "fs": f"b:{board_code}+f:!50",
                    "fields": "f12,f14,f3,f4,f8,f6,f7,f15,f16,f17,f18",
                }
                resp = requests.get(url, params=params, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                items = data.get("data", {}).get("diff", []) or []

                for item in items:
                    code = str(item.get("f12", ""))
                    if not code:
                        continue
                    raw = code.zfill(6)
                    secid_type = item.get("f13", 0)
                    ts_code = f"{raw}.SH" if secid_type == 1 else f"{raw}.SZ"
                    if ts_code not in stock_board_map:
                        stock_board_map[ts_code] = {
                            "concepts": [], "max_board_pct": 0, "max_board_name": "",
                        }
                    stock_board_map[ts_code]["concepts"].append(board_name)
                    if board["change_pct"] > stock_board_map[ts_code]["max_board_pct"]:
                        stock_board_map[ts_code]["max_board_pct"] = board["change_pct"]
                        stock_board_map[ts_code]["max_board_name"] = board_name
            except Exception as e2:
                print(f"[WARN] 板块{board_name}东方财富降级也失败: {e2}")
            continue

    return stock_board_map


def get_batch_money_flow(trade_date=None):
    """
    Tushare 批量获取全市场某日资金流向数据（1次API vs ~5000次逐只请求）
    返回: DataFrame(ts_code, trade_date, buy_lg_amount, sell_lg_amount, buy_elg_amount, sell_elg_amount, net_mf_amount)
    或 None
    """
    if not trade_date:
        trade_date = get_latest_trade_date()

    # 优先从缓存读取
    if not is_trading_hours():
        cached, cached_date = _load_cache("money_flow_batch")
        if cached:
            print(f"[CACHE] 使用 {cached_date} 的批量资金流向缓存")
            return cached

    try:
        df = pro.moneyflow(trade_date=trade_date,
                           fields="ts_code,trade_date,buy_lg_amount,sell_lg_amount,"
                                  "buy_elg_amount,sell_elg_amount,net_mf_amount")
        if df.empty:
            return None
        # 缓存
        _save_cache("money_flow_batch", df.to_dict("records"), trade_date)
        _cleanup_old_cache("money_flow_batch", keep_days=3)
        return df
    except Exception as e:
        print(f"[WARN] Tushare moneyflow 批量获取失败: {e}")
        # 降级到缓存
        cached, cached_date = _load_cache("money_flow_batch")
        if cached:
            print(f"[CACHE] 降级使用 {cached_date} 的批量资金流向缓存")
            # 重新构建 DataFrame
            import pandas as pd
            return pd.DataFrame(cached)
        return None


def get_batch_money_flow_multi(days=5):
    """
    Tushare 批量获取近N日全市场资金流向数据
    返回: {ts_code: [main_net_in_1, main_net_in_2, ...]} （按日期降序）
    """
    trade_dates = get_recent_trade_dates(days)
    if not trade_dates:
        return {}

    result = {}  # ts_code -> [net_mf_amount_by_date]
    for i, td in enumerate(trade_dates):
        # 优先缓存
        cached, cached_date = _load_cache(f"money_flow_batch_{td}")
        df = None
        if cached:
            print(f"[CACHE] 使用 {cached_date} 的资金流向缓存 (第{i+1}/{len(trade_dates)}天)")
            import pandas as pd
            df = pd.DataFrame(cached)
        else:
            try:
                df = pro.moneyflow(trade_date=td,
                                   fields="ts_code,trade_date,net_mf_amount")
                if not df.empty:
                    _save_cache(f"money_flow_batch_{td}", df.to_dict("records"), td)
                    _cleanup_old_cache(f"money_flow_batch_{td}", keep_days=3)
            except Exception as e:
                print(f"[WARN] Tushare moneyflow ({td}) 获取失败: {e}")
                continue

        if df is None or df.empty:
            continue

        for _, row in df.iterrows():
            ts_code = row["ts_code"]
            if ts_code not in result:
                result[ts_code] = []
            result[ts_code].append(float(row["net_mf_amount"]))

    return result


def get_stock_money_flow(ts_code, days=5, batch_data=None):
    """
    获取个股主力资金流向（近N日）
    优先从批量数据中提取，降级到东方财富逐只获取
    batch_data: get_batch_money_flow_multi() 返回的批量数据
    返回: [{date, main_net_in, ...}, ...]
    """
    # 优先从批量数据中提取
    if batch_data and ts_code in batch_data:
        values = batch_data[ts_code][:days]
        if values:
            trade_dates = get_recent_trade_dates(days)
            result = []
            for i, val in enumerate(values):
                result.append({
                    "date": trade_dates[i] if i < len(trade_dates) else "",
                    "main_net_in": val,
                    "small_net_in": 0,
                    "mid_net_in": 0,
                    "large_net_in": 0,
                })
            return result

    # 降级：东方财富逐只获取
    if not is_trading_hours():
        trade_date = get_latest_trade_date()
        cached, cached_date = _load_money_flow_cache(ts_code)
        if cached:
            return cached[:days]

    # 将 ts_code 转为东方财富格式
    code_raw = ts_code.split(".")[0]
    if ts_code.endswith(".SH"):
        secid = f"1.{code_raw}"
    else:
        secid = f"0.{code_raw}"

    url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
    params = {
        "lmt": str(days),
        "klt": "101",
        "secid": secid,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        klines = data.get("data", {}).get("klines", [])
        result = []
        for kline in klines:
            parts = kline.split(",")
            if len(parts) >= 10:
                result.append({
                    "date": parts[0],
                    "main_net_in": float(parts[1]) if parts[1] else 0,
                    "small_net_in": float(parts[4]) if parts[4] else 0,
                    "mid_net_in": float(parts[3]) if parts[3] else 0,
                    "large_net_in": float(parts[2]) if parts[2] else 0,
                })
        if result and is_trading_hours():
            _save_money_flow_cache(ts_code, result)
        return result
    except Exception as e:
        cached, cached_date = _load_money_flow_cache(ts_code)
        if cached:
            print(f"[CACHE] 资金流向实时获取失败({ts_code})，使用 {cached_date} 缓存")
            return cached[:days]
        return []


_money_flow_mem_cache = {}  # 内存缓存：trade_date -> {ts_code: [flow_data]}
_money_flow_cache_date = None  # 当前内存缓存对应的交易日


def _load_money_flow_cache(ts_code):
    """
    从缓存加载单只股票的资金流向数据
    返回: (flow_data, trade_date) 或 (None, None)
    """
    global _money_flow_mem_cache, _money_flow_cache_date

    trade_date = get_latest_trade_date()

    # 如果内存缓存是今天的数据，直接用
    if _money_flow_cache_date == trade_date:
        data = _money_flow_mem_cache.get(ts_code)
        if data:
            return data, trade_date

    # 内存缓存过期，从磁盘加载
    _money_flow_mem_cache = {}
    _money_flow_cache_date = None

    cached, cached_date = _load_cache("money_flow")
    if cached and isinstance(cached, dict):
        _money_flow_mem_cache = cached
        _money_flow_cache_date = cached_date
        data = cached.get(ts_code)
        if data:
            return data, cached_date

    return None, None


def _save_money_flow_cache(ts_code, flow_data):
    """缓存单只股票的资金流向数据（批量写入磁盘）"""
    global _money_flow_mem_cache, _money_flow_cache_date

    trade_date = get_latest_trade_date()
    _money_flow_mem_cache[ts_code] = flow_data
    _money_flow_cache_date = trade_date

    # 磁盘写入频率控制：每50只写一次（避免IO风暴）
    if len(_money_flow_mem_cache) % 50 == 0:
        _save_cache("money_flow", _money_flow_mem_cache, trade_date)


def _flush_money_flow_cache():
    """将内存中所有资金流向缓存刷到磁盘"""
    global _money_flow_mem_cache, _money_flow_cache_date
    if _money_flow_mem_cache and _money_flow_cache_date:
        trade_date = _money_flow_cache_date
        _save_cache("money_flow", _money_flow_mem_cache, trade_date)
        _cleanup_old_cache("money_flow", keep_days=3)


_concept_cache = {}  # ts_code -> [concept_name, ...]


def get_stock_concepts_eastmoney(ts_code):
    """
    获取单只股票所属的概念板块名称列表
    优先使用 Tushare concept_detail（稳定），失败时回退东方财富接口
    """
    global _concept_cache
    if ts_code in _concept_cache:
        return _concept_cache[ts_code]

    # 方法1：Tushare concept_detail（稳定可靠）
    try:
        df = pro.concept_detail(ts_code=ts_code, fields="ts_code,concept_name")
        if not df.empty:
            concepts = df["concept_name"].dropna().tolist()
            _concept_cache[ts_code] = concepts
            return concepts
    except Exception as e:
        print(f"[WARN] Tushare concept_detail失败({ts_code}): {e}")

    # 方法2：东方财富接口（备用）
    code_raw = ts_code.split(".")[0]
    secid = f"1.{code_raw}" if ts_code.endswith(".SH") else f"0.{code_raw}"
    url = "https://push2.eastmoney.com/api/qt/slist/get"
    params = {
        "secid": secid,
        "fields": "f12,f14",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
    }
    try:
        resp = requests.get(url, params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", {}).get("diff", []) or []
        concepts = [item.get("f14", "") for item in items if item.get("f14")]
        _concept_cache[ts_code] = concepts
        return concepts
    except Exception:
        _concept_cache[ts_code] = []
        return []


def get_limit_list(trade_date):
    """获取某日涨停股票列表"""
    try:
        df = pro.limit_list_d(trade_date=trade_date,
                              fields="ts_code,trade_date,name,close,open,amount,limit_amount,pct_chg,first_time,last_time,open_times,limit,fd_amount")
        if df.empty:
            return []
        return df.to_dict("records")
    except Exception:
        return []


def get_market_sentiment():
    """
    获取市场情绪指标
    返回: {limit_up_count, limit_down_count, ...}
    """
    url = "https://push2ex.eastmoney.com/getTopicZTPool"
    params = {
        "ut": "7eea3edcaed734bea9cb3f6ac2de5f15",
        "dpt": "wz.ztzt",
        "Ession": "1",
        "date": datetime.now().strftime("%Y%m%d"),
        "_": str(int(time.time() * 1000)),
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        pool = data.get("data", {}).get("pool", [])
        return {
            "limit_up_count": len(pool),
        }
    except Exception:
        return {"limit_up_count": 0}


# ============================================================
# 指标计算层
# ============================================================

def calc_ma(closes, period):
    """计算移动平均线"""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def calc_ma_series(closes, period):
    """计算MA序列"""
    result = []
    for i in range(len(closes)):
        if i < period - 1:
            result.append(None)
        else:
            result.append(sum(closes[i - period + 1:i + 1]) / period)
    return result


def calc_macd(closes, fast=12, slow=26, signal=9):
    """
    计算MACD指标
    返回: {dif_values, dea_values, macd_values}
    """
    if len(closes) < slow + signal:
        return None

    # EMA计算
    ema_fast = [closes[0]]
    ema_slow = [closes[0]]

    for i in range(1, len(closes)):
        ema_fast.append(closes[i] * (2 / (fast + 1)) + ema_fast[-1] * (1 - 2 / (fast + 1)))
        ema_slow.append(closes[i] * (2 / (slow + 1)) + ema_slow[-1] * (1 - 2 / (slow + 1)))

    dif = [f - s for f, s in zip(ema_fast, ema_slow)]

    dea = [dif[0]]
    for i in range(1, len(dif)):
        dea.append(dif[i] * (2 / (signal + 1)) + dea[-1] * (1 - 2 / (signal + 1)))

    macd_hist = [2 * (d - e) for d, e in zip(dif, dea)]

    return {"dif": dif, "dea": dea, "macd": macd_hist}


def calc_slope(values, period=5):
    """计算序列末端斜率（变化率）"""
    if len(values) < period + 1:
        return 0
    old_val = values[-period - 1]
    new_val = values[-1]
    if old_val == 0:
        return 0
    return (new_val - old_val) / old_val


# ============================================================
# 第1步：大盘环境判断
# ============================================================

def check_market_environment():
    """
    判断大盘当前环境
    返回: {status: "上升"/"震荡"/"下降", slope, ma20, description}
    """
    records = get_index_daily(INDEX_CODE, days=30)
    if len(records) < 20:
        return {"status": "unknown", "slope": 0, "description": "数据不足"}

    closes = [float(r["close"]) for r in records]
    ma20 = calc_ma(closes, 20)
    slope = calc_slope(closes, 5)

    # MA20斜率判断
    ma20_values = calc_ma_series(closes, 20)
    recent_ma20 = [v for v in ma20_values[-6:] if v is not None]
    ma20_slope = calc_slope(recent_ma20, 3) if len(recent_ma20) >= 4 else 0

    last_close = closes[-1]

    if ma20_slope > 0.002 and last_close > ma20:
        status = "上升"
    elif ma20_slope >= -0.002 and last_close > ma20 * 0.98:
        status = "震荡"
    else:
        status = "下降"

    return {
        "status": status,
        "slope": round(slope, 4),
        "ma20": round(ma20, 2),
        "ma20_slope": round(ma20_slope, 4),
        "last_close": round(last_close, 2),
        "description": f"大盘{'上升' if status == '上升' else '震荡' if status == '震荡' else '下降'}，MA20={ma20:.2f}，斜率={ma20_slope:.4f}",
    }


# ============================================================
# 第2步：基础过滤
# ============================================================

def basic_filter(stock_list, daily_cache, basic_cache, batch_basic_df=None, batch_daily_df=None, filter_params=None):
    """
    基础过滤：排除ST、停牌、新股、追高票、市值不合的
    支持批量数据预过滤（大幅减少API调用次数）
    :param filter_params: 可调参数覆盖 {"min_circ_mv": 50, "max_circ_mv": 300, "min_turnover": 3}
    返回: 通过的股票列表
    """
    # 解析参数
    fp = filter_params or {}
    p_min_circ_mv = fp.get("min_circ_mv", 50)
    p_max_circ_mv = fp.get("max_circ_mv", 300)
    p_min_turnover = fp.get("min_turnover", 3)

    today = datetime.now()
    list_date_threshold = (today - timedelta(days=60)).strftime("%Y%m%d")
    passed = []

    # 预处理批量数据，构建快速查找字典
    basic_map = {}  # ts_code -> row
    if batch_basic_df is not None and not batch_basic_df.empty:
        basic_map = dict(zip(batch_basic_df["ts_code"], batch_basic_df.to_dict("records")))

    daily_today_map = {}  # ts_code -> row
    if batch_daily_df is not None and not batch_daily_df.empty:
        daily_today_map = dict(zip(batch_daily_df["ts_code"], batch_daily_df.to_dict("records")))

    # P0: 批量获取 ST 股票集合和停牌股票集合
    st_stocks = set()
    suspended_stocks = set()
    try:
        from helpers import get_st_stocks, get_suspended_stocks
        st_stocks = get_st_stocks()
        suspended_stocks = get_suspended_stocks()
        if st_stocks:
            print(f"[P0] ST股票过滤: 检测到 {len(st_stocks)} 只ST股票")
        if suspended_stocks:
            print(f"[P0] 停牌股票过滤: 检测到 {len(suspended_stocks)} 只停牌股票")
    except Exception as e:
        print(f"[P0] ST/停牌过滤初始化失败，回退到名称匹配: {e}")

    for idx, stock in enumerate(stock_list):
        ts_code = stock["ts_code"]
        name = stock.get("name", "")

        # P0 排除ST：优先用 stock_st API 精确匹配，回退到名称匹配
        if st_stocks and ts_code in st_stocks:
            continue
        if not st_stocks:
            # 回退：名称匹配（兼容API调用失败的情况）
            if "ST" in name or "st" in name:
                continue
            if name.startswith("*"):
                continue

        # P0 排除停牌
        if suspended_stocks and ts_code in suspended_stocks:
            continue

        # 排除上市不满60天
        list_date = stock.get("list_date", "")
        if list_date and list_date > list_date_threshold:
            continue

        # ---- 批量数据预过滤（无需单独API调用）----
        b_row = basic_map.get(ts_code)
        d_row = daily_today_map.get(ts_code)

        if b_row:
            # 流通市值过滤
            circ_mv = b_row.get("circ_mv", 0)
            if circ_mv and (circ_mv < p_min_circ_mv * 10000 or circ_mv > p_max_circ_mv * 10000):
                continue
            # 换手率过滤
            turnover = b_row.get("turnover_rate", 0)
            if turnover and turnover < p_min_turnover:
                continue

            # 追高过滤：用批量日K数据
            if d_row:
                pct_chg = d_row.get("pct_chg", 0)
                if pct_chg and pct_chg > 9.5:  # 涨停的不追
                    continue
                low = d_row.get("low", 0)
                high = d_row.get("high", 0)
                # 长下影线检测
                close_d = d_row.get("close", 0)
                if low and high and close_d and low > 0:
                    lower_shadow = (close_d - low) / close_d * 100
                    body_range = (high - low) / low * 100
                    if lower_shadow > 3 and body_range > 5 and pct_chg < 0:
                        # 长下影阴线可能是诱多，暂时跳过
                        pass

            # 用批量数据直接构建基本信息，不需要单独调API
            if ts_code not in basic_cache:
                basic_cache[ts_code] = [{
                    "turnover_rate": turnover,
                    "circ_mv": circ_mv,
                }]
        else:
            # 回退到逐只查询（非交易时段或批量接口失败时）
            if idx > 0 and idx % 20 == 0:
                time.sleep(0.5)

            if ts_code not in daily_cache:
                daily_cache[ts_code] = get_daily(ts_code, days=30)
            records = daily_cache[ts_code]
            if len(records) < 20:
                continue

            closes = [float(r["close"]) for r in records[-20:]]
            if len(closes) >= 2:
                pct_20d = (closes[-1] - closes[0]) / closes[0] * 100
                if pct_20d > 50:
                    continue

            if ts_code not in basic_cache:
                basic_cache[ts_code] = get_daily_basic(ts_code, days=5)
            basics = basic_cache[ts_code]
            if not basics:
                continue

            latest_basic = basics[-1]
            circ_mv = latest_basic.get("circ_mv", 0)
            if circ_mv < p_min_circ_mv * 10000 or circ_mv > p_max_circ_mv * 10000:
                continue

            turnover = latest_basic.get("turnover_rate", 0)
            if turnover and turnover < p_min_turnover:
                continue

        # 确保有日线数据用于趋势计算
        if ts_code not in daily_cache or not daily_cache.get(ts_code):
            daily_cache[ts_code] = get_daily(ts_code, days=30)
        
        passed.append({
            "ts_code": ts_code,
            "name": name,
            "industry": stock.get("industry", ""),
            "circ_mv": b_row.get("circ_mv", 0) if b_row else (basic_cache.get(ts_code, [{}])[-1].get("circ_mv", 0) if basic_cache.get(ts_code) else 0),
            "turnover_rate": b_row.get("turnover_rate", 0) if b_row else (basic_cache.get(ts_code, [{}])[-1].get("turnover_rate", 0) if basic_cache.get(ts_code) else 0),
            "records": daily_cache.get(ts_code, []),
        })

    return passed


# ============================================================
# 三看检查工具函数
# ============================================================

def check_three_views(records):
    """
    检查股票的"三看"条件
    返回: (是否全部通过, 详细结果字典)
    """
    if len(records) < 20:
        return False, {"error": "数据不足"}
    
    closes = [float(r["close"]) for r in records]
    highs = [float(r["high"]) for r in records]
    lows = [float(r["low"]) for r in records]
    volumes = [float(r["vol"]) for r in records]
    
    result = {"all_passed": False, "details": {}}
    
    # ========== 一看：高低点抬高 ==========
    # 找最近5个波段的低点和高点（使用3日窗口确认极值，更稳健）
    local_lows = []
    local_highs = []
    window = 2  # 前后各2天，共5日窗口
    for i in range(window, len(records) - window):
        # 低点：比前后window天都低
        is_low = all(lows[i] < lows[i-j] for j in range(1, window+1)) and \
                 all(lows[i] < lows[i+j] for j in range(1, window+1))
        if is_low:
            local_lows.append((i, lows[i]))
        # 高点：比前后window天都高
        is_high = all(highs[i] > highs[i-j] for j in range(1, window+1)) and \
                  all(highs[i] > highs[i+j] for j in range(1, window+1))
        if is_high:
            local_highs.append((i, highs[i]))
    
    # 取最近3个
    recent_lows = local_lows[-3:] if len(local_lows) >= 3 else local_lows
    recent_highs = local_highs[-3:] if len(local_highs) >= 3 else local_highs
    
    low_increasing = len(recent_lows) >= 2 and all(
        recent_lows[i][1] > recent_lows[i-1][1] for i in range(1, len(recent_lows))
    )
    high_increasing = len(recent_highs) >= 2 and all(
        recent_highs[i][1] > recent_highs[i-1][1] for i in range(1, len(recent_highs))
    )
    
    result["details"]["high_low"] = {
        "passed": bool(low_increasing and high_increasing),
        "low_increasing": bool(low_increasing),
        "high_increasing": bool(high_increasing),
        "recent_lows": [round(x[1], 2) for x in recent_lows],
        "recent_highs": [round(x[1], 2) for x in recent_highs],
    }
    
    # ========== 二看：均线多头排列 ==========
    ma5 = calc_ma(closes, 5)
    ma10 = calc_ma(closes, 10)
    ma20 = calc_ma(closes, 20)
    
    ma_bull = ma5 and ma10 and ma20 and ma5 > ma10 > ma20
    
    result["details"]["ma"] = {
        "passed": bool(ma_bull),
        "ma5": round(ma5, 2) if ma5 else None,
        "ma10": round(ma10, 2) if ma10 else None,
        "ma20": round(ma20, 2) if ma20 else None,
    }
    
    # ========== 三看：量价配合 ==========
    today_vol = volumes[-1]
    vol_ma5 = calc_ma(volumes, 5)
    vol_ratio = today_vol / vol_ma5 if vol_ma5 and vol_ma5 > 0 else 1.0
    
    # 上涨日 vs 下跌日成交量（近10个交易日）
    up_vol_sum = 0
    down_vol_sum = 0
    up_count = 0
    down_count = 0
    for i in range(max(0, len(records)-10), len(records)):
        if closes[i] > float(records[i]["open"]):
            up_vol_sum += volumes[i]
            up_count += 1
        elif closes[i] < float(records[i]["open"]):
            down_vol_sum += volumes[i]
            down_count += 1
    
    up_vol_avg = up_vol_sum / up_count if up_count > 0 else 0
    down_vol_avg = down_vol_sum / down_count if down_count > 0 else 0
    
    vol_ok = vol_ratio > 1.0 and up_vol_avg > down_vol_avg
    
    result["details"]["volume"] = {
        "passed": bool(vol_ok),
        "vol_ratio": round(vol_ratio, 2),
        "up_vol_avg": round(up_vol_avg, 0) if up_vol_avg else 0,
        "down_vol_avg": round(down_vol_avg, 0) if down_vol_avg else 0,
    }
    
    # 综合判断
    result["all_passed"] = (
        result["details"]["high_low"]["passed"] and
        result["details"]["ma"]["passed"] and
        result["details"]["volume"]["passed"]
    )
    
    return result["all_passed"], result


# ============================================================
# 第3步：趋势确认
# ============================================================

def trend_confirm(candidates, filter_params=None):
    """
    趋势确认：MA20站稳 + MA20向上 + MACD金叉 + 放量 + 高低点结构
    :param filter_params: {"ma20_deviation_min": 1, "min_vol_ratio": 1.5, "min_price_change": 2}
    返回: 通过的候选股
    """
    fp = filter_params or {}
    p_ma20_dev_min = fp.get("ma20_deviation_min", 1)
    p_min_vol_ratio = fp.get("min_vol_ratio", 1.5)
    p_min_price_change = fp.get("min_price_change", 2)

    passed = []
    for c in candidates:
        records = c["records"]
        closes = [float(r["close"]) for r in records]
        highs = [float(r["high"]) for r in records]
        lows = [float(r["low"]) for r in records]
        volumes = [float(r["vol"]) for r in records]

        # MA20计算
        ma20_values = calc_ma_series(closes, 20)
        recent_ma20 = [v for v in ma20_values[-4:] if v is not None]
        if len(recent_ma20) < 3:
            continue

        # 条件1：收盘价 > MA20 * (1 + deviation_min%)
        last_close = closes[-1]
        last_open = float(records[-1]["open"])
        prev_close = closes[-2] if len(closes) >= 2 else last_open
        current_ma20 = recent_ma20[-1]
        if last_close < current_ma20 * (1 + p_ma20_dev_min / 100):
            continue

        # 条件2：MA20连续3日向上
        ma20_up = all(recent_ma20[i] >= recent_ma20[i - 1] for i in range(1, len(recent_ma20)))
        if not ma20_up:
            continue

        # 条件3：MACD金叉（强制）
        macd = calc_macd(closes)
        macd_valid = False
        if macd and len(macd["dif"]) >= 2:
            dif_now = macd["dif"][-1]
            dif_prev = macd["dif"][-2]
            dea_now = macd["dea"][-1]
            dea_prev = macd["dea"][-2]
            macd_now = macd["macd"][-1]
            macd_prev = macd["macd"][-2]
            
            # 金叉判断：DIF从下方穿越DEA，或MACD柱由负转正
            is_cross = (dif_prev <= dea_prev) and (dif_now > dea_now)
            is_hist_cross = (macd_prev <= 0) and (macd_now > 0)
            
            if is_cross or is_hist_cross:
                macd_valid = True
        
        if not macd_valid:
            continue

        # 条件4：放量确认（量比 >= 阈值）
        vol_ma5 = calc_ma(volumes, 5)
        if vol_ma5 and vol_ma5 > 0:
            vol_ratio = volumes[-1] / vol_ma5
            if vol_ratio < p_min_vol_ratio:
                continue
        else:
            continue

        # 条件5：涨幅确认（当日涨幅 >= 阈值）
        price_change_pct = (last_close - prev_close) / prev_close * 100
        if price_change_pct < p_min_price_change:
            continue

        # 条件6：三看检查（强制）
        three_views_passed, three_views_result = check_three_views(records)
        if not three_views_passed:
            continue
        
        # 保存三看详情用于展示
        c["three_views"] = three_views_result["details"]

        # 计算趋势强度评分
        ma20_slope = calc_slope(recent_ma20, 3)
        deviation = (last_close - current_ma20) / current_ma20 * 100  # 偏离度

        c["trend_score"] = 0
        # MA20偏离度评分（0-8分，原10分降为8分，腾出2分给HL结构）
        if deviation > 5:
            c["trend_score"] += 4  # 偏离太多可能追高
        elif deviation > 2:
            c["trend_score"] += 6
        else:
            c["trend_score"] += 8  # 刚站上，最佳买点

        # MA20斜率评分（0-12分，原15分降为12分，腾出3分给HL结构）
        if ma20_slope > 0.005:
            c["trend_score"] += 12
        elif ma20_slope > 0.002:
            c["trend_score"] += 10
        elif ma20_slope > 0:
            c["trend_score"] += 6
        else:
            c["trend_score"] += 2

        # 【新增】高低点结构评分（0-10分）
        c["hl_score"] = 0
        c["hl_structure"] = "unknown"
        try:
            # 构建DataFrame用于高低点分析
            import pandas as pd
            df = pd.DataFrame({
                'high': highs,
                'low': lows,
                'close': closes,
            })
            
            # 使用左右5根K线法识别局部极值
            n = 5
            local_highs = []
            local_lows = []
            
            for i in range(n, len(df) - n):
                current_high = df.iloc[i]['high']
                current_low = df.iloc[i]['low']
                left_highs = df.iloc[i-n:i]['high'].values
                left_lows = df.iloc[i-n:i]['low'].values
                right_highs = df.iloc[i+1:i+n+1]['high'].values
                right_lows = df.iloc[i+1:i+n+1]['low'].values
                
                if current_high > max(left_highs) and current_high > max(right_highs):
                    local_highs.append((i, current_high))
                if current_low < min(left_lows) and current_low < min(right_lows):
                    local_lows.append((i, current_low))
            
            # 分析高低点结构
            if len(local_highs) >= 3 and len(local_lows) >= 3:
                recent_highs = local_highs[-3:]
                recent_lows = local_lows[-3:]
                
                # 统计HH/HL/LH/LL
                hh = sum(1 for i in range(1, len(recent_highs)) if recent_highs[i][1] > recent_highs[i-1][1])
                hl = sum(1 for i in range(1, recent_lows)) if len(recent_lows) > 1 else 0
                hl = sum(1 for i in range(1, len(recent_lows)) if recent_lows[i][1] > recent_lows[i-1][1])
                lh = sum(1 for i in range(1, len(recent_highs)) if recent_highs[i][1] < recent_highs[i-1][1])
                ll = sum(1 for i in range(1, len(recent_lows)) if recent_lows[i][1] < recent_lows[i-1][1])
                
                # 评分
                if hh >= 2 and hl >= 2:
                    c["hl_score"] = 10  # 标准上升结构
                    c["hl_structure"] = "uptrend"
                elif hh >= 1 and hl >= 2:
                    c["hl_score"] = 6   # 低点抬高，高点待确认
                    c["hl_structure"] = "uptrend_weak"
                elif hh >= 2 and hl >= 1:
                    c["hl_score"] = 4   # 高点突破，低点待确认
                    c["hl_structure"] = "uptrend_uncertain"
                elif lh >= 2 and ll >= 2:
                    c["hl_score"] = -5  # 下降趋势，扣分
                    c["hl_structure"] = "downtrend"
                else:
                    c["hl_score"] = 2   # 震荡
                    c["hl_structure"] = "sideways"
            else:
                c["hl_score"] = 3  # 数据不足，给基础分
                c["hl_structure"] = "insufficient_data"
                
        except Exception as e:
            c["hl_score"] = 3
            c["hl_structure"] = "error"

        c["trend_score"] += c["hl_score"]
        c["ma20"] = round(current_ma20, 2)
        c["ma20_slope"] = round(ma20_slope, 4)
        c["deviation"] = round(deviation, 2)
        passed.append(c)

    return passed


# ============================================================
# 第4步：板块与资金
# ============================================================

def sector_and_money_filter(candidates, concept_boards, filter_params=None, batch_money_data=None, board_data_date=None):
    """
    板块涨幅过滤 + 主力资金过滤
    concept_boards: 概念板块涨幅排行列表
    filter_params: {"board_threshold": 0.5, "min_inflow_days": 2}
    batch_money_data: get_batch_money_flow_multi() 返回的批量资金数据（可选）
    board_data_date: 板块数据日期（用于检测是否过期）
    如果板块数据为空或过期（非交易时段/接口限制），降级为仅判断是否属于热门政策概念
    """
    fp = filter_params or {}
    p_board_threshold = fp.get("board_threshold", 0.5)
    p_min_inflow_days = fp.get("min_inflow_days", 2)

    # 构建板块涨幅映射
    board_pct_map = {b["concept_name"]: b["change_pct"] for b in concept_boards}

    # 检测板块数据是否过期（超过1个交易日）
    board_data_stale = False
    if board_data_date:
        try:
            latest_trade = get_latest_trade_date()
            from datetime import datetime
            board_dt = datetime.strptime(board_data_date, "%Y%m%d")
            latest_dt = datetime.strptime(latest_trade, "%Y%m%d")
            if (latest_dt - board_dt).days > 1:
                board_data_stale = True
                print(f"  [WARN] 板块数据已过期（{board_data_date} vs {latest_trade}），放宽过滤条件")
        except Exception:
            pass

    # 非交易时段/板块数据为空/数据过期：降级模式
    downgrade_mode = len(concept_boards) == 0 or board_data_stale

    # 市场情绪
    sentiment = get_market_sentiment()
    limit_up_count = sentiment.get("limit_up_count", 0)

    passed = []
    for c in candidates:
        ts_code = c["ts_code"]

        # 获取所属概念板块
        concepts = get_stock_concepts_eastmoney(ts_code)

        if downgrade_mode:
            # 降级模式：只要属于热门政策概念就通过板块过滤
            has_hot = any(
                any(hot in concept or concept in hot for hot in HOT_CONCEPTS)
                for concept in concepts
            )
            if not has_hot:
                continue
            max_board_pct = 0
            max_board_name = "热门概念(降级模式)"
            c["sector_score"] = 5  # 降级模式给基础分
        else:
            # 找所属概念中涨幅最大的
            max_board_pct = 0
            max_board_name = ""
            for concept in concepts:
                pct = board_pct_map.get(concept, 0)
                if pct and pct > max_board_pct:
                    max_board_pct = pct
                    max_board_name = concept

            # 条件：所属最强概念板块今日涨幅 > 阈值
            if max_board_pct <= p_board_threshold:
                continue

            # 板块名称 fallback：如果没匹配到但有概念列表，用第一个概念
            if not max_board_name and concepts:
                max_board_name = concepts[0]

            # 板块效应评分（0-20分）
            c["sector_score"] = 0
            if max_board_pct > 3:
                c["sector_score"] += 12
            elif max_board_pct > 2:
                c["sector_score"] += 10
            else:
                c["sector_score"] += 7

            if max_board_pct > 3:
                c["sector_score"] += 8
            elif max_board_pct > 2:
                c["sector_score"] += 5

        # 主力资金流向
        money_flow = get_stock_money_flow(ts_code, days=5, batch_data=batch_money_data)
        total_net_in = sum(m["main_net_in"] for m in money_flow)
        inflow_days = sum(1 for m in money_flow if m["main_net_in"] > 0)

        if not downgrade_mode:
            # 正常模式：资金过滤
            if total_net_in <= 0:
                continue
            if inflow_days < p_min_inflow_days:
                continue

        # 流通市值比
        circ_mv = c["circ_mv"]  # 万元
        inflow_ratio = abs(total_net_in) / (circ_mv * 10000) * 100 if circ_mv > 0 else 0

        # 资金面评分（0-20分）
        c["money_score"] = 0
        if total_net_in > 0:
            if inflow_ratio > 3:
                c["money_score"] += 10
            elif inflow_ratio > 1:
                c["money_score"] += 8
            elif inflow_ratio > 0.5:
                c["money_score"] += 6
            else:
                c["money_score"] += 3

            if inflow_days == 5:
                c["money_score"] += 10
            elif inflow_days >= 4:
                c["money_score"] += 8
            elif inflow_days >= 2:
                c["money_score"] += 5

        c["max_board_pct"] = max_board_pct
        c["max_board_name"] = max_board_name
        c["concepts"] = concepts
        c["total_net_in"] = round(total_net_in, 2)
        c["inflow_days"] = inflow_days
        c["inflow_ratio"] = round(inflow_ratio, 2)
        c["downgrade_mode"] = downgrade_mode

        passed.append(c)

    return passed


# ============================================================
# 第5步：加分项评分
# ============================================================

def bonus_scoring(candidates, concept_boards):
    """
    加分项评分：涨停基因、MACD、缩量回踩、市场情绪、政策概念、财务/业绩/回购
    """
    # 市场情绪
    sentiment = get_market_sentiment()
    limit_up_count = sentiment.get("limit_up_count", 0)

    # 获取近期涨停股列表（近20个交易日）
    limit_stocks = set()
    for i in range(20):
        trade_date = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
        if datetime.now() - timedelta(days=i) <= datetime.now() - timedelta(days=0):
            try:
                limits = get_limit_list(trade_date)
                for l in limits:
                    limit_stocks.add(l["ts_code"])
            except Exception:
                pass
        # 不要请求太多天，限制5天
        if i >= 4:
            break

    # 热门概念
    hot_board_names = set()
    for b in concept_boards[:10]:  # 涨幅前10的概念算热门
        hot_board_names.add(b["concept_name"])

    # P1: 预加载财务数据（业绩预告 + 回购），批量查询减少API调用
    forecast_cache = {}
    repurchase_cache = {}
    try:
        from helpers import get_forecast, get_repurchase
        for c in candidates:
            tc = c["ts_code"]
            if tc not in forecast_cache:
                forecast_cache[tc] = get_forecast(tc)
            if tc not in repurchase_cache:
                repurchase_cache[tc] = get_repurchase(tc)
    except Exception as e:
        print(f"  [P1] 财务/回购加分数据预加载失败: {e}")

    for c in candidates:
        records = c["records"]
        closes = [float(r["close"]) for r in records]
        volumes = [float(r["vol"]) for r in records]
        c["bonus_score"] = 0
        c["bonus_details"] = []

        # === 涨停基因（+10分）===
        if c["ts_code"] in limit_stocks:
            c["bonus_score"] += 10
            c["bonus_details"].append("涨停基因+10")

        # === MACD金叉（0轴上方+10分，0轴下方+5分）===
        macd = calc_macd(closes)
        if macd and len(macd["dif"]) >= 2:
            dif_now = macd["dif"][-1]
            dif_prev = macd["dif"][-2]
            dea_now = macd["dea"][-1]
            dea_prev = macd["dea"][-2]
            macd_now = macd["macd"][-1]
            macd_prev = macd["macd"][-2]

            # 金叉判断：DIF从下方穿越DEA
            is_cross = (dif_prev <= dea_prev) and (dif_now > dea_now)
            # 或者MACD柱由负转正
            is_hist_cross = (macd_prev <= 0) and (macd_now > 0)

            if is_cross or is_hist_cross:
                if dif_now > 0:  # 0轴上方
                    c["bonus_score"] += 10
                    c["bonus_details"].append("MACD零上金叉+10")
                else:
                    c["bonus_score"] += 5
                    c["bonus_details"].append("MACD零下金叉+5")

        # === 缩量回踩（+10分）===
        # 近3日内出现：最低价 < MA20*1.02 且 收盘价 > MA20 且 成交量 < 5日均量*0.7
        if len(records) >= 10:
            for j in range(-3, 0):
                idx = len(records) + j
                if idx < 10:
                    continue
                low = float(records[idx]["low"])
                close_r = float(records[idx]["close"])
                vol = float(records[idx]["vol"])

                ma20_r = calc_ma(closes[:idx + 1], 20)
                vol_ma5 = calc_ma(volumes[:idx + 1], 5)

                if ma20_r and vol_ma5:
                    if (low < ma20_r * 1.02 and close_r > ma20_r and vol < vol_ma5 * 0.7):
                        c["bonus_score"] += 10
                        c["bonus_details"].append("缩量回踩+10")
                        break

        # === 市场情绪（+5分）===
        if limit_up_count > 50:
            c["bonus_score"] += 5
            c["bonus_details"].append(f"情绪高涨(涨停{limit_up_count}家)+5")

        # === 政策概念（+8分）===
        concepts = c.get("concepts", [])
        for concept in concepts:
            for hot in HOT_CONCEPTS:
                if hot in concept or concept in hot:
                    c["bonus_score"] += 8
                    c["bonus_details"].append(f"政策概念({concept})+8")
                    break
            else:
                continue
            break

        # === P1: 业绩预告加分（+8分）===
        tc = c["ts_code"]
        fc_list = forecast_cache.get(tc, [])
        if fc_list:
            latest_fc = fc_list[0]
            fc_type = str(latest_fc.get("type", ""))
            net_min = latest_fc.get("net_profit_min")
            net_max = latest_fc.get("net_profit_max")
            # 业绩预增(预增)或扭亏(扭亏)或略增
            if fc_type in ("预增", "扭亏", "略增"):
                c["bonus_score"] += 8
                c["bonus_details"].append(f"业绩预告({fc_type})+8")

        # === P1: 回购加分（+5分）===
        rp_list = repurchase_cache.get(tc, [])
        if rp_list:
            latest_rp = rp_list[0]
            rp_amount = latest_rp.get("amount", 0)
            if rp_amount and float(rp_amount) > 10000:  # 回购金额 > 1亿
                c["bonus_score"] += 5
                c["bonus_details"].append("大额回购+5")

    return candidates


# ============================================================
# 第6步：汇总输出
# ============================================================

def final_ranking(candidates):
    """
    汇总评分，排序输出
    """
    results = []
    for c in candidates:
        total_score = (
            c.get("trend_score", 0) +
            c.get("sector_score", 0) +
            c.get("money_score", 0) +
            c.get("bonus_score", 0)
        )

        last_record = c["records"][-1]
        last_close = float(last_record["close"])
        last_pct_chg = float(last_record.get("pct_chg", 0))

        results.append({
            "ts_code": c["ts_code"],
            "name": c["name"],
            "industry": c.get("industry", ""),
            "price": round(last_close, 2),
            "pct_chg": round(last_pct_chg, 2),
            "circ_mv_yi": round(c["circ_mv"] / 10000, 2),  # 亿元
            "turnover_rate": round(c.get("turnover_rate", 0), 2),
            "ma20": c.get("ma20"),
            "deviation": c.get("deviation"),
            "max_board_pct": c.get("max_board_pct"),
            "max_board_name": c.get("max_board_name"),
            "concepts": c.get("concepts", [])[:5],  # 最多显示5个
            "total_net_in": c.get("total_net_in"),
            "inflow_days": c.get("inflow_days"),
            "inflow_ratio": c.get("inflow_ratio"),
            "trend_score": c.get("trend_score", 0),
            "sector_score": c.get("sector_score", 0),
            "money_score": c.get("money_score", 0),
            "bonus_score": c.get("bonus_score", 0),
            "bonus_details": c.get("bonus_details", []),
            "total_score": total_score,
            "downgrade_mode": c.get("downgrade_mode", False),
        })

    # 按总分排序
    results.sort(key=lambda x: x["total_score"], reverse=True)

    return results


# ============================================================
# 主筛选流程
# ============================================================

def _parse_params(params_list, key, default=None):
    """从 params_list 中提取指定 key 的值"""
    if not params_list:
        return default
    for p in params_list:
        if p.get("key") == key:
            return p.get("value", default)
    return default


def _build_data_warnings(board_empty, candidates):
    """
    检测数据质量并生成警告列表
    :param board_empty: 板块数据是否为空（非交易时段）
    :param candidates: 已通过筛选的候选股列表
    """
    warnings = []
    if board_empty:
        warnings.append("板块数据缺失（非交易时段），板块评分和资金过滤可能降级")
    # 检查资金数据是否全部为0
    if candidates:
        all_zero_money = all(c.get("total_net_in", 0) == 0 for c in candidates)
        if all_zero_money:
            warnings.append("主力资金数据全部为0（接口限制），资金面评分不可靠")
    return warnings


def run_screener(top_n=20, silent=False, force=False, params=None):
    """
    执行完整的6步选股流程
    :param top_n: 输出Top N结果
    :param silent: 静默模式（不打印日志）
    :param force: 强制模式（大盘下降时也继续筛选，用于测试）
    :param params: 策略参数列表 [{key, value}, ...] 用于覆盖默认值
    返回: {market, results, stats, run_time}
    """
    start_time = time.time()

    # 解析用户自定义参数
    min_circ_mv = _parse_params(params, "min_circ_mv", 50)
    max_circ_mv = _parse_params(params, "max_circ_mv", 300)
    min_turnover = _parse_params(params, "min_turnover", 3)
    ma20_deviation_min = _parse_params(params, "ma20_deviation_min", 1)
    min_vol_ratio = _parse_params(params, "min_vol_ratio", 1.5)
    min_price_change = _parse_params(params, "min_price_change", 2)
    board_threshold = _parse_params(params, "board_threshold", 0.5)
    min_inflow_days = _parse_params(params, "min_inflow_days", 2)

    if not silent:
        print("=" * 60)
        print(f"  A股短线选股引擎 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("=" * 60)

    # 第1步：大盘环境
    if not silent:
        print("\n[第1步] 判断大盘环境...")
    market = check_market_environment()
    if not silent:
        print(f"  → {market['description']}")

    if market["status"] == "下降" and not force:
        # 仍获取股票数量，让前端漏斗显示正确的全市场数
        try:
            stock_list = get_stock_list()
            total_stocks = len(stock_list)
        except Exception:
            total_stocks = 0
        return {
            "market": market,
            "results": [],
            "stats": {
                "total_stocks": total_stocks,
                "after_basic": 0,
                "after_trend": 0,
                "after_sector": 0,
                "final_count": 0,
                "message": "大盘处于下降趋势，建议观望，暂停选股",
            },
            "run_time": round(time.time() - start_time, 1),
            "screen_date": datetime.now().strftime("%Y-%m-%d"),
            "screen_time": datetime.now().strftime("%H:%M"),
        }

    if market["status"] == "下降" and force:
        if not silent:
            print("  [WARN] [强制模式] 大盘下降，已忽略风控继续筛选")

    # 获取全市场股票列表
    if not silent:
        print("\n[准备] 获取股票列表...")
    stock_list = get_stock_list()
    if not silent:
        print(f"  → 共 {len(stock_list)} 只股票")

    # [优化] 批量获取全市场当日基本面数据（1次API vs 5000次）
    batch_basic_df = None
    batch_daily_df = None
    if not silent:
        print("  → [批量] 获取全市场基本面数据...")
    try:
        batch_basic_df = get_batch_daily_basic()
        if batch_basic_df is not None:
            if not silent:
                print(f"  → 获取 {len(batch_basic_df)} 条基本面记录")
    except Exception as e:
        if not silent:
            print(f"  → 批量基本面失败，回退逐只查询: {e}")

    try:
        batch_daily_df = get_batch_daily()
        if batch_daily_df is not None:
            if not silent:
                print(f"  → 获取 {len(batch_daily_df)} 条行情记录")
    except Exception as e:
        if not silent:
            print(f"  → 批量行情失败，回退逐只查询: {e}")

    # 获取概念板块涨幅
    if not silent:
        print("  → 获取概念板块涨幅...")
    concept_boards = get_concept_board_data()
    if not silent:
        print(f"  → 共 {len(concept_boards)} 个概念板块")
    
    # 获取板块数据日期（用于检测是否过期）
    _, board_data_date = _load_cache("concept_boards")

    # [优化] 批量获取全市场资金流向数据（Tushare，1次API vs ~5000次）
    batch_money_data = None
    if not silent:
        print("  → [批量] 获取全市场资金流向（Tushare moneyflow）...")
    try:
        batch_money_data = get_batch_money_flow_multi(days=5)
        if batch_money_data:
            if not silent:
                print(f"  → 获取 {len(batch_money_data)} 只股票的资金流向数据")
        else:
            if not silent:
                print(f"  → 批量资金流向为空，将逐只获取")
    except Exception as e:
        if not silent:
            print(f"  → 批量资金流向失败，回退逐只获取: {e}")

    # [优化] 反向筛选：先确定热门板块，再获取热门板块成分股
    stock_board_map = {}
    if concept_boards:
        if not silent:
            print("  → [优化] 获取热门板块成分股（先筛板块再筛股）...")
        stock_board_map = get_hot_board_constituents(concept_boards, threshold_pct=board_threshold, max_boards=15)
        if not silent:
            print(f"  → 热门板块成分股共 {len(stock_board_map)} 只")
        # 如果无法获取成分股（concept_detail接口不可用），跳过板块预过滤
        if not stock_board_map:
            if not silent:
                print("  → [WARN] 无法获取板块成分股，跳过板块预过滤，直接全市场筛选")
    else:
        if not silent:
            print("  → 板块数据为空，将使用全市场筛选模式")

    # 构建筛选参数字典
    filter_params = {
        "min_circ_mv": min_circ_mv,
        "max_circ_mv": max_circ_mv,
        "min_turnover": min_turnover,
        "ma20_deviation_min": ma20_deviation_min,
        "min_vol_ratio": min_vol_ratio,
        "min_price_change": min_price_change,
        "board_threshold": board_threshold,
        "min_inflow_days": min_inflow_days,
    }

    # 第2步：基础过滤（使用批量数据预过滤）
    if not silent:
        print(f"\n[第2步] 基础过滤（排除ST/新股/追高/市值异常）...")
    daily_cache = {}
    basic_cache = {}
    after_basic = basic_filter(stock_list, daily_cache, basic_cache, batch_basic_df, batch_daily_df, filter_params=filter_params)
    if not silent:
        print(f"  → 通过 {len(after_basic)} 只")

    # [优化] 如果有板块成分股映射，做预过滤：只保留在热门板块中的股票
    if stock_board_map and after_basic:
        if not silent:
            print(f"  → [优化] 板块预过滤：从 {len(after_basic)} 只中筛选热门板块成分股...")
        after_basic = [s for s in after_basic if s["ts_code"] in stock_board_map]
        # 注入板块信息
        for s in after_basic:
            info = stock_board_map[s["ts_code"]]
            s["pre_concepts"] = info["concepts"]
            s["pre_max_board_pct"] = info["max_board_pct"]
            s["pre_max_board_name"] = info["max_board_name"]
        if not silent:
            print(f"  → 板块预过滤后 {len(after_basic)} 只")

    # 第3步：趋势确认
    if not silent:
        print(f"\n[第3步] 趋势确认（MA20站稳+MA20向上+MACD金叉+放量+涨幅）...")
    after_trend = trend_confirm(after_basic, filter_params=filter_params)
    if not silent:
        print(f"  → 通过 {len(after_trend)} 只")

    # 第4步：板块与资金（此时候选股已大幅减少，逐只查询快很多）
    if not silent:
        print(f"\n[第4步] 板块涨幅+主力资金过滤...")
    after_sector = sector_and_money_filter(after_trend, concept_boards,
                                            filter_params=filter_params,
                                            batch_money_data=batch_money_data,
                                            board_data_date=board_data_date)
    if not silent:
        print(f"  → 通过 {len(after_sector)} 只")

    if not after_sector:
        return {
            "market": market,
            "results": [],
            "stats": {
                "total_stocks": len(stock_list),
                "after_basic": len(after_basic),
                "after_trend": len(after_trend),
                "after_sector": 0,
                "final_count": 0,
                "message": "所有候选股未通过板块/资金筛选，今日无符合条件的标的",
            },
            "run_time": round(time.time() - start_time, 1),
            "screen_date": datetime.now().strftime("%Y-%m-%d"),
            "screen_time": datetime.now().strftime("%H:%M"),
        }

    # 第5步：加分项评分
    if not silent:
        print(f"\n[第5步] 加分项评分（涨停基因/MACD/缩量回踩/情绪/政策）...")
    scored = bonus_scoring(after_sector, concept_boards)

    # 第6步：汇总排序
    if not silent:
        print(f"\n[第6步] 汇总排序...")
    results = final_ranking(scored)
    top_results = results[:top_n]

    run_time = round(time.time() - start_time, 1)

    # 输出Top结果
    if not silent:
        print(f"\n{'=' * 60}")
        print(f"  筛选完成！共 {len(results)} 只符合条件，Top {len(top_results)} 如下：")
        print(f"{'=' * 60}")
        for i, r in enumerate(top_results):
            score_color = "[HOT]" if r["total_score"] >= 60 else "[GOOD]"
            print(f"\n  {score_color} #{i + 1} {r['name']}({r['ts_code']}) "
                  f"Price:{r['price']} {'+' if r['pct_chg'] > 0 else ''}{r['pct_chg']}%  "
                  f"总分: {r['total_score']}")
            print(f"     趋势:{r['trend_score']} 板块:{r['sector_score']} "
                  f"资金:{r['money_score']} 加分:{r['bonus_score']}")
            print(f"     板块: {r['max_board_name']}({r['max_board_pct']}%)  "
                  f"流通市值: {r['circ_mv_yi']}亿  "
                  f"主力净流入: {r['total_net_in']}万({r['inflow_days']}天)")
            if r["bonus_details"]:
                print(f"     加分项: {', '.join(r['bonus_details'])}")

        print(f"\n{'=' * 60}")
        print(f"  耗时 {run_time}s  |  大盘: {market['description']}")
        print(f"{'=' * 60}")

        _flush_money_flow_cache()

    return {
        "market": market,
        "results": results[:top_n],
        "all_results": results,  # 保留全部结果
        "stats": {
            "total_stocks": len(stock_list),
            "after_basic": len(after_basic),
            "after_trend": len(after_trend),
            "after_sector": len(after_sector),
            "final_count": len(results),
            "message": f"筛选完成，共{len(results)}只符合条件" if results else "无符合条件的标的",
            "data_warnings": _build_data_warnings(len(concept_boards) == 0, after_sector),
        },
        "run_time": run_time,
        "screen_date": datetime.now().strftime("%Y-%m-%d"),
        "screen_time": datetime.now().strftime("%H:%M"),
    }


# ============================================================
# 历史结果管理
# ============================================================

SCREEN_HISTORY_FILE = "data/screen_history.json"


def save_screen_result(result):
    """保存筛选结果到历史（通过 database.py 持久化到 SQLite）"""
    from database import save_screen_history
    from auth import get_current_user_id
    try:
        user_id = get_current_user_id()
    except Exception:
        user_id = 1
    save_screen_history(result, user_id)


def load_screen_history(days=7):
    """加载历史筛选结果（通过 database.py 从 SQLite 读取）"""
    from database import load_screen_history as db_load
    from auth import get_current_user_id
    try:
        user_id = get_current_user_id()
    except Exception:
        user_id = 1
    return db_load(days, user_id)


# ============================================================
# 策略二：板块龙头首板策略
# ============================================================

STRATEGY_META = {
    "trend_break": {
        "name": "趋势突破策略",
        "icon": "📈",
        "suitable": "大盘上升/震荡期",
        "hold_period": "3-10个交易日",
        "stop_loss": "跌破MA20 或 亏损8%",
        "buy_tip": "",
        "description": "买入信号：MA20站稳+MACD金叉+放量确认+三看通过（高低点抬高/均线多头/量价配合）+板块效应；持股周期：3-10个交易日；止损位：跌破MA20或亏损8%",
    },
    "sector_leader": {
        "name": "板块龙头首板策略",
        "icon": "🔥",
        "suitable": "情绪亢奋期（涨停家数>50）",
        "hold_period": "1-5个交易日",
        "stop_loss": "次日跌破分时均线 或 亏损5%",
        "buy_tip": "",
        "description": "买入信号：板块涨>2%+个股涨幅5-9.5%+换手5-15%+资金流入；加分项：三看确认+10分；持股周期：1-5个交易日；止损位：次日跌破分时均线或亏损5%",
    },
    "oversold_bounce": {
        "name": "超跌反弹策略",
        "icon": "🔄",
        "suitable": "大盘下跌末期",
        "hold_period": "5-20个交易日",
        "stop_loss": "跌破近期新低 或 亏损10%",
        "buy_tip": "",
        "description": "买入信号：近20日跌幅>阈值 + 止跌信号（长下影线/放量/MACD金叉）+ 技术改善（MA金叉/阳线/放量）；持股周期：5-20个交易日；止损位：跌破近期新低或亏损10%",
    },
}

# ============================================================
# 策略可调参数（默认最优值，用户可手动调整）
# ============================================================

STRATEGY_PARAMS = {
    "trend_break": [
        {
            "key": "min_circ_mv",
            "label": "最小流通市值(亿)",
            "value": 50,
            "min": 10, "max": 500, "step": 10,
            "desc": "流通市值下限，过小流动性差",
        },
        {
            "key": "max_circ_mv",
            "label": "最大流通市值(亿)",
            "value": 300,
            "min": 50, "max": 1000, "step": 50,
            "desc": "流通市值上限，过大弹性不足",
        },
        {
            "key": "min_turnover",
            "label": "最小换手率(%)",
            "value": 3,
            "min": 1, "max": 15, "step": 0.5,
            "desc": "日换手率下限，过低无资金关注",
        },
        {
            "key": "ma20_deviation_min",
            "label": "MA20偏离度下限(%)",
            "value": 1,
            "min": 0, "max": 5, "step": 0.5,
            "desc": "收盘价需高于MA20的最小百分比",
        },
        {
            "key": "min_vol_ratio",
            "label": "最小量比",
            "value": 1.5,
            "min": 1.0, "max": 3.0, "step": 0.1,
            "desc": "当日成交量/5日均量，确认放量突破",
        },
        {
            "key": "min_price_change",
            "label": "最小涨幅(%)",
            "value": 2,
            "min": 0, "max": 5, "step": 0.5,
            "desc": "当日最小涨幅，确认突破力度",
        },
        {
            "key": "min_inflow_days",
            "label": "最小资金流入天数",
            "value": 2,
            "min": 1, "max": 5, "step": 1,
            "desc": "近5日内主力净流入的最小天数",
        },
    ],
    "sector_leader": [
        {
            "key": "min_board_pct",
            "label": "热门板块涨幅阈值(%)",
            "value": 2,
            "min": 0.5, "max": 5, "step": 0.5,
            "desc": "板块涨幅达到此值才算热门",
        },
        {
            "key": "min_stock_pct",
            "label": "个股涨幅下限(%)",
            "value": 5,
            "min": 3, "max": 8, "step": 0.5,
            "desc": "只选涨幅在此范围以上的股票",
        },
        {
            "key": "max_stock_pct",
            "label": "个股涨幅上限(%)",
            "value": 9.5,
            "min": 5, "max": 10, "step": 0.5,
            "desc": "排除已涨停的(10%)，避免追高",
        },
        {
            "key": "min_turnover",
            "label": "最小换手率(%)",
            "value": 5,
            "min": 2, "max": 20, "step": 1,
            "desc": "换手率需达到此值才说明资金充分换手",
        },
        {
            "key": "max_turnover",
            "label": "最大换手率(%)",
            "value": 15,
            "min": 10, "max": 30, "step": 1,
            "desc": "换手率过高可能为出货",
        },
        {
            "key": "min_circ_mv",
            "label": "最小流通市值(亿)",
            "value": 50,
            "min": 10, "max": 200, "step": 10,
            "desc": "流通市值下限，过小流动性差",
        },
        {
            "key": "max_circ_mv",
            "label": "最大流通市值(亿)",
            "value": 300,
            "min": 100, "max": 1000, "step": 50,
            "desc": "流通市值上限，过大弹性不足",
        },
    ],
    "oversold_bounce": [
        {
            "key": "min_drop_pct",
            "label": "近20日最小跌幅(%)",
            "value": -20,
            "min": -50, "max": -10, "step": 5,
            "desc": "跌幅需达到此值才算超跌（负数）",
        },
        {
            "key": "min_circ_mv",
            "label": "最小流通市值(亿)",
            "value": 50,
            "min": 10, "max": 200, "step": 10,
            "desc": "流通市值下限，过小流动性差",
        },
        {
            "key": "max_circ_mv",
            "label": "最大流通市值(亿)",
            "value": 300,
            "min": 100, "max": 1000, "step": 50,
            "desc": "流通市值上限，过大弹性不足",
        },
        {
            "key": "lower_shadow_pct",
            "label": "下影线最低占比(%)",
            "value": 1.5,
            "min": 0.5, "max": 5, "step": 0.5,
            "desc": "长下影线判定阈值",
        },
        {
            "key": "body_range_pct",
            "label": "振幅最低要求(%)",
            "value": 3,
            "min": 1, "max": 8, "step": 0.5,
            "desc": "当日振幅需达到此值才视为有效信号",
        },
        {
            "key": "vol_ratio_threshold",
            "label": "放量倍数阈值",
            "value": 1.3,
            "min": 1, "max": 2, "step": 0.1,
            "desc": "放量确认：量比需超过此倍数（相对5日均量）",
        },
        {
            "key": "tech_confirm_min",
            "label": "技术面改善最低要求",
            "value": 1,
            "min": 0, "max": 3, "step": 1,
            "desc": "技术面改善信号数量要求（0=不强制，1=至少1个，2=至少2个，3=全部要求）",
        },
    ],
}


def get_strategy_params(strategy=None):
    """
    获取策略参数列表
    :param strategy: 策略名，None则返回全部
    """
    if strategy:
        return STRATEGY_PARAMS.get(strategy, [])
    return STRATEGY_PARAMS


def run_sector_leader_screener(top_n=20, silent=False, params=None):
    """
    策略二：板块龙头首板策略（优化版）
    核心优化：先确定热门板块 → 获取成分股（含行情数据）→ 精细筛选
    从5000+只股票骤减到200-500只，大幅减少API调用
    """
    start_time = time.time()
    if not silent:
        print("=" * 60)
        print(f"  策略二：板块龙头首板 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("=" * 60)

    # 解析用户自定义参数
    p_min_board_pct = _parse_params(params, "min_board_pct", 2)
    p_min_stock_pct = _parse_params(params, "min_stock_pct", 5)
    p_max_stock_pct = _parse_params(params, "max_stock_pct", 9.5)
    p_min_turnover = _parse_params(params, "min_turnover", 5)
    p_max_turnover = _parse_params(params, "max_turnover", 15)
    p_min_circ_mv = _parse_params(params, "min_circ_mv", 30)
    p_max_circ_mv = _parse_params(params, "max_circ_mv", 200)

    # 获取大盘环境
    market = check_market_environment()

    # 获取概念板块涨幅排行
    if not silent:
        print("  [准备] 获取概念板块涨幅...")
    concept_boards = get_concept_board_data()
    if not silent:
        print(f"  → 共 {len(concept_boards)} 个概念板块")

    # [优化] 批量获取全市场资金流向
    batch_money_data = None
    if not silent:
        print("  → [批量] 获取全市场资金流向（Tushare moneyflow）...")
    try:
        batch_money_data = get_batch_money_flow_multi(days=5)
        if batch_money_data:
            if not silent:
                print(f"  → 获取 {len(batch_money_data)} 只股票的资金流向数据")
        else:
            if not silent:
                print(f"  → 批量资金流向为空，将逐只获取")
    except Exception as e:
        if not silent:
            print(f"  → 批量资金流向失败: {e}")

    # 市场情绪
    sentiment = get_market_sentiment()
    limit_up_count = sentiment.get("limit_up_count", 0)

    # 获取涨停股集合
    limit_stocks = set()
    for i in range(5):
        trade_date = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
        try:
            limits = get_limit_list(trade_date)
            for l in limits:
                limit_stocks.add(l["ts_code"])
        except Exception:
            pass

    downgrade_mode = len(concept_boards) == 0

    # ====== 核心优化：先筛热门板块，再从成分股中选 ======
    hot_boards = [b for b in concept_boards if b["change_pct"] >= p_min_board_pct]
    hot_boards.sort(key=lambda x: x["change_pct"], reverse=True)
    hot_boards = hot_boards[:15]

    if not silent:
        print(f"  → [优化] 筛出 {len(hot_boards)} 个热门板块（涨幅≥{p_min_board_pct}%）")

    # 获取热门板块的成分股（含当日行情数据，一次API带出涨幅/换手率等）
    candidate_pool = {}  # ts_code -> {board_name, board_pct, concepts, ...}

    for board in hot_boards:
        board_code = board["concept_code"]
        if not board_code:
            continue
        try:
            url = "https://push2.eastmoney.com/api/qt/clist/get"
            params = {
                "pn": "1", "pz": "500", "po": "1", "np": "1",
                "fltt": "2", "invt": "2", "fid": "f3",
                "fs": f"b:{board_code}+f:!50",
                "fields": "f2,f3,f4,f5,f6,f7,f8,f12,f13,f14,f15,f16,f17,f18,f184",
            }
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            items = resp.json().get("data", {}).get("diff", []) or []

            for item in items:
                code = str(item.get("f12", ""))
                if not code:
                    continue
                raw = code.zfill(6)
                secid_type = item.get("f13", 0)
                ts_code = f"{raw}.SH" if secid_type == 1 else f"{raw}.SZ"

                # 只关注涨幅在设定范围内的
                pct_chg = item.get("f3", 0)
                if pct_chg < p_min_stock_pct or pct_chg > p_max_stock_pct:
                    continue

                if ts_code not in candidate_pool:
                    candidate_pool[ts_code] = {
                        "concepts": [],
                        "max_board_pct": 0,
                        "max_board_name": "",
                        "pct_chg": pct_chg,
                        "price": item.get("f2", 0),
                        "turnover_rate": item.get("f8", 0) if item.get("f8") else 0,
                        "circ_mv": item.get("f20", 0) if item.get("f20") else 0,  # 万元
                    }

                candidate_pool[ts_code]["concepts"].append(board["concept_name"])
                if board["change_pct"] > candidate_pool[ts_code]["max_board_pct"]:
                    candidate_pool[ts_code]["max_board_pct"] = board["change_pct"]
                    candidate_pool[ts_code]["max_board_name"] = board["concept_name"]
        except Exception as e:
            if not silent:
                print(f"  → [WARN] 板块{board['concept_name']}成分股获取失败: {e}")
            continue

    if not silent:
        print(f"  → [优化] 热门板块中涨幅{p_min_stock_pct}-{p_max_stock_pct}%的股票共 {len(candidate_pool)} 只（已大幅缩小候选池）")

    # 如果东方财富接口调用后 candidate_pool 仍为空，自动进入降级模式
    if not candidate_pool and not downgrade_mode:
        if not silent:
            print(f"  → [WARN] 东方财富接口未返回数据，自动进入降级模式...")
        downgrade_mode = True

    # [降级模式] 东方财富不可用或 candidate_pool 为空：用 Tushare daily + daily_basic 批量数据替代
    if not candidate_pool and downgrade_mode:
        if not silent:
            print("  → [降级] 东方财富接口不可用，使用 Tushare 批量数据...")
        try:
            # daily_basic 提供 turnover_rate, circ_mv
            batch_basic = get_batch_daily_basic()
            # daily 提供 pct_chg, close (价格)
            batch_daily = get_batch_daily()
            
            if batch_basic is not None and not batch_basic.empty and batch_daily is not None and not batch_daily.empty:
                # 合并两个数据源（daily_basic 也有 close，需要重命名避免冲突）
                daily_df = batch_daily[["ts_code", "pct_chg", "close"]].rename(columns={"close": "price"})
                merged = batch_basic.merge(daily_df, on="ts_code", how="inner")
                
                # 筛选涨幅在 5-9.5% 范围内的股票
                filtered = merged[
                    (merged["pct_chg"] >= p_min_stock_pct) &
                    (merged["pct_chg"] <= p_max_stock_pct) &
                    (merged["turnover_rate"] >= p_min_turnover) &
                    (merged["turnover_rate"] <= p_max_turnover) &
                    (merged["circ_mv"] >= p_min_circ_mv * 10000) &
                    (merged["circ_mv"] <= p_max_circ_mv * 10000)
                ]
                for _, row in filtered.iterrows():
                    ts_code = row["ts_code"]
                    if ts_code not in candidate_pool:
                        candidate_pool[ts_code] = {
                            "concepts": [],
                            "max_board_pct": 0,
                            "max_board_name": "",
                            "pct_chg": float(row.get("pct_chg", 0)),
                            "price": float(row.get("price", 0)),
                            "turnover_rate": float(row.get("turnover_rate", 0)),
                            "circ_mv": float(row.get("circ_mv", 0)),
                        }
                if not silent:
                    print(f"  → [降级] 从 Tushare 筛出 {len(candidate_pool)} 只候选股")
            else:
                if not silent:
                    print("  → [降级] Tushare 数据为空，无法执行降级选股")
        except Exception as e:
            if not silent:
                print(f"  → [降级] Tushare 获取失败: {e}")

    # 获取股票列表（用于补充 name、list_date 等信息）
    stock_list = get_stock_list()
    stock_info_map = {s["ts_code"]: s for s in stock_list}

    today = datetime.now()
    list_date_threshold = (today - timedelta(days=60)).strftime("%Y%m%d")

    passed = []
    daily_cache = {}

    for ts_code, pool_info in candidate_pool.items():
        info = stock_info_map.get(ts_code, {})
        name = info.get("name", "")
        if not name:
            name = ts_code

        # 排除ST
        if "ST" in name or "st" in name or name.startswith("*"):
            continue
        # 排除次新
        list_date = info.get("list_date", "")
        if list_date and list_date > list_date_threshold:
            continue

        # 核心条件：流通市值在设定范围内
        circ_mv = pool_info.get("circ_mv", 0)
        if circ_mv < p_min_circ_mv * 10000 or circ_mv > p_max_circ_mv * 10000:
            continue

        # 核心条件：换手率在设定范围内
        turnover = pool_info.get("turnover_rate", 0)
        if not turnover or turnover < p_min_turnover or turnover > p_max_turnover:
            continue

        # 获取资金流向（只对候选池中的少量股票调用）
        money_flow = get_stock_money_flow(ts_code, days=5, batch_data=batch_money_data)
        total_net_in = sum(m["main_net_in"] for m in money_flow)
        if total_net_in <= 0:
            continue
        inflow_days = sum(1 for m in money_flow if m["main_net_in"] > 0)
        circ_mv_yi = circ_mv / 10000
        inflow_ratio = abs(total_net_in) / (circ_mv * 10000) * 100 if circ_mv > 0 else 0

        # === 评分（分4个维度，与趋势突破策略保持一致）===
        trend_score = 0
        sector_score = 0
        money_score = 0
        bonus_score = 0
        bonus_details = []

        # 维度1：趋势强度（涨幅+换手率，0-40分）
        pct_chg = pool_info["pct_chg"]
        if 8 <= pct_chg <= 9.5:
            trend_score += 20
        elif 6 <= pct_chg < 8:
            trend_score += 15
        else:
            trend_score += 8

        if 8 <= turnover <= 12:
            trend_score += 20
        elif 5 <= turnover < 8:
            trend_score += 15
        else:
            trend_score += 10

        # 维度2：板块效应（0-25分）
        max_board_pct = pool_info["max_board_pct"]
        if max_board_pct > 4:
            sector_score = 25
        elif max_board_pct > 3:
            sector_score = 20
        else:
            sector_score = 12

        # 维度3：资金面（0-20分）
        if inflow_ratio > 3:
            money_score = 15
        elif inflow_ratio > 1:
            money_score = 12
        else:
            money_score = 7
        if inflow_days >= 4:
            money_score += 5
        money_score = min(money_score, 20)

        # 维度4：加分项（涨停基因+业绩预告+回购，0-15分）
        if ts_code in limit_stocks:
            bonus_score += 10
            bonus_details.append("涨停基因+10")

        # P1: 业绩预告加分
        try:
            from helpers import get_forecast, get_repurchase
            fc_list = get_forecast(ts_code)
            if fc_list:
                fc_type = str(fc_list[0].get("type", ""))
                if fc_type in ("预增", "扭亏", "略增"):
                    bonus_score += 3
                    bonus_details.append(f"业绩预告({fc_type})+3")
            # P1: 回购加分
            rp_list = get_repurchase(ts_code)
            if rp_list:
                rp_amount = rp_list[0].get("amount", 0)
                if rp_amount and float(rp_amount) > 10000:
                    bonus_score += 2
                    bonus_details.append("大额回购+2")
        except Exception:
            pass

        bonus_score = min(bonus_score, 15)

        # 【新增】三看检查加分（0-10分）- 技术面确认
        try:
            if ts_code not in daily_cache:
                daily_cache[ts_code] = get_daily(ts_code, days=40)
            records = daily_cache[ts_code]
            
            if len(records) >= 20:
                three_views_passed, three_views_result = check_three_views(records)
                if three_views_passed:
                    bonus_score += 10
                    bonus_details.append("三看确认+10")
                else:
                    # 部分通过也给少量分数
                    passed_count = sum(1 for v in three_views_result["details"].values() if v["passed"])
                    if passed_count == 2:
                        bonus_score += 5
                        bonus_details.append(f"三看部分确认({passed_count}/3)+5")
                    elif passed_count == 1:
                        bonus_score += 2
                        bonus_details.append(f"三看部分确认({passed_count}/3)+2")
        except Exception:
            pass
        
        bonus_score = min(bonus_score, 15)  # 重新限制

        # 【新增】高低点结构评分（0-10分）- 从涨幅评分中腾挪
        hl_score = 0
        hl_structure = "unknown"
        try:
            # 获取近40日K线用于高低点分析
            if ts_code not in daily_cache:
                daily_cache[ts_code] = get_daily(ts_code, days=40)
            records = daily_cache[ts_code]
            
            if len(records) >= 25:
                highs = [float(r["high"]) for r in records]
                lows = [float(r["low"]) for r in records]
                
                # 使用左右5根K线法识别局部极值
                n = 5
                local_highs = []
                local_lows = []
                
                for i in range(n, len(records) - n):
                    current_high = highs[i]
                    current_low = lows[i]
                    left_highs = highs[i-n:i]
                    left_lows = lows[i-n:i]
                    right_highs = highs[i+1:i+n+1]
                    right_lows = lows[i+1:i+n+1]
                    
                    if current_high > max(left_highs) and current_high > max(right_highs):
                        local_highs.append((i, current_high))
                    if current_low < min(left_lows) and current_low < min(right_lows):
                        local_lows.append((i, current_low))
                
                # 分析结构
                if len(local_highs) >= 3 and len(local_lows) >= 3:
                    recent_highs = local_highs[-3:]
                    recent_lows = local_lows[-3:]
                    
                    hh = sum(1 for i in range(1, len(recent_highs)) if recent_highs[i][1] > recent_highs[i-1][1])
                    hl = sum(1 for i in range(1, len(recent_lows)) if recent_lows[i][1] > recent_lows[i-1][1])
                    lh = sum(1 for i in range(1, len(recent_highs)) if recent_highs[i][1] < recent_highs[i-1][1])
                    ll = sum(1 for i in range(1, len(recent_lows)) if recent_lows[i][1] < recent_lows[i-1][1])
                    
                    if hh >= 2 and hl >= 2:
                        hl_score = 10
                        hl_structure = "uptrend"
                    elif hh >= 1 and hl >= 2:
                        hl_score = 6
                        hl_structure = "uptrend_weak"
                    elif hh >= 2 and hl >= 1:
                        hl_score = 4
                        hl_structure = "uptrend_uncertain"
                    elif lh >= 2 and ll >= 2:
                        hl_score = -5  # 下降趋势扣分
                        hl_structure = "downtrend"
                    else:
                        hl_score = 2
                        hl_structure = "sideways"
                else:
                    hl_score = 3
                    hl_structure = "insufficient_data"
        except Exception:
            hl_score = 3
            hl_structure = "error"
        
        # 调整趋势评分：原涨幅+换手40分 → 35分，腾出5分给HL结构
        trend_score = int(trend_score * 35 / 40) + hl_score

        total_score = trend_score + sector_score + money_score + bonus_score

        passed.append({
            "ts_code": ts_code,
            "name": name,
            "industry": info.get("industry", ""),
            "price": pool_info.get("price", 0),
            "pct_chg": round(pct_chg, 2),
            "circ_mv_yi": round(circ_mv_yi, 2),
            "turnover_rate": round(turnover, 2),
            "max_board_pct": max_board_pct,
            "max_board_name": pool_info["max_board_name"],
            "concepts": pool_info["concepts"][:5],
            "total_net_in": round(total_net_in, 2),
            "inflow_days": inflow_days,
            "inflow_ratio": round(inflow_ratio, 2),
            "trend_score": trend_score,
            "sector_score": sector_score,
            "money_score": money_score,
            "bonus_score": bonus_score,
            "hl_score": hl_score,
            "hl_structure": hl_structure,
            "bonus_details": bonus_details,
            "total_score": total_score,
        })

    # 按总分排序
    passed.sort(key=lambda x: x["total_score"], reverse=True)
    top_results = passed[:top_n]
    run_time = round(time.time() - start_time, 1)

    if not silent:
        print(f"\n  筛选完成！共 {len(passed)} 只，Top {len(top_results)}：")
        for i, r in enumerate(top_results):
            print(f"  [#{i+1}] {r['name']}({r['ts_code']}) price:{r['price']} pct:{r['pct_chg']}% score:{r['total_score']}")
        print(f"  耗时 {run_time}s")

    _flush_money_flow_cache()

    return {
        "market": market,
        "results": top_results,
        "all_results": passed,
        "stats": {
            "total_stocks": len(stock_list),
            "after_basic": len(candidate_pool),
            "after_trend": len(candidate_pool),
            "after_sector": len(passed),
            "final_count": len(passed),
            "message": f"板块龙头策略：共{len(passed)}只符合条件" if passed else "无符合条件的龙头标的",
            "data_warnings": _build_data_warnings(len(concept_boards) == 0, passed),
        },
        "run_time": run_time,
        "screen_date": datetime.now().strftime("%Y-%m-%d"),
        "screen_time": datetime.now().strftime("%H:%M"),
        "strategy": "sector_leader",
        "strategy_meta": STRATEGY_META["sector_leader"],
    }


# ============================================================
# 策略三：超跌反弹策略
# ============================================================

def run_oversold_bounce_screener(top_n=20, silent=False, params=None):
    """
    策略三：超跌反弹策略（优化版）
    核心优化：批量基本面预过滤市值，减少逐只API调用
    """
    start_time = time.time()
    if not silent:
        print("=" * 60)
        print(f"  策略三：超跌反弹 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("=" * 60)

    # 解析用户自定义参数
    p_min_drop_pct = _parse_params(params, "min_drop_pct", -20)
    p_min_circ_mv = _parse_params(params, "min_circ_mv", 50)
    p_max_circ_mv = _parse_params(params, "max_circ_mv", 300)
    p_lower_shadow_pct = _parse_params(params, "lower_shadow_pct", 1.5)
    p_body_range_pct = _parse_params(params, "body_range_pct", 3)
    p_vol_ratio_threshold = _parse_params(params, "vol_ratio_threshold", 1.3)
    p_tech_confirm_min = _parse_params(params, "tech_confirm_min", 1)

    # 获取大盘环境
    market = check_market_environment()

    # 获取股票列表
    if not silent:
        print("  [准备] 获取股票列表...")
    stock_list = get_stock_list()
    total_stocks_original = len(stock_list)  # 保存原始全市场数量
    if not silent:
        print(f"  → 共 {len(stock_list)} 只")

    # [优化] 批量获取全市场当日基本面
    batch_basic_df = None
    if not silent:
        print("  → [优化] 批量获取全市场基本面数据...")
    try:
        batch_basic_df = get_batch_daily_basic()
        if batch_basic_df is not None and not batch_basic_df.empty:
            if not silent:
                print(f"  → 获取 {len(batch_basic_df)} 条基本面记录")
            # 预过滤：只保留市值达标的股票
            large_caps = batch_basic_df[
                (batch_basic_df["circ_mv"] >= p_min_circ_mv * 10000) &
                (batch_basic_df["circ_mv"] <= p_max_circ_mv * 10000)
            ]
            large_cap_set = set(large_caps["ts_code"].tolist())
            if not silent:
                print(f"  → [优化] 市值{p_min_circ_mv}~{p_max_circ_mv}亿的股票 {len(large_cap_set)} 只（从{len(stock_list)}只缩小）")
            stock_list = [s for s in stock_list if s["ts_code"] in large_cap_set]
            if not silent:
                print(f"  → [优化] 预过滤后候选池 {len(stock_list)} 只")
    except Exception as e:
        if not silent:
            print(f"  → 批量基本面失败，回退逐只查询: {e}")

    # 获取概念板块
    if not silent:
        print("  [准备] 获取概念板块涨幅...")
    concept_boards = get_concept_board_data()

    # [优化] 批量获取全市场资金流向
    batch_money_data = None
    if not silent:
        print("  → [批量] 获取全市场资金流向（Tushare moneyflow）...")
    try:
        batch_money_data = get_batch_money_flow_multi(days=5)
        if batch_money_data:
            if not silent:
                print(f"  → 获取 {len(batch_money_data)} 只股票的资金流向数据")
    except Exception as e:
        if not silent:
            print(f"  → 批量资金流向失败: {e}")

    # [优化-新增] 批量获取近45日全市场日线数据，替代逐只 get_daily 调用
    batch_daily_data = {}
    if not silent:
        print("  → [批量] 获取近45日全市场日线数据（替代逐只K线查询）...")
    try:
        batch_daily_data = get_batch_daily_multi(days=45)
        if batch_daily_data:
            if not silent:
                print(f"  → 获取 {len(batch_daily_data)} 只股票的日线数据")
    except Exception as e:
        if not silent:
            print(f"  → 批量日线获取失败，将降级逐只查询: {e}")

    today = datetime.now()
    list_date_threshold = (today - timedelta(days=90)).strftime("%Y%m%d")

    daily_cache = {}
    basic_cache = {}
    passed = []
    total_checked = 0

    for idx, stock in enumerate(stock_list):
        if idx > 0 and idx % 50 == 0 and not batch_daily_data:
            # 仅在降级逐只查询模式下限速
            time.sleep(0.5)

        ts_code = stock["ts_code"]
        name = stock.get("name", "")

        # 排除ST / *ST
        if "ST" in name or "st" in name or name.startswith("*"):
            continue

        # 排除上市不满90天
        list_date = stock.get("list_date", "")
        if list_date and list_date > list_date_threshold:
            continue

        # 获取日K数据（优先批量，降级逐只）
        if batch_daily_data and ts_code in batch_daily_data:
            records = batch_daily_data[ts_code]
        else:
            if ts_code not in daily_cache:
                daily_cache[ts_code] = get_daily(ts_code, days=40)
            records = daily_cache[ts_code]
        if len(records) < 25:
            continue

        closes = [float(r["close"]) for r in records]
        volumes = [float(r["vol"]) for r in records]
        last_close = closes[-1]
        last_pct_chg = float(records[-1].get("pct_chg", 0))

        # 核心条件1：近20日跌幅超过阈值
        pct_20d = (last_close - closes[-21]) / closes[-21] * 100 if len(closes) >= 21 else 0
        if pct_20d > p_min_drop_pct:
            continue

        total_checked += 1

        # [优化] 优先使用批量数据获取市值
        circ_mv = 0
        if batch_basic_df is not None:
            row = batch_basic_df[batch_basic_df["ts_code"] == ts_code]
            if not row.empty:
                circ_mv = row.iloc[0].get("circ_mv", 0)
        if not circ_mv:
            if ts_code not in basic_cache:
                basic_cache[ts_code] = get_daily_basic(ts_code, days=5)
            basics = basic_cache[ts_code]
            if not basics:
                continue
            circ_mv = basics[-1].get("circ_mv", 0)
        else:
            turnover = batch_basic_df[batch_basic_df["ts_code"] == ts_code].iloc[0].get("turnover_rate", 0)

        # 核心条件2：流通市值达标
        if circ_mv < p_min_circ_mv * 10000 or circ_mv > p_max_circ_mv * 10000:
            continue

        # 核心条件3：近2日出现止跌信号
        stop_signals = []

        for j in range(-2, 0):
            r_idx = len(records) + j
            if r_idx < 5:
                continue
            r = records[r_idx]
            low = float(r["low"])
            high = float(r["high"])
            close_r = float(r["close"])
            open_r = float(r["open"])
            vol = float(r["vol"])

            # 信号A：长下影线
            lower_shadow = (close_r - low) / close_r * 100 if close_r > 0 else 0
            body_range = (high - low) / low * 100 if low > 0 else 0
            if lower_shadow > p_lower_shadow_pct and body_range > p_body_range_pct:
                stop_signals.append("长下影线")

            # 信号B：放量阳线
            pct = float(r.get("pct_chg", 0))
            vol_ma5 = calc_ma(volumes[:r_idx + 1], 5)
            if pct > 1 and vol_ma5 and vol > vol_ma5 * p_vol_ratio_threshold:
                stop_signals.append("放量阳线")

        # 信号C：底部MACD金叉
        macd = calc_macd(closes)
        if macd and len(macd["dif"]) >= 2:
            dif_now = macd["dif"][-1]
            dif_prev = macd["dif"][-2]
            dea_now = macd["dea"][-1]
            dea_prev = macd["dea"][-2]
            if (dif_prev <= dea_prev) and (dif_now > dea_now):
                stop_signals.append("MACD金叉")

        # ============================================================
        # 【新增】技术面改善三大信号（支持买入决策）
        # ============================================================
        tech_improve_signals = []

        # 信号D：MA5上穿MA20（金叉）— 短期趋势由空转多
        ma5_series = calc_ma_series(closes, 5)
        ma20_series = calc_ma_series(closes, 20)
        if ma5_series and ma20_series and ma5_series[-1] is not None and ma20_series[-1] is not None:
            # 排除None值，取最近两个有效值
            valid_ma5 = [v for v in ma5_series[-3:] if v is not None]
            valid_ma20 = [v for v in ma20_series[-3:] if v is not None]
            if len(valid_ma5) >= 2 and len(valid_ma20) >= 2:
                ma5_yesterday, ma5_today = valid_ma5[-2], valid_ma5[-1]
                ma20_yesterday, ma20_today = valid_ma20[-2], valid_ma20[-1]
                # 昨日MA5 <= MA20，今日MA5 > MA20（均线金叉）
                if ma5_yesterday <= ma20_yesterday and ma5_today > ma20_today:
                    tech_improve_signals.append("MA5上穿MA20")
                # 或者MA5已在MA20上方（多头排列）
                elif ma5_today > ma20_today:
                    tech_improve_signals.append("MA5在MA20上方")

        # 信号E：量比放大（相对5日均量显著放大）
        if len(volumes) >= 5:
            vol_ma5_all = calc_ma(volumes, 5)
            if vol_ma5_all and vol_ma5_all > 0:
                vol_ratio_now = volumes[-1] / vol_ma5_all
                if vol_ratio_now >= p_vol_ratio_threshold:
                    tech_improve_signals.append("量比放大")

        # 信号F：出现阳线（今日收盘 > 开盘）
        if last_pct_chg > 0:
            tech_improve_signals.append("今日阳线")

        # 技术面改善信号统计（去重）
        tech_confirm_count = len(set(tech_improve_signals))

        # 如果设置了技术面改善最低要求，不满足则不通过
        if p_tech_confirm_min > 0 and tech_confirm_count < p_tech_confirm_min:
            continue

        if not stop_signals:
            continue

        # 核心条件4：所属行业有政策利好（属于热门概念）
        concepts = get_stock_concepts_eastmoney(ts_code)
        has_hot = any(
            any(hot in concept or concept in hot for hot in HOT_CONCEPTS)
            for concept in concepts
        )

        # 查找所属最强概念板块
        max_board_pct = 0
        max_board_name = ""
        if concept_boards:
            board_pct_map = {b["concept_name"]: b["change_pct"] for b in concept_boards}
            for concept in concepts:
                pct = board_pct_map.get(concept, 0)
                if pct and pct > max_board_pct:
                    max_board_pct = pct
                    max_board_name = concept

        # 板块名称 fallback：如果没匹配到但有概念列表，用第一个概念
        if not max_board_name and concepts:
            max_board_name = concepts[0]

        # 资金面
        money_flow = get_stock_money_flow(ts_code, days=5, batch_data=batch_money_data)
        total_net_in = sum(m["main_net_in"] for m in money_flow)
        inflow_days = sum(1 for m in money_flow if m["main_net_in"] > 0)
        inflow_ratio = abs(total_net_in) / (circ_mv * 10000) * 100 if circ_mv > 0 else 0

        circ_mv_yi = circ_mv / 10000

        # 获取换手率
        turnover_rate = 0
        if batch_basic_df is not None:
            row = batch_basic_df[batch_basic_df["ts_code"] == ts_code]
            if not row.empty:
                turnover_rate = row.iloc[0].get("turnover_rate", 0)

        # === 评分体系（100分制，分4个维度）===
        trend_score = 0
        sector_score = 0
        money_score = 0
        bonus_score = 0
        bonus_details = []

        # 维度1：趋势强度（跌幅+止跌信号，0-55分）
        # 跌幅评分（0-25分）：跌幅越大反弹空间越大
        if -40 <= pct_20d <= -30:
            trend_score += 25
        elif -50 <= pct_20d < -40:
            trend_score += 20
        elif -30 < pct_20d <= -20:
            trend_score += 15
        else:
            trend_score += 10

        # 止跌信号评分（0-30分）
        signal_count = len(set(stop_signals))
        if signal_count >= 3:
            trend_score += 30
            bonus_details.append(f"多重止跌({','.join(stop_signals[:3])})+30")
        elif signal_count == 2:
            trend_score += 25
            bonus_details.append(f"双信号({','.join(stop_signals)})+25")
        else:
            trend_score += 15
            bonus_details.append(f"单信号({stop_signals[0]})+15")

        # 维度2：板块效应（政策概念，0-15分）
        if has_hot:
            sector_score = 15
        else:
            sector_score = 5

        # 维度3：资金面（0-15分）
        if total_net_in > 0:
            money_score += 10
            bonus_details.append(f"资金流入{inflow_days}天+10")
        if inflow_ratio > 1:
            money_score += 5
            bonus_details.append("资金占比高+5")

        # 维度4：加分项（市值+业绩预告+回购，0-15分）
        if 100 * 10000 <= circ_mv <= 300 * 10000:
            bonus_score += 10
            bonus_details.append("市值区间佳+10")
        elif circ_mv <= 500 * 10000:
            bonus_score += 5
        else:
            bonus_score += 2

        # P1: 业绩预告加分
        try:
            from helpers import get_forecast, get_repurchase
            fc_list = get_forecast(ts_code)
            if fc_list:
                fc_type = str(fc_list[0].get("type", ""))
                if fc_type in ("预增", "扭亏", "略增"):
                    bonus_score += 3
                    bonus_details.append(f"业绩预告({fc_type})+3")
            # P1: 回购加分
            rp_list = get_repurchase(ts_code)
            if rp_list:
                rp_amount = rp_list[0].get("amount", 0)
                if rp_amount and float(rp_amount) > 10000:
                    bonus_score += 2
                    bonus_details.append("大额回购+2")
        except Exception:
            pass

        bonus_score = min(bonus_score, 15)

        # ============================================================
        # 【新增】维度5：技术面改善加分（0-15分，原20分降为15分，腾5分给HL结构）
        # ============================================================
        tech_score = 0
        if tech_improve_signals:
            signal_count = len(set(tech_improve_signals))
            if signal_count == 3:
                tech_score = 15
                bonus_details.append("三线改善(MA金叉+放量+阳线)+15")
            elif signal_count == 2:
                tech_score = 12
                bonus_details.append("双线改善(" + '+'.join(set(tech_improve_signals)) + ")+12")
            else:
                tech_score = 8
                bonus_details.append("单线改善(" + tech_improve_signals[0] + ")+8")
        else:
            # 无技术改善信号，降分处理
            bonus_details.append("无技术面改善信号")

        # 【新增】维度6：高低点结构评分（0-10分）
        # 超跌反弹策略中，HL结构用于确认下跌是否结束、反弹是否开始
        hl_score = 0
        hl_structure = "unknown"
        try:
            # 使用已有的 highs/lows 数据（前面已计算）
            n = 5
            local_highs = []
            local_lows = []
            
            for i in range(n, len(records) - n):
                current_high = highs[i]
                current_low = lows[i]
                left_highs = highs[i-n:i]
                left_lows = lows[i-n:i]
                right_highs = highs[i+1:i+n+1]
                right_lows = lows[i+1:i+n+1]
                
                if current_high > max(left_highs) and current_high > max(right_highs):
                    local_highs.append((i, current_high))
                if current_low < min(left_lows) and current_low < min(right_lows):
                    local_lows.append((i, current_low))
            
            # 分析结构（超跌反弹策略中，关注低点是否停止创新低）
            if len(local_highs) >= 3 and len(local_lows) >= 3:
                recent_highs = local_highs[-3:]
                recent_lows = local_lows[-3:]
                
                hh = sum(1 for i in range(1, len(recent_highs)) if recent_highs[i][1] > recent_highs[i-1][1])
                hl = sum(1 for i in range(1, len(recent_lows)) if recent_lows[i][1] > recent_lows[i-1][1])
                lh = sum(1 for i in range(1, len(recent_highs)) if recent_highs[i][1] < recent_highs[i-1][1])
                ll = sum(1 for i in range(1, len(recent_lows)) if recent_lows[i][1] < recent_lows[i-1][1])
                
                # 超跌反弹策略的特殊逻辑：
                # - HL >= 2（低点抬高）：下跌可能结束，反弹有望开始 +10分
                # - HH >= 1 且 HL >= 1：初步企稳 +6分
                # - 继续LL（低点创新低）：下跌趋势延续，不适合抄底 -5分
                if hl >= 2:
                    hl_score = 10
                    hl_structure = "rebound_ready"
                elif hh >= 1 and hl >= 1:
                    hl_score = 6
                    hl_structure = "stabilizing"
                elif ll >= 2:
                    hl_score = -5  # 仍在创新低，不适合抄底
                    hl_structure = "downtrend_continues"
                else:
                    hl_score = 3
                    hl_structure = "uncertain"
            else:
                hl_score = 3
                hl_structure = "insufficient_data"
        except Exception:
            hl_score = 3
            hl_structure = "error"

        total_score = trend_score + sector_score + money_score + bonus_score + tech_score + hl_score

        passed.append({
            "ts_code": ts_code,
            "name": name,
            "industry": stock.get("industry", ""),
            "price": round(last_close, 2),
            "pct_chg": round(last_pct_chg, 2),
            "pct_20d": round(pct_20d, 2),
            "circ_mv_yi": round(circ_mv_yi, 2),
            "turnover_rate": round(turnover_rate, 2),
            "max_board_pct": round(max_board_pct, 2),
            "max_board_name": max_board_name,
            "concepts": concepts[:5],
            "total_net_in": round(total_net_in, 2),
            "inflow_days": inflow_days,
            "inflow_ratio": round(inflow_ratio, 2),
            "stop_signals": list(set(stop_signals)),
            "tech_improve_signals": list(set(tech_improve_signals)),
            "tech_confirm_count": tech_confirm_count,
            "trend_score": trend_score,
            "sector_score": sector_score,
            "money_score": money_score,
            "bonus_score": bonus_score,
            "tech_score": tech_score,
            "hl_score": hl_score,
            "hl_structure": hl_structure,
            "bonus_details": bonus_details,
            "total_score": total_score,
        })

    # 按总分排序
    passed.sort(key=lambda x: x["total_score"], reverse=True)
    top_results = passed[:top_n]
    run_time = round(time.time() - start_time, 1)

    if not silent:
        print(f"\n  筛选完成！共 {len(passed)} 只，Top {len(top_results)}：")
        for i, r in enumerate(top_results):
            print(f"  [#{i+1}] {r['name']}({r['ts_code']}) "
                  f"Price:{r['price']} {r['pct_chg']}% 近20日:{r['pct_20d']}% 总分:{r['total_score']}")
        print(f"  耗时 {run_time}s")

    _flush_money_flow_cache()

    return {
        "market": market,
        "results": top_results,
        "all_results": passed,
        "stats": {
            "total_stocks": total_stocks_original,  # 使用原始全市场数量
            "after_basic": total_checked,
            "after_trend": len(passed) + total_checked - len(passed),  # 止跌信号过滤
            "after_sector": len(passed),
            "final_count": len(passed),
            "message": f"超跌反弹策略：共{len(passed)}只符合条件" if passed else "无符合条件的超跌标的",
            "data_warnings": _build_data_warnings(len(concept_boards) == 0, passed),
        },
        "run_time": run_time,
        "screen_date": datetime.now().strftime("%Y-%m-%d"),
        "screen_time": datetime.now().strftime("%H:%M"),
        "strategy": "oversold_bounce",
        "strategy_meta": STRATEGY_META["oversold_bounce"],
    }


# ============================================================
# 统一调度入口
# ============================================================

def run_strategy(strategy="trend_break", top_n=20, silent=False, force=False, params=None):
    """
    统一策略调度入口
    :param strategy: "trend_break" / "sector_leader" / "oversold_bounce"
    :param top_n: 输出数量
    :param silent: 静默模式
    :param force: 强制模式
    :param params: 策略参数列表 [{key, value}, ...] 用于覆盖默认值
    """
    if strategy == "sector_leader":
        result = run_sector_leader_screener(top_n=top_n, silent=silent, params=params)
    elif strategy == "oversold_bounce":
        result = run_oversold_bounce_screener(top_n=top_n, silent=silent, params=params)
    else:
        result = run_screener(top_n=top_n, silent=silent, force=force, params=params)
        result["strategy"] = "trend_break"
        result["strategy_meta"] = STRATEGY_META["trend_break"]

    return result


if __name__ == "__main__":
    import sys
    strategy = sys.argv[1] if len(sys.argv) > 1 else "trend_break"
    result = run_strategy(strategy=strategy, top_n=20)
    if result["results"]:
        save_screen_result(result)
        print(f"\n结果已保存到 {SCREEN_HISTORY_FILE}")
