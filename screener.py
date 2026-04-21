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


_recent_trade_dates_cache = {}  # days -> [date_list]

def get_recent_trade_dates(days=5):
    """
    获取最近N个交易日日期（降序排列）
    返回: ["YYYYMMDD", ...]
    v3.5.3 性能优化: 添加进程内缓存，同一次选股只查一次trade_cal API
    """
    global _recent_trade_dates_cache
    # 返回缓存（最多缓存比请求的天数更多的结果）
    if days in _recent_trade_dates_cache:
        return _recent_trade_dates_cache[days]
    # 如果有更多天数的缓存，直接截取
    for cached_days, cached_dates in _recent_trade_dates_cache.items():
        if cached_days >= days and cached_dates:
            result = cached_dates[:days]
            _recent_trade_dates_cache[days] = result
            return result

    dates = []
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=max(days, 45) * 2)).strftime("%Y%m%d")
    try:
        df = pro.trade_cal(exchange="SSE", start_date=start_date, end_date=end_date, is_open="1")
        if not df.empty:
            all_dates = df.sort_values("cal_date", ascending=False)["cal_date"].tolist()
            # 缓存最多60天供后续使用
            _recent_trade_dates_cache[len(all_dates)] = all_dates
            dates = all_dates[:days]
            _recent_trade_dates_cache[days] = dates
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
        # v3.5.2: 如果当天数据为空（盘中/刚收盘），自动尝试前一个交易日
        if df.empty:
            prev_dates = get_recent_trade_dates(3)
            for pd_ in prev_dates:
                if pd_ != trade_date:
                    print(f"[WARN] ths_daily 返回空数据 ({trade_date})，尝试 {pd_}...")
                    df = pro.ths_daily(trade_date=pd_,
                                       fields="ts_code,trade_date,pct_change,close,vol,turnover_rate")
                    if not df.empty:
                        trade_date = pd_
                        break
            if df.empty:
                print(f"[WARN] ths_daily 所有尝试均返回空")
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
    返回: {ts_code: {"history": [net_mf_amount, ...], "buy_lg_amount": float, "sell_lg_amount": float,
                     "buy_elg_amount": float, "sell_elg_amount": float, "latest_date": str}}
           每个ts_code的值是dict，同时包含历史净流入序列和最新日完整大单字段
    """
    trade_dates = get_recent_trade_dates(days)
    if not trade_dates:
        return {}

    # v3.5.2 修复：返回dict结构而非list，支持大单评分
    result = {}  # ts_code -> dict with history + latest fields
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
                # v3.5.2: 取完整字段用于大单分析（首日取全部字段，后续只取net_mf节省内存）
                fields = "ts_code,trade_date,net_mf_amount,buy_lg_amount,sell_lg_amount,buy_elg_amount,sell_elg_amount"
                df = pro.moneyflow(trade_date=td, fields=fields)
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
            net_val = float(row.get("net_mf_amount", 0))
            if ts_code not in result:
                result[ts_code] = {
                    "history": [],
                    "buy_lg_amount": 0,
                    "sell_lg_amount": 0,
                    "buy_elg_amount": 0,
                    "sell_elg_amount": 0,
                    "latest_date": td,
                }
            result[ts_code]["history"].append(net_val)
            # 用最新一日（第一个遍历到的日期）的大单完整字段
            if result[ts_code]["latest_date"] == td or result[ts_code].get("_filled") is None:
                result[ts_code]["buy_lg_amount"] = float(row.get("buy_lg_amount", 0))
                result[ts_code]["sell_lg_amount"] = float(row.get("sell_lg_amount", 0))
                result[ts_code]["buy_elg_amount"] = float(row.get("buy_elg_amount", 0))
                result[ts_code]["sell_elg_amount"] = float(row.get("sell_elg_amount", 0))

        # 标记第一轮已填充完整字段
        if i == 0:
            for ts_code in result:
                result[ts_code]["_filled"] = True

    return result


def get_stock_money_flow(ts_code, days=5, batch_data=None):
    """
    获取个股主力资金流向（近N日）
    仅使用 Tushare 批量数据（moneyflow）
    batch_data: get_batch_money_flow_multi() 返回的批量数据 (dict结构)
    返回: [{date, main_net_in, ...}, ...]
    """
    # v3.5.2: 适配新dict结构 {ts_code: {"history": [...], "buy_lg_amount": ...}}
    if batch_data and ts_code in batch_data:
        entry = batch_data[ts_code]
        if isinstance(entry, dict) and "history" in entry:
            values = entry["history"][:days]
        else:
            # 兼容旧的list结构
            values = entry[:days] if isinstance(entry, list) else []
        
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

    # 无批量数据时尝试缓存
    cached, cached_date = _load_money_flow_cache(ts_code)
    if cached:
        print(f"[CACHE] 资金流向({ts_code})，使用 {cached_date} 缓存")
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
_concept_bulk_loaded = False  # 是否已完成全量概念预加载

# v3.5.2: 无效概念标签黑名单（这些标签无行业区分度，无法匹配ths_daily板块名）
_NOISE_CONCEPTS = frozenset({
    # 融资融券类
    '转融券标的', '融资融券', '融资标的股', '融券标的股', '融资融券标的',
    # 沪深港通类
    '深股通', '沪股通', '港股通', '陆股通',
    # 指数成分类（无行业含义）
    '沪深300样本股', '上证50样本股', '上证180成份股', '中证500样本股',
    '标普道琼斯A股', 'MSCI中国A股', '富时罗素', '同花顺漂亮100',
    # 优先股等非行业概念
    '优先股概念', '预增预盈', '股权激励',
})


def preload_all_concepts():
    """
    v3.5.3 性能优化: 全市场概念数据一次性预加载（替代逐只 concept_detail 查询）
    策略：先获取所有concept_id（ths_index N类），再批量遍历每个概念的成分股 ths_member
    构建反向索引：{ts_code: [concept_name, ...]}
    
    调用时机：板块评分开始前调用一次，此后所有 get_stock_concepts_eastmoney 走缓存
    预期耗时：<60s（约400~600个有效概念，每个ths_member ~0.1s）
    """
    global _concept_cache, _concept_bulk_loaded
    if _concept_bulk_loaded:
        return
    
    print("  [性能] 预加载全市场概念数据（替代逐只concept_detail）...")
    
    # 尝试从磁盘缓存加载（当日缓存有效）
    cached, cached_date = _load_cache("concept_reverse_index")
    today = datetime.now().strftime("%Y%m%d")
    trade_date = get_latest_trade_date()
    if cached and cached_date == trade_date:
        _concept_cache.update(cached)
        _concept_bulk_loaded = True
        print(f"  [缓存] 概念反向索引已从缓存加载: {len(_concept_cache)} 只股票")
        return
    
    try:
        # 第1步：获取所有N类（普通概念）板块列表
        df_idx = pro.ths_index(type="N", fields="ts_code,name")
        if df_idx is None or df_idx.empty:
            print("  [WARN] ths_index返回空，概念预加载失败，降级到逐只查询")
            _concept_bulk_loaded = True  # 标记避免重复尝试
            return
        
        concepts = df_idx.to_dict("records")
        print(f"  [性能] 找到 {len(concepts)} 个N类概念板块，开始构建反向索引...")
        
        # 第2步：遍历每个概念，获取成分股
        reverse_index = {}  # ts_code -> [concept_name, ...]
        success_count = 0
        
        for i, concept in enumerate(concepts):
            concept_code = concept["ts_code"]
            concept_name = concept["name"]
            
            if concept_name in _NOISE_CONCEPTS:
                continue
            
            try:
                df_member = pro.ths_member(ts_code=concept_code, fields="ts_code,con_code,con_name")
                if df_member is not None and not df_member.empty:
                    for _, row in df_member.iterrows():
                        # ths_member 返回 con_code 字段（标准格式如 000001.SZ）
                        stock_code = str(row.get("con_code", "")).strip()
                        if not stock_code or len(stock_code) < 6:
                            # 尝试 ts_code 字段
                            stock_code = str(row.get("ts_code", "")).strip()
                        if stock_code and "." in stock_code:
                            if stock_code not in reverse_index:
                                reverse_index[stock_code] = []
                            if concept_name not in reverse_index[stock_code]:
                                reverse_index[stock_code].append(concept_name)
                    success_count += 1
                
                # 进度提示（每100个）
                if (i + 1) % 100 == 0:
                    print(f"  [性能] 已处理 {i+1}/{len(concepts)} 个概念，反向索引 {len(reverse_index)} 只股票")
                    
            except Exception as e:
                # 限速时等待
                if "每分钟" in str(e):
                    import time as _time
                    _time.sleep(60)
                    try:
                        df_member = pro.ths_member(ts_code=concept_code, fields="ts_code,con_code,con_name")
                        if df_member is not None and not df_member.empty:
                            for _, row in df_member.iterrows():
                                stock_code = str(row.get("con_code", row.get("ts_code", ""))).strip()
                                if stock_code and "." in stock_code:
                                    if stock_code not in reverse_index:
                                        reverse_index[stock_code] = []
                                    if concept_name not in reverse_index[stock_code]:
                                        reverse_index[stock_code].append(concept_name)
                    except Exception:
                        pass
        
        # 更新内存缓存
        _concept_cache.update(reverse_index)
        # 将没有概念的股票也标记（避免后续再查）
        _concept_bulk_loaded = True
        
        # 保存到磁盘缓存
        _save_cache("concept_reverse_index", reverse_index, trade_date)
        print(f"  [性能] 概念预加载完成: {len(reverse_index)} 只股票有概念数据，成功概念 {success_count}/{len(concepts)}")
        
    except Exception as e:
        print(f"  [WARN] 概念预加载失败: {e}，降级到逐只查询")
        _concept_bulk_loaded = True  # 避免重复失败


def get_stock_concepts_eastmoney(ts_code):
    """
    获取单只股票所属的概念板块名称列表（仅使用 Tushare）
    v3.5.2: 过滤无行业区分度的噪声标签，只保留有价值的行业概念
    v3.5.3: 如果 preload_all_concepts() 已运行，直接走内存缓存（0次API）
    """
    global _concept_cache
    if ts_code in _concept_cache:
        return _concept_cache[ts_code]
    
    # 如果已经全量预加载，未命中说明该股票没有概念数据
    if _concept_bulk_loaded:
        _concept_cache[ts_code] = []
        return []

    # 方法1：Tushare concept_detail（稳定可靠）
    try:
        df = pro.concept_detail(ts_code=ts_code, fields="ts_code,concept_name")
        if not df.empty:
            raw_concepts = df["concept_name"].dropna().tolist()
            # v3.5.2: 过滤噪声标签，只保留有行业区分度的概念
            concepts = [c for c in raw_concepts if c not in _NOISE_CONCEPTS]
            _concept_cache[ts_code] = concepts
            return concepts
    except Exception as e:
        print(f"[WARN] Tushare concept_detail失败({ts_code}): {e}")

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
    使用 Tushare limit_list_d 接口
    """
    try:
        trade_date = get_latest_trade_date()
        df = pro.limit_list_d(trade_date=trade_date, fields="ts_code,trade_date,name,pct_chg,limit")
        if not df.empty:
            # limit=U表示涨停，D表示跌停
            limit_up = len(df[df["limit"] == "U"])
            limit_down = len(df[df["limit"] == "D"])
            return {
                "limit_up_count": limit_up,
                "limit_down_count": limit_down,
            }
        return {"limit_up_count": 0, "limit_down_count": 0}
    except Exception:
        return {"limit_up_count": 0, "limit_down_count": 0}
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


def get_dynamic_threshold(market_status):
    """
    趋势突破策略 v3.1 动态及格线（方案B权重调整后）
    
    方案B权重体系：趋势(50) + 板块(15) + 资金(10) + 共振(15) + 催化(10) = 100
    动态门槛同比调整：牛市高标准 / 震荡中标准 / 熊市低门槛
    
    v3.5 调整：大幅降低门槛以适配弱市环境（板块数据源切换后分数普遍降低）
    """
    _threshold_map = {
        "上升": 60,
        "强上升": 60,
        "震荡": 45,
        "下降": 35,
        "unknown": 40,
    }
    return _threshold_map.get(market_status, 45)
    return _threshold_map.get(market_status, 65)


# ============================================================
# 第2步：基础过滤
# ============================================================

def basic_filter(stock_list, daily_cache, basic_cache, batch_basic_df=None, batch_daily_df=None, filter_params=None, batch_daily_multi=None):
    """
    基础过滤：排除ST、停牌、新股、追高票、市值不合的
    支持批量数据预过滤（大幅减少API调用次数）
    :param filter_params: 可调参数覆盖 {"min_circ_mv": 50, "max_circ_mv": 300, "min_turnover": 1.5}
    :param batch_daily_multi: v3.5.3 get_batch_daily_multi()返回的{ts_code: [records]}
    返回: 通过的股票列表
    """
    # 解析参数
    fp = filter_params or {}
    p_min_circ_mv = float(fp.get("min_circ_mv", 50))
    p_max_circ_mv = float(fp.get("max_circ_mv", 300))
    p_min_turnover = float(fp.get("min_turnover", 1.5))

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

            # v3.5.3: 即使在回退路径，也优先查batch_daily_multi
            if batch_daily_multi and ts_code in batch_daily_multi:
                daily_cache[ts_code] = batch_daily_multi[ts_code]
            elif ts_code not in daily_cache:
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
        # v3.5.3: 优先使用 batch_daily_multi（O(days*API) vs O(N*API)）
        if ts_code not in daily_cache or not daily_cache.get(ts_code):
            if batch_daily_multi and ts_code in batch_daily_multi:
                daily_cache[ts_code] = batch_daily_multi[ts_code]
            else:
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
    
    # ========== 一看：高低点抬高（统一调用公共函数，n=3适合波段选股） ==========
    from helpers import analyze_hl_points
    hl_result = analyze_hl_points(highs=highs, lows=lows, n=3)

    recent_lows = [(idx, p) for idx, p in hl_result.get("recent_lows", [])]
    recent_highs = [(idx, p) for idx, p in hl_result.get("recent_highs", [])]

    low_increasing = hl_result.get("low_trend") == "up"
    high_increasing = hl_result.get("high_trend") == "up"
    
    result["details"]["high_low"] = {
        "passed": bool(low_increasing and high_increasing),
        "low_increasing": bool(low_increasing),
        "high_increasing": bool(high_increasing),
        "recent_lows": [round(x[1], 2) for x in recent_lows],
        "recent_highs": [round(x[1], 2) for x in recent_highs],
        "_hl_structure": hl_result.get("structure"),
        "_hl_score": hl_result.get("score", 0),
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
    # [方案C-2] 原逻辑要求三看全过（AND），改为至少通过2看
    passed_count = sum([
        1 if result["details"]["high_low"]["passed"] else 0,
        1 if result["details"]["ma"]["passed"] else 0,
        1 if result["details"]["volume"]["passed"] else 0,
    ])
    result["all_passed"] = passed_count >= 2
    result["passed_count"] = passed_count  # 暴露通过数量供后续评分使用
    
    return result["all_passed"], result


# ============================================================
# 第3步：趋势确认
# ============================================================

def trend_scoring(candidates, filter_params=None):
    """
    趋势突破策略 v3.0 趋势面评分（满分35分）
    
    核心原则：去冗余、加验证、补出场。满分100分，建议实操场门槛≥75分。
    
    评分维度：
      - 趋势强度(13分)：合并原MA20位置+方向+HL结构，去冗余
      - MACD状态(12分)：保留，删除"刚金叉+3"奖励
      - 均线形态(10分)：新增 MA5/MA10/MA20 多头排列
    
    :return: 所有候选股（带trend_score），不做淘汰，由final_ranking统一门槛筛选
    """
    fp = filter_params or {}
    
    # 从参数面板读取阈值（用户可调，默认值与界面一致）
    p_deviation_min = float(fp.get("ma20_deviation_min", 0))       # 界面默认0%
    p_vol_ratio = float(fp.get("min_vol_ratio", 1.2))              # 界面默认1.2
    p_price_chg = float(fp.get("min_price_change", 0.5))           # 界面默认0.5%
    MIN_TREND_SCORE = float(fp.get("min_trend_score", 8))           # 极低门槛保留所有票
    
    scored = []
    for c in candidates:
        records = c["records"]
        closes = [float(r["close"]) for r in records]
        highs = [float(r["high"]) for r in records]
        lows = [float(r["low"]) for r in records]
        volumes = [float(r["vol"]) for r in records]

        # === 数据校验 ===
        ma20_values = calc_ma_series(closes, 20)
        recent_ma20 = [v for v in ma20_values[-4:] if v is not None]
        if len(recent_ma20) < 3:
            c["trend_score"] = 0
            c["trend_detail"] = {"error": "MA20数据不足"}
            scored.append(c)
            continue

        last_close = closes[-1]
        prev_close = closes[-2] if len(closes) >= 2 else last_close
        current_ma20 = recent_ma20[-1]

        # === 开始3维评分 ===
        ts = 0  # trend_score
        detail = {}

        # ── 维度1：趋势强度（0-13分）合并MA20位置+方向+HL ──
        deviation = (last_close - current_ma20) / current_ma20 * 100
        
        # 1a. MA20位置（0-5分）：站上即有基础分，阈值由参数控制
        if last_close >= current_ma20:
            if deviation <= max(1, p_deviation_min):
                pos_s = 5   # 刚站上或微偏，最健康
                detail["ma20_pos"] = f"站稳({deviation:+.1f}%)"
            elif deviation <= max(3, p_deviation_min * 3):
                pos_s = 4   # 温和偏高
                detail["ma20_pos"] = f"偏高({deviation:+.1f}%)"
            elif deviation <= max(6, p_deviation_min * 6):
                pos_s = 2   # 明显偏高
                detail["ma20_pos"] = f"过高({deviation:+.1f}%)⚠️"
            else:
                pos_s = 1   # 过度追高
                detail["ma20_pos"] = f"危险高位({deviation:+.1f}%)❌"
        else:
            pos_s = 0
            detail["ma20_pos"] = f"未站上MA20({deviation:.1f}%)"

        # 1b. MA20方向（0-4分）
        ma20_up_days = sum(1 for i in range(1, len(recent_ma20)) if recent_ma20[i] >= recent_ma20[i-1])
        if ma20_up_days == 3:
            dir_s = 4; detail["ma20_dir"] = "MA20连升3日↑↑"
        elif ma20_up_days == 2:
            dir_s = 3; detail["ma20_dir"] = "MA20升2日↑"
        elif ma20_up_days == 1:
            dir_s = 2; detail["ma20_dir"] = "MA20升1日→"
        else:
            dir_s = 0; detail["ma20_dir"] = "MA20走平或降↓"

        # 1c. 高低点HL结构（0-4分）
        try:
            from helpers import analyze_hl_points
            hl_result = analyze_hl_points(highs=highs, lows=lows, n=4)
            hl_s = min(hl_result.get("score", 2), 4)
            c["hl_structure"] = hl_result.get("structure", "insufficient_data")
            c["_hl_signal"] = hl_result.get("signal", "")
            detail["hl"] = c["hl_structure"]
        except Exception:
            hl_s = 1; detail["hl"] = "N/A"; c["hl_structure"] = "error"

        trend_strength = pos_s + dir_s + hl_s
        ts += trend_strength
        detail["trend_strength"] = f"{trend_strength}/13(位{pos_s}+向{dir_s}+形{hl_s})"

        # ── 维度2：MACD状态（0-12分）[无额外奖励] ──
        macd_s = 0
        macd = calc_macd(closes)
        if macd and len(macd["dif"]) >= 2:
            dif_now = macd["dif"][-1]; dif_prev = macd["dif"][-2]
            dea_now = macd["dea"][-1]; dea_prev = macd["dea"][-2]
            macd_now = macd["macd"][-1]; macd_prev = macd["macd"][-2]

            is_cross = (dif_prev <= dea_prev) and (dif_now > dea_now)
            is_hist_cross = (macd_prev <= 0) and (macd_now > 0)

            if is_cross or is_hist_cross:
                if dif_now > 0 and dea_now > 0:
                    macd_s = 12; c["macd_signal"] = "zero_cross_up"; detail["macd"] = "零上金叉🔥"
                else:
                    macd_s = 9;  c["macd_signal"] = "cross_below_zero"; detail["macd"] = "零下金叉↗"
            elif dif_now > dea_now and dea_now > 0:
                macd_s = 11; c["macd_signal"] = "bullish_trend"; detail["macd"] = "多头格局✓"
            elif dif_now > dea_now:
                macd_s = 7;  c["macd_signal"] = "weak_bullish"; detail["macd"] = "弱多头~"
            elif dif_now > dea_prev:
                macd_s = 3;  detail["macd"] = "接近金叉…"
            else:
                macd_s = 0;  detail["macd"] = "空头✗"
        else:
            detail["macd"] = "数据不足"
        
        ts += macd_s

        # ── 维度3：均线多头排列（0-10分）MA5 > MA10 > MA20 ──
        ma_pattern_s = 0
        ma5_val = calc_ma(closes, 5)
        ma10_val = calc_ma(closes, 10)
        if ma5_val and ma10_val and current_ma20:
            if ma5_val > ma10_val > current_ma20:
                # 完美多头排列 — 进一步判断发散程度
                spread_5_20 = (ma5_val - current_ma20) / current_ma20 * 100
                if spread_5_20 > 2:
                    ma_pattern_s = 10; detail["ma_pattern"] = "完美多头(发散)★★★"
                elif spread_5_20 > 0.5:
                    ma_pattern_s = 8;  detail["ma_pattern"] = "标准多头★★"
                else:
                    ma_pattern_s = 6;  detail["ma_pattern"] = "粘合多头★"
            elif ma5_val > current_ma20:
                ma_pattern_s = 4;  detail["ma_pattern"] = "部分多头(MA5>MA20)"
            else:
                ma_pattern_s = 0;  detail["ma_pattern"] = "均线混乱"
        else:
            detail["ma_pattern"] = "数据不足"
        
        ts += ma_pattern_s
        
        # === 辅助数据 + 参数联动加分 ===
        vol_ma5 = calc_ma(volumes, 5)
        vol_ratio = volumes[-1] / vol_ma5 if (vol_ma5 and vol_ma5 > 0) else 0
        price_change_pct = (last_close - prev_close) / prev_close * 100
        
        # [v3.0] 量比参数联动：超过用户设定阈值时小幅加分（非硬过滤）
        vol_bonus = 0
        if vol_ratio >= p_vol_ratio:
            if vol_ratio >= p_vol_ratio * 1.5:
                vol_bonus = 1   # 明显放量
            else:
                vol_bonus = 0.5 # 适度放量
        
        # [v3.0] 涨幅参数联动：达到用户最低涨幅要求时小幅加分
        momentum_bonus = 0
        if p_price_chg > 0 and price_change_pct >= p_price_chg:
            momentum_bonus = min(price_change_pct / p_price_chg * 0.3, 1.5)
        
        ts += round(vol_bonus + momentum_bonus, 1)
        
        three_views_passed, three_views_result = check_three_views(records)
        c["three_views"] = three_views_result["details"]
        c["three_views_passed_count"] = three_views_result.get("passed_count", 0)

        c["trend_score"] = ts
        c["ma20"] = round(current_ma20, 2)
        c["deviation"] = round(deviation, 2)
        c["vol_ratio"] = round(vol_ratio, 2) if vol_ratio else 0
        c["price_change_pct"] = round(price_change_pct, 2)
        c["trend_detail"] = detail

        scored.append(c)

    return scored


# ============================================================
# 第4步：板块与资金
# ============================================================

def sector_money_scoring(candidates, concept_boards, filter_params=None,
                          batch_money_data=None, board_data_date=None):
    """
    趋势突破策略 v3.1 板块面+资金面评分（权重调整后）
    
    板块面（15分）：
      - 板块相对强弱(12分)：板块涨幅-大盘涨幅（相对市场超额）
      - 板块热度排名(8分)：排名前15算分
    
    资金面（10分）：
      - 流入强度(8分)：主力净流入强度
      - 大单净买结构(7分)：大单净买占比验证真实主力
      - 流入持续性(5分)：辅助项
    
    方案B权重调整（v3.1）：趋势可靠数据权重上调，板块/资金面下调
      趋势(50) + 板块(15) + 资金(10) + 共振(15) + 催化(10) = 100
    
    :return: 所有候选股（带sector_score, money_score），不做淘汰
    """
    fp = filter_params or {}
    p_min_inflow_days = fp.get("min_inflow_days", 1)   # 界面默认1天

    # 构建板块涨幅映射 + 计算大盘基准
    board_pct_map = {b["concept_name"]: b["change_pct"] for b in concept_boards}
    
    # 大盘涨跌幅作为超额计算基准
    market_change = 0
    try:
        from helpers import get_index_quotes as _giq
        idx_data = _giq()
        if idx_data and len(idx_data) > 0:
            sh_idx = [i for i in idx_data if i.get("code") in ("000001.SH", "sh000001")]
            if sh_idx:
                market_change = float(sh_idx[0].get("change_pct", 0))
    except Exception:
        pass

    # 检测板块数据是否过期
    board_data_stale = False
    if board_data_date:
        try:
            latest_trade = get_latest_trade_date()
            import datetime as _dt
            board_dt = _dt.strptime(board_data_date, "%Y%m%d")
            latest_dt = _dt.strptime(latest_trade, "%Y%m%d")
            if (latest_dt - board_dt).days > 1:
                board_data_stale = True
                print(f"  [WARN] 板块数据已过期（{board_data_date} vs {latest_trade}），放宽评分")
        except Exception:
            pass

    downgrade_mode = len(concept_boards) == 0 or board_data_stale

    scored = []
    for c in candidates:
        ts_code = c["ts_code"]
        
        # === 板块评分（0-20分）===
        concepts = get_stock_concepts_eastmoney(ts_code)
        max_board_pct = 0
        max_board_name = ""

        if downgrade_mode:
            has_hot = any(
                any(hot in concept or concept in hot for hot in HOT_CONCEPTS)
                for concept in concepts
            )
            if has_hot:
                sector_base = 10; max_board_name = "热门概念(降级)"
            else:
                sector_base = 4;  max_board_name = concepts[0] if concepts else "未知"
            rank_bonus = 0
        else:
            for concept in concepts:
                pct = board_pct_map.get(concept, 0)
                if pct and abs(pct) > abs(max_board_pct):
                    max_board_pct = pct
                    max_board_name = concept
            
            if not max_board_name and concepts:
                max_board_name = concepts[0]

            # ── 相对强弱（0-12分）：板块超额 vs 大盘 ──
            excess_return = max_board_pct - market_change  # 相对大盘的超额收益
            
            if excess_return > 3:
                sector_base = 12   # 显著跑赢大盘
            elif excess_return > 1.5:
                sector_base = 10   # 稳健跑赢
            elif excess_return > 0.5:
                sector_base = 7    # 微幅跑赢
            elif excess_return > -0.5:
                sector_base = 4    # 持平大盘
            elif excess_return > -2:
                sector_base = 2    # 弱于大盘但可接受
            else:
                sector_base = 0    # 明显弱于大盘
            
            # ── 排名加分（0-8分）──
            rank_bonus = 0
            for idx, b in enumerate(concept_boards[:15]):
                if b["concept_name"] == max_board_name or (concepts and b["concept_name"] in concepts):
                    if idx < 3:     rank_bonus = 8
                    elif idx < 6:   rank_bonus = 6
                    elif idx < 10:  rank_bonus = 4
                    elif idx < 15:  rank_bonus = 2
                    break

        ss = min(sector_base + rank_bonus, 20)
        # 方案B(v3.1)：板块满分20分 → 压缩到15分
        c["sector_score"] = int(ss * 15 / 20)   # max: 15
        c["max_board_pct"] = round(max_board_pct, 2)
        c["max_board_name"] = max_board_name
        c["excess_return"] = round(max_board_pct - market_change, 2) if not downgrade_mode else None
        c["market_benchmark"] = round(market_change, 2)
        c["concepts"] = concepts
        c["downgrade_mode"] = downgrade_mode

        # === 资金面评分（0-20分）===
        money_flow = get_stock_money_flow(ts_code, days=5, batch_data=batch_money_data)
        total_net_in = sum(m["main_net_in"] for m in money_flow)
        inflow_days = sum(1 for m in money_flow if m["main_net_in"] > 0)

        circ_mv = c["circ_mv"]
        inflow_ratio = abs(total_net_in) / (circ_mv * 10000) * 100 if circ_mv > 0 else 0
        
        ms = 0  # money_score
        
        # ── 子项1：流入强度（0-8分，降权）──
        if total_net_in > 0:
            if inflow_ratio > 2:   ms += 8
            elif inflow_ratio > 1: ms += 6
            elif inflow_ratio > 0.3: ms += 4
            else:                  ms += 2
        else:
            # 净流出也给基础分保留票
            if inflow_ratio > -1:  ms += 2
            elif inflow_ratio > -3: ms += 1
            else:                   ms += 0
        
        # ── 子项2：大单净买结构（0-7分，新增！）──
        # 用大单买卖差额占比验证资金真实性
        big_buy_ratio = 0
        try:
            # 从moneyflow批量数据的原始字段中提取大单结构
            if batch_money_data and ts_code in batch_money_data and isinstance(batch_money_data[ts_code], dict):
                raw = batch_money_data[ts_code]
                buy_lg = float(raw.get("buy_lg_amount", 0))
                sell_lg = float(raw.get("sell_lg_amount", 0))
                buy_elg = float(raw.get("buy_elg_amount", 0))
                sell_elg = float(raw.get("sell_elg_amount", 0))
                
                big_total = buy_lg + sell_lg + buy_elg + sell_elg
                if big_total > 0:
                    big_buy_ratio = (buy_lg + buy_elg - sell_lg - sell_elg) / big_total * 100
                    
                    if big_buy_ratio > 10:
                        ms += 7   # 强力大单扫货
                    elif big_buy_ratio > 5:
                        ms += 5   # 大单偏多
                    elif big_buy_ratio > 0:
                        ms += 3   # 大单略占优
                    elif big_buy_ratio > -5:
                        ms += 1   # 多空均衡
                    # else: 0分，大单偏空
        except Exception:
            pass
        
        c["big_buy_ratio"] = round(big_buy_ratio, 2) if big_buy_ratio != 0 else 0
        
        # ── 子项3：流入持续性（0-5分，辅助项，阈值由参数控制）──
        if inflow_days >= p_min_inflow_days:
            if inflow_days >= 4:     ms += 5
            elif inflow_days >= 3:   ms += 4
            elif inflow_days >= 2:   ms += 3
            else:                    ms += 2
        else:
            ms += 1  # 未达到最低天数也给1分保留
        
        # 方案B(v3.1)：资金满分20分 → 压缩到10分
        c["money_score"] = int(min(ms, 20) * 10 / 20)   # max: 10
        c["total_net_in"] = round(total_net_in, 2)
        c["inflow_days"] = inflow_days
        c["inflow_ratio"] = round(inflow_ratio, 2)

        scored.append(c)

    return scored


# ═══════════════════════════════════════════
# 第5步：多周期共振评分（v3.0: 三看升级为主维度15分）
# ═══════════════════════════════════════════

def resonance_scoring(candidates):
    """
    趋势突破策略 v3.0 多周期共振（满分15分）
    
    三看检查从加分项提升为主维度，权重大幅增加。
    核心逻辑：量价配合+均线结构+高低点形态三者共振才是真突破。
    评分：全过15/过半9/部分5/不过0
    """
    for c in candidates:
        tv_count = c.get("three_views_passed_count", 0)
        
        if tv_count >= 3:
            rs = 15; detail = "三看全通过🔥"
        elif tv_count == 2:
            rs = 9;  detail = "二看通过(2/3)"
        elif tv_count == 1:
            rs = 5;  detail = "一看通过(1/3)"
        else:
            rs = 0;  detail = "未通过(0/3)"
        
        c["resonance_score"] = rs
        c["resonance_detail"] = detail
    
    return candidates


def catalyst_scoring(candidates, limit_stocks=None, limit_up_count=0):
    """
    趋势突破策略 v3.1 催化加分（满分10分，严格capped）
    
    方案B(v3.1)权重体系：趋势(50) + 板块(15) + 资金(10) + 共振(15) + 催化(10) = 100
    
    基本面催化(5分)：
      - 业绩预告(30日内)预增/扭亏/略增 → +5（与回购互斥取高）
      - 大额回购(>1亿) → +5 / (>3000万) → +3
    
    技术形态催化(3分)：
      - 缩量回踩企稳(MA20附近缩量) → +3
    
    情绪催化(2分)：
      - 近5日涨停板 → +2
      
    v3.5.3 性能优化: 不再预加载全部股票的forecast/repurchase（O(2N)API调用），
                   改为延迟查询 + 仅对趋势+板块总分>=25的候选股查询财务数据
    """
    # v3.5.3: 延迟加载财务数据（仅对高分候选股查询，减少~80%API调用）
    forecast_cache = {}
    repurchase_cache = {}
    _forecast_fn = None
    _repurchase_fn = None
    try:
        from helpers import get_forecast, get_repurchase
        _forecast_fn = get_forecast
        _repurchase_fn = get_repurchase
    except ImportError:
        print(f"  [WARN] 财务函数导入失败，跳过基本面催化")

    limit_stocks = limit_stocks or set()
    
    for c in candidates:
        records = c.get("records", [])
        closes = [float(r["close"]) for r in records]
        volumes = [float(r["vol"]) for r in records]
        
        cat_s = 0
        details = []

        # === 基本面催化（0-5分，互斥取高）===
        tc = c["ts_code"]
        fc_score = 0; rp_score = 0
        
        # v3.5.3: 延迟查询 — 仅对趋势+板块>=25分的候选股查财务数据
        # 这排除了大部分低分票(约70%+)，将API调用从2912次降至~900次
        _trend_score = c.get("trend_score", 0)
        _sector_score = c.get("sector_score", 0)
        _should_check_fund = (_trend_score + _sector_score) >= 25
        
        if _should_check_fund and _forecast_fn:
            if tc not in forecast_cache:
                try:
                    forecast_cache[tc] = _forecast_fn(tc)
                except Exception:
                    forecast_cache[tc] = []
            if tc not in repurchase_cache:
                try:
                    repurchase_cache[tc] = _repurchase_fn(tc)
                except Exception:
                    repurchase_cache[tc] = []
        
        # 业绩预告
        fc_list = forecast_cache.get(tc, [])
        if fc_list:
            latest_fc = fc_list[0]
            fc_type = str(latest_fc.get("type", ""))
            if fc_type in ("预增", "扭亏", "略增"):
                fc_score = 5

        # 回购
        rp_list = repurchase_cache.get(tc, [])
        if rp_list:
            latest_rp = rp_list[0]
            rp_amount = latest_rp.get("amount", 0)
            if rp_amount and float(rp_amount) > 10000:
                rp_score = 5
            elif rp_amount and float(rp_amount) > 3000:
                rp_score = 3
        
        # 取基本面催化最高值
        fund_cat = max(fc_score, rp_score)
        cat_s += fund_cat
        if fc_score >= rp_score and fc_score > 0:
            details.append(f"业绩{fc_list[0].get('type','')}+{fc_score}")
        elif rp_score > 0:
            rp_amt = float(repurchase_cache[tc][0].get("amount", 0))
            details.append(f"回购{int(rp_amt/10000)}亿+{rp_score}")

        # === 技术形态催化（0-3分）===
        if len(records) >= 10:
            for j in range(-3, 0):
                idx = len(records) + j
                if idx < 10:
                    continue
                low = float(records[idx]["low"])
                close_r = float(records[idx]["close"])
                vol = float(records[idx]["vol"])
                ma20_r = calc_ma(closes[:idx + 1], 20)
                vol_ma5_r = calc_ma(volumes[:idx + 1], 5)
                if ma20_r and vol_ma5_r:
                    if low < ma20_r * 1.02 and close_r > ma20_r and vol < vol_ma5_r * 0.8:
                        cat_s += 3; details.append("缩量回踩+3"); break

        # === 情绪催化（0-2分）===
        if tc in limit_stocks:
            cat_s += 2; details.append("近5日涨停+2")

        c["catalyst_score"] = min(cat_s, 10)
        c["catalyst_details"] = details

    return candidates


# ============================================================
# 第6步：汇总输出
# ============================================================

def final_ranking(candidates, min_score=75):
    """
    趋势突破策略 v3.0 汇总输出
    
    总分 = 趋势面(35) + 板块面(20) + 资金面(20) + 共振(15) + 催化(10) = 满分100分
    实操场门槛: ≥75分（可调）
    
    核心原则：去冗余、加验证、补出场
    """
    # ★ 诊断：收集全部分数用于分析
    _all_scores = []
    
    results = []
    for c in candidates:
        total_score = (
            c.get("trend_score", 0) +
            c.get("sector_score", 0) +
            c.get("money_score", 0) +
            c.get("resonance_score", 0) +
            c.get("catalyst_score", 0)
        )
        _all_scores.append(total_score)

        # 门槛筛选
        if total_score < min_score:
            continue

        last_record = c["records"][-1]
        last_close = float(last_record["close"])
        last_pct_chg = float(last_record.get("pct_chg", 0))

        # ===== 审计轨迹数据（趋势突破策略）=====
        # 预提取：从内部字典中取值，转换为用户可读描述
        _trend_detail = c.get("trend_detail", {})
        _tv_cnt = c.get("three_views_passed_count", 0)
        _dev = c.get("deviation", 0)
        _vr = c.get("vol_ratio", 0)
        _pc = c.get("price_change_pct", 0)

        # 趋势强度综合分(位+向+形) — 用户可见的评分摘要
        _ts_display = str(_trend_detail.get("trend_strength", c.get("trend_score", "?")))
        # MACD状态文字描述
        _macd_display = str(_trend_detail.get("macd", "未知"))
        # 均线形态描述
        _ma_pattern = str(_trend_detail.get("ma_pattern", "未知"))
        # MA20位置描述(站上/偏离)
        _ma20_desc = "站上MA20" if last_close >= (c.get("ma20") or 0) else f"偏离{abs(_dev):.1f}%"

        # 门禁条件
        _ma20_ok = last_close >= (c.get("ma20") or 0)
        _macd_s_val = 0
        try:
            _macd_raw = str(_trend_detail.get("macd", ""))
            if "零上金叉" in _macd_raw or "多头格局" in _macd_raw: _macd_s_val = 12
            elif "零下金叉" in _macd_raw: _macd_s_val = 9
            elif "弱多头" in _macd_raw: _macd_s_val = 7
            elif "接近金叉" in _macd_raw: _macd_s_val = 3
            else: _macd_s_val = 0
        except Exception:
            pass

        _ms = check_market_environment().get("status", "unknown")
        _fp_ref = {}  # v3.5.2 fix: filter_params不在作用域内，用空dict避免NameError

        match_audit_tb = {
            "strategy": "trend_break",
            "version": "v3.5",
            "market_status": _ms,
            "screen_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "params": {k: v for k, v in (_fp_ref.items() if isinstance(_fp_ref, dict) else {})},
            "gates": [
                {"name": "MA20站稳",     "passed": bool(_ma20_ok),              "actual": f"{_dev:+.2f}%",          "threshold": ">=0%",          "detail": _ma20_desc},
                {"name": "MACD状态",     "passed": _macd_s_val >= 7,           "actual": _macd_display,             "threshold": ">=弱多头(7分)"},
                {"name": "量比检查",     "passed": _vr >= _fp_ref.get("min_vol_ratio", 1.2), "actual": f"{_vr:.2f}",    "threshold": f">={_fp_ref.get('min_vol_ratio', 1.2)}"},
                {"name": "三看共振",     "passed": _tv_cnt >= 2,               "actual": f"{_tv_cnt}/3",           "threshold": ">=2/3"},
                {"name": "涨幅过滤",     "passed": last_pct_chg < 9.5,         "actual": f"{last_pct_chg:.2f}%",   "threshold": "<9.5%(非涨停)"},
                {"name": "板块涨幅",     "passed": c.get("max_board_pct", 0) > 0, "actual": f"{c.get('max_board_pct', 0):.2f}%", "threshold": ">0%", "detail": c.get("max_board_name", "N/A")},
                {"name": "总分门槛",     "passed": True,                       "actual": f"{total_score:.0f}",      "threshold": f">={min_score}"},
            ],
            "scoring": {
                "趋势面(50)": {
                    "score": int(c.get("trend_score", 0)),
                    "cap": 50,
                    "items": [
                        {"label": "MA20位置+方向+HL",
                         "raw": f"{_ts_display}分(位+向+形综合)",
                         "score": c.get("trend_score", 0),
                         "rule": "MA20位置(站上/偏离)+方向(向上/向下)+HL结构综合评分"},
                        {"label": "MACD状态",
                         "raw": _macd_display,
                         "score": _macd_s_val,
                         "rule": "零上金叉=12/零下金叉=9/弱多头=7/接近金叉=3"},
                        {"label": "均线形态",
                         "raw": _ma_pattern,
                         "score": _trend_detail.get("ma_pattern_score", _macd_s_val // 2),
                         "rule": "多头排列加分，空头排列扣分"},
                    ],
                },
                "板块面(15)": {
                    "score": int(c.get("sector_score", 0)),
                    "cap": 15,
                    "items": [
                        {"label": "最强板块",
                         "raw": f"{c.get('max_board_name','?')} ({c.get('max_board_pct',0)}%)",
                         "score": int(c.get("sector_score", 0)),
                         "rule": f"板块涨幅≥{c.get('max_board_pct',0)}%时得分更高"},
                        {"label": "相对超额",
                         "raw": f"{c.get('excess_return', '?')}%{' (降级)' if c.get('downgrade_mode') else ''}",
                         "score": c.get("excess_return", 0),
                         "rule": "相对大盘超额收益(%)，降级模式减半"},
                    ],
                },
                "资金面(10)": {
                    "score": int(c.get("money_score", 0)),
                    "cap": 10,
                    "items": [
                        {"label": "主力净流入",
                         "raw": format_wan(c.get("total_net_in", 0)) + f" · 连续{c.get('inflow_days',0)}天",
                         "score": c.get("inflow_days", 0),
                         "rule": "净流入>0得基础分，连续3天以上加分"},
                        {"label": "大单净买比",
                         "raw": f"{c.get('big_buy_ratio',0)}%",
                         "score": c.get("big_buy_ratio", 0),
                         "rule": "大单买入占比越高越好"},
                        {"label": "流入强度比",
                         "raw": f"{c.get('inflow_ratio',0)}%",
                         "score": c.get("inflow_ratio", 0),
                         "rule": "流入占成交额比例"},
                    ],
                },
                "共振面(15)": {
                    "score": int(c.get("resonance_score", 0)),
                    "cap": 15,
                    "items": [
                        {"label": "三看通过数",
                         "raw": str(_tv_cnt) + "/3",
                         "score": c.get("resonance_score", 0),
                         "rule": "量价/K线/均线三看共振≥2项通过"},
                    ],
                },
                "催化(10)": {
                    "score": int(c.get("catalyst_score", 0)),
                    "cap": 10,
                    "items": [
                        {"label": "催化剂",
                         "raw": ", ".join(c.get("catalyst_details", ["无催化"])),
                         "score": c.get("catalyst_score", 0),
                         "rule": "概念热点/事件驱动等加分项"},
                    ],
                },
                "total": {
                    "sum": total_score,
                    "capped": False,
                    "final": total_score,
                },
            },
            # 核心数据一览（仅保留不在Gates/Scoring中出现的快照值）
            "metrics": {
                "price_change_pct": round(_pc, 2),
                "excess_return": c.get("excess_return"),
                "macd_signal": c.get("macd_signal", "unknown"),
            },
        }

        results.append({
            "ts_code": c["ts_code"],
            "name": c["name"],
            "industry": c.get("industry", ""),
            "price": round(last_close, 2),
            "pct_chg": round(last_pct_chg, 2),
            "circ_mv_yi": round(c["circ_mv"] / 10000, 2),
            "turnover_rate": round(c.get("turnover_rate", 0), 2),
            "ma20": c.get("ma20"),
            "deviation": c.get("deviation"),
            "max_board_pct": c.get("max_board_pct"),
            "max_board_name": c.get("max_board_name"),
            "concepts": c.get("concepts", [])[:5],
            # === 新版5维评分 ===
            "total_net_in": c.get("total_net_in"),
            "inflow_days": c.get("inflow_days"),
            "inflow_ratio": c.get("inflow_ratio"),
            # 评分明细
            "trend_score": c.get("trend_score", 0),
            "trend_detail": c.get("trend_detail", {}),
            "sector_score": c.get("sector_score", 0),
            "excess_return": c.get("excess_return"),       # [NEW] 相对超额
            "money_score": c.get("money_score", 0),
            "big_buy_ratio": c.get("big_buy_ratio"),         # [NEW] 大单净买比
            "resonance_score": c.get("resonance_score", 0),   # [NEW] 三看主维度
            "catalyst_score": c.get("catalyst_score", 0),     # [NEW] 催化加分
            "catalyst_details": c.get("catalyst_details", []),
            "total_score": total_score,
            "downgrade_mode": c.get("downgrade_mode", False),
            # === 审计轨迹 ===
            "match_audit": match_audit_tb,
        })

    # 按总分排序
    results.sort(key=lambda x: x["total_score"], reverse=True)

    # ★ 分数诊断输出（仅非静默模式）
    if _all_scores and len(_all_scores) > 0:
        import math
        _all_sorted = sorted(_all_scores, reverse=True)
        _max_score = _all_sorted[0] if _all_sorted else 0
        _avg = sum(_all_scores) / len(_all_scores)
        buckets = {'>=80':0,'75-79':0,'70-74':0,'60-69':0,'50-59':0,'40-49':0,'30-39':0,'<30':0}
        for s in _all_scores:
            if s >= 80: buckets['>=80'] += 1
            elif s >= 75: buckets['75-79'] += 1
            elif s >= 70: buckets['70-74'] += 1
            elif s >= 60: buckets['60-69'] += 1
            elif s >= 50: buckets['50-59'] += 1
            elif s >= 40: buckets['40-49'] += 1
            elif s >= 30: buckets['30-39'] += 1
            else: buckets['<30'] += 1
        
        total_n = len(_all_scores)
        print(f"\n{'='*55}")
        print(f"  [分数诊断] 共{total_n}只候选股 | 门槛={min_score}")
        print(f"  最高分:{_max_score} | 平均分:{_avg:.1f} | 达标:{len(results)}只")
        bar_len = max(1, round(total_n / 5))
        for k in ['>=80','75-79','70-74','60-69','50-59','40-49','30-39','<30']:
            cnt = buckets[k]
            pct = cnt / max(total_n, 1) * 100
            bar = '*' * round(cnt / bar_len)
            if cnt > 0 and cnt < bar_len: bar = '.' * (bar_len - cnt - 1) + '*' + bar
            marker = ' <-门槛' if k == f'>={min_score}' and cnt > 0 else ''
            print(f"  {k:>6}: {cnt:>5}只 ({pct:5.1f}%) {bar}{marker}")

    return results


# ============================================================
# 主筛选流程
# ============================================================

def _parse_params(params_list, key, default=None):
    """从 params_list 中提取指定 key 的值，自动类型转换"""
    if not params_list:
        return default
    
    for p in params_list:
        if p.get("key") == key:
            value = p.get("value", default)
            # 如果是数字字符串，尝试转换为数字
            if isinstance(value, str):
                try:
                    # 尝试转换为浮点数
                    return float(value)
                except ValueError:
                    # 如果不是数字，返回原值
                    return value
            elif isinstance(value, (int, float)):
                return value
            else:
                return value
    
    return default


def hlStructureLabelCN(val):
    """HL结构状态英文→中文（后端审计数据用）"""
    _m = {
        'rebound_ready':'反弹就绪', 'stabilizing':'初步企稳',
        'weak_uptrend':'弱势反弹', 'strong_uptrend':'强势反弹',
        'downtrend_continues':'继续创新低',
        'uncertain':'不确定', 'insufficient_data':'数据不足', 'error':'计算异常'
    }
    return _m.get(val, val or '—')


def format_wan(val):
    """将金额格式化为万/亿单位"""
    if val is None: return '0'
    abs_v = abs(val)
    if abs_v >= 10000:
        return f"{val/10000:.2f}亿"
    return f"{val/10000:.0f}万" if abs_v >= 1 else f"{val:.0f}"


def _drop_score_rule(pct):
    """返回跌幅对应的评分规则描述"""
    if pct < -50:   return "< -50% = 20分"
    elif pct < -40:  return "[-50%, -40%) = 17分"
    elif pct < -30:  return "[-40%, -30%) = 13分"
    elif pct < -25:  return "[-30%, -25%) = 10分"
    elif pct <= -20: return "[-25%, -20%] = 7分"
    else:           return f"{pct:.1f}% 未达-20%门槛"


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
    # [方案B] 更新默认值为放宽后的合理阈值
    min_circ_mv = _parse_params(params, "min_circ_mv", 50)
    max_circ_mv = _parse_params(params, "max_circ_mv", 300)
    min_turnover = _parse_params(params, "min_turnover", 1.5)          # [B] 原3→1.5
    ma20_deviation_min = _parse_params(params, "ma20_deviation_min", 0)  # [B] 原1→0（站上即算）
    min_vol_ratio = _parse_params(params, "min_vol_ratio", 1.2)       # [B] 原1.5→1.2
    min_price_change = _parse_params(params, "min_price_change", 0.5) # [B] 原2→0.5
    board_threshold = _parse_params(params, "board_threshold", 0.3)   # [B] 原0.5→0.3
    min_inflow_days = _parse_params(params, "min_inflow_days", 1)     # [B] 原2→1

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

    # ★ 风控由动态门槛接管：降市自动降低门槛(50分)，不再硬拦截
    if market["status"] == "下降":
        if not silent:
            _dt = get_dynamic_threshold("下降")
            print(f"  ⚠️ 大盘处于下降趋势 → 动态门槛降至{_dt}分（方案B权重v3.1）")

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

    # [v3.5.3 性能优化] 批量获取近45日全市场日线数据（替代basic_filter中逐只get_daily）
    # 参考: 超跌反弹策略(L2956)已使用此函数，趋势突破策略之前遗漏了
    batch_daily_data = {}
    if not silent:
        print("  → [性能] 批量获取近45日线（消除逐只K线查询）...")
    try:
        batch_daily_data = get_batch_daily_multi(days=45)
        if batch_daily_data:
            if not silent:
                print(f"  → {len(batch_daily_data)} 只股票日线已预加载")
    except Exception as e:
        if not silent:
            print(f"  → 批量日线失败，降级逐只查询: {e}")

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
    after_basic = basic_filter(stock_list, daily_cache, basic_cache, batch_basic_df, batch_daily_df,
                                filter_params=filter_params, batch_daily_multi=batch_daily_data)
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

    # 第3步：趋势面评分（v3.0: 不淘汰，全部打分）
    if not silent:
        print(f"\n[第3步] 趋势面评分（趋势强度13 + MACD12 + 均线形态10 = 满分35）...")
    after_trend = trend_scoring(after_basic, filter_params=filter_params)
    if not silent:
        print(f"  → 已评分 {len(after_trend)} 只")

    # v3.5.3: 概念预加载（一次性构建反向索引，替代1456次逐只concept_detail查询）
    preload_all_concepts()

    # 第4步：板块面+资金面评分（v3.0: 不淘汰）
    if not silent:
        print(f"\n[第4步] 板块面+资金面评分（板块20 + 资金20 = 满分40）...")
    after_sector = sector_money_scoring(after_trend, concept_boards,
                                        filter_params=filter_params,
                                        batch_money_data=batch_money_data,
                                        board_data_date=board_data_date)
    if not silent:
        print(f"  → 已评分 {len(after_sector)} 只")

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
                "message": "基础筛选后无候选股",
            },
            "run_time": round(time.time() - start_time, 1),
            "screen_date": datetime.now().strftime("%Y-%m-%d"),
            "screen_time": datetime.now().strftime("%H:%M"),
        }

    # 第5步：多周期共振评分（v3.0: 三看升级为主维度15分）
    if not silent:
        print(f"\n[第5步] 多周期共振评分（三看检查 = 满分15）...")
    scored_resonance = resonance_scoring(after_sector)

    # 第6步：催化加分（v3.0: 基本面5 + 技术3 + 情绪2 = 满分10）
    if not silent:
        print(f"\n[第6步] 催化加分（基本面 + 技术形态 + 情绪 = 满分10）...")
    
    # 准备涨停数据给catalyst_scoring使用
    _limit_stocks = set()
    try:
        for i in range(5):
            td = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
            try:
                for l in get_limit_list(td):
                    _limit_stocks.add(l["ts_code"])
            except Exception:
                pass
    except Exception:
        pass
    
    scored_catalyst = catalyst_scoring(scored_resonance,
                                       limit_stocks=_limit_stocks,
                                       limit_up_count=get_market_sentiment().get("limit_up_count", 0))

    # 第7步：汇总排序（v3.0: 总分100，动态门槛）
    # 方案A: 根据大盘环境自动调整及格线
    _market_status = market.get("status", "unknown") if market else "unknown"
    _dynamic_threshold = get_dynamic_threshold(_market_status)
    # 允许参数面板手动覆盖（用户明确指定时优先使用）
    min_final_score = filter_params.get("min_final_score", _dynamic_threshold) if filter_params else _dynamic_threshold
    if not silent:
        print(f"  [动态门槛] 大盘状态={_market_status} → 及格线={min_final_score}分（固定75→动态适配）")
        print(f"\n[第7步] 汇总排序（总分100，门槛≥{min_final_score}）...")
    results = final_ranking(scored_catalyst, min_score=min_final_score)
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
                  f"资金:{r['money_score']} 共振:{r.get('resonance_score',0)} 催化:{r.get('catalyst_score',0)}")
            print(f"     板块: {r['max_board_name']}({r['max_board_pct']}%)  "
                  f"流通市值: {r['circ_mv_yi']}亿  "
                  f"主力净流入: {r['total_net_in']}万({r['inflow_days']}天)")
            if r.get("bonus_details"):
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
            # ★ 动态门槛信息
            "dynamic_threshold": {
                "market_status": _market_status,
                "threshold": min_final_score,
                "threshold_label": f"{_market_status}市({min_final_score}分)",
            },
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
        "description": "买入信号：①趋势面(~37分)MA20位置(5)+方向(4)+HL结构(4)+MACD状态(0~12)+均线多头(0~10)；②板块(15分)相对超额收益+概念热度排名；③资金(10分)主力净流入+大单结构+持续性；④共振(15分)三看共振(全过15/二看9/一看5)；⑤催化(10分)业绩预告/回购/涨停/缩量回踩；持股：3-10日；止损：跌破MA20或亏8%",
    },
    "sector_leader": {
        "name": "极简龙头策略",
        "icon": "🔥",
        "suitable": "情绪亢奋期（板块效应明显时）",
        "hold_period": "1-3个交易日",
        "stop_loss": "跌破板块平均涨幅 或 亏损5%",
        "buy_tip": "",
        "description": "极简逻辑：板块Top5 x 个股Top5；二值判断：龙头或观察；条件：换手>=8%+市值50-200亿；核心理念：龙头不评分，只选最强",
    },
    "oversold_bounce": {
        "name": "超跌反弹策略",
        "icon": "🔄",
        "suitable": "大盘下跌末期",
        "hold_period": "5-20个交易日",
        "stop_loss": "跌破近期新低 或 亏损10%",
        "buy_tip": "",
        "description": "买入信号：①趋势(35分)跌幅分级(<-50=20/-50~-40=17/-40~-30=13/-30~-25=10/-25~-20=7)+三重止跌信号(长下影线/放量阳线/MACD金叉，3/2/1)；②板块(15/5分)命中热门概念=15否则=5；③资金(15分)净流入(+10)+连续≥3天(+5)+占比>1%(+5)；④加分(15分)市值区间(10)+业绩预增(+3)+回购>1亿(+2)；⑤技术改善(15分)MA5上穿MA20+显著放量(量比≥2.0)+阳线(>0.5%)，3/2/1；⑥HL结构(-5~10分)低点抬高=+10/企稳=+6/创新低=-5；持股：5-20日；止损：跌破新低或亏10%",
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
            "value": 1.5,   # [B] 原3→1.5
            "min": 0.5, "max": 15, "step": 0.5,   # [B] min从1改为0.5
            "desc": "日换手率下限，放宽覆盖更多标的",
        },
        {
            "key": "ma20_deviation_min",
            "label": "MA20偏离度下限(%)",
            "value": 0,    # [B] 原1→0（站上即算）
            "min": -2, "max": 5, "step": 0.5,     # [B] 允许负值（接近MA20也算）
            "desc": "[评分制] 影响趋势分，非硬性门槛",
        },
        {
            "key": "min_vol_ratio",
            "label": "最小量比",
            "value": 1.2,  # [B] 原1.5→1.2
            "min": 0.8, "max": 3.0, "step": 0.1,   # [B] min从1.0改为0.8
            "desc": "[评分制] 影响展示和排序",
        },
        {
            "key": "min_price_change",
            "label": "最小涨幅(%)",
            "value": 0.5,  # [B] 原2→0.5
            "min": 0, "max": 10, "step": 0.5,
            "desc": "[评分制] 影响排序而非淘汰",
        },
        {
            "key": "min_inflow_days",
            "label": "最小资金流入天数",
            "value": 1,    # [B] 原2→1
            "min": 0, "max": 5, "step": 1,          # [B] min从1改为0（允许0天）
            "desc": "[评分制] 流入天数影响资金分",
        },
    ],
    "sector_leader": [
        {
            "key": "min_board_pct",
            "label": "板块涨幅阈值(%)",
            "value": 2,
            "min": 0.5, "max": 5, "step": 0.5,
            "desc": "板块涨幅达到此值才算热门（默认取Top5板块）",
        },
        {
            "key": "min_turnover",
            "label": "最小换手率(%)",
            "value": 8,
            "min": 3, "max": 20, "step": 1,
            "desc": "换手率需≥此值才说明资金充分换手",
        },
        {
            "key": "min_circ_mv",
            "label": "最小流通市值(亿)",
            "value": 50,
            "min": 20, "max": 200, "step": 10,
            "desc": "流通市值下限，过小流动性差",
        },
        {
            "key": "max_circ_mv",
            "label": "最大流通市值(亿)",
            "value": 200,
            "min": 50, "max": 500, "step": 50,
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
    【极简版板块龙头策略 v4.0】
    核心理念：龙头是二值判断，不评分
    
    选股逻辑：
    1. 取概念板块涨幅 Top5
    2. 每个板块内按涨幅排序取 Top5 个股
    3. 筛选：换手率≥8%、市值50-200亿、排除ST/次新
    
    输出：推荐（板块内最强）/ 观察（跟风强势）
    """
    start_time = time.time()
    if not silent:
        print("=" * 60)
        print(f"  极简龙头策略 v4.0 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("=" * 60)

    # 解析参数（简化版只有3个核心参数）
    p_min_board_pct = _parse_params(params, "min_board_pct", 2)   # 板块最低涨幅
    p_min_turnover = _parse_params(params, "min_turnover", 8)     # 换手率门槛（默认8%）
    p_min_circ_mv = _parse_params(params, "min_circ_mv", 50)      # 市值下限（亿）
    p_max_circ_mv = _parse_params(params, "max_circ_mv", 200)     # 市值上限（亿）

    # 获取大盘环境
    market = check_market_environment()

    # Step 1: 获取概念板块涨幅排行
    if not silent:
        print("  [Step1] 获取概念板块涨幅...")
    concept_boards = get_concept_board_data()
    if not silent:
        print(f"  → 共 {len(concept_boards)} 个概念板块")

    if not concept_boards:
        if not silent:
            print("  → [WARN] 板块数据为空，无法执行选股")
        return _empty_sector_leader_result(market, silent)

    # Step 2: 取 Top5 板块
    top_boards = sorted(concept_boards, key=lambda x: x.get("change_pct", 0), reverse=True)[:5]
    if not silent:
        print(f"  [Step2] Top5 板块:")
        for i, b in enumerate(top_boards):
            print(f"    #{i+1} {b.get('concept_name','?')} 涨幅 {b.get('change_pct',0):.2f}%")

    # Step 3: 批量获取全市场行情数据
    if not silent:
        print("  [Step3] 批量获取全市场行情...")
    batch_basic = get_batch_daily_basic()
    batch_daily = get_batch_daily()
    
    if batch_basic is None or batch_basic.empty or batch_daily is None or batch_daily.empty:
        if not silent:
            print("  → [WARN] Tushare 数据为空")
        return _empty_sector_leader_result(market, silent)

    # 合并数据
    daily_df = batch_daily[["ts_code", "pct_chg", "close"]].rename(columns={"close": "price"})
    merged = batch_basic.merge(daily_df, on="ts_code", how="inner")

    # 预过滤：换手率 + 市值
    filtered = merged[
        (merged["turnover_rate"] >= p_min_turnover) &
        (merged["circ_mv"] >= p_min_circ_mv * 10000) &
        (merged["circ_mv"] <= p_max_circ_mv * 10000)
    ]

    # 获取股票基础信息（排除ST/次新）
    stock_list = get_stock_list()
    stock_info_map = {s["ts_code"]: s for s in stock_list}
    today = datetime.now()
    list_date_threshold = (today - timedelta(days=60)).strftime("%Y%m%d")

    # Step 4: 对每个Top板块，找出其成分股中符合条件的股票
    results = []  # 最终推荐结果
    
    for board in top_boards:
        board_code = board.get("concept_code", "")
        board_name = board.get("concept_name", "?")
        board_pct = board.get("change_pct", 0)

        if not silent:
            print(f"\n  [Step4] 处理板块: {board_name} ({board_pct:.2f}%)")

        # 获取该板块的成分股
        board_stocks = _get_board_stocks_fast(board_code, board_name, filtered, stock_info_map, 
                                                list_date_threshold, silent)

        if not board_stocks:
            if not silent:
                print(f"    → 该板块无符合条件个股")
            continue

        # 按涨幅降序，取Top5
        board_stocks.sort(key=lambda x: x["pct_chg"], reverse=True)
        top5 = board_stocks[:5]

        if not silent:
            print(f"    -> 符合条件 {len(board_stocks)} 只，取Top5:")
            for i, s in enumerate(top5):
                tag = "[LEADER]" if i == 0 else f"#{i+1}"
                print(f"      {tag} {s['name']}({s['ts_code']}) 涨幅{s['pct_chg']:.2f}% 换手{s['turnover_rate']:.1f}%")

        for rank, stock in enumerate(top5):
            # 推荐理由
            if rank == 0:
                recommendation = "龙头"
                reason = f"板块内涨幅最高({stock['pct_chg']:.2f}%)，板块今日强势({board_pct:.2f}%)"
            else:
                recommendation = "观察"
                reason = f"跟风强势，板块({board_pct:.2f}%)带动"

            results.append({
                "ts_code": stock["ts_code"],
                "name": stock["name"],
                "industry": stock.get("industry", ""),
                "price": round(stock.get("price", 0), 2),
                "pct_chg": round(stock["pct_chg"], 2),
                "turnover_rate": round(stock.get("turnover_rate", 0), 2),
                "circ_mv_yi": round(stock.get("circ_mv", 0) / 10000, 2),
                "max_board_pct": round(board_pct, 2),
                "max_board_name": board_name,
                "board_rank": rank + 1,  # 在板块内排名
                "recommendation": recommendation,
                "reason": reason,
                # === 审计轨迹（简化版）===
                "match_audit": {
                    "strategy": "sector_leader_simple",
                    "version": "v4.0",
                    "market_status": market.get("status", "unknown"),
                    "screen_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "gates": [
                        {"name": "板块涨幅", "actual": f"{board_pct:.2f}%", "threshold": f">={p_min_board_pct}%", "passed": board_pct >= p_min_board_pct, "detail": board_name},
                        {"name": "换手率", "actual": f"{stock['turnover_rate']:.2f}%", "threshold": f">={p_min_turnover}%", "passed": stock.get("turnover_rate", 0) >= p_min_turnover},
                        {"name": "市值", "actual": f"{stock.get('circ_mv', 0)/10000:.2f}亿", "threshold": f"[{p_min_circ_mv},{p_max_circ_mv}]亿", "passed": p_min_circ_mv <= stock.get('circ_mv', 0)/10000 <= p_max_circ_mv},
                    ],
                    "scoring": {},
                    # 核心数据一览（仅保留不在Gates中出现的快照值）
                    "metrics": {
                        "pct_chg": round(stock["pct_chg"], 2),
                        "board_rank": rank + 1,
                    },
                },
            })

    # 按板块排名和涨幅综合排序
    results.sort(key=lambda x: (x["board_rank"], -x["pct_chg"]))
    top_results = results[:top_n]
    
    run_time = round(time.time() - start_time, 1)

    if not silent:
        print(f"\n{'='*60}")
        print(f"  极简龙头筛选完成！共 {len(results)} 只，Top {len(top_results)}：")
        for i, r in enumerate(top_results):
            print(f"  [#{i+1}] {r['recommendation']} {r['name']}({r['ts_code']}) | 板块:{r['max_board_name']}({r['max_board_pct']:.1f}%) | 涨幅:{r['pct_chg']:.2f}%")
        print(f"  耗时 {run_time}s")

    return {
        "market": market,
        "results": top_results,
        "all_results": results,
        "stats": {
            "total_boards": len(concept_boards),
            "top_boards_count": len(top_boards),
            "final_count": len(results),
            "message": f"极简龙头：{len(results)}只（Top5板块×Top5个股）" if results else "今日无符合条件的龙头标的",
        },
        "run_time": run_time,
        "screen_date": datetime.now().strftime("%Y-%m-%d"),
        "screen_time": datetime.now().strftime("%H:%M"),
        "strategy": "sector_leader",
        "strategy_meta": STRATEGY_META["sector_leader"],
    }


def _get_board_stocks_fast(board_code, board_name, market_df, stock_info_map, list_date_threshold, silent):
    """
    快速获取板块成分股中符合条件的股票（简化版）
    使用 Tushare ths_member API 获取板块成分股
    """
    stocks = []
    
    try:
        # 获取该板块的成分股（使用ths_member）
        df_member = pro.ths_member(ts_code=board_code, fields="ts_code,con_code,con_name")
        if df_member is None or df_member.empty:
            if not silent:
                print(f"    → [WARN] 板块 {board_name} 无成分股数据")
            return stocks
        
        # 获取成分股代码集合
        member_codes = set()
        for _, row in df_member.iterrows():
            # ths_member返回的con_code是标准格式（如000001.SZ）
            code = str(row.get("con_code", "")).strip()
            if code and "." in code:
                member_codes.add(code)
        
        if not silent:
            print(f"    → 板块 {board_name} 共有 {len(member_codes)} 只成分股")
        
        # 在市场数据中筛选属于该板块的股票
        for _, row in market_df.iterrows():
            ts_code = row["ts_code"]
            if ts_code not in member_codes:
                continue
                
            info = stock_info_map.get(ts_code, {})
            name = info.get("name", "")
            
            # 排除ST
            if not name or "ST" in name or name.startswith("*"):
                continue
            # 排除次新
            list_date = info.get("list_date", "")
            if list_date and list_date > list_date_threshold:
                continue
            
            stocks.append({
                "ts_code": ts_code,
                "name": name,
                "industry": info.get("industry", ""),
                "price": float(row.get("price", 0)),
                "pct_chg": float(row.get("pct_chg", 0)),
                "turnover_rate": float(row.get("turnover_rate", 0)),
                "circ_mv": float(row.get("circ_mv", 0)),
            })
    
    except Exception as e:
        if not silent:
            print(f"    → [WARN] 获取板块 {board_name} 成分股失败: {e}")
    
    return stocks


def _empty_sector_leader_result(market, silent):
    """返回空结果的统一格式"""
    return {
        "market": market,
        "results": [],
        "all_results": [],
        "stats": {
            "total_boards": 0,
            "top_boards_count": 0,
            "final_count": 0,
            "message": "无符合条件的龙头标的",
        },
        "run_time": 0,
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

    # 【MN-1修复 v3.3.1】将循环内import提升到函数顶部（避免每只股票重复import）
    try:
        from helpers import get_forecast, get_repurchase
    except ImportError:
        get_forecast, get_repurchase = None, None

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

        # 排除ST / *ST（【MN-2修复】更精确的ST检测，避免误伤含"st"的英文名如"best"）
        name_upper = name.upper()
        if "ST" in name_upper or name.startswith("*"):
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

            # 信号A：长下影线（【v3.3修复】使用标准K线公式）
            # 标准: 下影线 = (min(收盘,开盘) - 最低) / 最低 × 100%
            # 旧版: (close - low) / close × 100% （阴线时低估下影线长度）
            body_low = min(close_r, open_r)
            lower_shadow = (body_low - low) / low * 100 if low > 0 else 0
            body_range = (high - low) / low * 100 if low > 0 else 100
            if lower_shadow > p_lower_shadow_pct and body_range > p_body_range_pct:
                stop_signals.append("长下影线")

            # 信号B：放量阳线
            pct = float(r.get("pct_chg", 0))
            vol_ma5 = calc_ma(volumes[:r_idx + 1], 5)
            vol_r = float(r.get("vol", 0))
            if pct > 1 and vol_ma5 and vol_r > vol_ma5 * p_vol_ratio_threshold:
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

        # 信号D：MA5上穿MA20（金叉）— 短期趋势由空转多（【v3.3修复】只保留金叉信号，去掉宽松的"在上方"判断）
        ma5_series = calc_ma_series(closes, 5)
        ma20_series = calc_ma_series(closes, 20)
        if ma5_series and ma20_series and ma5_series[-1] is not None and ma20_series[-1] is not None:
            # 排除None值，取最近两个有效值
            valid_ma5 = [v for v in ma5_series[-3:] if v is not None]
            valid_ma20 = [v for v in ma20_series[-3:] if v is not None]
            if len(valid_ma5) >= 2 and len(valid_ma20) >= 2:
                ma5_yesterday, ma5_today = valid_ma5[-2], valid_ma5[-1]
                ma20_yesterday, ma20_today = valid_ma20[-2], valid_ma20[-1]
                # 【P1-#4】仅保留金叉：昨日MA5<=MA20，今日MA5>MA20
                # 去掉 "elif MA5在MA20上方" 的宽松判定——超跌反弹要找拐点而非已涨很久的票
                if ma5_yesterday <= ma20_yesterday and ma5_today > ma20_today:
                    tech_improve_signals.append("MA5上穿MA20")

        # 信号E：显著量比放大（【P1修复】阈值动态计算，保持比止跌信号B更高门槛）
        # 设计意图：止跌=温和放量即可(用p_vol_ratio_threshold)，技术改善=需更显著的放量
        # 公式：max(用户阈值*1.5, 2.0) → 确保始终高于止跌信号，同时随参数联动
        _tech_vol_thr = max(p_vol_ratio_threshold * 1.5, 2.0)
        if len(volumes) >= 5:
            vol_ma5_all = calc_ma(volumes, 5)
            if vol_ma5_all and vol_ma5_all > 0:
                vol_ratio_now = volumes[-1] / vol_ma5_all
                if vol_ratio_now >= _tech_vol_thr:
                    tech_improve_signals.append("显著放量")

        # 信号F：出现阳线（【v3.3修复】涨幅>0.5%才算有效改善，排除微涨噪音）
        if last_pct_chg > 0.5:
            tech_improve_signals.append("今日阳线")

        # 技术面改善信号统计（去重）
        tech_confirm_count = len(set(tech_improve_signals))

        # 如果设置了技术面改善最低要求，不满足则不通过
        if p_tech_confirm_min > 0 and tech_confirm_count < p_tech_confirm_min:
            continue

        if not stop_signals:
            continue

        # 核心条件4：所属行业有政策利好（属于热门概念）
        # 【P1-#6 v3.3修复】动态概念匹配：优先用当日概念板块Top15，降级到HOT_CONCEPTS硬编码
        concepts = get_stock_concepts_eastmoney(ts_code)
        
        # 动态获取当日热门概念名称（涨幅Top15）
        dynamic_hot_concepts = []
        if concept_boards:
            sorted_boards = sorted(concept_boards, key=lambda x: abs(x.get("change_pct", 0)), reverse=True)
            dynamic_hot_concepts = [b["concept_name"] for b in sorted_boards[:15]]
        
        # 匹配逻辑：先尝试动态概念，再降级硬编码
        all_hot = set(dynamic_hot_concepts) if dynamic_hot_concepts else set(HOT_CONCEPTS)
        has_hot = any(
            any(hot in concept or concept in hot for hot in all_hot)
            for concept in concepts
        )
        # 匹配结果静默处理：非模式下可在此添加日志
        # if not silent and has_hot and dynamic_hot_concepts:
        #     matched = [h for h in all_hot if any(h in c or c in h for c in concepts)]

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

        # 【CR-1修复 v3.3.1】bonus_details必须在每只股票的评分循环内初始化
        # 原bug: bonus_details只在sector_leader(L2541)中初始化，超跌反弹策略未定义→NameError
        bonus_details = []

        # ============================================================
        # 【Bug修复 v3.2】维度1：趋势强度（跌幅+止跌信号，0-35分，硬上限）
        # 评分规则：跌幅越深反弹空间越大，止跌信号越多企稳概率越高
        # ============================================================
        # 跌幅评分（0-20分）：覆盖全部区间（<-50 / -50~-40 / -40~-30 / -30~-25 / -25~-20）
        if pct_20d < -50:                          # ← 【修复A1】补充最深跌幅
            drop_score = 20
        elif -50 <= pct_20d < -40:
            drop_score = 17
        elif -40 <= pct_20d < -30:
            drop_score = 13
        elif -30 <= pct_20d < -25:
            drop_score = 10
        elif -25 <= pct_20d <= -20:
            drop_score = 7                          # ← 【修复A2】-20%阈值股给7分（合理）
        else:                                        # -20 < pct_20d < 0（未达门槛）已在过滤阶段排除
            drop_score = 0

        # 止跌信号评分（0-15分）：多信号共振加分
        signal_count = len(set(stop_signals))
        if signal_count >= 3:
            signal_score = 15
            bonus_details.append(f"三重止跌({','.join(stop_signals[:3])})")
        elif signal_count == 2:
            signal_score = 12
            bonus_details.append(f"双止跌({','.join(stop_signals)})")
        else:
            signal_score = 8
            bonus_details.append(f"单止跌({stop_signals[0]})")

        trend_score = min(drop_score + signal_score, 35)  # ← 【修复B】硬上限35分

        # 维度2：板块效应（政策概念，0-15分）
        sector_score = 15 if has_hot else 5

        # 维度3：资金面（0-15分）
        money_score = 0
        if total_net_in > 0:
            money_score += 10
            bonus_details.append(f"资金净流入{inflow_days}天+10")
        # 【P1-#5 v3.3修复】新增：持续净流入天数加分
        # 单日流入可能是偶然，持续多日才是真主力行为
        if inflow_days >= 3:
            money_score += 5
            bonus_details.append(f"连续{inflow_days}日流入+5")
        if inflow_ratio > 1:
            money_score += 5  # 占比加分保留，但会被min(15)截断
            bonus_details.append("资金占比高+5")
        money_score = min(money_score, 15)  # 硬上限15分

        # 维度4：加分项（市值+业绩预告+回购，0-15分）
        bonus_score = 0
        # 【P0修复】使用用户配置的参数替代硬编码100/300/500
        _bonus_mv_min = p_min_circ_mv * 10000
        _bonus_mv_mid = p_max_circ_mv * 10000
        if _bonus_mv_min <= circ_mv <= _bonus_mv_mid:
            bonus_score += 10
            bonus_details.append(f"市值区间佳({p_min_circ_mv}-{p_max_circ_mv}亿)+10")
        elif circ_mv <= max(_bonus_mv_mid * 2, 500 * 10000):
            bonus_score += 5
        else:
            bonus_score += 2

        # P1: 业绩预告+回购加分（import已在函数顶部完成，避免循环内重复import）
        try:
            if get_forecast:
                fc_list = get_forecast(ts_code)
                if fc_list:
                    fc_type = str(fc_list[0].get("type", ""))
                    if fc_type in ("预增", "扭亏", "略增"):
                        bonus_score += 3
                        bonus_details.append(f"业绩预告({fc_type})+3")
            if get_repurchase:
                rp_list = get_repurchase(ts_code)
                if rp_list:
                    rp_amount = rp_list[0].get("amount", 0)
                    if rp_amount and float(rp_amount) > 10000:
                        bonus_score += 2
                        bonus_details.append("大额回购+2")
        except Exception as e:
            # 【MN-4修复 v3.3.1】不再静默吞异常，记录日志便于排查
            if not silent:
                print(f"  ⚠️ 业绩预告/回购数据获取失败({ts_code}): {e}")

        bonus_score = min(bonus_score, 15)  # ← 【修复B】硬上限15分

        # 维度5：技术面改善（MA金叉/放量/阳线，0-15分）
        tech_score = 0
        if tech_improve_signals:
            tech_signal_count = len(set(tech_improve_signals))
            if tech_signal_count == 3:
                tech_score = 15
                bonus_details.append("三线改善(MA金叉+放量+阳线)")
            elif tech_signal_count == 2:
                tech_score = 10
                bonus_details.append("双线改善(" + '+'.join(set(tech_improve_signals)) + ")")
            else:
                tech_score = 6
                bonus_details.append("单线改善(" + tech_improve_signals[0] + ")")
        else:
            bonus_details.append("无技术面改善")
        tech_score = min(tech_score, 15)  # ← 【修复B】硬上限15分

        # 维度6：HL结构（0-10分，特殊规则：LL<-5分）
        hl_score = 0
        hl_structure = "unknown"
        try:
            highs = [float(r["high"]) for r in records]
            lows = [float(r["low"]) for r in records]
            # 【P2-#7 v3.3修复】窗口从n=5改为n=3，超跌反弹是短线策略，需要更灵敏的拐点检测
            n = 3
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

            if len(local_highs) >= 3 and len(local_lows) >= 3:
                recent_highs = local_highs[-3:]
                recent_lows = local_lows[-3:]

                hh = sum(1 for i in range(1, len(recent_highs)) if recent_highs[i][1] > recent_highs[i-1][1])
                hl = sum(1 for i in range(1, len(recent_lows)) if recent_lows[i][1] > recent_lows[i-1][1])
                ll = sum(1 for i in range(1, len(recent_lows)) if recent_lows[i][1] < recent_lows[i-1][1])

                # 超跌反弹核心逻辑：低点抬高（HL）= 反弹信号；高点抬高（HH）= 趋势确认
                if hl >= 2:
                    hl_score = 10
                    hl_structure = "rebound_ready"
                    bonus_details.append("HL结构:低点抬高≥2(反弹信号)")
                elif hh >= 1 and hl >= 1:
                    hl_score = 6
                    hl_structure = "stabilizing"
                    bonus_details.append("HL结构:初步企稳")
                elif ll >= 2:
                    hl_score = -5          # ← 仍在创新低，不适合抄底
                    hl_structure = "downtrend_continues"
                    bonus_details.append("HL结构:继续创新低(⚠️风险)")
                else:
                    hl_score = 3
                    hl_structure = "uncertain"
            else:
                hl_score = 3
                hl_structure = "insufficient_data"
        except Exception as e:
            # 【MN-5修复 v3.3.1】HL计算异常时记录原因
            hl_score = 3
            hl_structure = "error"
            if not silent:
                print(f"  ⚠️ HL结构计算异常({ts_code}): {e}")

        # 总分：各维度均有硬上限 → 总分理论上限=35+15+15+15+15+10=105
        # 【MN-6修复】但实际被 min(total,100) 截断，所以真正上限是100
        total_score = trend_score + sector_score + money_score + bonus_score + tech_score + hl_score
        total_score = min(max(total_score, 0), 100)  # ← 【修复B】总分硬上限100，不低于0

        # 【新增 v3.3.1】筛选审计追踪：完整记录每只股票的匹配过程
        # 目的：人工核查规则正确性 + 后续迭代优化依据
        
        # --- 审计数据所需的变量预计算（避免作用域问题） ---
        # 业绩预告相关
        _fc_type_str = "无"
        _fc_score_val = 0
        if 'fc_list' in dir() and fc_list and len(fc_list) > 0:
            _fc_type_str = str(fc_list[0].get("type", ""))
            if _fc_type_str in ("预增", "扭亏", "略增"):
                _fc_score_val = 3
        # 回购相关
        _rp_val_str = "无"
        _rp_score_val = 0
        if 'rp_list' in dir() and rp_list and len(rp_list) > 0:
            _rp_amt = rp_list[0].get("amount", 0)
            if _rp_amt:
                try:
                    _rp_val_str = f"{float(_rp_amt):.0f}万"
                    if float(_rp_amt) > 10000:
                        _rp_score_val = 2
                except (ValueError, TypeError):
                    pass
        # 技术改善子项得分
        _vol_ratio_now_val = vol_ratio_now if 'vol_ratio_now' in dir() else (volumes[-1]/calc_ma(volumes,5) if len(volumes)>=5 and calc_ma(volumes,5) and calc_ma(volumes,5)>0 else 0)
        _vol_score_val = 5 if isinstance(_vol_ratio_now_val,(int,float)) and _vol_ratio_now_val >= 2.0 else 0
        _yang_score_val = 5 if last_pct_chg > 0.5 else 0
        
        # --- 筛选门禁（gates）：记录每个过滤条件的通过情况 ---
        audit_gates = [
            {"name": "ST排除", "passed": not ("ST" in name_upper or name.startswith("*")), 
             "detail": f"{'非ST' if 'ST' not in name_upper else '⚠含ST'}"},
            {"name": "上市天数", "passed": True if not list_date or list_date <= list_date_threshold else False,
             "detail": f"{list_date or '?'}上市" + (f"({(today - datetime.strptime(list_date,'%Y%m%d')).days}天>90天)" if list_date and len(list_date)==8 else "")},
            {"name": "数据量", "passed": len(records) >= 25,
             "detail": f"{len(records)}条日线>=25条✓" if len(records)>=25 else f"{len(records)}条<25条✗"},
            {"name": "近20日跌幅", "passed": pct_20d <= p_min_drop_pct,
             "actual": f"{pct_20d:.2f}%", "threshold": f"<={p_min_drop_pct}%",
             "detail": f"{pct_20d:.2f}% {'✓' if pct_20d<=p_min_drop_pct else '✗'} < {p_min_drop_pct}%"},
            {"name": "流通市值", "passed": p_min_circ_mv*10000 <= circ_mv <= p_max_circ_mv*10000,
             "actual": f"{circ_mv_yi:.2f}亿", "threshold": f"{p_min_circ_mv}~{p_max_circ_mv}亿",
             "detail": f"{circ_mv_yi:.2f}亿 ∈ [{p_min_circ_mv},{p_max_circ_mv}] ✓"},
            {"name": "止跌信号", "passed": len(stop_signals) > 0,
             "count": signal_count, "signals": sorted(set(stop_signals)),
             "detail": f"{signal_count}个: {','.join(sorted(set(stop_signals)))}" if stop_signals else "无信号✗"},
            {"name": "技术改善最低", "passed": tech_confirm_count >= p_tech_confirm_min,
             "actual": tech_confirm_count, "threshold": f">={p_tech_confirm_min}",
             "detail": f"{tech_confirm_count}/{p_tech_confirm_min} ✓" if tech_confirm_count>=p_tech_confirm_min else f"{tech_confirm_count}/{p_tech_confirm_min} ✗"},
        ]

        # --- 评分明细（scoring）：记录每维度的原始值、得分、规则 ---
        audit_scoring = {
            "①趋势(35)": {
                "score": trend_score, "cap": 35,
                "items": [
                    {"label": "跌幅分级", "raw": f"{round(pct_20d,1)}%", "score": drop_score,
                     "rule": _drop_score_rule(pct_20d)},
                    {"label": "止跌信号", "raw": f"{signal_count}个({','.join(sorted(set(stop_signals)))})", "score": signal_score,
                     "rule": f"三重=15/双=12/单=8"},
                ]
            },
            "②板块(15)": {
                "score": sector_score,
                "items": [
                    {"label": "概念匹配", "raw": f"{', '.join(concepts[:5] if concepts else [])}", "score": sector_score,
                     "rule": f"动态Top15={sector_score}分" if dynamic_hot_concepts else f"硬编码概念={sector_score}分",
                     "matched_concepts": [h for h in all_hot if any(h in c or c in h for c in concepts)]},
                ]
            },
            "③资金(15)": {
                "score": money_score, "cap": 15,
                "items": [
                    {"label": "净流入", "raw": f"{'+'+format_wan(total_net_in) if total_net_in>0 else format_wan(total_net_in)} ({inflow_days}天)", 
                     "score": min((10 if total_net_in>0 else 0)+(5 if inflow_days>=3 else 0),15),
                     "rule": "流入>0(+10) + 连续>=3天(+5) + 占比>1%(+5)"},
                    {"label": "占比", "raw": f"{round(inflow_ratio,3)}%", "score": min(inflow_ratio>1,5)*5 if inflow_ratio>1 else 0,
                     "rule": f"流入比>流通市值的1%"},
                ]
            },
            "④加分项(15)": {
                "score": bonus_score, "cap": 15,
                "items": [
                    {"label": "市值区间", "raw": f"{round(circ_mv_yi,1)}亿",
                     "score": 10 if 100<=circ_mv_yi<=300 else (5 if circ_mv_yi<=500 else 2),
                     "rule": "100~300=10 / <=500=5 / >500=2"},
                    {"label": "业绩预告", "raw": _fc_type_str,
                     "score": _fc_score_val,
                     "rule": "预增/扭亏/略增=+3"},
                    {"label": "回购", "raw": _rp_val_str,
                     "score": _rp_score_val,
                     "rule": "金额>1亿=+2"},
                ]
            },
            "⑤技术改善(15)": {
                "score": tech_score, "cap": 15,
                "items": [
                    {"label": "MA金叉", "raw": "是" if "MA5上穿MA20" in tech_improve_signals else "否",
                     "score": 10 if "MA5上穿MA20" in tech_improve_signals else 0,
                     "rule": "MA5上穿MA20 = 10分"},
                    {"label": "显著放量", "raw": f"{_vol_ratio_now_val:.2f}倍",
                     "score": _vol_score_val,
                     "rule": "量比>=2.0 = 5分"},
                    {"label": "阳线", "raw": f"+{round(last_pct_chg,2)}%",
                     "score": _yang_score_val,
                     "rule": "涨幅>0.5%=5分"},
                ]
            },
            "⑥HL结构": {
                "score": hl_score,
                "items": [
                    {"label": "结构状态", "raw": hlStructureLabelCN(hl_structure),
                     "score": hl_score,
                     "rule": "反弹=+10 / 企稳=+6 / 创新低=-5 / 不确定=+3"},
                ]
            },
            "total": {
                "sum": trend_score+sector_score+money_score+bonus_score+tech_score+hl_score,
                "final": total_score,
                "capped": (trend_score+sector_score+money_score+bonus_score+tech_score+hl_score)!=total_score,
            }
        }

        # --- 关键指标快照 ---
        # 核心数据一览（仅保留不在Gates/Scoring中出现的快照值）
        audit_metrics = {
            "pct_20d": round(pct_20d, 2),
        }

        match_audit = {
            "strategy": "oversold_bounce",
            "version": "v3.5",
            "market_status": market.get("status", "unknown") if isinstance(market, dict) else "unknown",
            "screen_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "params": {p["key"]: p["value"] for p in params} if params else None,
            "gates": audit_gates,
            "scoring": audit_scoring,
            "metrics": audit_metrics,
        }

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
            "match_audit": match_audit,  # 【v3.3.1】筛选审计追踪数据
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
            "after_trend": total_checked,  # 【MJ-1修复】原表达式=total_checked(恒等式)，改为有意义的值
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
