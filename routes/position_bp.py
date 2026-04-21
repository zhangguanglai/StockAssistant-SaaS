# -*- coding: utf-8 -*-
"""
routes/position_bp.py - 持仓、资金、交易、行情、复盘、回测、预警相关路由
这是业务量最大的模块，包含核心的交易管理功能

v3.1.1: 修复兼容层写入不删除的问题，改用 database.py 原生 CRUD
"""

from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request

from auth import login_required, get_current_user_id
from database import (
    load_portfolio_compat,  # 仅用于读取
    get_capital, save_capital,
    get_all_positions, get_position, get_position_by_code,
    create_position, update_position as db_update_position, delete_position as db_delete_position,
    get_trades, add_trade as db_add_trade, delete_trade as db_delete_trade,
    get_all_trades, get_trade_stats,
)
from helpers import (
    pro, is_trade_time, should_use_realtime_source, enrich_positions, calc_position_meta,
    get_stock_info, get_tushare_daily,
    get_realtime_quote, get_index_quotes, INDEX_CODES,
    load_stock_list, get_adj_factor, adjust_kline_by_adj_factor,
)

position_bp = Blueprint("position", __name__)


# ============================================================
# 辅助函数
# ============================================================

def _build_position_list(user_id):
    """从 SQLite 构建兼容 enrich_positions 的持仓列表"""
    positions = get_all_positions(user_id)
    pos_list = []
    for p in positions:
        trades = get_trades(p["id"])
        buy_trades = [t for t in trades if t["trade_type"] == "buy"]
        sell_trades = [t for t in trades if t["trade_type"] == "sell"]

        total_buy_cost = sum(float(t["buy_price"]) * int(t["buy_volume"]) for t in buy_trades)
        total_buy_vol = sum(int(t["buy_volume"]) for t in buy_trades)
        total_sell_vol = sum(int(t["sell_volume"]) for t in sell_trades)
        hold_vol = total_buy_vol - total_sell_vol
        avg_cost = round(total_buy_cost / total_buy_vol, 4) if total_buy_vol > 0 else 0

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
                    "trade_id": t["id"],
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
                }
                for t in trades
            ],
            "avg_cost": avg_cost,
            "total_volume": hold_vol,
        })
    return pos_list


# ============================================================
# 大盘指数 API
# ============================================================

@position_bp.route("/api/index")
def get_index_data():
    """获取大盘指数行情（新浪 → Tushare 降级）+ 高低点结构分析"""
    from helpers import cache_get, cache_set, get_hl_structure
    cache_key = "api_index"
    cached = cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)

    if should_use_realtime_source():
        quotes = get_index_quotes()
        # 实时源都失败时降级到 Tushare
        if not quotes:
            quotes = _get_index_quotes_tushare()
            for v in quotes.values():
                v["_source"] = "tushare"
    else:
        quotes = _get_index_quotes_tushare()
        for v in quotes.values():
            v["_source"] = "tushare"

    # 添加高低点结构分析（上证指数和沪深300）
    for ts_code in quotes:
        try:
            hl = get_hl_structure(ts_code, n=5, lookback=60)
            quotes[ts_code]["hl_structure"] = {
                "structure": hl.get("structure"),
                "score": hl.get("score"),
                "signal": hl.get("signal"),
                "high_trend": hl.get("high_trend"),
                "low_trend": hl.get("low_trend"),
            }
        except Exception as e:
            print(f"[WARN] 大盘高低点分析失败({ts_code}): {e}")
            quotes[ts_code]["hl_structure"] = None

    ttl = 15 if is_trade_time() else 300
    cache_set(cache_key, quotes, ttl)
    return jsonify(quotes)


@position_bp.route("/api/index/hl-structure")
def get_index_hl_structure():
    """获取大盘指数高低点结构详细分析（支持参数调整）"""
    from helpers import get_hl_structure
    
    # 支持参数：n（窗口大小，默认5），lookback（回溯天数，默认60）
    n = request.args.get("n", 5, type=int)
    lookback = request.args.get("lookback", 60, type=int)
    
    result = {}
    for ts_code in INDEX_CODES:
        try:
            hl = get_hl_structure(ts_code, n=n, lookback=lookback)
            result[ts_code] = {
                "name": INDEX_CODES[ts_code]["name"],
                "structure": hl.get("structure"),
                "score": hl.get("score"),
                "signal": hl.get("signal"),
                "recent_highs": hl.get("recent_highs"),
                "recent_lows": hl.get("recent_lows"),
                "high_trend": hl.get("high_trend"),
                "low_trend": hl.get("low_trend"),
                "hh_count": hl.get("hh_count"),
                "hl_count": hl.get("hl_count"),
                "lh_count": hl.get("lh_count"),
                "ll_count": hl.get("ll_count"),
                "analysis_date": hl.get("analysis_date"),
            }
        except Exception as e:
            result[ts_code] = {"error": str(e)}
    
    return jsonify(result)


def _get_index_quotes_tushare():
    """从 Tushare 获取指数行情（降级方案），返回 dict"""
    quotes = {}
    for ts_code in INDEX_CODES:
        try:
            end_date = datetime.now().strftime("%Y%m%d")
            df = pro.index_daily(ts_code=ts_code, end_date=end_date, limit=2)
            if not df.empty:
                row = df.iloc[0]
                pre_close = float(df.iloc[1]["close"]) if len(df) >= 2 else float(row.get("pre_close", 0))
                quotes[ts_code] = {
                    "name": INDEX_CODES[ts_code]["name"],
                    "price": float(row["close"]),
                    "pct_chg": float(row["pct_chg"]),
                    "change": float(row["change"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "open": float(row["open"]),
                    "pre_close": pre_close,
                    "vol": float(row["vol"]),
                    "amount": float(row["amount"]),
                }
        except Exception as e:
            print(f"[ERROR] 获取指数{ts_code}失败: {e}")
    return quotes


# ============================================================
# 资金账本 API
# ============================================================

@position_bp.route("/api/capital", methods=["GET"])
@login_required
def get_capital_api():
    """获取资金信息"""
    user_id = get_current_user_id()
    capital = get_capital(user_id)
    trade_count = get_trade_stats(user_id)
    return jsonify({
        "capital": capital,
        "trade_count": trade_count.get("total", 0),
    })


@position_bp.route("/api/capital", methods=["PUT"])
@login_required
def update_capital():
    """更新资金"""
    user_id = get_current_user_id()
    body = request.get_json()
    capital = get_capital(user_id)
    initial = float(body["initial"]) if "initial" in body else capital.get("initial", 0)
    cash = float(body["cash"]) if "cash" in body else capital.get("cash", 0)
    save_capital(initial, cash, user_id)
    return jsonify({"message": "资金已更新", "capital": {"initial": initial, "cash": cash}})


@position_bp.route("/api/capital/reset", methods=["POST"])
@login_required
def reset_capital():
    """重置资金"""
    user_id = get_current_user_id()
    save_capital(0, 0, user_id)
    return jsonify({"message": "资金已重置"})


# ============================================================
# 持仓管理 API
# ============================================================

@position_bp.route("/api/positions", methods=["GET"])
@login_required
def get_positions():
    """获取所有持仓（含实时行情）"""
    user_id = get_current_user_id()
    positions = _build_position_list(user_id)
    enriched = enrich_positions(positions)

    capital = get_capital(user_id)
    total_market_value = round(sum(p["market_value"] for p in enriched), 2)
    total_cost = round(sum(p["avg_cost"] * p["total_volume"] for p in enriched), 2)
    total_profit = round(sum(p["profit"] for p in enriched), 2)
    today_profit = round(sum(p["today_profit"] for p in enriched), 2)
    initial_capital = capital.get("initial", 0)
    cash = capital.get("cash", 0)

    total_assets = total_market_value + cash
    total_profit_pct = 0
    if initial_capital > 0:
        total_profit_pct = round((total_assets - initial_capital) / initial_capital * 100, 2)
    elif total_cost > 0:
        total_profit_pct = round(total_profit / total_cost * 100, 2)

    summary = {
        "total_market_value": total_market_value,
        "total_cost": total_cost,
        "total_profit": total_profit,
        "total_profit_pct": total_profit_pct,
        "today_profit": today_profit,
        "position_count": len(enriched),
        "is_trade_time": is_trade_time(),
        "last_update": datetime.now().strftime("%H:%M:%S"),
        "cash": cash,
        "initial_capital": initial_capital,
        "total_assets": total_assets,
        "alert_count": sum(len(p.get("alerts", [])) for p in enriched),
    }

    return jsonify({"positions": enriched, "summary": summary})


@position_bp.route("/api/positions", methods=["POST"])
@login_required
def add_position():
    """新增持仓"""
    user_id = get_current_user_id()
    body = request.get_json()
    ts_code = body.get("ts_code", "").strip().upper()
    if not ts_code:
        return jsonify({"error": "股票代码不能为空"}), 400

    code_raw = ts_code.split(".")[0]
    if len(code_raw) == 6:
        if code_raw.startswith("6") or code_raw.startswith("9"):
            ts_code = f"{code_raw}.SH"
        elif code_raw.startswith("8") or code_raw.startswith("4"):
            ts_code = f"{code_raw}.BJ"
        else:
            ts_code = f"{code_raw}.SZ"

    buy_price = float(body.get("buy_price", 0))
    buy_volume = int(body.get("buy_volume", 0))
    if buy_price <= 0 or buy_volume <= 0:
        return jsonify({"error": "买入价格和数量必须大于0"}), 400

    buy_date = body.get("buy_date", datetime.now().strftime("%Y-%m-%d"))
    fee = float(body.get("fee", 0))
    note = body.get("note", "")
    reason = body.get("reason", "")
    emotion = body.get("emotion", "")

    buy_amount = buy_price * buy_volume + fee
    capital = get_capital(user_id)
    current_cash = capital.get("cash", 0)
    if current_cash < buy_amount:
        return jsonify({"error": f"可用现金不足，需要 ¥{buy_amount:.2f}，当前仅 ¥{current_cash:.2f}"}), 400

    info = get_stock_info(ts_code)
    existing = get_position_by_code(ts_code, user_id)

    if existing:
        # 追加买入已有持仓
        db_add_trade(existing["id"], "buy",
                    buy_date=buy_date, buy_price=buy_price, buy_volume=buy_volume,
                    fee=fee, reason=reason, emotion=emotion, note=note)
    else:
        # 创建新持仓 + 第一笔买入
        pos_id = create_position(ts_code, info.get("name", ""), info.get("industry", ""), user_id)
        db_add_trade(pos_id, "buy",
                    buy_date=buy_date, buy_price=buy_price, buy_volume=buy_volume,
                    fee=fee, reason=reason, emotion=emotion, note=note)

    save_capital(capital.get("initial", 0), round(current_cash - buy_amount, 2), user_id)
    return jsonify({"message": "持仓已保存", "ts_code": ts_code}), 200


@position_bp.route("/api/positions/<int:position_id>", methods=["PUT"])
@login_required
def update_position(position_id):
    """修改持仓"""
    user_id = get_current_user_id()
    body = request.get_json()
    pos = get_position(position_id, user_id)
    if not pos:
        return jsonify({"error": "持仓不存在"}), 404

    kwargs = {}
    if "name" in body:
        kwargs["name"] = body["name"]
    if "industry" in body:
        kwargs["industry"] = body["industry"]
    if "stop_loss" in body:
        kwargs["stop_loss"] = float(body["stop_loss"]) if body["stop_loss"] else None
    if "stop_profit" in body:
        kwargs["stop_profit"] = float(body["stop_profit"]) if body["stop_profit"] else None

    if kwargs:
        db_update_position(position_id, user_id, **kwargs)
    return jsonify({"message": "持仓已更新"})


@position_bp.route("/api/positions/<int:position_id>", methods=["DELETE"])
@login_required
def delete_position(position_id):
    """删除持仓（从 SQLite 直接删除，级联删除交易记录）"""
    user_id = get_current_user_id()
    pos = get_position(position_id, user_id)
    if not pos:
        return jsonify({"error": "持仓不存在"}), 404

    # 计算市值并回收现金
    pos_list = _build_position_list(user_id)
    deleted_pos = next((p for p in pos_list if p["id"] == position_id), None)
    if deleted_pos:
        calc_position_meta(deleted_pos)
        enriched = enrich_positions([deleted_pos])
        if enriched:
            market_value = enriched[0].get("market_value", 0)
            capital = get_capital(user_id)
            new_cash = round(capital.get("cash", 0) + market_value, 2)
            save_capital(capital.get("initial", 0), new_cash, user_id)

    # 从 SQLite 删除（级联删除交易记录）
    db_delete_position(position_id, user_id)
    return jsonify({"message": "持仓已删除，市值已回收至可用现金"}), 200


@position_bp.route("/api/positions/<int:position_id>/trades", methods=["POST"])
@login_required
def add_trade_route(position_id):
    """追加买入"""
    user_id = get_current_user_id()
    body = request.get_json()

    pos = get_position(position_id, user_id)
    if not pos:
        return jsonify({"error": "持仓不存在"}), 404

    buy_price = float(body.get("buy_price", 0))
    buy_volume = int(body.get("buy_volume", 0))
    if buy_price <= 0 or buy_volume <= 0:
        return jsonify({"error": "买入价格和数量必须大于0"}), 400

    add_amount = buy_price * buy_volume + float(body.get("fee", 0))
    capital = get_capital(user_id)
    current_cash = capital.get("cash", 0)
    if current_cash < add_amount:
        return jsonify({"error": f"可用现金不足，需要 ¥{add_amount:.2f}，当前仅 ¥{current_cash:.2f}"}), 400

    db_add_trade(position_id, "buy",
                buy_date=body.get("buy_date", datetime.now().strftime("%Y-%m-%d")),
                buy_price=buy_price, buy_volume=buy_volume,
                fee=float(body.get("fee", 0)),
                reason=body.get("reason", ""),
                emotion=body.get("emotion", ""),
                note=body.get("note", ""))

    save_capital(capital.get("initial", 0), round(current_cash - add_amount, 2), user_id)
    return jsonify({"message": "交易已追加"})


@position_bp.route("/api/positions/<int:position_id>/sell", methods=["POST"])
@login_required
def sell_position(position_id):
    """卖出"""
    user_id = get_current_user_id()
    body = request.get_json()

    pos = get_position(position_id, user_id)
    if not pos:
        return jsonify({"error": "持仓不存在"}), 404

    # 计算当前持仓量
    pos_list = _build_position_list(user_id)
    pos_data = next((p for p in pos_list if p["id"] == position_id), None)
    if not pos_data:
        return jsonify({"error": "持仓不存在"}), 404

    calc_position_meta(pos_data)
    current_volume = pos_data["meta"]["total_volume"]
    avg_cost = pos_data["meta"]["avg_cost"]

    sell_volume = int(body.get("sell_volume", 0))
    sell_price = float(body.get("sell_price", 0))
    sell_date = body.get("sell_date", datetime.now().strftime("%Y-%m-%d"))
    fee = float(body.get("fee", 0))
    note = body.get("note", "")
    reason = body.get("reason", "")

    if sell_price <= 0 or sell_volume <= 0:
        return jsonify({"error": "卖出价格和数量必须大于0"}), 400
    if sell_volume > current_volume:
        return jsonify({"error": f"卖出数量不能超过持仓量 {current_volume} 股"}), 400

    sell_amount = sell_price * sell_volume
    cost_amount = avg_cost * sell_volume
    sell_profit = round(sell_amount - cost_amount - fee, 2)
    sell_profit_pct = round(sell_profit / cost_amount * 100, 2) if cost_amount > 0 else 0

    # 添加卖出交易记录
    db_add_trade(position_id, "sell",
                sell_date=sell_date, sell_price=sell_price, sell_volume=sell_volume,
                fee=fee, reason=reason, note=note,
                sell_profit=sell_profit)

    # 更新现金
    capital = get_capital(user_id)
    new_cash = round(capital.get("cash", 0) + sell_amount - fee, 2)
    save_capital(capital.get("initial", 0), new_cash, user_id)

    # 如果全部卖出，删除持仓
    new_volume = current_volume - sell_volume
    if new_volume == 0:
        db_delete_position(position_id, user_id)

    return jsonify({
        "message": "卖出成功" if new_volume == 0 else "减仓成功",
        "sell_profit": sell_profit,
        "sell_profit_pct": sell_profit_pct,
        "cleared": new_volume == 0,
    })


@position_bp.route("/api/positions/<int:position_id>/trades/<int:trade_id>", methods=["DELETE"])
@login_required
def delete_trade(position_id, trade_id):
    """删除某笔交易记录"""
    user_id = get_current_user_id()
    pos = get_position(position_id, user_id)
    if not pos:
        return jsonify({"error": "持仓不存在"}), 404

    trades = get_trades(position_id)
    if not any(t["id"] == trade_id for t in trades):
        return jsonify({"error": "交易记录不存在"}), 404

    db_delete_trade(trade_id)
    return jsonify({"message": "交易记录已删除"})


# ============================================================
# 交易流水 API
# ============================================================

@position_bp.route("/api/trade-log")
@login_required
def get_trade_log():
    """获取全部交易流水"""
    user_id = get_current_user_id()
    all_trades = get_all_trades(user_id)

    trade_list = []
    for t in all_trades:
        trade_type = t.get("trade_type", "buy")
        if trade_type == "sell":
            trade_list.append({
                "trade_id": t["id"],
                "position_name": t.get("position_name", t.get("ts_code", "")),
                "ts_code": t["ts_code"],
                "trade_type": "sell",
                "date": t.get("sell_date", ""),
                "price": t.get("sell_price", 0),
                "volume": t.get("sell_volume", 0),
                "fee": t.get("fee", 0),
                "profit": t.get("sell_profit", 0),
                "profit_pct": 0,
                "note": t.get("note", ""),
                "reason": t.get("reason", ""),
                "emotion": "",
            })
        else:
            trade_list.append({
                "trade_id": t["id"],
                "position_name": t.get("position_name", t.get("ts_code", "")),
                "ts_code": t["ts_code"],
                "trade_type": "buy",
                "date": t.get("buy_date", ""),
                "price": t.get("buy_price", 0),
                "volume": t.get("buy_volume", 0),
                "fee": t.get("fee", 0),
                "profit": 0,
                "profit_pct": 0,
                "note": t.get("note", ""),
                "reason": t.get("reason", ""),
                "emotion": t.get("emotion", ""),
            })

    trade_list.sort(key=lambda x: x["date"], reverse=True)

    stats = get_trade_stats(user_id)
    return jsonify({
        "trades": trade_list,
        "stats": {
            "buy_count": stats.get("buy_count", 0),
            "sell_count": stats.get("sell_count", 0),
            "total_fee": round(stats.get("total_fee", 0), 2),
            "total_sell_profit": round(stats.get("total_sell_profit", 0), 2),
            "win_rate": stats.get("win_rate", 0),
        }
    })


# ============================================================
# 止损止盈 API
# ============================================================

@position_bp.route("/api/positions/<int:position_id>/alerts", methods=["PUT"])
@login_required
def set_alerts(position_id):
    """设置止损止盈价"""
    user_id = get_current_user_id()
    body = request.get_json()
    pos = get_position(position_id, user_id)
    if not pos:
        return jsonify({"error": "持仓不存在"}), 404

    stop_loss = float(body["stop_loss"]) if body.get("stop_loss") else None
    stop_profit = float(body["stop_profit"]) if body.get("stop_profit") else None

    db_update_position(position_id, user_id, stop_loss=stop_loss, stop_profit=stop_profit)
    return jsonify({
        "message": "止损止盈已设置",
        "stop_loss": stop_loss,
        "stop_profit": stop_profit,
    })


@position_bp.route("/api/positions/<int:position_id>/levels")
@login_required
def get_price_levels(position_id):
    """计算持仓的支撑位/压力位 [v4.3 增强版: time_prob+traffic_light+R4R5+实时价]"""
    import pandas as pd
    from helpers import calc_atr_profile, calc_price_time_prob, calc_price_hit_rate, get_realtime_quotes

    user_id = get_current_user_id()
    pos = get_position(position_id, user_id)
    if not pos:
        return jsonify({"error": "持仓不存在"}), 404

    ts_code = pos["ts_code"]
    trades = get_trades(position_id)
    buys = [t for t in trades if t["trade_type"] == "buy"]
    total_cost = sum(float(t["buy_price"]) * int(t["buy_volume"]) for t in buys)
    total_vol = sum(int(t["buy_volume"]) for t in buys)
    sells = [t for t in trades if t["trade_type"] == "sell"]
    sold_vol = sum(int(t.get("sell_volume", 0)) for t in sells)
    hold_vol = total_vol - sold_vol
    avg_cost = total_cost / total_vol if total_vol > 0 else None

    # [v4.3 5.4] 尝试获取实时价作为基准（与sell-check保持一致）
    latest_rt = None
    rt_source = None
    try:
        _rt_data = get_realtime_quotes([ts_code])
        if isinstance(_rt_data, list) and len(_rt_data) > 0 and _rt_data[0].get("price"):
            latest_rt = float(_rt_data[0]["price"])
            rt_source = _rt_data[0].get("_source", "unknown")
        elif isinstance(_rt_data, dict):
            latest_rt = float(_rt_data.get("price", 0)) if _rt_data.get("price") else None
            rt_source = _rt_data.get("_source", "unknown")
    except Exception as e:
        pass  # 实时价获取失败，回退用收盘价

    try:
        end_date = datetime.now().strftime("%Y%m%d")
        df = pro.daily(ts_code=ts_code, end_date=end_date, limit=120)
        if df.empty:
            return jsonify({"error": "无法获取K线数据"}), 500
        df = df.sort_values("trade_date")
    except Exception as e:
        return jsonify({"error": f"K线获取失败: {e}"}, 500)

    closes = df["close"].tolist()
    highs = df["high"].tolist()
    lows = df["low"].tolist()

    # [v4.3 5.4] 统一价格基准：优先实时价，否则用最后收盘价
    latest_close = closes[-1] if closes else None
    price_base = latest_rt or latest_close
    data_source_label = f"{rt_source}实时价" if latest_rt else f"Tushare收盘价({latest_close})"

    # ── ATR波动率分析 ──
    atr = calc_atr_profile(closes=closes, highs=highs, lows=lows, latest_price=price_base)

    recent_30_lows = lows[-30:] if len(lows) >= 30 else lows
    s1 = round(min(recent_30_lows), 2)
    s1_desc = f"近30日最低价 ¥{s1}，价格底部有成交密集区支撑，跌破视为趋势转弱"

    recent_20_closes = closes[-20:] if len(closes) >= 20 else closes
    ma20 = round(sum(recent_20_closes) / len(recent_20_closes), 2)
    s2 = ma20
    s2_desc = f"MA20均线 ¥{s2}，中期趋势线，多头行情下是动态支撑，跌破均线警戒"

    _atr_stop_pct = atr.get("stop_loss_pct", 0.08)
    s3 = round(avg_cost * (1 - _atr_stop_pct), 2) if avg_cost else None
    s3_label = f"成本-{_atr_stop_pct*100:.0f}%({atr['tier_label']})"
    s3_desc = f"ATR{atr['tier_label']}止损 ¥{s3}，根据个股波动率自适应调整" if s3 else None

    recent_30_highs = highs[-30:] if len(highs) >= 30 else highs
    r1 = round(max(recent_30_highs), 2)
    r1_desc = f"近30日最高价 ¥{r1}，近期高点形成阻力，突破则确认上涨动能"

    _po = atr.get("pressure_offset", 0.12)
    _to = atr.get("target_offset", 0.15)
    _r2_from_ma = round(ma20 * (1 + _po), 2)
    _r2_dynamic = round(price_base * (1 + _po * 0.67), 2) if price_base else _r2_from_ma
    r2 = max(_r2_from_ma, _r2_dynamic) if price_base else _r2_from_ma
    r2_desc = f"ATR{atr['tier_label']}动态压力位 ¥{r2}(MA+{_po*100:.0f}%)"

    # R4/R5 成本目标位（场景C增强：完整展示）
    r4 = round(avg_cost * (1 + _to * 0.67), 2) if avg_cost else None
    r5 = round(avg_cost * (1 + _to), 2) if avg_cost else None

    # ── [v4.3 5.1] 为关键价位添加时间概率 ──
    def _tp(target_price, direction):
        return calc_price_time_prob(
            current_price=price_base, target_price=target_price,
            atr_profile=atr, direction=direction,
            closes=closes, highs=highs, lows=lows)

    supports = [
        {"label": "S1 近30日最低", "price": s1, "desc": s1_desc, "type": "strong",
         "is_key": True, "time_prob": _tp(s1, "down")},
        {"label": "S2 MA20均线", "price": s2, "desc": s2_desc, "type": "medium",
         "is_key": False, "time_prob": _tp(s2, "down")},
    ]
    if s3:
        supports.append({"label": s3_label, "price": s3, "desc": s3_desc, "type": "soft",
                         "is_key": True, "time_prob": _tp(s3, "down")})
    supports.sort(key=lambda x: (x["price"] or 0))

    resistances = [
        {"label": "R1 近30日最高", "price": r1, "desc": r1_desc, "type": "strong",
         "is_key": True, "time_prob": _tp(r1, "up")},
        {"label": "R2 动态压力位", "price": r2, "desc": r2_desc, "type": "medium",
         "is_key": False, "time_prob": _tp(r2, "up")},
    ]
    # [v4.3 5.1] R4/R5 可选展示
    if r4:
        resistances.append({"label": f"R4 成本+{int(_to*67)}%", "price": r4,
                            "type": "soft", "is_key": True,
                            "desc": f"第一止盈目标(ATR{atr['tier_label']})",
                            "time_prob": _tp(r4, "up")})
    if r5:
        resistances.append({"label": f"R5 成本+{_to*100:.0f}%", "price": r5,
                            "type": "soft", "is_key": False,
                            "desc": f"理想收益目标(ATR{atr['tier_label']})",
                            "time_prob": _tp(r5, "up")})
    resistances.sort(key=lambda x: (x["price"] or 0), reverse=True)

    # [v4.3 5.1] 计算盈亏比例用于红绿灯
    profit_pct = ((price_base - avg_cost) / avg_cost * 100) if (avg_cost and avg_cost > 0 and price_base) else None

    result = {
        "ts_code": ts_code,
        "supports": supports,
        "resistances": resistances,
        "cost_ref": {
            "avg_cost": round(avg_cost, 3) if avg_cost else None,
            "cost_minus": round(avg_cost * (1 - _atr_stop_pct), 2) if avg_cost else None,
            "cost_plus_target": round(avg_cost * (1 + _to * 0.67), 2) if avg_cost else None,
            "cost_plus_double": round(avg_cost * (1 + _to * 1.5), 2) if avg_cost else None,
        },
        "ma20": ma20,
        "latest_close": latest_close,
        # [v4.3 5.4] 实时价基准
        "realtime_price": latest_rt,
        "data_source": data_source_label,
        "calc_basis": f"基于近{len(df)}个交易日K线数据计算(ATR{atr['tier_label']}全面自适应 v4.3)",
        "version": "v4.3",
        "atr_profile": {k: v for k, v in atr.items() if k != "raw_tr_list"},
        # [v4.3 5.1] 版本标识
        "has_time_prob": True,
    }

    # [v4.3 5.1] 红绿灯快判信号
    result["traffic_light"] = _calc_traffic_light(price_base, s1, r1, profit_pct, danger_count=0, ma20=ma20)

    # [v4.3 5.1] P2-9 命中率统计
    try:
        _sp_prices = [s["price"] for s in supports if s.get("price")]
        _rp_prices = [r["price"] for r in resistances if r.get("price")]
        result["hit_rate"] = calc_price_hit_rate(
            df=df, support_prices=_sp_prices, resistance_prices=_rp_prices)
    except Exception as e:
        result["hit_rate"] = {"error": str(e)}

    return jsonify(result)


# ============================================================
# 卖出智能检查 (v3.4)
# ============================================================

def _calc_traffic_light(current_price, s1, r1, profit_pct=None, danger_count=0,
                         ma20=None):
    """
    [v4.3 增强] 红绿灯快判模式 — 4价位(S1/S2/R1+现价) + 行动强度
    用于异动/快速判断场景，给出最简明的决策指引。

    Args:
        current_price: 当前价格
        s1: 近30日最低支撑位
        r1: 近30日最高阻力位
        profit_pct: 盈亏百分比(用于文案)
        danger_count: 风险警报数量(量化行动强度) [v4.3新增]
        ma20: MA20均线价格(作为中间参照) [v4.3新增]

    Returns:
        dict: { color, signal, action, intensity, key_price_levels }
    """
    if not current_price or current_price <= 0:
        return {"color": "gray", "signal": "无法获取价格数据", "action": "等待数据",
                "intensity": 0, "key_price_levels": []}

    # 核心逻辑：现价相对于S1/R1的位置决定信号
    _r1_safe = r1 * 0.98 if r1 else float('inf')
    _s1_safe = s1 * 1.02 if s1 else 0

    # [v4.3 5.2] 行动强度量化 (0~3级)
    _intensity = min(3, max(0, danger_count))

    if current_price >= _r1_safe:
        # 接近或超过近期高点
        color = "yellow"
        signal = "接近压力区" if current_price < r1 else "突破近期高点"
        _base_action = f"至少减仓1/3，锁定利润(当前盈利{profit_pct:+.1f}%)" if profit_pct else "考虑分批止盈"
        # [v4.3 5.2] danger_count强化建议
        if _intensity >= 2:
            action = _base_action + " [风险警报较多，建议加大减仓力度]"
        elif _intensity == 1:
            action = _base_action + " [存在风险信号]"
        else:
            action = _base_action
        # [v4.3 5.2] 加入S2(MA20)作为中间参照
        levels = [
            {"label": "R1 阻力位", "price": round(r1, 2) if r1 else None, "role": "第一减仓位"},
            {"label": "现价", "price": round(current_price, 2), "role": "当前位置"},
            {"label": "S2 MA20", "price": round(ma20, 2) if ma20 else None, "role": "趋势参考线"},
            {"label": "S1 支撑位", "price": round(s1, 2) if s1 else None, "role": "防守底线"},
        ]
    elif current_price <= _s1_safe:
        color = "red"
        signal = "逼近支撑位" if current_price > s1 else "跌破支撑位"
        _base_action = "警惕破位风险，准备止损" if current_price > s1 else "趋势已破坏，建议减仓/清仓"
        if _intensity >= 2:
            action = _base_action + " [多重警报确认，立即执行止损]"
        elif _intensity == 1:
            action = _base_action + " [有预警信号]"
        else:
            action = _base_action
        levels = [
            {"label": "R1 阻力位", "price": round(r1, 2) if r1 else None, "role": "反弹目标"},
            {"label": "S2 MA20", "price": round(ma20, 2) if ma20 else None, "role": "上方压力"},
            {"label": "现价", "price": round(current_price, 2), "role": "当前位置"},
            {"label": "S1 支撑位", "price": round(s1, 2) if s1 else None, "role": "最后防线"},
        ]
    else:
        color = "green"
        signal = "安全区间"
        if not danger_count or danger_count == 0:
            action = "持有为主，关注量价变化"
        elif danger_count == 1:
            action = "中性偏弱，适度降低仓位"
        else:
            action = f"偏弱({danger_count}项警报)，建议减仓至半仓以下"
        levels = [
            {"label": "R1 阻力位", "price": round(r1, 2) if r1 else None, "role": "上方目标"},
            {"label": "现价", "price": round(current_price, 2), "role": "当前位置（安全）"},
            {"label": "S2 MA20", "price": round(ma20, 2) if ma20 else None, "role": "下方支撑"},
            {"label": "S1 支撑位", "price": round(s1, 2) if s1 else None, "role": "安全垫"},
        ]

    # 过滤掉None价格的level（当ma20不可用时）
    levels = [lv for lv in levels if lv.get("price") is not None]
    if len(levels) < 3:
        # 如果过滤后太少，回退到原始3价位模式
        levels = [
            {"label": "R1 阻力位", "price": round(r1, 2) if r1 else None, "role": "顶部"},
            {"label": "现价", "price": round(current_price, 2), "role": "当前"},
            {"label": "S1 支撑位", "price": round(s1, 2) if s1 else None, "role": "底部"},
        ]

    return {
        "color": color,
        "signal": signal,
        "action": action,
        "intensity": _intensity,
        "key_price_levels": levels,
    }


@position_bp.route("/api/positions/<int:position_id>/sell-check")
@login_required
def get_sell_check(position_id):
    """
    卖出综合检查 — 返回所有卖出决策需要的辅助数据
    五维检查: 盈亏状态 / 技术面(4项) / 价格参考位 / 风险警示 / 快捷操作
    """
    import pandas as pd
    from helpers import get_realtime_quotes, get_adj_factor, adjust_kline_by_adj_factor
    from math import isclose

    user_id = get_current_user_id()
    pos = get_position(position_id, user_id)
    if not pos:
        return jsonify({"error": "持仓不存在"}), 404

    ts_code = pos["ts_code"]
    trades = get_trades(position_id)
    buys = [t for t in trades if t["trade_type"] == "buy"]
    sells = [t for t in trades if t["trade_type"] == "sell"]
    total_buy_cost = sum(float(t["buy_price"]) * int(t["buy_volume"]) for t in buys)
    total_buy_vol = sum(int(t["buy_volume"]) for t in buys)
    sold_vol = sum(int(t["sell_volume"]) for t in sells)
    hold_vol = total_buy_vol - sold_vol
    avg_cost = round(total_buy_cost / total_buy_vol, 4) if total_buy_vol > 0 else 0
    stock_name = pos.get("name", ts_code)

    # --- 获取K线 + 实时行情 ---
    data_source = "tushare"
    realtime_price = 0.0
    quote = {}  # 实时行情数据
    try:
        end_date = datetime.now().strftime("%Y%m%d")
        df = pro.daily(ts_code=ts_code, end_date=end_date, limit=60)
        if df.empty:
            return jsonify({"error": "无法获取K线数据"}), 500
        df = df.sort_values("trade_date").reset_index(drop=True)

        # 复权处理
        adj_df = get_adj_factor(ts_code, start_date=df["trade_date"].iloc[0], end_date=end_date)
        if adj_df is not None:
            df = adjust_kline_by_adj_factor(df, adj_df)

        # 尝试获取实时价格
        rt_data = get_realtime_quotes([ts_code])
        quote = rt_data.get(ts_code, {})
        rp = float(quote.get("price", 0))
        if rp > 0:
            realtime_price = rp
            data_source = quote.get("_source", "unknown")
            closes = df["close"].tolist()
            closes[-1] = rp
            df.loc[df.index[-1], "close"] = rp
        else:
            realtime_price = round(df.iloc[-1]["close"], 2)
    except Exception as e:
        return jsonify({"error": f"数据获取失败: {e}"}), 500

    latest = realtime_price
    profit_pct = round((latest - avg_cost) / avg_cost * 100, 2) if avg_cost > 0 else 0
    profit_amount = round((latest - avg_cost) * hold_vol, 2)

    # --- 维度一: 盈亏状态检查 ---
    def classify_profit_level(pct):
        if pct > 20: return ("big_profit", "丰厚盈利", "#22c55e", f"大赚{pct:.1f}%！🎉 强烈建议分批止盈，落袋为安。会买的是徒弟，会卖的是师傅。", "当前浮盈可观，可分批锁定利润：先卖50%兑现，剩余仓位博更高收益。")
        elif pct > 10: return ("good_profit", "稳健盈利", "#22c55e", f"盈利{pct:.1f}%✨ 可考虑分批止盈。", "趋势健康时可继续持有；若出现放量下跌信号则获利了结。")
        elif pct > 3: return ("small_profit", "微利持有", "#eab308", f"微利{pct:.1f}%，趋势向好可继续持有。", "若出现技术面转弱信号，及时获利离场。")
        elif pct > -3: return ("break_even", "持平观望", "#eab308", f"基本持平（{pct:+.1f}%），正常波动范围。", "不急于卖出，观察后续走势。")
        elif pct > -8: return ("light_loss", "轻度亏损", "#f97316", f"浮亏{abs(pct):.1f}%，轻度亏损属于正常波动。", "若看好中长期可继续持有；若需要资金可择机减仓，避免扩大亏损。")
        elif pct > -15: return ("loss", "中度亏损", "#ef4444", f"⚠️ 浮亏{abs(pct):.1f}%，接近止损线。", "如果跌破支撑位(S1/S2)，建议果断止损截断损失。")
        else: return ("deep_loss", "深度套牢", "#dc2626", f"🔴 深度亏损¥{abs(profit_amount):,.0f}({pct:.1f}%)！卖出即锁定亏损。", "考虑：①是否已触底？②资金是否有更好去处？③是否需要止损？")

    pl_code, pl_label, pl_color, pl_summary, pl_detail = classify_profit_level(profit_pct)
    profit_check = {"level": pl_code, "label": pl_label, "color": pl_color,
                    "pct": profit_pct, "amount": profit_amount,
                    "suggestion": pl_summary, "detail": pl_detail}

    # --- 技术面计算 ---
    closes = df["close"].tolist()
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    volumes = df["vol"].tolist()

    # MA5/MA20
    ma5 = round(sum(closes[-5:]) / 5, 2) if len(closes) >= 5 else None
    # 【v3.5 新增】MA10 中期均线
    ma10 = round(sum(closes[-10:]) / 10, 2) if len(closes) >= 10 else None
    ma20_val = round(sum(closes[-20:]) / 20, 2) if len(closes) >= 20 else None
    ma5_gt_ma20 = None

    def _ma_close(a, b, tol=0.02):
        """判断两根均线是否接近（2%容差）"""
        if a is None or b is None or b == 0:
            return False
        return abs(a - b) / b <= tol

    # 均线排列 (v3.5 升级为三层MA5/MA10/MA20判断)
    ma5_gt_ma20 = (ma5 and ma20_val and ma5 > ma20_val)
    ma5_gt_ma10 = (ma5 and ma10 and ma5 > ma10)
    ma10_gt_ma20 = (ma10 and ma20_val and ma10 > ma20_val)
    
    # 初始化ma_detail，确保所有分支都有值
    ma_detail = f"MA5={ma5 or 'N/A'} | MA10={ma10 or 'N/A'} | MA20={ma20_val or 'N/A'}"
    ma_signal = "unknown"
    ma_label = "数据不足"
    ma_passed = None
    ma_sell = False
    
    if ma5 and ma10 and ma20_val:
        # 【三层完整判断】
        if ma5_gt_ma10 and ma10_gt_ma20:
            # 完美多头：MA5 > MA10 > MA20
            ma_signal = "bullish"
            ma_label = "多头排列(强势)"
            ma_passed = True
            ma_sell = False
            ma_color = "#22c55e"
            ma_icon = "🟢"
            ma_advice = "均线完美多头排列(短>中>长)，趋势向上，安心持有"
            ma_detail = f"MA5={ma5:.2f} > MA10={ma10:.2f} > MA20={ma20_val:.2f}"
        elif ma5_gt_ma20 and not ma5_gt_ma10:
            # 短期跌破中期但仍在长期之上 → 转弱信号
            if _ma_close(ma5, ma10):
                ma_signal = "neutral"
                ma_label = "多头整理(接近MA10)"
                ma_passed = True
                ma_sell = False
                ma_color = "#eab308"
                ma_icon = "🟡"
                ma_advice = "短期均线回落至MA10附近，进入震荡整理"
                ma_detail = f"MA5≈{ma5:.2f} ≈ MA10={ma10:.2f} > MA20={ma20_val:.2f}"
            else:
                ma_signal = "weak_bearish"
                ma_label = "多头转弱(破MA10)"
                ma_passed = False
                ma_sell = True
                ma_color = "#f97316"
                ma_icon = "🟠"
                ma_advice = "价格已跌破MA10中期均线，短期动能减弱"
                ma_detail = f"MA5={ma5:.2f} < MA10={ma10:.2f} 但仍 > MA20={ma20_val:.2f}"
        elif not ma5_gt_ma20 and ma5_gt_ma10 and ma10_gt_ma20:
            # MA5在MA10之上但MA10接近或跌破MA20
            ma_signal = "weak_bearish"
            ma_label = "中期转弱(MA10近MA20)"
            ma_passed = False
            ma_sell = True
            ma_color = "#f97316"
            ma_icon = "🟠"
            ma_advice = "中期趋势走弱，MA10逼近MA20，注意风险"
            ma_detail = f"MA5={ma5:.2f} > MA10={ma10:.2f} ≈ MA20={ma20_val:.2f}"
        elif not ma5_gt_ma10 and not ma10_gt_ma20:
            # 完全空头：MA5 < MA10 < MA20
            ma_signal = "bearish"
            ma_label = "空头排列(弱势)"
            ma_passed = False
            ma_sell = True
            ma_color = "#ef4444"
            ma_icon = "🔴"
            ma_advice = "均线空头排列，趋势向下，建议减仓或清仓"
            ma_detail = f"MA5={ma5:.2f} < MA10={ma10:.2f} < MA20={ma20_val:.2f}"
        else:
            # 降级：MA10数据不足时回退到二线判断(MA5 vs MA20)
            ma5_gt_ma20 = (ma5 > ma20_val) if (ma5 and ma20_val) else False
            if ma5_gt_ma20:
                if latest >= ma5:
                    ma_signal = "bullish"
                    ma_label = "多头排列(强势)"
                    ma_passed = True
                    ma_sell = False
                    ma_detail = f"MA5={ma5:.2f} > MA20={ma20_val:.2f}（MA10数据不足，二线判断）"
                elif latest >= ma20_val:
                    ma_signal = "weak_bearish"
                    ma_label = "多头排列(转弱)"
                    ma_passed = True
                    ma_sell = False
                    ma_detail = f"MA5={ma5:.2f} > 现价 > MA20={ma20_val:.2f}"
                else:  # latest < ma20_val
                    ma_signal = "bearish"
                    ma_label = "多头排列(破位)"
                    ma_passed = False
                    ma_sell = True
                    ma_detail = f"现价 {latest:.2f} < MA20={ma20_val:.2f}（破位）"
            # 空头排列：MA5 < MA20（降级）
            else:
                if latest <= ma20_val:
                    ma_signal = "bearish"
                    ma_label = "空头排列(弱势)"
                    ma_passed = False
                    ma_sell = True
                    ma_detail = f"MA5={ma5:.2f} < MA20={ma20_val:.2f} 且现价{latest:.2f}<MA20"
                elif latest <= ma5:
                    ma_signal = "weak_bearish"
                    ma_label = "空头排列(反弹)"
                    ma_passed = False
                    ma_sell = False
                    ma_detail = f"MA5={ma5:.2f}<MA20={ma20_val:.2f}, 但现价{latest:.2f}>MA5"
                else:  # latest > ma5
                    ma_signal = "bullish"
                    ma_label = "空头排列(反转)"
                    ma_passed = True
                    ma_sell = False
                    ma_detail = f"MA5={ma5:.2f}<MA20={ma20_val:.2f}, 现价{latest:.2f}>MA5，有反转迹象"
    else:
        ma_signal = "unknown"
        ma_label = "数据不足"
        ma_passed = None
        ma_sell = False
    ma_status = {"signal": ma_signal, "label": ma_label, "ma5": ma5, "ma10": ma10, "ma20": ma20_val,
                 "passed": ma_passed, "suggest_sell": ma_sell, "ma5_gt_ma20": (ma5 > ma20_val) if (ma5 and ma20_val) else None,
                 "detail": ma_detail}

    # 量价配合（交互体验优化版）
    vol_ma5 = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else 1
    vol_ratio = round(volumes[-1] / vol_ma5, 2) if vol_ma5 > 0 else 1
    
    # 【v3.5.1 修复】用实时价格计算今日涨跌幅，与通达信/三看确认一致
    # closes[-1]是Tushare日线收盘价(可能有延迟)，latest是新浪实时价
    # 三看确认已做此修复(sina_quote.price替换closes[-1])，卖出检查需同步
    pct_1d = (latest - closes[-2]) / closes[-2] * 100 if len(closes) >= 2 and closes[-2] > 0 else 0
    
    # DEBUG: 确认日涨跌计算数据源（v3.5.1 调试日志）
    print(f"[DEBUG sell-check {ts_code}] latest(实时)={latest}, closes[-2](前收)={closes[-2]:.3f}, "
          f"closes[-1](Tushare收盘)={df['close'].iloc[-1]:.3f} if not df.empty else 'N/A', "
          f"pct_1d(实时)={pct_1d:.2f}%, data_source={data_source}")
    
    # 交互体验优化：多层级描述系统
    if vol_ratio > 1.5 and pct_1d < -0.5:
        vp_signal = "volume_down"
        vp_label = "放量下跌，主力出货信号"
        vp_passed = False
        vp_sell = True
        vp_color = "#ef4444"  # 红色
        vp_icon = "📉"
        vp_advice = "主力出货信号，建议减仓或止损"
        vp_explanation = f"成交量比5日均量高出{((vol_ratio-1)*100):.0f}%，但价格下跌{pct_1d:.1f}%，表明抛压沉重，可能有主力出货"
        vp_action_level = "high"
        vp_suggested_action = "减仓30-50%或设置止损"
    elif vol_ratio < 0.8 and pct_1d < -0.5:
        vp_signal = "weak_down"
        vp_label = "缩量下跌，抛压减轻"
        vp_passed = True
        vp_sell = False
        vp_color = "#3b82f6"  # 蓝色
        vp_icon = "↘️"
        vp_advice = "抛压减轻但趋势弱，观望"
        vp_explanation = f"成交量比5日均量低{((1-vol_ratio)*100):.0f}%，价格下跌{pct_1d:.1f}%，抛压减轻但缺乏买盘支撑"
        vp_action_level = "medium"
        vp_suggested_action = "观望，等待放量确认方向"
    elif vol_ratio > 1.5 and pct_1d > 0.5:
        vp_signal = "volume_up"
        vp_label = "放量上涨，动能增强"
        vp_passed = True
        vp_sell = False
        vp_color = "#22c55e"  # 绿色
        vp_icon = "🔥"
        vp_advice = "量价配合良好，动能增强，可持有或加仓"
        vp_explanation = f"成交量比5日均量高出{((vol_ratio-1)*100):.0f}%，价格上涨{pct_1d:.1f}%，量价齐升，上涨动能强劲"
        vp_action_level = "low"
        vp_suggested_action = "可持有或小幅加仓，关注持续性"
    elif vol_ratio < 0.8:
        vp_signal = "low_vol"
        vp_label = "成交清淡，缺乏动能"
        vp_passed = True
        vp_sell = False
        vp_color = "#94a3b8"  # 灰色
        vp_icon = "⚪"
        vp_advice = "交投清淡，市场关注度低，观望等待放量信号"
        vp_explanation = f"成交量比5日均量低{((1-vol_ratio)*100):.0f}%，市场关注度下降，缺乏明确方向"
        vp_action_level = "low"
        vp_suggested_action = "观望，等待放量确认方向"
    else:
        vp_signal = "normal"
        vp_label = "成交量正常"
        vp_passed = True
        vp_sell = False
        vp_color = "#6b7280"  # 中灰色
        vp_icon = "📊"
        vp_advice = "成交量稳定，维持当前仓位"
        vp_explanation = f"成交量与5日均量基本持平（量比{vol_ratio:.1f}），价格波动{pct_1d:+.1f}%，属于正常市场波动"
        vp_action_level = "none"
        vp_suggested_action = "维持当前仓位，按原计划操作"
    
    # 构建增强的量价配合对象
    volume_price = {
        "signal": vp_signal,
        "label": vp_label,
        "vol_ratio": vol_ratio,
        "passed": vp_passed,
        "suggest_sell": vp_sell,
        "pct_chg": round(pct_1d, 2),
        # 交互体验增强字段
        "color": vp_color,
        "icon": vp_icon,
        "advice": vp_advice,
        "strength": "strong" if vol_ratio > 1.5 else "moderate" if vol_ratio > 1.2 else "normal" if vol_ratio > 0.8 else "weak",
        # 新增解释性字段（P0优化）
        "explanation": vp_explanation,
        "action_level": vp_action_level,
        "suggested_action": vp_suggested_action,
        "vol_interpretation": "非常活跃" if vol_ratio > 1.5 else "活跃" if vol_ratio > 1.2 else "正常" if vol_ratio > 0.8 else "清淡",
        "price_trend": "上涨" if pct_1d > 0.5 else "下跌" if pct_1d < -0.5 else "震荡"
    }

    # 连续涨跌判断（v3.4.4 重写）
    # 核心思路：构建方向数组，从最近一天开始找连续同向天数
    # directions[i] = 第i天相对第(i-1)天的方向 (1=涨, -1=跌, 0=平盘)
    
    directions = []
    for i in range(len(closes) - 1, 0, -1):
        diff = closes[i] - closes[i - 1]
        # 涨跌幅阈值 0.01元，避免精度误差
        if diff > 0.01:
            directions.append(1)   # 涨
        elif diff < -0.01:
            directions.append(-1)  # 跌
        else:
            directions.append(0)   # 平盘
    
    # 从最近一天开始，找到第一个非平盘方向后的连续同向天数
    consec_up, consec_down = 0, 0
    found_direction = None
    consec_count = 0
    
    for d in directions:
        if d == 0:
            # 平盘跳过（不中断已有计数）
            if found_direction is not None:
                consec_count += 1  # 平盘也计入连续天数
            continue
        if found_direction is None:
            # 找到第一个非平盘方向
            found_direction = d
            consec_count = 1
        elif d == found_direction:
            # 同方向继续
            consec_count += 1
        else:
            # 方向反转，停止
            break
    
    if found_direction == 1:
        consec_up = consec_count
        consec_down = 0
    elif found_direction == -1:
        consec_down = consec_count
        consec_up = 0
    # else: 全部平盘，保持0
    
    # 分类逻辑保持不变
    if consec_up >= 5:
        cs_signal = "overbought"; cs_label = f"连涨{consec_up}天过热"; cs_passed = False; cs_sell = True
    elif consec_down >= 5:
        cs_signal = "oversold"; cs_label = f"连跌{consec_down}天超跌"; cs_passed = True; cs_sell = False
    elif consec_up >= 3:
        cs_signal = "strong_up"; cs_label = f"连涨{consec_up}天"; cs_passed = True; cs_sell = False
    elif consec_down >= 3:
        cs_signal = "weak_down"; cs_label = f"连跌{consec_down}天"; cs_passed = False; cs_sell = True
    else:
        cs_signal = "normal"; cs_label = f"正常(涨{consec_up}/跌{consec_down})"; cs_passed = True; cs_sell = False
    consecutive = {"days_up": consec_up, "days_down": consec_down,
                   "signal": cs_signal, "label": cs_label,
                   "passed": cs_passed, "suggest_sell": cs_sell}

    # 位置分析 (距30日高低点)
    high_30d = max(highs[-30:]) if len(highs) >= 30 else max(highs)
    low_30d = min(lows[-30:]) if len(lows) >= 30 else min(lows)
    dist_to_high = (high_30d - latest) / latest * 100 if latest > 0 else 0
    dist_to_low = (latest - low_30d) / latest * 100 if latest > 0 else 0
    if dist_to_low < 3:
        pa_signal = "near_bottom"; pa_label = f"底部区域(距30日底部{dist_to_low:.1f}%)"; pa_passed = True; pa_sell = False
    elif dist_to_high < 3:
        pa_signal = "near_top"; pa_label = f"顶部区域(距30日顶部{dist_to_high:.1f}%)"; pa_passed = False; pa_sell = True
    elif dist_to_low < 8:
        pa_signal = "low_area"; pa_label = f"偏低位置(距30日底部{dist_to_low:.1f}%)"; pa_passed = True; pa_sell = False
    elif dist_to_high < 8:
        pa_signal = "high_area"; pa_label = f"接近30日高点(距顶部{dist_to_high:.1f}%)"; pa_passed = False; pa_sell = True  # 偏高位置应触发卖出
    else:
        pa_signal = "neutral"; pa_label = f"中间位置(距30日高-{dist_to_high:.1f}%/距30日低+{dist_to_low:.1f}%)"; pa_passed = True; pa_sell = False
    position_analysis = {"dist_to_high_pct": round(dist_to_high, 1), "dist_to_low_pct": round(dist_to_low, 1),
                         "signal": pa_signal, "label": pa_label,
                         "passed": pa_passed, "suggest_sell": pa_sell}

    # 综合评分 (0-100, 分越高越应该持有不卖) - v3.4.3 保守化调整（方案1）
    score = 60  # 基础分60，满分100可达
    reasons_list = []
    # 均线：失败惩罚 > 通过奖励
    if not ma_sell: score += 10; reasons_list.append(f"均线{ma_label}(+10)")
    else: score -= 20; reasons_list.append(f"均线{ma_label}(-20)")
    # 量价：放量下跌是强信号，加重扣分
    if not vp_sell: score += 10; reasons_list.append(f"量价{vp_label}(+10)")
    else: score -= 25; reasons_list.append(f"量价{vp_label}(-25)")
    # 走势：失败惩罚 > 通过奖励
    if not cs_sell: score += 10; reasons_list.append(f"走势{cs_label}(+10)")
    else: score -= 15; reasons_list.append(f"走势{cs_label}(-15)")
    # 位置：对称调整
    if not pa_sell: score += 10; reasons_list.append(f"位置{pa_label}(+10)")
    else: score -= 10; reasons_list.append(f"位置{pa_label}(-10)")

    score = max(0, min(100, score))
    # 细分档位
    if score >= 90: verdict = "hold_strong"; verdict_label = "强烈持有"
    elif score >= 75: verdict = "hold"; verdict_label = "正常持有"
    elif score >= 50: verdict = "reduce"; verdict_label = "观望/减仓"
    elif score >= 25: verdict = "sell"; verdict_label = "建议减仓"
    else: verdict = "sell_strong"; verdict_label = "建议清仓"

    technical_check = {
        "ma_status": ma_status,
        "volume_price": volume_price,
        "consecutive": consecutive,
        "position_analysis": position_analysis,
        "overall": {"score": score, "verdict": verdict, "label": verdict_label,
                    "summary": f"{verdict_label} (综合评分{score}分)", "reasons": reasons_list}
    }

    # ── [v4.2 P2-7] 统一ATR波动率分析（替代原来的内联计算）──
    from helpers import calc_atr_profile, calc_price_time_prob, calc_price_hit_rate

    recent_30_lows = lows[-30:] if len(lows) >= 30 else lows
    recent_30_highs = highs[-30:] if len(highs) >= 30 else highs
    s1 = round(min(recent_30_lows), 2)
    s2 = ma20_val or round(sum(closes[-20:]) / 20, 2) if len(closes) >= 20 else s1

    # ── [v4.2 P2-7] 统一ATR波动率分析（替代原来的内联计算）──
    atr = calc_atr_profile(closes=closes, highs=highs, lows=lows, latest_price=latest)
    _atr_stop_pct = atr.get("stop_loss_pct", 0.08)
    _po = atr.get("pressure_offset", 0.12)
    _to = atr.get("target_offset", 0.15)

    s3 = round(avg_cost * (1 - _atr_stop_pct), 2) if avg_cost else None
    s3_label = f"成本-{_atr_stop_pct*100:.0f}%({atr['tier_label']})"

    # 动态压力位：MA20 + ATR压力偏移 和 现价+偏移*0.67 取较高值
    r1 = round(max(recent_30_highs), 2) if len(recent_30_highs) >= 1 else round(max(highs), 2)
    _r2_from_ma = round(s2 * (1 + _po), 2)
    _r2_dynamic = round(latest * (1 + _po * 0.67), 2) if latest else _r2_from_ma
    r2 = max(_r2_from_ma, _r2_dynamic) if latest else _r2_from_ma

    # 成本参考位也用 ATR 自适应（替代硬编码+10%/+20%）
    r4 = round(avg_cost * (1 + _to * 0.67), 2) if avg_cost else None  # 第一止盈目标
    r5 = round(avg_cost * (1 + _to), 2) if avg_cost else None          # 理想收益目标

    # ── [v4.2 P2-8] 为每个价位计算时间预期和达成概率 ──
    def _tp(target_price, direction):
        """快捷函数：计算单个价位的时间概率"""
        return calc_price_time_prob(
            current_price=latest, target_price=target_price,
            atr_profile=atr, direction=direction,
            closes=closes, highs=highs, lows=lows)

    # [P0-1] 每个价位附带推荐操作标签 + ★关键动作标注 + P2-8时间概率
    supports = [
        {"label": "S2 MA20均线", "price": s2, "type": "medium",
         "desc": "中期趋势线，跌破说明趋势转弱", "action": "减仓观察", "action_ratio": 50, "is_key": False,
         "time_prob": _tp(s2, "down")},
        {"label": "S1 近30日最低", "price": s1, "type": "strong",
         "desc": "近期强支撑，跌破视为破位", "action": "加仓/反弹点", "action_ratio": 30, "is_key": True,
         "time_prob": _tp(s1, "down")},
    ]
    if s3:
        supports.insert(0, {"label": s3_label, "price": s3, "type": "soft",
                            "desc": f"ATR{atr['tier_label']}止损底线(波动率自适应)", "action": "硬止损清仓", "action_ratio": 100, "is_key": True,
                            "time_prob": _tp(s3, "down")})

    resistances = [
        {"label": "R1 近30日最高", "price": r1, "type": "strong",
         "desc": "近期阻力顶，突破确认上涨动能", "action": "第一减仓位", "action_ratio": 33, "is_key": True,
         "time_prob": _tp(r1, "up")},
        {"label": "R2 动态压力位", "price": r2, "type": "medium",
         "desc": f"ATR{atr['tier_label']}止盈区(MA+{_po*100:.0f}%)", "action": "分批止盈", "action_ratio": 50, "is_key": False,
         "time_prob": _tp(r2, "up")},
    ]
    if r4:
        resistances.append({"label": f"R4 成本+{_to*67:.0f}%", "price": r4, "type": "soft",
                            "desc": f"第一止盈目标(ATR{atr['tier_label']})", "action": "可全出/落袋为安", "action_ratio": 100, "is_key": True,
                            "time_prob": _tp(r4, "up")})
    if r5:
        resistances.append({"label": f"R5 成本+{_to*100:.0f}%", "price": r5, "type": "soft",
                            "desc": f"理想收益目标(ATR{atr['tier_label']})", "action": "留底仓", "action_ratio": 50, "is_key": False,
                            "time_prob": _tp(r5, "up")})

    # 支撑位按价格升序排列（从低到高），压力位按价格降序（从高到低）
    supports.sort(key=lambda x: (x["price"] or 0))
    resistances.sort(key=lambda x: (x["price"] or 0), reverse=True)

    # --- 风险警报（前置初始化，traffic_light引用需要）---
    danger_count = 0
    warning_count = 0
    alerts = []

    price_levels = {
        "resistances": resistances, "supports": supports,
        "current_price": latest, "ma20": s2,
        "cost_ref": {"avg_cost": avg_cost, "cost_minus": s3, "cost_plus_target": r4, "cost_plus_double": r5},
        "calc_basis": f"基于近{len(df)}个交易日K线数据计算(ATR{atr['tier_label']}全面自适应 v4.2)",
        "version": "v4.2",
        # [P2-7] ATR波动率档案
        "atr_profile": {k: v for k, v in atr.items() if k != "raw_tr_list"},
        # [P2-8] 版本标识（含时间概率）
        "has_time_prob": True,
        # [P1-4] 红绿灯快判模式：4价位 + 行动强度
        "traffic_light": _calc_traffic_light(latest, s1, r1, profit_pct, danger_count, ma20=s2)
    }

    # ── [v4.2 P2-9] 历史价位命中率统计 ──
    try:
        _sp_prices = [s["price"] for s in supports if s.get("price")]
        _rp_prices = [r["price"] for r in resistances if r.get("price")]
        # 用更长周期数据(120天)做回测
        df_long = pro.daily(ts_code=ts_code, end_date=end_date, limit=120)
        if not df_long.empty:
            price_levels["hit_rate"] = calc_price_hit_rate(
                df=df_long,
                support_prices=_sp_prices,
                resistance_prices=_rp_prices
            )
    except Exception as e:
        print(f"[WARN] P2-9 hit_rate 计算跳过: {e}")

    # --- 风险警报逻辑（变量已在上方初始化）---
    if ma_sell and ma_signal == "bearish":
        if ma5_gt_ma20:  # 多头排列但价格破位
            alert_text = f"多头破位(价格<MA20={ma20_val})"
        else:  # 空头排列
            alert_text = f"均线空头排列(MA5<{ma20_val})"
        alerts.append({"level": "warning", "icon": "📉", "text": alert_text})
        warning_count += 1
    if vp_sell and vp_signal == "volume_down":
        alerts.append({"level": "danger", "icon": "📉", "text": f"放量下跌(量比{vol_ratio})，主力出货信号"})
        danger_count += 1
    if pl_code in ("deep_loss", "loss"):
        alerts.append({"level": "danger" if danger_count > 0 else "warning",
                       "icon": "💸", "text": f"深度亏损({pl_label} {profit_pct:+.1f}%)",
                       "detail": pl_detail})
    if cs_sell and cs_signal == "overbought":
        alerts.append({"level": "notice", "icon": "🔥", "text": f"连续上涨{consec_up}天，短线过热注意回调"})
    # 三重危险合并
    if danger_count >= 2 or (danger_count >= 1 and pl_code == "deep_loss"):
        alerts.insert(0, {"level": "critical", "icon": "🚨",
                          "text": "多重危险信号叠加！建议立即减仓或清仓控制损失"})

    # --- 快捷操作 ---
    quick_actions = [
        {"action": "full_sell", "label": f"全部清仓 ({hold_vol}股)", "volume": hold_vol, "price_hint": "使用现价"},
        {"action": "half_sell", "label": f"减半卖出 ({hold_vol // 2}股)", "volume": hold_vol // 2, "price_hint": "使用现价"},
    ]

    # [v4.3 5.3] 买入/卖出场景联动：B锚点与A价位交叉对比
    _buy_sell_link = None
    try:
        from helpers import calc_buy_price_anchors
        _bpa = calc_buy_price_anchors(df, latest_realtime=latest)
        if _bpa and _bpa.get("target_profit") and r2:
            _b_target = _bpa["target_profit"].get("price")
            _b_safety = _bpa["safety_support"].get("price")
            _b_stop = _bpa["stop_loss_line"].get("price")
            _buy_sell_link = {
                "buy_anchors": {
                    "target_profit": _b_target,
                    "safety_support": _b_safety,
                    "stop_loss_line": _b_stop,
                    "buy_zone_low": _bpa.get("buy_zone", {}).get("low"),
                    "buy_zone_high": _bpa.get("buy_zone", {}).get("high"),
                },
                # B-target vs A-R2: 买入目标是否已达成或超越卖出压力位
                "cross_check": {
                    # target_profit(买入第一目标) vs R2(动态压力位)
                    "target_vs_r2": {
                        "buy_target": round(_b_target, 2) if _b_target else None,
                        "sell_r2": r2,
                        "status": "exceeded" if (_b_target and latest >= _b_target)
                                 else ("approaching" if (_b_target and r2 and latest >= r2 * 0.95) else "not_reached"),
                        "note": (f"现价{latest:.2f}已超过买入目标{_b_target:.2f}" if (_b_target and latest >= _b_target)
                                else f"距买入目标{_b_target:.2f}还需涨{((_b_target-latest)/latest*100):.1f}%") if _b_target else None,
                    },
                    # safety_support(安全垫) vs S1(30日最低): 是否跌破原始支撑
                    "safety_vs_s1": {
                        "buy_safety": _b_safety,
                        "sell_s1": s1,
                        "status": "broken" if (_b_safety and latest < _b_safety)
                                  else ("near" if (_b_safety and latest < _b_safety * 1.03) else "safe"),
                    },
                    # stop_loss_line vs S3(cost止损)
                    "stop_vs_s3": {
                        "buy_stop": _b_stop,
                        "sell_s3": s3,
                        "closer_one": "B止损" if (_b_stop and s3 and _b_stop > s3) else ("S3成本止损" if s3 else None),
                    }
                },
            }
    except Exception as e:
        print(f"[WARN] 5.3 买卖联动计算跳过: {e}")

    return jsonify({
        "position": {
            "id": position_id, "ts_code": ts_code, "name": stock_name,
            "total_volume": hold_vol, "avg_cost": avg_cost,
            "current_price": latest, "profit_pct": profit_pct,
            "profit_amount": profit_amount, "market_value": round(hold_vol * latest, 2),
            "today_pct_chg": round(pct_1d, 2),
            "stop_loss": pos.get("stop_loss"), "stop_profit": pos.get("stop_profit"),
        },
        "profit_check": profit_check,
        "technical_check": technical_check,
        "price_levels": price_levels,
        "risk_alerts": alerts,
        "quick_actions": quick_actions,
        "data_source": data_source,
        # [v4.3 5.3]
        "buy_sell_linkage": _buy_sell_link,
        "check_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


@position_bp.route("/api/positions/<int:position_id>/advice")
@login_required
def get_position_advice(position_id):
    """持仓操作策略建议（使用实时数据）"""
    from helpers import get_realtime_quotes
    
    user_id = get_current_user_id()
    pos = get_position(position_id, user_id)
    if not pos:
        return jsonify({"error": "持仓不存在"}), 404

    ts_code = pos["ts_code"]
    trades = get_trades(position_id)
    buys = [t for t in trades if t["trade_type"] == "buy"]
    sells = [t for t in trades if t["trade_type"] == "sell"]
    total_buy_cost = sum(float(t["buy_price"]) * int(t["buy_volume"]) for t in buys)
    total_buy_vol = sum(int(t["buy_volume"]) for t in buys)
    sold_vol = sum(int(t["sell_volume"]) for t in sells)
    hold_vol = total_buy_vol - sold_vol
    avg_cost = total_buy_cost / total_buy_vol if total_buy_vol > 0 else 0

    try:
        end_date = datetime.now().strftime("%Y%m%d")
        df = pro.daily(ts_code=ts_code, end_date=end_date, limit=60)
        if df.empty:
            return jsonify({"error": "无法获取K线数据"}), 500
        df = df.sort_values("trade_date")
    except Exception as e:
        return jsonify({"error": f"K线获取失败: {e}"}), 500

    closes = df["close"].tolist()
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    volumes = df["vol"].tolist()
    latest = closes[-1]
    
    # 获取实时行情替换最新价格
    realtime_data = get_realtime_quotes([ts_code])
    quote = realtime_data.get(ts_code, {})
    realtime_price = quote.get("price", 0)
    realtime_pct = quote.get("pct_chg", 0)  # 获取今日涨跌幅
    if realtime_price > 0:
        closes[-1] = realtime_price
        latest = realtime_price
    
    # 记录数据源
    data_source = quote.get("_source", "tushare")
    data_time = quote.get("_time", "")

    ma5 = round(sum(closes[-5:]) / 5, 3) if len(closes) >= 5 else closes[-1]
    ma20 = round(sum(closes[-20:]) / 20, 3) if len(closes) >= 20 else closes[-1]
    vol_ma5 = round(sum(volumes[-5:]) / 5, 0) if len(volumes) >= 5 else volumes[-1]

    # 连续涨跌判断（v3.4.4 重写）
    # 核心思路：构建方向数组，从最近一天开始找连续同向天数
    # directions[i] = 第i天相对第(i-1)天的方向 (1=涨, -1=跌, 0=平盘)
    
    print(f"[DEBUG] data_source={data_source}, realtime_price={realtime_price}, realtime_pct={realtime_pct}")
    print(f"[DEBUG] closes count={len(closes)}, last5={closes[-5:] if len(closes)>=5 else closes}")
    
    directions = []
    for i in range(len(closes) - 1, 0, -1):
        diff = closes[i] - closes[i - 1]
        # 涨跌幅阈值 0.01元，避免精度误差
        if diff > 0.01:
            directions.append(1)   # 涨
        elif diff < -0.01:
            directions.append(-1)  # 跌
        else:
            directions.append(0)   # 平盘
    
    print(f"[DEBUG] directions(前10个, 近→远)={directions[:10]}")
    
    # 从最近一天开始，找到第一个非平盘方向后的连续同向天数
    consec_up, consec_down = 0, 0
    found_direction = None
    consec_count = 0
    
    for d in directions:
        if d == 0:
            # 平盘跳过（不中断已有计数）
            if found_direction is not None:
                consec_count += 1  # 平盘也计入连续天数
            continue
        if found_direction is None:
            # 找到第一个非平盘方向
            found_direction = d
            consec_count = 1
        elif d == found_direction:
            # 同方向继续
            consec_count += 1
        else:
            # 方向反转，停止
            break
    
    if found_direction == 1:
        consec_up = consec_count
        consec_down = 0
    elif found_direction == -1:
        consec_down = consec_count
        consec_up = 0
    # else: 全部平盘，保持0
    
    print(f"[DEBUG] consec_up={consec_up}, consec_down={consec_down}, direction={'up' if found_direction==1 else 'down' if found_direction==-1 else 'flat'}")

    pct_5d = (latest - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 else 0
    high_30d = max(highs[-30:]) if len(highs) >= 30 else max(highs)
    low_30d = min(lows[-30:]) if len(lows) >= 30 else min(lows)
    vol_ratio = volumes[-1] / vol_ma5 if vol_ma5 > 0 else 1.0

    profit_pct = (latest - avg_cost) / avg_cost * 100 if avg_cost > 0 else 0
    profit_amount = (latest - avg_cost) * hold_vol if hold_vol > 0 else 0
    market_value = latest * hold_vol if hold_vol > 0 else 0

    if ma5 > ma20 and latest > ma5:
        trend = "多头排列"
        trend_score = 2
    elif ma5 > ma20 and latest <= ma5:
        trend = "多头回调"
        trend_score = 1
    elif ma5 <= ma20 and latest > ma20:
        trend = "震荡整理"
        trend_score = 0
    else:
        trend = "空头排列"
        trend_score = -2

    score = 0
    reasons = []
    # 分项评分（5维度），用于前端展示分解明细
    scores = {"trend": 0, "profit": 0, "volume_price": 0, "position": 0, "momentum": 0, "structure": 0}

    # 1. 趋势分
    score += trend_score * 15
    scores["trend"] = trend_score * 15
    if trend_score >= 1:
        reasons.append(f"趋势{trend}，均线向上（+{trend_score*15}）")
    elif trend_score <= -1:
        reasons.append(f"趋势{trend}，均线向下（{trend_score*15}）")
    else:
        reasons.append(f"趋势{trend}，方向不明（+0）")

    # 2. 盈亏分
    if profit_pct > 20:
        score += 5
        scores["profit"] = 5
        reasons.append(f"浮盈{profit_pct:.1f}%，获利丰厚但注意回调风险（+5）")
    elif profit_pct > 10:
        score += 15
        scores["profit"] = 15
        reasons.append(f"浮盈{profit_pct:.1f}%，趋势健康可持有（+15）")
    elif profit_pct > 3:
        score += 20
        scores["profit"] = 20
        reasons.append(f"浮盈{profit_pct:.1f}%，稳健盈利（+20）")
    elif profit_pct > -3:
        score += 5
        scores["profit"] = 5
        reasons.append(f"浮盈{profit_pct:.1f}%，微盈/持平观望（+5）")
    elif profit_pct > -8:
        score -= 10
        scores["profit"] = -10
        reasons.append(f"浮亏{abs(profit_pct):.1f}%，轻度亏损关注支撑（-10）")
    elif profit_pct > -15:
        score -= 20
        scores["profit"] = -20
        reasons.append(f"浮亏{abs(profit_pct):.1f}%，中度亏损建议止损（-20）")
    else:
        score -= 25
        scores["profit"] = -25
        reasons.append(f"浮亏{abs(profit_pct):.1f}%，严重亏损强烈建议止损（-25）")

    # 3. 量价配合
    if vol_ratio > 1.5 and pct_5d > 0:
        score += 15
        scores["volume_price"] = 15
        reasons.append(f"放量上涨（量比{vol_ratio:.1f}），动能增强（+15）")
    elif vol_ratio > 1.2 and pct_5d > 0:
        score += 8
        scores["volume_price"] = 8
        reasons.append(f"温和放量（量比{vol_ratio:.1f}），量价配合（+8）")
    elif vol_ratio < 0.6 and pct_5d < 0:
        score -= 15
        scores["volume_price"] = -15
        reasons.append(f"缩量下跌（量比{vol_ratio:.1f}），抛压减轻但趋势弱（-15）")
    elif vol_ratio > 1.5 and pct_5d < 0:
        score -= 10
        scores["volume_price"] = -10
        reasons.append(f"放量下跌（量比{vol_ratio:.1f}），主力出货信号（-10）")
    elif vol_ratio < 0.8:
        score -= 3
        scores["volume_price"] = -3
        reasons.append(f"缩量（量比{vol_ratio:.1f}），交投清淡（-3）")
    else:
        scores["volume_price"] = 0
        reasons.append(f"成交量正常（量比{vol_ratio:.1f}）（+0）")

    # 4. 距离压力/支撑位
    dist_to_high = (high_30d - latest) / latest * 100
    dist_to_low = (latest - low_30d) / latest * 100
    if dist_to_low < 3:
        score += 10
        scores["position"] = 10
        reasons.append(f"贴近30日低点（距底部{dist_to_low:.1f}%），安全边际高（+10）")
    elif dist_to_low < 8:
        score += 5
        scores["position"] = 5
        reasons.append(f"距30日低点{dist_to_low:.1f}%，偏低位置（+5）")
    elif dist_to_high < 3:
        score -= 10
        scores["position"] = -10
        reasons.append(f"贴近30日高点（距顶部{dist_to_high:.1f}%），阻力区域（-10）")
    elif dist_to_high < 8:
        score -= 5
        scores["position"] = -5
        reasons.append(f"距30日高点{dist_to_high:.1f}%，偏高位置（-5）")
    else:
        scores["position"] = 0
        reasons.append(f"距30日高点{dist_to_high:.1f}%，距低点{dist_to_low:.1f}%，中性位置（+0）")

    # 5. 连涨/连跌（动量）
    if consec_up >= 5:
        score -= 10
        scores["momentum"] = -10
        reasons.append(f"连涨{consec_up}天，短线过热注意回调（-10）")
    elif consec_up >= 3:
        score += 5
        scores["momentum"] = 5
        reasons.append(f"连涨{consec_up}天，短线强势（+5）")
    elif consec_down >= 5:
        score += 10
        scores["momentum"] = 10
        reasons.append(f"连跌{consec_down}天，超跌可能反弹（+10）")
    elif consec_down >= 3:
        score -= 5
        scores["momentum"] = -5
        reasons.append(f"连跌{consec_down}天，趋势偏弱（-5）")


    # 【新增】6. 高低点结构分析（统一调用 analyze_hl_points，动态n）
    hl_structure = {"structure": "unknown", "score": 0, "signal": "无法分析", "recent_highs": [], "recent_lows": []}
    try:
        from helpers import analyze_hl_points
        
        # 动态n：根据持仓周期自适应
        # 短线(<5天)→n=2灵敏，波段(5~20天)→n=4稳健，长线(>20天)→n=5保守
        if buys:
            from datetime import datetime as dt
            earliest_buy = min(t.get("buy_date", "") for t in buys)
            try:
                buy_dt = dt.strptime(earliest_buy, "%Y-%m-%d")
                hold_days = (dt.now() - buy_dt).days
            except Exception:
                hold_days = 0
            
            if hold_days <= 5:
                hl_n = 2   # 短线：灵敏捕捉短期结构
            elif hold_days <= 20:
                hl_n = 4   # 波段：平衡灵敏度与可靠性
            else:
                hl_n = 5   # 长线：过滤噪音，关注中期方向
        else:
            hl_n = 4  # 默认波段级别
        
        hl_result = analyze_hl_points(highs=highs, lows=lows, n=hl_n)
        hl_structure = {
            "structure": hl_result.get("structure", "unknown"),
            "score": hl_result.get("score", 0),
            "signal": hl_result.get("signal", "无法分析"),
            "high_trend": hl_result.get("high_trend", "flat"),
            "low_trend": hl_result.get("low_trend", "flat"),
            "recent_highs": [p[1] for p in hl_result.get("recent_highs", [])],
            "recent_lows": [p[1] for p in hl_result.get("recent_lows", [])],
            "_hl_n": hl_n,  # 记录实际使用的窗口参数
            "_hold_days": hold_days if buys else None,
        }
        
        # 根据结构调整评分
        if hl_result["structure"] == "uptrend":
            hl_bonus = 10
            score += hl_bonus
            scores["structure"] = hl_bonus
            reasons.append(f"高低点结构健康（{hl_result['signal']}）+{hl_bonus}")
        elif hl_result["structure"] == "downtrend":
            hl_penalty = -15
            score += hl_penalty
            scores["structure"] = hl_penalty
            reasons.append(f"高低点结构恶化（{hl_result['signal']}）{hl_penalty}")
        elif hl_result["high_trend"] == "up":
            hl_bonus = 5
            score += hl_bonus
            scores["structure"] = hl_bonus
            reasons.append(f"高点突破，趋势转强 +{hl_bonus}")
        elif hl_result["low_trend"] == "down":
            hl_penalty = -8
            score += hl_penalty
            scores["structure"] = hl_penalty
            reasons.append(f"低点下移，风险增加 {hl_penalty}")
    except Exception as e:
        print(f"[WARN] 高低点结构分析失败: {e}")

    # 建议映射
    if score >= 30:
        action = "加仓"
        action_color = "success"
    elif score >= 10:
        action = "持有"
        action_color = "primary"
    elif score >= -10:
        action = "观望"
        action_color = "warning"
    elif score >= -25:
        action = "减仓"
        action_color = "danger"
    else:
        action = "清仓"
        action_color = "danger"

    # 操作图标和描述
    action_icon_map = {"加仓": "🚀", "持有": "📌", "观望": "👀", "减仓": "📉", "清仓": "🗑️"}
    action_desc_map = {
        "加仓": "建议逢低加仓",
        "持有": "当前趋势良好可继续持有",
        "观望": "趋势不明建议观望",
        "减仓": "建议逢高减仓控制风险",
        "清仓": "建议清仓规避风险",
    }
    action_icon = action_icon_map.get(action, "📌")
    action_desc = action_desc_map.get(action, "")

    # 计算总资产和现金（用于仓位占比）
    from database import get_capital
    capital = get_capital(user_id)
    total_assets = capital.get("total_assets", 0) if capital else 0
    position_pct = round(market_value / total_assets * 100, 1) if total_assets > 0 else 0

    # 具体操作建议
    if action == "加仓":
        add_vol = max(100, (hold_vol * 0.2 // 100) * 100)
        add_price = round(latest * 0.98, 2)  # 回调2%入场
        add_note = f"建议回调至 ¥{add_price} 附近加仓 {add_vol} 股"
        reduce_note = "暂无需减仓"
    elif action == "持有":
        add_vol = max(100, (hold_vol * 0.1 // 100) * 100)
        add_price = round(latest * 1.01, 2)
        add_note = f"如突破 ¥{add_price} 可小幅加仓 {add_vol} 股"
        reduce_note = "趋势未变，继续持有"
    elif action == "观望":
        add_vol = 0
        add_note = "趋势不明，暂不加仓"
        reduce_note = "可分批减仓锁定部分收益"
    elif action == "减仓":
        reduce_vol = max(100, (hold_vol * 0.3 // 100) * 100)
        reduce_note = f"建议减仓约 {reduce_vol} 股降低风险"
        add_note = "暂不加仓"
    else:  # 清仓
        reduce_vol = hold_vol
        reduce_note = f"建议清仓全部 {hold_vol} 股"
        add_note = "不建议加仓"

    # 与止损止盈弹窗保持一致：直接使用30日最低/最高，不额外偏移
    stop_loss_price = round(low_30d, 2)
    stop_loss_note = f"跌破 ¥{stop_loss_price}（30日低点）建议止损"
    take_profit_price = round(high_30d, 2)
    take_profit_note = f"涨至 ¥{take_profit_price}（30日高点）可考虑止盈"

    return jsonify({
        "ts_code": ts_code,
        "score": score,
        "scores": scores,  # 6维度分项分解: trend/profit/volume_price/position/momentum/structure
        "action": action,
        "action_icon": action_icon,
        "action_desc": action_desc,
        "action_color": action_color,
        "reasons": reasons,
        "indicators": {
            "trend": trend,
            "ma5": ma5,
            "ma20": ma20,
            "vol_ratio": round(vol_ratio, 2),
            "consec_up": consec_up,
            "consec_down": consec_down,
            "pct_5d": round(pct_5d, 2),
            "dist_to_high_30d": round(dist_to_high, 1),
            "dist_to_low_30d": round(dist_to_low, 1),
            "latest": latest,
            "avg_cost": round(avg_cost, 3),
            "profit_pct": round(profit_pct, 2),
            "position_pct": position_pct,
        },
        "hl_structure": {
            "structure": hl_structure.get("structure", "unknown"),
            "score": hl_structure.get("score", 0),
            "signal": hl_structure.get("signal", ""),
            "high_trend": hl_structure.get("high_trend", "flat"),
            "low_trend": hl_structure.get("low_trend", "flat"),
            "recent_highs": hl_structure.get("recent_highs", []),
            "recent_lows": hl_structure.get("recent_lows", []),
        },
        "profit": {
            "profit_pct": round(profit_pct, 2),
            "profit_amount": round(profit_amount, 2),
            "market_value": round(market_value, 2),
            "avg_cost": round(avg_cost, 3),
            "hold_volume": hold_vol,
        },
        "suggestions": {
            "add": {"volume": add_vol if action in ("加仓", "持有") else 0, "price": add_price if action in ("加仓", "持有") else latest, "note": add_note},
            "reduce": {"volume": reduce_vol if action in ("减仓", "清仓") else 0, "price": latest, "note": reduce_note},
            "stop_loss": {"price": stop_loss_price, "note": stop_loss_note},
            "take_profit": {"price": take_profit_price, "note": take_profit_note},
        },
        "_source": data_source,
        "_time": data_time,
    })


# ============================================================
# K线图 API
# ============================================================

@position_bp.route("/api/kline/<ts_code>")
@login_required
def get_kline_data(ts_code):
    """获取个股日K线数据（最近60日），支持前复权"""
    try:
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=120)).strftime("%Y%m%d")

        # 获取日K线数据
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df.empty:
            return jsonify({"dates": [], "klines": [], "ma5": [], "ma20": [], "volumes": []})
        df = df.sort_values("trade_date")

        # 获取复权因子并做前复权
        adj_df = get_adj_factor(ts_code, start_date=start_date, end_date=end_date)
        if not adj_df.empty:
            df = adjust_kline_by_adj_factor(df, adj_df)

        dates = df["trade_date"].tolist()
        klines = df[["open", "close", "low", "high"]].round(3).values.tolist()
        closes = df["close"].tolist()
        volumes = df["vol"].tolist()

        # 计算MA5和MA20（使用pandas rolling计算更准确）
        import pandas as pd
        df_temp = pd.DataFrame({'close': closes})
        ma5_list = df_temp['close'].rolling(window=5, min_periods=1).mean().round(3).tolist()
        ma20_list = df_temp['close'].rolling(window=20, min_periods=1).mean().round(3).tolist()
        # 前4个MA5和前19个MA20设为None（数据不足）
        ma5 = [None] * 4 + ma5_list[4:] if len(ma5_list) > 4 else ma5_list
        ma20 = [None] * 19 + ma20_list[19:] if len(ma20_list) > 19 else ma20_list

        dates_fmt = [d[4:6] + "/" + d[6:8] for d in dates]

        return jsonify({
            "dates": dates_fmt,
            "klines": klines,
            "ma5": ma5,
            "ma20": ma20,
            "volumes": volumes,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# 复盘报告 API
# ============================================================

@position_bp.route("/api/review/<period>")
@login_required
def get_review_report(period):
    """交易复盘报告"""
    user_id = get_current_user_id()
    all_trades = get_all_trades(user_id)

    trade_list = []
    for t in all_trades:
        trade_type = t.get("trade_type", "buy")
        if trade_type == "sell":
            trade_list.append({
                "date": t.get("sell_date", ""),
                "trade_type": "sell",
                "position_name": t.get("position_name", t.get("ts_code", "")),
                "ts_code": t["ts_code"],
                "price": float(t.get("sell_price", 0)),
                "volume": int(t.get("sell_volume", 0)),
                "fee": float(t.get("fee", 0)),
                "profit": float(t.get("sell_profit", 0)),
                "reason": t.get("reason", ""),
                "emotion": "",
            })
        else:
            trade_list.append({
                "date": t.get("buy_date", ""),
                "trade_type": "buy",
                "position_name": t.get("position_name", t.get("ts_code", "")),
                "ts_code": t["ts_code"],
                "price": float(t.get("buy_price", 0)),
                "volume": int(t.get("buy_volume", 0)),
                "fee": float(t.get("fee", 0)),
                "profit": 0,
                "reason": t.get("reason", ""),
                "emotion": t.get("emotion", ""),
            })

    trade_list.sort(key=lambda x: x["date"], reverse=True)

    now = datetime.now()
    if period == "week":
        week_start = now - timedelta(days=now.weekday())
        cutoff = week_start.strftime("%Y-%m-%d")
    elif period == "month":
        cutoff = now.replace(day=1).strftime("%Y-%m-%d")
    else:
        cutoff = "2000-01-01"

    trades = [t for t in trade_list if t["date"] >= cutoff]

    buys = [t for t in trades if t["trade_type"] == "buy"]
    sells = [t for t in trades if t["trade_type"] == "sell"]
    win_sells = [t for t in sells if t["profit"] > 0]
    lose_sells = [t for t in sells if t["profit"] <= 0]

    buy_count = len(buys)
    sell_count = len(sells)
    total_fee = sum(t["fee"] for t in trades)
    total_sell_profit = sum(t["profit"] for t in sells)
    win_rate = round(len(win_sells) / sell_count * 100, 1) if sell_count > 0 else 0

    avg_win = sum(t["profit"] for t in win_sells) / len(win_sells) if win_sells else 0
    avg_loss = abs(sum(t["profit"] for t in lose_sells) / len(lose_sells)) if lose_sells else 0.01
    profit_loss_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else float("inf")

    max_win = max((t["profit"] for t in sells), default=0)
    max_loss = min((t["profit"] for t in sells), default=0)

    emotion_stats = {}
    for t in trades:
        emo = t.get("emotion", "")
        if emo:
            emotion_stats[emo] = emotion_stats.get(emo, 0) + 1

    reason_words = {}
    for t in trades:
        reason = t.get("reason", "")
        if reason:
            for word in reason.replace("，", ",").replace("、", ",").split(","):
                word = word.strip()
                if len(word) >= 2:
                    reason_words[word] = reason_words.get(word, 0) + 1
    top_reasons = sorted(reason_words.items(), key=lambda x: x[1], reverse=True)[:8]

    if sell_count == 0:
        summary_text = f"本期内共{buy_count}笔买入，暂无卖出记录，无法评估策略效果。"
    elif win_rate >= 60 and profit_loss_ratio >= 2:
        summary_text = f"优秀！胜率{win_rate}%且盈亏比{profit_loss_ratio}，策略有明显正期望。保持纪律，控制仓位。"
    elif win_rate >= 50 and total_sell_profit > 0:
        summary_text = f"良好。胜率{win_rate}%，盈利{total_sell_profit:.0f}元。盈亏比{profit_loss_ratio}偏低可优化止盈策略。"
    elif total_sell_profit > 0:
        summary_text = f"整体盈利{total_sell_profit:.0f}元，但胜率仅{win_rate}%。靠少数大盈利支撑，需提高选股准确度。"
    elif win_rate >= 40:
        summary_text = f"胜率{win_rate}%尚可但亏损{abs(total_sell_profit):.0f}元，盈亏比{profit_loss_ratio}不足。需要改进止损策略。"
    else:
        summary_text = f"胜率{win_rate}%，亏损{abs(total_sell_profit):.0f}元。策略需要全面复盘调整，建议减少交易频率，提高选股标准。"

    return jsonify({
        "period": period,
        "period_label": {"week": "本周", "month": "本月", "all": "全部"}.get(period, period),
        "stats": {
            "buy_count": buy_count,
            "sell_count": sell_count,
            "win_count": len(win_sells),
            "lose_count": len(lose_sells),
            "win_rate": win_rate,
            "total_fee": round(total_fee, 2),
            "total_sell_profit": round(total_sell_profit, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_loss_ratio": profit_loss_ratio,
            "max_win": round(max_win, 2),
            "max_loss": round(max_loss, 2),
        },
        "emotion_stats": emotion_stats,
        "top_reasons": top_reasons,
        "trades": trades,
        "buys": buys,
        "sells": sells,
        "summary": summary_text,
    })


# ============================================================
# 市场环境判断 API
# ============================================================

@position_bp.route("/api/market-regime")
@login_required
def get_market_regime():
    """判断当前市场环境（使用实时数据源）"""
    try:
        # 获取实时指数行情（东方财富→新浪→Tushare降级）
        index_quotes = get_index_quotes()
        sh_quote = index_quotes.get("sh", {})
        realtime_close = sh_quote.get("price", 0)
        
        # 获取历史数据计算MA和波动率
        end_date = datetime.now().strftime("%Y%m%d")
        df = pro.index_daily(ts_code="000001.SH", end_date=end_date, limit=60)
        if df.empty:
            return jsonify({"error": "无法获取指数数据"}), 500
        df = df.sort_values("trade_date")
        closes = df["close"].tolist()
        
        # 如果有实时价格，用实时价格替换最新收盘价
        if realtime_close > 0:
            closes[-1] = realtime_close

        ma20 = sum(closes[-20:]) / 20
        ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else sum(closes) / len(closes)
        pct_20d = (closes[-1] - closes[-20]) / closes[-20] * 100 if len(closes) >= 20 else 0

        returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(len(closes)-20, len(closes))]
        avg_ret = sum(returns) / len(returns) if returns else 0
        variance = sum((r - avg_ret) ** 2 for r in returns) / len(returns) if returns else 0
        volatility = (variance ** 0.5) * (252 ** 0.5) * 100

        if ma20 > ma60 and pct_20d > 5:
            regime, regime_score = "牛市", 85
        elif ma20 > ma60 and pct_20d > 2:
            regime, regime_score = "偏强震荡", 65
        elif ma20 < ma60 and pct_20d < -5:
            regime, regime_score = "熊市", 15
        elif ma20 < ma60 and pct_20d < -2:
            regime, regime_score = "偏弱震荡", 35
        else:
            regime, regime_score = "震荡市", 50

        strategies = {
            "牛市": {"max_position": "70-90%", "action": "积极加仓，持股为主", "stop_loss": "成本-10%或跌破MA20", "take_profit": "分批止盈，MA20上方10-20%", "style": "趋势跟踪，强者恒强", "tips": ["牛市不轻易止损，让利润奔跑", "优先持有强势股，弱势股及时换股", "可用浮盈加仓，但总仓位不超过90%", "关注板块轮动，适时切换热点"]},
            "偏强震荡": {"max_position": "50-70%", "action": "精选个股，波段操作", "stop_loss": "成本-8%或跌破MA20", "take_profit": "MA20+8%~15%，触及即减", "style": "波段操作，高抛低吸", "tips": ["震荡市注意高抛低吸，不宜追高", "突破压力位加仓，接近阻力位减仓", "控制单只股票仓位不超过30%"]},
            "震荡市": {"max_position": "30-50%", "action": "轻仓观望，精选低吸", "stop_loss": "成本-5%，严格止损", "take_profit": "MA20+5%~10%", "style": "短线为主，快进快出", "tips": ["震荡市多看少动，等待方向选择", "只在支撑位附近低吸，不在压力位追涨", "保留大量现金，等待趋势确认", "单只股票仓位不超过20%"]},
            "偏弱震荡": {"max_position": "20-30%", "action": "防御为主，减少交易", "stop_loss": "成本-5%，坚决止损", "take_profit": "有盈利就走，不贪", "style": "防守反击，见好就收", "tips": ["弱势震荡减少操作频率，避免反复亏损", "只在超跌反弹时轻仓参与", "现金为王，耐心等待底部信号"]},
            "熊市": {"max_position": "0-20%", "action": "空仓休息，或极小仓位博反弹", "stop_loss": "成本-3%，无条件止损", "take_profit": "有利润就走，不恋战", "style": "空仓等待，不抄底", "tips": ["熊市最大的策略就是不操作", "如果一定要做，只用总资金10%以内", "不抄底！不抄底！不抄底！", "等待MA20上穿MA60的右侧信号"]},
        }

        strategy = strategies.get(regime, strategies["震荡市"])

        # 获取实时涨跌幅
        realtime_pct_chg = sh_quote.get("pct_chg", 0)
        realtime_change = sh_quote.get("change", 0)
        
        # 【新增】今日市场速览数据（来自市场Tab）
        market_summary = {}
        try:
            today = datetime.now().strftime("%Y%m%d")
            
            # 1. 涨停数据（今日）
            try:
                limit_df = pro.limit_list(trade_date=today)
                if not limit_df.empty:
                    up_count = len(limit_df[limit_df['limit'] == 'U'])
                    market_summary['limit_up_count'] = up_count
                else:
                    market_summary['limit_up_count'] = None
            except:
                market_summary['limit_up_count'] = None
            
            # 2. 北向资金（今日）
            try:
                hsgt_df = pro.moneyflow_hsgt(start_date=today, end_date=today)
                if not hsgt_df.empty:
                    north_money = hsgt_df.iloc[0].get('north_money', 0)
                    market_summary['northbound_flow'] = round(north_money, 2)
                else:
                    market_summary['northbound_flow'] = None
            except:
                market_summary['northbound_flow'] = None
            
            # 3. 领涨板块（今日）
            try:
                sector_df = pro.moneyflow_ind_ths(trade_date=today)
                if not sector_df.empty and 'net_mf_amount' in sector_df.columns:
                    # 按净流入排序取前3
                    top_sectors = sector_df.nlargest(3, 'net_mf_amount')[['name', 'net_mf_amount']]
                    market_summary['top_sectors'] = [
                        {"name": row['name'], "flow": round(row['net_mf_amount'], 2)}
                        for _, row in top_sectors.iterrows()
                    ]
                else:
                    market_summary['top_sectors'] = []
            except:
                market_summary['top_sectors'] = []
                
        except Exception:
            market_summary = {'limit_up_count': None, 'northbound_flow': None, 'top_sectors': []}
        
        return jsonify({
            "regime": regime,
            "regime_score": regime_score,
            "indicators": {
                "index_close": round(closes[-1], 2),
                "ma20": round(ma20, 2),
                "ma60": round(ma60, 2),
                "pct_20d": round(pct_20d, 2),
                "volatility": round(volatility, 2),
                "pct_chg": realtime_pct_chg,  # 当日实时涨跌幅
                "change": realtime_change,    # 当日实时涨跌额
            },
            "strategy": strategy,
            "market_summary": market_summary,  # 【新增】今日市场速览
            "_source": sh_quote.get("_source", "tushare"),  # 数据源标识
            "_time": sh_quote.get("_time", ""),  # 时间戳
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# 选股回测 API
# ============================================================

# 回测结果缓存：{(strategy, days, hold): (result, timestamp)}
_backtest_cache = {}
_BACKTEST_CACHE_TTL = 3600  # 缓存1小时

@position_bp.route("/api/backtest")
@login_required
def run_backtest():
    """选股规则历史回测（策略联动版）"""
    days_back = int(request.args.get("days", 30))
    hold_days = int(request.args.get("hold", 5))
    strategy = request.args.get("strategy", "trend_break")  # 新增：策略类型

    # 检查缓存
    cache_key = (strategy, days_back, hold_days)
    cached = _backtest_cache.get(cache_key)
    if cached:
        result, timestamp = cached
        if time.time() - timestamp < _BACKTEST_CACHE_TTL:
            return jsonify(result)

    try:
        from screener import get_recent_trade_dates
        import pandas as pd

        total_days = days_back + hold_days + 10
        all_dates = get_recent_trade_dates(total_days)
        if not all_dates:
            return jsonify({"error": "无法获取交易日历"}), 500

        all_dates_asc = list(reversed(all_dates))
        eval_end_idx = max(0, len(all_dates_asc) - hold_days - 3)
        eval_dates = all_dates_asc[max(0, eval_end_idx - days_back): eval_end_idx]

        if not eval_dates:
            return jsonify({"error": "回测日期区间不足"}), 500

        import pandas as pd
        
        daily_basic_cache = {}
        daily_cache = {}  # 用于获取历史日线数据

        def get_daily_basic_cached(td):
            if td in daily_basic_cache:
                return daily_basic_cache[td]
            try:
                df = pro.daily_basic(
                    trade_date=td,
                    fields="ts_code,trade_date,close,pct_chg,turnover_rate,circ_mv,vol,amount"
                )
                daily_basic_cache[td] = df if df is not None else pd.DataFrame()
            except Exception:
                daily_basic_cache[td] = pd.DataFrame()
            return daily_basic_cache[td]

        def get_daily_cached(td):
            """获取日线数据（用于计算MA等技术指标）"""
            if td in daily_cache:
                return daily_cache[td]
            try:
                df = pro.daily(trade_date=td, fields="ts_code,close,open,high,low,vol,amount,pct_chg")
                daily_cache[td] = df if df is not None else pd.DataFrame()
            except Exception:
                daily_cache[td] = pd.DataFrame()
            return daily_cache[td]

        def get_ma(ts_code, end_date, days=20):
            """计算某股票的历史MA"""
            try:
                df = pro.daily(ts_code=ts_code, end_date=end_date, limit=days+5)
                if df is None or df.empty or len(df) < days:
                    return None
                df = df.sort_values("trade_date")
                return df["close"].tail(days).mean()
            except Exception:
                return None

        def screen_candidates_strategy(df_basic, df_daily, date, strategy_type):
            """根据策略类型筛选候选股票"""
            if df_basic is None or df_basic.empty:
                return pd.DataFrame()

            # 基础过滤（所有策略通用）
            candidates = df_basic[
                (~df_basic["ts_code"].str.contains("BJ")) &
                (df_basic["circ_mv"] > 50 * 10000) &
                (df_basic["turnover_rate"] > 1.0)
            ].copy()

            if candidates.empty:
                return candidates

            if strategy_type == "trend_break":
                # 趋势突破策略：涨跌幅适中 + 近期有趋势
                candidates = candidates[
                    (candidates["pct_chg"].between(-2, 5)) &
                    (candidates["vol"] > candidates["vol"].median())  # 放量
                ]
            elif strategy_type == "sector_leader":
                # 板块龙头策略：涨幅较大但不过高
                candidates = candidates[
                    (candidates["pct_chg"].between(3, 9)) &
                    (candidates["turnover_rate"].between(3, 15))
                ]
            elif strategy_type == "oversold_bounce":
                # 超跌反弹策略：近期下跌 + 当日止跌
                # 简化为仅使用当日涨跌幅，避免shift操作在边界情况下的问题
                candidates = candidates[
                    candidates["pct_chg"].between(-5, 3)
                ]
            else:
                # 默认策略
                candidates = candidates[candidates["pct_chg"].between(-2, 3)]

            return candidates

        results = []
        total_screened = 0
        total_pass = 0
        win_count = 0

        for date in eval_dates:
            date_idx = all_dates_asc.index(date)
            df_today_basic = get_daily_basic_cached(date)
            df_today_daily = get_daily_cached(date)

            if df_today_basic is None or df_today_basic.empty:
                continue

            # 使用策略筛选
            candidates = screen_candidates_strategy(df_today_basic, df_today_daily, date, strategy)

            total_screened += len(df_today_basic)
            total_pass += len(candidates)

            if candidates.empty:
                continue

            future_idx = date_idx + hold_days
            if future_idx >= len(all_dates_asc):
                continue
            future_date = all_dates_asc[future_idx]
            df_future = get_daily_basic_cached(future_date)
            if df_future is None or df_future.empty:
                continue

            df_merged = candidates[["ts_code", "close"]].merge(
                df_future[["ts_code", "close"]].rename(columns={"close": "close_future"}),
                on="ts_code", how="inner"
            )
            if df_merged.empty:
                continue

            df_merged["return_pct"] = (df_merged["close_future"] - df_merged["close"]) / df_merged["close"] * 100

            for _, r in df_merged.iterrows():
                ret = float(r["return_pct"])
                results.append({
                    "date": date,
                    "ts_code": r["ts_code"],
                    "buy_price": round(float(r["close"]), 3),
                    "sell_price": round(float(r["close_future"]), 3),
                    "return_pct": round(ret, 2),
                    "hold_days": hold_days,
                })
                if ret > 0:
                    win_count += 1

        if not results:
            return jsonify({
                "period": f"近{days_back}天",
                "hold_days": hold_days,
                "total_signals": 0,
                "win_rate": 0, "avg_return": 0, "max_return": 0, "max_loss": 0,
                "profit_loss_ratio": 0, "max_drawdown": 0,
                "trades": [],
                "summary": "回测期内未触发选股信号。"
            })

        returns = [r["return_pct"] for r in results]
        total_trades = len(results)
        avg_ret = sum(returns) / total_trades
        max_ret = max(returns)
        min_ret = min(returns)
        wr = round(win_count / total_trades * 100, 1)

        profits = [r for r in returns if r > 0]
        losses = [abs(r) for r in returns if r < 0]
        avg_profit = sum(profits) / len(profits) if profits else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        profit_loss_ratio = round(avg_profit / avg_loss, 2) if avg_loss > 0 else 99.0

        cum = 0
        peak = 0
        max_drawdown = 0
        for r in returns:
            cum += r
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_drawdown:
                max_drawdown = dd
        max_drawdown = round(max_drawdown, 2)

        if wr >= 60 and avg_ret > 1:
            summary = f"全市场回测：胜率{wr}%，平均收益{avg_ret:.2f}%，盈亏比{profit_loss_ratio}，{hold_days}日持有期表现优秀，规则有效。"
        elif wr >= 50 and avg_ret > 0:
            summary = f"全市场回测：胜率{wr}%，平均收益{avg_ret:.2f}%，盈亏比{profit_loss_ratio}，{hold_days}日持有期有正期望，可继续优化。"
        else:
            summary = f"全市场回测：胜率{wr}%，平均收益{avg_ret:.2f}%，盈亏比{profit_loss_ratio}，{hold_days}日持有期效果不佳，需要调整筛选标准。"

        result = {
            "period": f"近{days_back}天",
            "hold_days": hold_days,
            "total_signals": total_trades,
            "screened_total": total_screened,
            "win_rate": wr,
            "avg_return": round(avg_ret, 2),
            "max_return": round(max_ret, 2),
            "max_loss": round(min_ret, 2),
            "avg_profit": round(avg_profit, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_loss_ratio": profit_loss_ratio,
            "max_drawdown": max_drawdown,
            "trades": sorted(results, key=lambda x: x["return_pct"], reverse=True)[:50],
            "summary": summary,
        }
        # 保存到缓存
        _backtest_cache[cache_key] = (result, time.time())
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# 预警推送 API
# ============================================================

@position_bp.route("/api/alerts/check")
@login_required
def check_price_alerts():
    """检查所有持仓的止损/止盈触发情况"""
    user_id = get_current_user_id()
    positions = get_all_positions(user_id)
    triggered = []

    for p in positions:
        stop_loss = p.get("stop_loss")
        stop_profit = p.get("stop_profit")
        ts_code = p["ts_code"]
        name = p.get("name", ts_code)

        if not stop_loss and not stop_profit:
            continue

        try:
            df = pro.daily(ts_code=ts_code, end_date=datetime.now().strftime("%Y%m%d"), limit=2)
            if df.empty:
                continue
            latest = df.iloc[-1]["close"]
            latest_date = df.iloc[-1]["trade_date"]
        except Exception:
            continue

        if stop_loss and latest <= float(stop_loss):
            triggered.append({
                "type": "stop_loss", "level": "danger",
                "ts_code": ts_code, "name": name,
                "current_price": round(latest, 2),
                "trigger_price": float(stop_loss),
                "message": f"🚨 {name}({ts_code}) 触发止损！现价¥{latest:.2f} ≤ 止损价¥{stop_loss}",
                "date": latest_date,
            })

        if stop_profit and latest >= float(stop_profit):
            triggered.append({
                "type": "stop_profit", "level": "success",
                "ts_code": ts_code, "name": name,
                "current_price": round(latest, 2),
                "trigger_price": float(stop_profit),
                "message": f"🎉 {name}({ts_code}) 触发止盈！现价¥{latest:.2f} ≥ 止盈价¥{stop_profit}",
                "date": latest_date,
            })

    return jsonify({
        "triggered": triggered,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(triggered),
    })


# ============================================================
# 行情数据 API
# ============================================================

@position_bp.route("/api/quote/<ts_code>")
@login_required
def get_quote(ts_code):
    """获取单只股票实时行情"""
    ts_code = ts_code.upper()
    if is_trade_time():
        quotes = get_realtime_quotes([ts_code])
    else:
        quotes = {}
        daily = get_tushare_daily(ts_code)
        if daily:
            info = get_stock_info(ts_code)
            daily["name"] = info["name"]
            quotes[ts_code] = daily
    return jsonify(quotes.get(ts_code, {"error": "未获取到行情数据"}))


@position_bp.route("/api/history/<ts_code>")
@login_required
def get_history(ts_code):
    """获取日K线历史数据"""
    ts_code = ts_code.upper()
    days = request.args.get("days", 30, type=int)
    days = min(days, 500)

    try:
        end_date = datetime.now().strftime("%Y%m%d")
        df = pro.daily(ts_code=ts_code, end_date=end_date, limit=days)
        if df.empty:
            return jsonify({"data": []})
        df = df.sort_values("trade_date")
        records = df.to_dict("records")
        for r in records:
            for k in ["open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"]:
                if k in r:
                    r[k] = float(r[k])
        return jsonify({"data": records})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# 辅助 API
# ============================================================

@position_bp.route("/api/search")
def search_stock():
    """搜索股票"""
    keyword = request.args.get("keyword", "").strip()
    if not keyword:
        return jsonify({"results": []})

    stocks = load_stock_list()
    keyword_upper = keyword.upper()

    results = []
    for s in stocks:
        if (keyword_upper in s["ts_code"].upper() or
            keyword in s["name"] or
            keyword_upper in s["symbol"]):
            results.append({
                "ts_code": s["ts_code"],
                "name": s["name"],
                "industry": s.get("industry", ""),
                "market": s.get("market", ""),
            })
        if len(results) >= 20:
            break

    return jsonify({"results": results})


@position_bp.route("/api/check-three-views")
@login_required
def check_three_views():
    """
    检查股票的"三看"条件
    返回：高低点抬高、均线多头排列、量价配合的检查结果
    """
    import pandas as pd
    
    ts_code = request.args.get("ts_code", "").strip()
    if not ts_code:
        return jsonify({"error": "请提供股票代码"}), 400

    try:
        # 获取最近60天的日线数据（前复权）
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")

        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df.empty or len(df) < 20:
            return jsonify({"error": "数据不足，无法分析"}), 400

        df = df.sort_values("trade_date").reset_index(drop=True)

        # 获取前复权因子
        try:
            adj_df = pro.adj_factor(ts_code=ts_code, start_date=start_date, end_date=end_date)
            if not adj_df.empty:
                adj_df = adj_df.sort_values("trade_date").reset_index(drop=True)
                df = df.merge(adj_df[["trade_date", "adj_factor"]], on="trade_date", how="left")
                latest_adj = df["adj_factor"].iloc[-1] if pd.notna(df["adj_factor"].iloc[-1]) else 1.0
                df["close"] = (df["close"] * df["adj_factor"]) / latest_adj
                df["high"] = (df["high"] * df["adj_factor"]) / latest_adj
                df["low"] = (df["low"] * df["adj_factor"]) / latest_adj
                df["open"] = (df["open"] * df["adj_factor"]) / latest_adj
        except Exception:
            pass

        # 计算均线
        df["ma5"] = df["close"].rolling(window=5).mean()
        df["ma10"] = df["close"].rolling(window=10).mean()
        df["ma20"] = df["close"].rolling(window=20).mean()

        # 获取最新数据
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else None

        # 强制使用新浪财经实时行情（绕过东方财富和 Tushare 降级）
        from helpers import get_realtime_quotes_sina
        data_source = "tushare"
        realtime_price = 0.0
        sina_success = False
        try:
            sina_data = get_realtime_quotes_sina([ts_code], use_cache=False)
            sina_quote = sina_data.get(ts_code, {})
            rp = sina_quote.get("price", 0)
            if rp > 0:
                realtime_price = rp
                data_source = "sina"
                sina_success = True
                closes = df["close"].tolist()
                closes[-1] = rp
                df.loc[df.index[-1], "close"] = rp
                latest = df.iloc[-1]
                print(f"[INFO] 三看确认 {ts_code} 新浪实时价: ¥{rp}")
            else:
                print(f"[WARN] 三看确认 {ts_code} 新浪行情返回空价格，使用 Tushare 历史价")
        except Exception as e:
            print(f"[WARN] 三看确认 {ts_code} 新浪行情获取失败: {e}，使用 Tushare 历史价")

        result = {
            "ts_code": ts_code,
            "name": get_stock_info(ts_code).get("name", ""),
            "trade_date": latest["trade_date"],
            "close": round(float(latest["close"]), 3),
            "realtime_price": round(realtime_price, 3) if realtime_price > 0 else None,
            "data_source": data_source,
            "sina_success": sina_success,
            "checks": {}
        }

        # ========== 一看：高低点抬高（统一调用公共函数） ==========
        from helpers import analyze_hl_points
        _hl_n = 2  # 三看检查用短期窗口（灵敏捕捉3~5天波段）
        hl_result = analyze_hl_points(
            highs=df["high"].tolist(),
            lows=df["low"].tolist(),
            n=_hl_n
        )

        recent_lows = [(idx, p) for idx, p in hl_result.get("recent_lows", [])]
        recent_highs = [(idx, p) for idx, p in hl_result.get("recent_highs", [])]

        low_increasing = hl_result.get("low_trend") == "up"
        high_increasing = hl_result.get("high_trend") == "up"

        # ---- 构建用户友好的高低点描述 ----
        _struct = hl_result.get("structure", "unknown")
        _sig = hl_result.get("signal", "")
        _lt = hl_result.get("low_trend", "flat")
        _ht = hl_result.get("high_trend", "flat")
        # n值含义说明（供用户理解分析灵敏度）
        _window_days = _hl_n * 2 + 1  # 实际K线窗口宽度
        _scope_label = {
            2: "短线(n=2·5日)",
            3: "短波(n=3·7日)",
            4: "波段(n=4·9日)",
            5: "趋势(n=5·11日)",
        }.get(_hl_n, f"窗口(n={_hl_n})")

        # description：结论 + 窗口信息 + 结构类型
        if low_increasing and high_increasing:
            desc_map = {
                "uptrend": f"✅ 高低点抬高 [{_scope_label}] — 近期呈上升结构",
                "weak_uptrend": f"⚡ 高低点偏高 [{_scope_label}] — 高点突破但低点待确认",
                "bottoming": f"📐 底部构建 [{_scope_label}] — 低点已抬高，等待高点突破",
                "sideways": f"➡️ 高低点微升 [{_scope_label}] — 震荡偏强方向待确认",
            }
            hl_desc = desc_map.get(_struct, f"✅ 高低点抬高 [{_scope_label}]")
        elif low_increasing and not high_increasing:
            hl_desc = f"🔸 仅低点抬高 [{_scope_label}] — 支撑上移但压力未突破"
        elif not low_increasing and high_increasing:
            hl_desc = f"⚠️ 仅高点抬高 [{_scope_label}] — 注意风险，支撑在下移"
        else:
            desc_map_fail = {
                "downtrend": f"❌ 高低点下移 [{_scope_label}] — 呈下降结构，注意回避",
                "weak_downtrend": f"🔻 高点回落 [{_scope_label}] — 趋势转弱需警惕",
                "topping": f"📍 顶部构筑 [{_scope_label}] — 低点开始下移",
                "sideways": f"➖ 高低点未抬 [{_scope_label}] — 方向不明",
            }
            hl_desc = desc_map_fail.get(_struct, f"❌ 高低点未抬高 [{_scope_label}]")

        # detail：具体点位 + 趋势箭头 + n值标注
        low_prices = [round(x[1], 2) for x in recent_lows]
        high_prices = [round(x[1], 2) for x in recent_highs]
        low_arrow = "↗" if _lt == "up" else ("↘" if _lt == "down" else "→")
        high_arrow = "↗" if _ht == "up" else ("↘" if _ht == "down" else "→")
        lt_text = {"up":"持续抬高","down":"不断下移","flat":"平稳"}[_lt]
        ht_text = {"up":"突破向上","down":"逐步回落","flat":"持平"}[_ht]

        parts = []
        if low_prices:
            parts.append(f"近{len(low_prices)}个低点: {' → '.join(f'{p:.2f}' for p in low_prices)} {low_arrow} {lt_text}")
        if high_prices:
            parts.append(f"近{len(high_prices)}个高点: {' → '.join(f'{p:.2f}' for p in high_prices)} {high_arrow} {ht_text}")
        # 在detail末尾追加窗口说明
        window_note = f"(分析窗口: {_window_days}日K线)"
        hl_detail = (" | ".join(parts) + " | " + window_note) if parts else (f"{_sig} | {window_note}")

        result["checks"]["high_low"] = {
            "passed": bool(low_increasing and high_increasing),
            "low_increasing": bool(low_increasing),
            "high_increasing": bool(high_increasing),
            "recent_lows": low_prices,
            "recent_highs": high_prices,
            "description": hl_desc,
            "detail": hl_detail,
            "_hl_structure": _struct,
            "_hl_score": hl_result.get("score", 0),
            "_hl_signal": _sig,
            "_hl_n": _hl_n,           # 窗口参数（前端可按此做差异化展示）
            "_hl_window_days": _window_days,  # 实际K线窗口天数
        }

        # ========== 二看：均线多头排列 ==========
        ma5 = float(latest["ma5"]) if not pd.isna(latest["ma5"]) else 0
        ma10 = float(latest["ma10"]) if not pd.isna(latest["ma10"]) else 0
        ma20 = float(latest["ma20"]) if not pd.isna(latest["ma20"]) else 0

        ma_bull = ma5 > ma10 > ma20 > 0

        result["checks"]["ma"] = {
            "passed": bool(ma_bull),
            "ma5": round(ma5, 3),
            "ma10": round(ma10, 3),
            "ma20": round(ma20, 3),
            "description": "均线多头排列" if ma_bull else "均线非多头排列",
            "detail": f"MA5={ma5:.2f} {'>' if ma5 > ma10 else '<'} MA10={ma10:.2f} {'>' if ma10 > ma20 else '<'} MA20={ma20:.2f}"
        }

        # ========== 三看：量价配合 ==========
        # 标准量比 = 当日成交量 / 过去5日平均成交量
        today_vol = float(latest["vol"])
        vol_ma5 = df["vol"].tail(5).mean()  # 最近5日平均成交量（包含当日）
        vol_ratio = today_vol / vol_ma5 if vol_ma5 > 0 else 1.0

        # 上涨日 vs 下跌日成交量（近10个交易日）
        up_days = df[df["close"] > df["open"]].tail(10)
        down_days = df[df["close"] < df["open"]].tail(10)
        up_vol = up_days["vol"].mean() if len(up_days) > 0 else 0
        down_vol = down_days["vol"].mean() if len(down_days) > 0 else 0

        # 计算当日涨跌幅
        pct_1d = (latest["close"] - prev["close"]) / prev["close"] * 100 if prev is not None else 0
        
        vol_ok = vol_ratio > 0.8 and up_vol > down_vol
        
        # 交互体验优化：多层级描述系统（颜色+图标+自然语言）
        # 第一层：信号分类（用于快速识别）
        # 1. 危险信号：放量下跌（主力出货）
        if vol_ratio > 1.3 and pct_1d < -0.5:
            signal = "danger"
            icon = "📉"
            short_desc = "放量下跌，警惕出货"
            color = "#ef4444"  # 红色
            advice = "主力出货信号，建议减仓或止损"
            explanation = f"成交量比5日均量高出{((vol_ratio-1)*100):.0f}%，但价格下跌{pct_1d:.1f}%，表明抛压沉重，可能有主力出货"
            action_level = "high"
            suggested_action = "减仓30-50%或设置止损"
            vol_interpretation = "非常活跃"
            price_trend = "下跌"
            # 覆盖vol_ok：放量下跌无论量能结构如何都危险
            vol_ok = False
        # 2. 强势信号：极度放量且上涨量能显著占优
        elif vol_ratio > 1.5 and up_vol > down_vol * 1.5:
            signal = "strong"
            icon = "🔥"
            short_desc = "成交极度活跃，量价齐升"
            color = "#22c55e"  # 绿色
            advice = "量价配合极佳，动能强劲，可持有或加仓"
            explanation = f"成交量比5日均量高出{((vol_ratio-1)*100):.0f}%，上涨日成交量是下跌日的{up_vol/down_vol:.1f}倍，量价齐升，上涨动能强劲"
            action_level = "low"
            suggested_action = "可持有或小幅加仓，关注持续性"
            vol_interpretation = "非常活跃"
            price_trend = "上涨"
        # 3. 良好信号：放量且上涨量能占优
        elif vol_ratio > 1.2 and up_vol > down_vol:
            signal = "good"
            icon = "📈"
            short_desc = "成交活跃，上涨有量支撑"
            color = "#3b82f6"  # 蓝色
            advice = "量价配合良好，趋势健康，可继续持有"
            explanation = f"成交量比5日均量高出{((vol_ratio-1)*100):.0f}%，上涨日成交量是下跌日的{up_vol/down_vol:.1f}倍，量价配合良好"
            action_level = "none"
            suggested_action = "维持当前仓位，趋势向好可继续持有"
            vol_interpretation = "活跃"
            price_trend = "上涨"
        # 4. 正常信号：量比正常且上涨量能占优（通过）
        elif vol_ratio >= 0.8 and up_vol > down_vol:
            signal = "normal"
            icon = "📊"
            short_desc = "成交量正常，上涨量能充足"
            color = "#6b7280"  # 中灰色
            advice = "成交量稳定，上涨有量支撑，维持当前仓位"
            explanation = f"成交量与5日均量基本持平（量比{vol_ratio:.1f}），上涨日成交量是下跌日的{up_vol/down_vol:.1f}倍，量价配合正常"
            action_level = "none"
            suggested_action = "维持当前仓位，按原计划操作"
            vol_interpretation = "正常"
            price_trend = "震荡"
        # 5. 不足信号：量比正常但上涨量能不足（不通过）
        elif vol_ratio >= 0.8 and up_vol <= down_vol:
            signal = "weak"
            icon = "⚪"
            short_desc = "成交量正常但上涨量能不足"
            color = "#94a3b8"  # 灰色
            advice = "成交量稳定但上涨缺乏量能支撑，建议观望"
            explanation = f"成交量与5日均量基本持平（量比{vol_ratio:.1f}），但上涨日成交量仅为下跌日的{up_vol/down_vol:.1f}倍，上涨缺乏量能支撑"
            action_level = "medium"
            suggested_action = "观望，等待放量确认方向"
            vol_interpretation = "正常"
            price_trend = "震荡"
        # 6. 清淡信号：量比过低（不通过）
        else:  # vol_ratio < 0.8
            signal = "weak"
            icon = "⚪"
            short_desc = "成交清淡，缺乏动能"
            color = "#94a3b8"  # 灰色
            advice = "交投清淡，市场关注度低，观望等待放量信号"
            explanation = f"成交量比5日均量低{((1-vol_ratio)*100):.0f}%，市场关注度下降，缺乏明确方向"
            action_level = "medium"
            suggested_action = "观望，等待放量确认方向"
            vol_interpretation = "清淡"
            price_trend = "震荡"

        # 第二层：详细数据说明（用于深入分析）
        vol_ratio_label = ""
        if vol_ratio > 1.5:
            vol_ratio_label = f"非常活跃（比平时高{((vol_ratio-1)*100):.0f}%）"
        elif vol_ratio > 1.2:
            vol_ratio_label = f"活跃（比平时高{((vol_ratio-1)*100):.0f}%）"
        elif vol_ratio < 0.8:
            vol_ratio_label = f"清淡（比平时低{((1-vol_ratio)*100):.0f}%）"
        else:
            vol_ratio_label = "正常范围"

        # 第三层：多维度数据（用于前端可视化）
        up_down_ratio = up_vol / down_vol if down_vol > 0 else 0
        volume_status = "favorable" if up_vol > down_vol else "unfavorable"
        strength_score = min(100, int(vol_ratio * 30))  # 简单强度评分
        
        result["checks"]["volume"] = {
            # 基础判断
            "passed": bool(vol_ok),
            "description": short_desc,
            
            # 交互体验增强字段
            "signal": signal,          # danger/strong/good/weak/normal
            "icon": icon,              # 表情图标
            "color": color,            # 颜色编码
            "advice": advice,          # 操作建议
            
            # 详细数据层（用于前端展示）
            "indicators": {
                "vol_ratio": round(float(vol_ratio), 2),
                "vol_ratio_label": vol_ratio_label,
                "today_vol": round(today_vol, 0),
                "vol_ma5": round(float(vol_ma5), 0),
                "up_vol_avg": round(float(up_vol), 0),
                "down_vol_avg": round(float(down_vol), 0),
                "up_down_ratio": round(float(up_down_ratio), 2),
                "pct_chg": round(pct_1d, 2),
                "volume_status": volume_status,
                "strength_score": strength_score,
            },
            
            # 新增解释性字段（P0优化）
            "explanation": explanation,
            "action_level": action_level,
            "suggested_action": suggested_action,
            "vol_interpretation": vol_interpretation,
            "price_trend": price_trend,
            
            # 详细描述（自然语言）
            "detail": f"{icon} 今日成交量{round(today_vol/10000, 1)}万手，比5日均量{('高' if vol_ratio > 1 else '低')}{abs((vol_ratio-1)*100):.0f}%。上涨日平均成交{round(up_vol/10000, 1)}万手，是下跌日{round(up_down_ratio, 1)}倍。{advice}"
        }

        # 综合结论
        all_passed = result["checks"]["high_low"]["passed"] and result["checks"]["ma"]["passed"] and result["checks"]["volume"]["passed"]
        passed_count = sum(1 for c in result["checks"].values() if c["passed"])

        result["summary"] = {
            "all_passed": all_passed,
            "passed_count": passed_count,
            "total_count": 3,
            "recommendation": "符合进场条件" if all_passed else ("谨慎买入" if passed_count >= 2 else "建议观望"),
            "suggestion": "三看全部满足，可以买入" if all_passed else (
                "满足2项，可小仓位试探" if passed_count >= 2 else "条件不满足，建议等待"
            )
        }

        # [P1-6] 买入价格锚点 — 复用已有K线数据，不增加额外请求
        try:
            from helpers import calc_buy_price_anchors
            _rp_for_buy = realtime_price if realtime_price > 0 else float(latest["close"])
            result["buy_price_anchors"] = calc_buy_price_anchors(df, latest_realtime=_rp_for_buy)
        except Exception as _bpa_e:
            print(f"[WARN] 买入价格锚点计算失败: {_bpa_e}")
            result["buy_price_anchors"] = None

        return jsonify(result)

    except Exception as e:
        import traceback
        print(f"[ERROR] 三看检查失败: {e}")
        print(traceback.format_exc())
        return jsonify({"error": f"检查失败: {str(e)}"}), 500


@position_bp.route("/api/export")
@login_required
def export_portfolio():
    """导出持仓数据"""
    user_id = get_current_user_id()
    data = load_portfolio_compat(user_id)
    return jsonify(data)


@position_bp.route("/api/import", methods=["POST"])
@login_required
def import_portfolio():
    """导入持仓数据"""
    user_id = get_current_user_id()
    body = request.get_json()
    if not body or "positions" not in body:
        return jsonify({"error": "无效的导入数据"}), 400

    from database import save_portfolio_compat
    mode = body.get("import_mode", "replace")

    if mode == "replace":
        # 先删除所有现有持仓
        existing = get_all_positions(user_id)
        for p in existing:
            db_delete_position(p["id"], user_id)

    data = load_portfolio_compat(user_id)

    if mode == "merge":
        existing_codes = {p["ts_code"] for p in data.get("positions", [])}
        for p in body["positions"]:
            if p["ts_code"] not in existing_codes:
                data["positions"].append(p)
    else:
        data["positions"] = body["positions"]

    save_portfolio_compat(data, user_id)
    return jsonify({"message": f"导入成功（{mode}模式），共{len(data['positions'])}条持仓"})


@position_bp.route("/api/summary")
@login_required
def get_summary():
    """获取汇总数据"""
    user_id = get_current_user_id()
    positions = _build_position_list(user_id)
    enriched = enrich_positions(positions)
    capital = get_capital(user_id)

    total_market_value = round(sum(p["market_value"] for p in enriched), 2)
    total_cost = round(sum(p["avg_cost"] * p["total_volume"] for p in enriched), 2)
    total_profit = round(sum(p["profit"] for p in enriched), 2)
    today_profit = round(sum(p["today_profit"] for p in enriched), 2)
    cash = capital.get("cash", 0)

    summary = {
        "total_market_value": total_market_value,
        "total_cost": total_cost,
        "total_profit": total_profit,
        "total_profit_pct": 0,
        "today_profit": today_profit,
        "is_trade_time": is_trade_time(),
        "last_update": datetime.now().strftime("%H:%M:%S"),
        "cash": cash,
        "initial_capital": capital.get("initial", 0),
        "total_assets": total_market_value + cash,
    }

    initial_capital = capital.get("initial", 0)
    if initial_capital > 0:
        summary["total_profit_pct"] = round((summary["total_assets"] - initial_capital) / initial_capital * 100, 2)
    elif total_cost > 0:
        summary["total_profit_pct"] = round(total_profit / total_cost * 100, 2)

    return jsonify(summary)
