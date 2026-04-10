# -*- coding: utf-8 -*-
"""
routes/position_bp.py - 持仓、资金、交易、行情、复盘、回测、预警相关路由
这是业务量最大的模块，包含核心的交易管理功能

v3.1.1: 修复兼容层写入不删除的问题，改用 database.py 原生 CRUD
"""

from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request
import pandas as pd

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
    get_stock_info, get_tushare_daily, get_realtime_quotes_eastmoney,
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
    """获取大盘指数行情（东方财富 → 新浪 → Tushare 降级）+ 高低点结构分析"""
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
    """计算持仓的支撑位/压力位"""
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
    sold_vol = sum(int(t["sell_volume"]) for t in sells)
    hold_vol = total_vol - sold_vol
    avg_cost = total_cost / total_vol if total_vol > 0 else None

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

    recent_30_lows = lows[-30:] if len(lows) >= 30 else lows
    s1 = round(min(recent_30_lows), 2)
    s1_desc = f"近30日最低价 ¥{s1}，价格底部有成交密集区支撑，跌破视为趋势转弱"

    recent_20_closes = closes[-20:] if len(closes) >= 20 else closes
    ma20 = round(sum(recent_20_closes) / len(recent_20_closes), 2)
    s2 = ma20
    s2_desc = f"MA20均线 ¥{s2}，中期趋势线，多头行情下是动态支撑，跌破均线警戒"

    s3 = round(avg_cost * 0.95, 2) if avg_cost else None
    s3_desc = f"成本价-5% ¥{s3}，持仓成本的安全边际，跌破即亏损扩大建议止损" if s3 else None

    recent_30_highs = highs[-30:] if len(highs) >= 30 else highs
    r1 = round(max(recent_30_highs), 2)
    r1_desc = f"近30日最高价 ¥{r1}，近期高点形成阻力，突破则确认上涨动能"

    r2 = round(ma20 * 1.10, 2)
    r2_desc = f"MA20+10% ¥{r2}，基于中期均线的合理止盈区，偏离均线过大易回调"

    latest_close = closes[-1] if closes else None
    r3 = round(latest_close * 1.10, 2) if latest_close else None
    r3_desc = f"现价+10% ¥{r3}，涨停板极限位，短线操作的最激进目标" if r3 else None

    cost_ref = {
        "avg_cost": round(avg_cost, 3) if avg_cost else None,
        "cost_minus5": round(avg_cost * 0.95, 2) if avg_cost else None,
        "cost_minus8": round(avg_cost * 0.92, 2) if avg_cost else None,
        "cost_plus10": round(avg_cost * 1.10, 2) if avg_cost else None,
        "cost_plus20": round(avg_cost * 1.20, 2) if avg_cost else None,
    }

    supports = [
        {"label": "S1 近30日最低", "price": s1, "desc": s1_desc, "type": "strong"},
        {"label": "S2 MA20均线", "price": s2, "desc": s2_desc, "type": "medium"},
    ]
    if s3:
        supports.append({"label": "S3 成本-5%", "price": s3, "desc": s3_desc, "type": "soft"})

    resistances = [
        {"label": "R1 近30日最高", "price": r1, "desc": r1_desc, "type": "strong"},
        {"label": "R2 MA20+10%", "price": r2, "desc": r2_desc, "type": "medium"},
    ]
    if r3:
        resistances.append({"label": "R3 涨停位", "price": r3, "desc": r3_desc, "type": "soft"})

    return jsonify({
        "ts_code": ts_code,
        "supports": supports,
        "resistances": resistances,
        "cost_ref": cost_ref,
        "ma20": ma20,
        "latest_close": latest_close,
        "calc_basis": f"基于近{len(df)}个交易日K线数据计算",
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
    if realtime_price > 0:
        closes[-1] = realtime_price
        latest = realtime_price
    
    # 记录数据源
    data_source = quote.get("_source", "tushare")
    data_time = quote.get("_time", "")

    ma5 = round(sum(closes[-5:]) / 5, 3) if len(closes) >= 5 else closes[-1]
    ma20 = round(sum(closes[-20:]) / 20, 3) if len(closes) >= 20 else closes[-1]
    vol_ma5 = round(sum(volumes[-5:]) / 5, 0) if len(volumes) >= 5 else volumes[-1]

    consec_up, consec_down = 0, 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] > closes[i - 1]:
            consec_up += 1
        else:
            break
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] < closes[i - 1]:
            consec_down += 1
        else:
            break

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

    # 1. 趋势分
    score += trend_score * 15
    if trend_score >= 1:
        reasons.append(f"趋势{trend}，均线向上（+{trend_score*15}）")
    elif trend_score <= -1:
        reasons.append(f"趋势{trend}，均线向下（{trend_score*15}）")
    else:
        reasons.append(f"趋势{trend}，方向不明（+0）")

    # 2. 盈亏分
    if profit_pct > 20:
        score += 5
        reasons.append(f"浮盈{profit_pct:.1f}%，获利丰厚但注意回调风险（+5）")
    elif profit_pct > 10:
        score += 15
        reasons.append(f"浮盈{profit_pct:.1f}%，趋势健康可持有（+15）")
    elif profit_pct > 3:
        score += 20
        reasons.append(f"浮盈{profit_pct:.1f}%，稳健盈利（+20）")
    elif profit_pct > -3:
        score += 5
        reasons.append(f"浮盈{profit_pct:.1f}%，微盈/持平观望（+5）")
    elif profit_pct > -8:
        score -= 10
        reasons.append(f"浮亏{abs(profit_pct):.1f}%，轻度亏损关注支撑（-10）")
    elif profit_pct > -15:
        score -= 20
        reasons.append(f"浮亏{abs(profit_pct):.1f}%，中度亏损建议止损（-20）")
    else:
        score -= 25
        reasons.append(f"浮亏{abs(profit_pct):.1f}%，严重亏损强烈建议止损（-25）")

    # 3. 量价配合
    if vol_ratio > 1.5 and pct_5d > 0:
        score += 15
        reasons.append(f"放量上涨（量比{vol_ratio:.1f}），动能增强（+15）")
    elif vol_ratio > 1.2 and pct_5d > 0:
        score += 8
        reasons.append(f"温和放量（量比{vol_ratio:.1f}），量价配合（+8）")
    elif vol_ratio < 0.6 and pct_5d < 0:
        score -= 15
        reasons.append(f"缩量下跌（量比{vol_ratio:.1f}），抛压减轻但趋势弱（-15）")
    elif vol_ratio > 1.5 and pct_5d < 0:
        score -= 10
        reasons.append(f"放量下跌（量比{vol_ratio:.1f}），主力出货信号（-10）")
    elif vol_ratio < 0.7:
        score -= 3
        reasons.append(f"缩量（量比{vol_ratio:.1f}），交投清淡（-3）")
    else:
        reasons.append(f"成交量正常（量比{vol_ratio:.1f}）（+0）")

    # 4. 距离压力/支撑位
    dist_to_high = (high_30d - latest) / latest * 100
    dist_to_low = (latest - low_30d) / latest * 100
    if dist_to_low < 3:
        score += 10
        reasons.append(f"贴近30日低点（距底部{dist_to_low:.1f}%），安全边际高（+10）")
    elif dist_to_low < 8:
        score += 5
        reasons.append(f"距30日低点{dist_to_low:.1f}%，偏低位置（+5）")
    elif dist_to_high < 3:
        score -= 10
        reasons.append(f"贴近30日高点（距顶部{dist_to_high:.1f}%），阻力区域（-10）")
    elif dist_to_high < 8:
        score -= 5
        reasons.append(f"距30日高点{dist_to_high:.1f}%，偏高位置（-5）")
    else:
        reasons.append(f"距30日高点{dist_to_high:.1f}%，距低点{dist_to_low:.1f}%，中性位置（+0）")

    # 5. 连涨/连跌
    if consec_up >= 5:
        score -= 10
        reasons.append(f"连涨{consec_up}天，短线过热注意回调（-10）")
    elif consec_up >= 3:
        score += 5
        reasons.append(f"连涨{consec_up}天，短线强势（+5）")
    elif consec_down >= 5:
        score += 10
        reasons.append(f"连跌{consec_down}天，超跌可能反弹（+10）")
    elif consec_down >= 3:
        score -= 5
        reasons.append(f"连跌{consec_down}天，趋势偏弱（-5）")

    # 【新增】6. 高低点结构分析
    hl_structure = {"structure": "unknown", "score": 0, "signal": "无法分析", "recent_highs": [], "recent_lows": []}
    try:
        from helpers import analyze_hl_structure
        import pandas as pd
        
        df_hl = pd.DataFrame({
            'high': highs,
            'low': lows,
            'close': closes,
        })
        hl_result = analyze_hl_structure(df_hl, n=5)
        hl_structure = hl_result
        
        # 根据结构调整评分
        if hl_result["structure"] == "uptrend":
            hl_bonus = 10
            score += hl_bonus
            reasons.append(f"高低点结构健康（{hl_result['signal']}）+{hl_bonus}")
        elif hl_result["structure"] == "downtrend":
            hl_penalty = -15
            score += hl_penalty
            reasons.append(f"高低点结构恶化（{hl_result['signal']}）{hl_penalty}")
        elif hl_result["high_trend"] == "up":
            hl_bonus = 5
            score += hl_bonus
            reasons.append(f"高点突破，趋势转强 +{hl_bonus}")
        elif hl_result["low_trend"] == "down":
            hl_penalty = -8
            score += hl_penalty
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

        ma5 = [None] * (len(closes) - 5) + [round(sum(closes[max(0, i-4):i+1]) / min(5, i+1), 3) for i in range(4, len(closes))]
        if len(ma5) < len(dates):
            ma5 = [None] * (len(dates) - len(ma5)) + ma5
        ma20 = [None] * (len(closes) - 20) + [round(sum(closes[max(0, i-19):i+1]) / min(20, i+1), 3) for i in range(19, len(closes))]
        if len(ma20) < len(dates):
            ma20 = [None] * (len(dates) - len(ma20)) + ma20

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
        quotes = get_realtime_quotes_eastmoney([ts_code])
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
    ts_code = request.args.get("ts_code", "").strip()
    if not ts_code:
        return jsonify({"error": "请提供股票代码"}), 400

    try:
        # 获取最近60天的日线数据
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")

        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df.empty or len(df) < 20:
            return jsonify({"error": "数据不足，无法分析"}), 400

        df = df.sort_values("trade_date").reset_index(drop=True)

        # 计算均线
        df["ma5"] = df["close"].rolling(window=5).mean()
        df["ma10"] = df["close"].rolling(window=10).mean()
        df["ma20"] = df["close"].rolling(window=20).mean()

        # 获取最新数据
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else None

        result = {
            "ts_code": ts_code,
            "name": get_stock_info(ts_code).get("name", ""),
            "trade_date": latest["trade_date"],
            "close": round(float(latest["close"]), 3),
            "checks": {}
        }

        # ========== 一看：高低点抬高 ==========
        # 找最近5个波段的低点和高点（使用3日窗口确认极值，更稳健）
        lows = []
        highs = []
        window = 2  # 前后各2天，共5日窗口
        for i in range(window, len(df) - window):
            # 低点：比前后window天都低
            is_low = all(df.iloc[i]["low"] < df.iloc[i-j]["low"] for j in range(1, window+1)) and \
                     all(df.iloc[i]["low"] < df.iloc[i+j]["low"] for j in range(1, window+1))
            if is_low:
                lows.append((i, float(df.iloc[i]["low"])))
            # 高点：比前后window天都高
            is_high = all(df.iloc[i]["high"] > df.iloc[i-j]["high"] for j in range(1, window+1)) and \
                      all(df.iloc[i]["high"] > df.iloc[i+j]["high"] for j in range(1, window+1))
            if is_high:
                highs.append((i, float(df.iloc[i]["high"])))

        # 取最近3个低点和高点
        recent_lows = lows[-3:] if len(lows) >= 3 else lows
        recent_highs = highs[-3:] if len(highs) >= 3 else highs

        low_increasing = len(recent_lows) >= 2 and all(
            recent_lows[i][1] > recent_lows[i-1][1] for i in range(1, len(recent_lows))
        )
        high_increasing = len(recent_highs) >= 2 and all(
            recent_highs[i][1] > recent_highs[i-1][1] for i in range(1, len(recent_highs))
        )

        result["checks"]["high_low"] = {
            "passed": bool(low_increasing and high_increasing),
            "low_increasing": bool(low_increasing),
            "high_increasing": bool(high_increasing),
            "recent_lows": [round(x[1], 3) for x in recent_lows],
            "recent_highs": [round(x[1], 3) for x in recent_highs],
            "description": "高低点抬高" if (low_increasing and high_increasing) else "高低点未抬高",
            "detail": f"最近{len(recent_lows)}个低点: {[round(x[1], 2) for x in recent_lows]} | 最近{len(recent_highs)}个高点: {[round(x[1], 2) for x in recent_highs]}"
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

        vol_ok = vol_ratio > 1.0 and up_vol > down_vol

        result["checks"]["volume"] = {
            "passed": bool(vol_ok),
            "vol_ratio": round(float(vol_ratio), 2),
            "today_vol": round(today_vol, 0),
            "vol_ma5": round(float(vol_ma5), 0),
            "up_vol_avg": round(float(up_vol), 0),
            "down_vol_avg": round(float(down_vol), 0),
            "description": "量价配合良好" if vol_ok else "量价配合不佳",
            "vol_ratio_detail": round(float(vol_ratio), 2),
            "today_vol": round(today_vol, 0),
            "vol_ma5": round(float(vol_ma5), 0),
            "up_down_ratio": round(float(up_vol / down_vol), 2) if down_vol > 0 else 0,
            "up_vol_avg": round(float(up_vol), 0),
            "down_vol_avg": round(float(down_vol), 0),
            "detail": f"量比={vol_ratio:.2f}（当日/5日均），上涨日均量/下跌日均量={up_vol/down_vol:.2f}（最近10个交易日）"
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
