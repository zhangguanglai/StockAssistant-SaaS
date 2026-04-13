# -*- coding: utf-8 -*-
"""
routes/watch_bp.py - 观察池、策略效果跟踪、股票对比相关路由
包含：观察池 CRUD + 跟踪日报 + 策略效果跟踪 + 股票对比
"""

from datetime import datetime
from flask import Blueprint, jsonify, request

from auth import login_required, get_current_user_id
from database import (
    get_watch_list_items, add_watch_item, update_watch_item,
    remove_watch_item, clear_watch_list as db_clear_watch,
)
from helpers import (
    pro, get_realtime_quote, get_realtime_quotes_eastmoney,
    get_stock_info, get_index_quotes,
)

watch_bp = Blueprint("watch", __name__)

STRATEGY_NAMES = {
    "trend_break": "趋势突破",
    "sector_leader": "板块龙头",
    "oversold_bounce": "超跌反弹",
}


# ============================================================
# 观察池 API
# ============================================================

@watch_bp.route("/api/watch-list", methods=["GET"])
@login_required
def get_watch_list():
    """获取观察池列表，附带最新行情
    
    支持查询参数：
      ?strategy=trend_break  — 按来源策略筛选
    """
    user_id = get_current_user_id()
    filter_strategy = request.args.get("strategy", "").strip()

    items = get_watch_list_items(user_id)
    if filter_strategy:
        items = [i for i in items if i.get("add_strategy") == filter_strategy]

    if not items:
        return jsonify({"items": [], "count": 0})

    enriched = []
    for item in items:
        ts_code = item["ts_code"]
        try:
            quote = get_realtime_quote(ts_code)
            if quote:
                item["current_price"] = quote.get("price", item.get("add_price", 0))
                item["current_pct_chg"] = quote.get("pct_chg", 0)
                add_price = item.get("add_price", 0)
                if add_price and add_price > 0:
                    chg = (quote.get("price", 0) - add_price) / add_price * 100
                    item["track_chg_pct"] = round(chg, 2)
        except Exception:
            pass
        enriched.append(item)

    # 排序：添加日期(新→旧) → 评分(高→低) → 跟踪收益(高→低)
    # 日期用字符串降序（YYYY-MM-DD格式天然可比较）
    enriched.sort(key=lambda x: (
        x.get("add_date") or "",          # 日期升序
        x.get("add_score", 0) or 0,       # 分数升序  
        x.get("track_chg_pct", 0) or 0,   # 收益升序
    ), reverse=True)
    
    return jsonify({"items": enriched, "count": len(enriched)})


@watch_bp.route("/api/watch-list", methods=["POST"])
@login_required
def add_to_watch_list():
    """将股票加入观察池"""
    user_id = get_current_user_id()
    body = request.get_json()
    if not body or not body.get("ts_code"):
        return jsonify({"error": "缺少 ts_code"}), 400

    ts_code = body["ts_code"]

    existing = get_watch_list_items(user_id)
    if any(item["ts_code"] == ts_code for item in existing):
        return jsonify({"message": "已在观察池中", "count": len(existing)})

    add_watch_item(
        ts_code=ts_code,
        name=body.get("name", ""),
        add_price=body.get("price", 0),
        add_strategy=body.get("strategy", ""),
        add_score=body.get("total_score", 0),
        tag=body.get("tag", ""),
        note=body.get("note", ""),
        user_id=user_id,
    )

    count = len(get_watch_list_items(user_id))
    return jsonify({"message": f"{body.get('name', ts_code)} 已加入观察池", "count": count})


@watch_bp.route("/api/watch-list/batch", methods=["POST"])
@login_required
def batch_add_watch_list():
    """批量将选股结果加入观察池"""
    user_id = get_current_user_id()
    body = request.get_json()
    stocks = body.get("stocks", []) if body else []
    if not stocks:
        return jsonify({"error": "缺少 stocks 数组"}), 400

    existing = {item["ts_code"] for item in get_watch_list_items(user_id)}
    added = 0

    for s in stocks:
        ts_code = s.get("ts_code", "")
        if ts_code and ts_code not in existing:
            add_watch_item(
                ts_code=ts_code,
                name=s.get("name", ""),
                add_price=s.get("price", 0),
                add_strategy=s.get("strategy", ""),
                add_score=s.get("total_score", 0),
                tag=s.get("tag", ""),
                user_id=user_id,
            )
            existing.add(ts_code)
            added += 1

    count = len(get_watch_list_items(user_id))
    return jsonify({"message": f"已加入 {added} 只到观察池", "added": added, "count": count})


@watch_bp.route("/api/watch-list/<ts_code>", methods=["DELETE"])
@login_required
def remove_from_watch_list(ts_code):
    """从观察池移除"""
    user_id = get_current_user_id()
    before = len(get_watch_list_items(user_id))
    remove_watch_item(ts_code, user_id)
    after = len(get_watch_list_items(user_id))
    removed = before - after
    return jsonify({"message": f"已移除 {removed} 只", "count": after})


@watch_bp.route("/api/watch-list/<ts_code>", methods=["PUT"])
@login_required
def update_watch_list_item(ts_code):
    """更新观察池单条记录"""
    user_id = get_current_user_id()
    body = request.get_json() or {}
    updates = {}
    if "tag" in body:
        updates["tag"] = body["tag"]
    if "note" in body:
        updates["note"] = body["note"]
    if "group" in body:
        updates["tag"] = body["group"]

    if not updates:
        return jsonify({"error": "无更新字段"}), 400

    update_watch_item(ts_code, user_id, **updates)
    count = len(get_watch_list_items(user_id))
    return jsonify({"message": f"已更新 {ts_code}", "count": count})


@watch_bp.route("/api/watch-list/clear", methods=["DELETE"])
@login_required
def clear_watch_list():
    """清空观察池"""
    user_id = get_current_user_id()
    db_clear_watch(user_id)
    return jsonify({"message": "观察池已清空"})


@watch_bp.route("/api/watch-list/report")
@login_required
def watch_list_report():
    """生成选股跟踪日报"""
    user_id = get_current_user_id()
    items = get_watch_list_items(user_id)
    if not items:
        return jsonify({"report": None, "message": "观察池为空"})

    report_items = []
    profit_count = 0
    loss_count = 0
    total_chg = 0
    valid_count = 0

    for item in items:
        ts_code = item["ts_code"]
        add_price = item.get("add_price", 0)
        if not add_price or add_price <= 0:
            report_items.append({
                "ts_code": ts_code,
                "name": item.get("name", ""),
                "add_price": add_price,
                "current_price": 0,
                "chg_pct": 0,
                "status": "无基准价"
            })
            continue

        try:
            quote = get_realtime_quote(ts_code)
            current = quote.get("price", 0) if quote else 0
            chg = (current - add_price) / add_price * 100 if current > 0 else 0
        except Exception:
            current = 0
            chg = 0

        total_chg += chg
        valid_count += 1
        if chg > 0:
            profit_count += 1
        elif chg < 0:
            loss_count += 1

        status = "盈利" if chg > 0 else ("亏损" if chg < 0 else "持平")
        report_items.append({
            "ts_code": ts_code,
            "name": item.get("name", ""),
            "add_price": add_price,
            "current_price": current,
            "chg_pct": round(chg, 2),
            "status": status,
            "add_date": item.get("add_date", ""),
            "add_strategy": item.get("add_strategy", ""),
        })

    report_items.sort(key=lambda x: x.get("chg_pct", 0) or 0, reverse=True)

    avg_chg = round(total_chg / valid_count, 2) if valid_count > 0 else 0
    wr = round(profit_count / valid_count * 100, 1) if valid_count > 0 else 0

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total": len(items),
        "valid": valid_count,
        "profit_count": profit_count,
        "loss_count": loss_count,
        "avg_chg_pct": avg_chg,
        "win_rate": wr,
        "items": report_items,
    }

    summary = f"观察池{len(items)}只，有效{valid_count}只，平均收益{avg_chg:+.2f}%，胜率{wr}%"

    return jsonify({"report": report, "summary": summary})


# ============================================================
# 自选股策略建议
# ============================================================

@watch_bp.route("/api/watch-list/<ts_code>/advice")
@login_required
def get_watch_advice(ts_code):
    """自选股操作策略建议（基于技术分析，使用实时数据）"""
    from helpers import pro, get_realtime_quotes
    user_id = get_current_user_id()
    items = get_watch_list_items(user_id)
    item = next((i for i in items if i["ts_code"] == ts_code), None)
    if not item:
        return jsonify({"error": "自选股不存在"}), 404

    add_price = item.get("add_price", 0)

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

    # 用加入自选的参考价计算盈亏
    ref_price = add_price if add_price > 0 else ma20
    profit_pct = (latest - ref_price) / ref_price * 100 if ref_price > 0 else 0

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

    # 趋势分
    score += trend_score * 15
    if trend_score >= 1:
        reasons.append(f"趋势{trend}，均线向上（+{trend_score*15}）")
    elif trend_score <= -1:
        reasons.append(f"趋势{trend}，均线向下（{trend_score*15}）")
    else:
        reasons.append(f"趋势{trend}，方向不明（+0）")

    # 盈亏分（相对参考价）
    if profit_pct > 15:
        score += 20
        reasons.append(f"相对参考价浮盈{profit_pct:.1f}%，表现强势（+20）")
    elif profit_pct > 5:
        score += 15
        reasons.append(f"相对参考价浮盈{profit_pct:.1f}%，趋势健康（+15）")
    elif profit_pct > 0:
        score += 10
        reasons.append(f"相对参考价浮盈{profit_pct:.1f}%，值得期待（+10）")
    elif profit_pct > -5:
        score += 5
        reasons.append(f"相对参考价浮亏{abs(profit_pct):.1f}%，回调正常（+5）")
    elif profit_pct > -15:
        score -= 10
        reasons.append(f"相对参考价浮亏{abs(profit_pct):.1f}%，需关注（-10）")
    else:
        score -= 20
        reasons.append(f"相对参考价浮亏{abs(profit_pct):.1f}%，持续走弱（-20）")

    # 量价配合
    if vol_ratio > 1.5 and pct_5d > 0:
        score += 15
        reasons.append(f"放量上涨（量比{vol_ratio:.1f}），动能增强（+15）")
    elif vol_ratio > 1.2 and pct_5d > 0:
        score += 8
        reasons.append(f"温和放量（量比{vol_ratio:.1f}），量价配合（+8）")
    elif vol_ratio < 0.6 and pct_5d < 0:
        score -= 15
        reasons.append(f"缩量下跌（量比{vol_ratio:.1f}），抛压减轻（-15）")
    elif vol_ratio > 1.5 and pct_5d < 0:
        score -= 10
        reasons.append(f"放量下跌（量比{vol_ratio:.1f}），主力出货（-10）")
    elif vol_ratio < 0.7:
        score -= 3
        reasons.append(f"缩量（量比{vol_ratio:.1f}），交投清淡（-3）")
    else:
        reasons.append(f"成交量正常（量比{vol_ratio:.1f}）（+0）")

    # 距离压力/支撑位
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

    # 连涨/连跌
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

    # 建议映射
    if score >= 30:
        action, action_color = "关注", "primary"
    elif score >= 10:
        action, action_color = "持有", "primary"
    elif score >= -10:
        action, action_color = "观望", "warning"
    elif score >= -25:
        action, action_color = "减仓", "danger"
    else:
        action, action_color = "放弃", "danger"

    action_icon_map = {"关注": "🔔", "持有": "📌", "观望": "👀", "减仓": "📉", "放弃": "🗑️"}
    action_desc_map = {
        "关注": "值得跟踪关注",
        "持有": "当前趋势良好可继续观察",
        "观望": "趋势不明建议观望",
        "减仓": "建议减仓规避风险",
        "放弃": "建议从自选移除",
    }

    stop_loss_price = round(low_30d * 1.01, 2)
    stop_loss_note = f"跌破 ¥{stop_loss_price}（30日低点）建议止损或移除"
    take_profit_price = round(high_30d * 0.95, 2)
    take_profit_note = f"涨至 ¥{take_profit_price}（30日高点95%）可考虑止盈"

    return jsonify({
        "ts_code": ts_code,
        "score": score,
        "action": action,
        "action_icon": action_icon_map.get(action, "📌"),
        "action_desc": action_desc_map.get(action, ""),
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
            "avg_cost": ref_price,
            "profit_pct": round(profit_pct, 2),
            "position_pct": 0,
        },
        "profit": {
            "profit_pct": round(profit_pct, 2),
            "profit_amount": 0,
            "market_value": 0,
            "avg_cost": ref_price,
            "hold_volume": 0,
        },
        "suggestions": {
            "add": {"volume": 0, "price": round(latest * 0.98, 2), "note": f"参考买入价 ¥{round(latest * 0.98, 2)}（回调2%入场）"},
            "reduce": {"volume": 0, "price": latest, "note": f"当前价 ¥{latest}，建议参考止损/止盈位"},
            "stop_loss": {"price": stop_loss_price, "note": stop_loss_note},
            "take_profit": {"price": take_profit_price, "note": take_profit_note},
        },
        "_source": data_source,
        "_time": data_time,
    })


@watch_bp.route("/api/watch-list/<ts_code>/strategy-advice")
@login_required
def get_watch_strategy_advice(ts_code):
    """
    观察池股票基于选股策略的买入建议
    显示：1) 为什么被策略筛选得分高  2) 买入策略建议
    与持仓股票的操作建议区分，专注于策略逻辑而非持仓盈亏
    """
    from helpers import pro, get_realtime_quotes
    user_id = get_current_user_id()
    items = get_watch_list_items(user_id)
    item = next((i for i in items if i["ts_code"] == ts_code), None)
    if not item:
        return jsonify({"error": "自选股不存在"}), 404

    add_price = item.get("add_price", 0)
    add_strategy = item.get("add_strategy", "")
    add_score = item.get("add_score", 0)
    add_date = item.get("add_date", "")

    # 获取K线数据
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

    # 获取实时行情
    realtime_data = get_realtime_quotes([ts_code])
    quote = realtime_data.get(ts_code, {})
    realtime_price = quote.get("price", 0)
    if realtime_price > 0:
        closes[-1] = realtime_price
        latest = realtime_price

    data_source = quote.get("_source", "tushare")
    data_time = quote.get("_time", "")

    # 计算技术指标
    ma5 = round(sum(closes[-5:]) / 5, 3) if len(closes) >= 5 else closes[-1]
    ma20 = round(sum(closes[-20:]) / 20, 3) if len(closes) >= 20 else closes[-1]
    ma60 = round(sum(closes[-60:]) / 60, 3) if len(closes) >= 60 else closes[-1]
    vol_ma5 = round(sum(volumes[-5:]) / 5, 0) if len(volumes) >= 5 else volumes[-1]
    vol_ratio = volumes[-1] / vol_ma5 if vol_ma5 > 0 else 1.0

    # 计算涨跌幅
    pct_5d = (latest - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 else 0
    pct_20d = (latest - closes[-21]) / closes[-21] * 100 if len(closes) >= 21 else 0
    high_20d = max(highs[-20:]) if len(highs) >= 20 else max(highs)
    low_20d = min(lows[-20:]) if len(lows) >= 20 else min(lows)
    high_60d = max(highs[-60:]) if len(highs) >= 60 else max(highs)
    low_60d = min(lows[-60:]) if len(lows) >= 60 else min(lows)

    # 根据策略类型生成策略依据和买入建议
    strategy_reasons = []
    buy_advice = {}
    stop_loss = {}
    take_profit = {}

    if add_strategy == "oversold_bounce":
        # 超跌反弹策略分析
        decline_20d = abs(pct_20d) if pct_20d < 0 else 0
        dist_to_low = (latest - low_20d) / low_20d * 100
        rebound_sign = latest > ma5 and ma5 > closes[-6]  # 价格站上MA5且MA5向上

        strategy_reasons.append(f"近20日跌幅 {decline_20d:.1f}%，符合超跌条件（>20%）")
        strategy_reasons.append(f"当前价格 ¥{latest}，距20日低点 ¥{low_20d} 反弹 {dist_to_low:.1f}%")
        if rebound_sign:
            strategy_reasons.append("出现止跌信号：价格站上MA5，短期趋势转暖")
        strategy_reasons.append(f"策略评分：{add_score}分（满分100）")

        # 买入建议
        entry_price = round(low_20d * 1.02, 2)  # 低点上方2%
        buy_advice = {
            "type": "分批建仓",
            "entry_price_1": round(low_20d * 1.01, 2),
            "entry_price_2": round(low_20d * 1.03, 2),
            "position": "20-30%",
            "note": f"超跌反弹策略：在 ¥{round(low_20d * 1.01, 2)}~¥{round(low_20d * 1.03, 2)} 区间分批建仓，博取反弹收益"
        }
        stop_loss = {
            "price": round(low_20d * 0.97, 2),
            "note": f"跌破20日低点 ¥{low_20d} 的97%（¥{round(low_20d * 0.97, 2)}）止损，防止深跌"
        }
        take_profit = {
            "price_1": round(high_20d * 0.9, 2),
            "price_2": round(ma20, 2),
            "note": f"反弹至20日高点90%（¥{round(high_20d * 0.9, 2)}）或MA20（¥{ma20}）附近止盈"
        }

    elif add_strategy == "sector_leader":
        # 板块龙头策略分析
        strategy_reasons.append(f"板块涨幅领先，当日涨幅 {pct_5d:.1f}%")
        strategy_reasons.append(f"换手率充足（量比{vol_ratio:.1f}），资金关注度高")
        strategy_reasons.append(f"主力净流入，板块效应明显")
        strategy_reasons.append(f"策略评分：{add_score}分（满分100）")

        # 买入建议
        buy_advice = {
            "type": "追涨打板",
            "entry_price_1": round(latest * 0.995, 2),
            "entry_price_2": round(latest * 1.02, 2),
            "position": "10-20%",
            "note": f"板块龙头策略：在 ¥{round(latest * 0.995, 2)}~¥{round(latest * 1.02, 2)} 区间追涨，博取连板收益"
        }
        stop_loss = {
            "price": round(latest * 0.95, 2),
            "note": f"跌破买入价5%（¥{round(latest * 0.95, 2)}）止损，防止追高被套"
        }
        take_profit = {
            "price_1": round(latest * 1.08, 2),
            "price_2": round(latest * 1.15, 2),
            "note": f"涨幅8%（¥{round(latest * 1.08, 2)}）减仓一半，涨幅15%（¥{round(latest * 1.15, 2)}）清仓"
        }

    elif add_strategy == "trend_break":
        # 趋势突破策略分析
        strategy_reasons.append(f"MA20（¥{ma20}）> MA60（¥{ma60}），中期趋势向上")
        strategy_reasons.append(f"价格站稳MA20上方，趋势确认")
        strategy_reasons.append(f"量比{vol_ratio:.1f}，放量突破")
        strategy_reasons.append(f"策略评分：{add_score}分（满分100）")

        # 买入建议
        buy_advice = {
            "type": "趋势跟随",
            "entry_price_1": round(ma20 * 1.01, 2),
            "entry_price_2": round(ma20 * 1.03, 2),
            "position": "20-30%",
            "note": f"趋势突破策略：在MA20上方 ¥{round(ma20 * 1.01, 2)}~¥{round(ma20 * 1.03, 2)} 区间建仓，跟随趋势"
        }
        stop_loss = {
            "price": round(ma20 * 0.97, 2),
            "note": f"跌破MA20的97%（¥{round(ma20 * 0.97, 2)}）止损，趋势破坏即离场"
        }
        take_profit = {
            "price_1": round(high_60d, 2),
            "price_2": round(high_60d * 1.1, 2),
            "note": f"涨至60日高点 ¥{high_60d} 减仓，突破后看高至 ¥{round(high_60d * 1.1, 2)}"
        }

    else:
        # 通用策略分析
        strategy_reasons.append(f"策略评分：{add_score}分")
        strategy_reasons.append(f"加入时间：{add_date}")
        strategy_reasons.append(f"加入价格：¥{add_price}")

        buy_advice = {
            "type": "参考建仓",
            "entry_price_1": round(latest * 0.98, 2),
            "entry_price_2": round(latest * 1.0, 2),
            "position": "10-20%",
            "note": f"参考买入区间 ¥{round(latest * 0.98, 2)}~¥{latest}"
        }
        stop_loss = {
            "price": round(low_20d * 0.98, 2),
            "note": f"跌破20日低点98%止损"
        }
        take_profit = {
            "price_1": round(high_20d * 0.95, 2),
            "price_2": None,
            "note": f"涨至20日高点95%止盈"
        }

    return jsonify({
        "ts_code": ts_code,
        "name": item.get("name", ""),
        "strategy": add_strategy,
        "strategy_name": STRATEGY_NAMES.get(add_strategy, "未知策略"),
        "strategy_score": add_score,
        "add_price": add_price,
        "add_date": add_date,
        "current_price": latest,
        "current_change": round((latest - add_price) / add_price * 100, 2) if add_price > 0 else 0,
        "indicators": {
            "ma5": ma5,
            "ma20": ma20,
            "ma60": ma60,
            "vol_ratio": round(vol_ratio, 2),
            "pct_5d": round(pct_5d, 2),
            "pct_20d": round(pct_20d, 2),
            "high_20d": high_20d,
            "low_20d": low_20d,
        },
        "strategy_reasons": strategy_reasons,
        "buy_advice": buy_advice,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "_source": data_source,
        "_time": data_time,
    })


# ============================================================
# 策略效果跟踪
# ============================================================

@watch_bp.route("/api/strategy-performance")
@login_required
def strategy_performance():
    """策略效果跟踪：按策略维度统计胜率、收益、评分区间"""
    user_id = get_current_user_id()
    items = get_watch_list_items(user_id)
    if not items:
        return jsonify({
            "strategies": {},
            "score_distribution": [],
            "total_tracked": 0,
            "overall_win_rate": 0,
            "overall_avg_chg": 0,
        })

    all_codes = [item["ts_code"] for item in items]
    try:
        all_quotes = get_realtime_quotes_eastmoney(all_codes)
    except Exception:
        all_quotes = {}

    if not all_quotes:
        try:
            from screener import get_recent_trade_dates
            recent = get_recent_trade_dates(1)
            if recent:
                df = pro.daily(trade_date=recent[0], fields="ts_code,close")
                if df is not None and not df.empty:
                    for _, row in df.iterrows():
                        tc = row["ts_code"]
                        if tc in all_codes:
                            all_quotes[tc] = {"price": float(row["close"])}
        except Exception:
            pass

    strategy_stats = {}
    score_groups = {}

    def get_score_range(score):
        if score >= 80:
            return "80-100分"
        elif score >= 60:
            return "60-79分"
        elif score >= 40:
            return "40-59分"
        else:
            return "0-39分"

    total_valid = 0
    total_profit = 0
    total_chg_sum = 0

    for item in items:
        ts_code = item["ts_code"]
        add_price = item.get("add_price", 0)
        strategy = item.get("add_strategy", "") or "未标记"
        score = item.get("add_score", 0) or 0

        quote = all_quotes.get(ts_code, {})
        current = quote.get("price", 0) or 0

        if not add_price or add_price <= 0 or current <= 0:
            chg_pct = 0
            valid = False
        else:
            chg_pct = round((current - add_price) / add_price * 100, 2)
            valid = True

        if not valid:
            continue

        total_valid += 1
        total_chg_sum += chg_pct

        record = {
            "ts_code": ts_code,
            "name": item.get("name", ""),
            "add_price": add_price,
            "current_price": current,
            "chg_pct": chg_pct,
            "score": score,
            "add_date": item.get("add_date", ""),
        }

        if strategy not in strategy_stats:
            strategy_stats[strategy] = {
                "strategy": strategy,
                "display_name": STRATEGY_NAMES.get(strategy, strategy),
                "count": 0, "profit": 0, "loss": 0, "flat": 0,
                "total_chg": 0, "items": [],
            }
        ss = strategy_stats[strategy]
        ss["count"] += 1
        ss["total_chg"] += chg_pct
        ss["items"].append(record)
        if chg_pct > 0:
            ss["profit"] += 1
            total_profit += 1
        elif chg_pct < 0:
            ss["loss"] += 1
        else:
            ss["flat"] += 1

        sr = get_score_range(score)
        if sr not in score_groups:
            score_groups[sr] = {"range": sr, "count": 0, "profit": 0, "loss": 0, "total_chg": 0}
        sg = score_groups[sr]
        sg["count"] += 1
        sg["total_chg"] += chg_pct
        if chg_pct > 0:
            sg["profit"] += 1
        elif chg_pct < 0:
            sg["loss"] += 1

    for ss in strategy_stats.values():
        ss["win_rate"] = round(ss["profit"] / ss["count"] * 100, 1) if ss["count"] > 0 else 0
        ss["avg_chg"] = round(ss["total_chg"] / ss["count"], 2) if ss["count"] > 0 else 0
        ss["max_profit"] = max((r["chg_pct"] for r in ss["items"]), default=0)
        ss["max_loss"] = min((r["chg_pct"] for r in ss["items"]), default=0)
        ss["items"].sort(key=lambda x: x["chg_pct"], reverse=True)
        ss.pop("total_chg", None)

    for sg in score_groups.values():
        sg["win_rate"] = round(sg["profit"] / sg["count"] * 100, 1) if sg["count"] > 0 else 0
        sg["avg_chg"] = round(sg["total_chg"] / sg["count"], 2) if sg["count"] > 0 else 0
        sg.pop("total_chg", None)

    overall_wr = round(total_profit / total_valid * 100, 1) if total_valid > 0 else 0
    overall_avg = round(total_chg_sum / total_valid, 2) if total_valid > 0 else 0

    score_dist = sorted(score_groups.values(), key=lambda x: x["range"], reverse=True)

    return jsonify({
        "strategies": strategy_stats,
        "score_distribution": score_dist,
        "total_tracked": total_valid,
        "overall_win_rate": overall_wr,
        "overall_avg_chg": overall_avg,
    })


# ============================================================
# 股票对比
# ============================================================

@watch_bp.route("/api/compare")
@login_required
def compare_stocks():
    """多只股票对比"""
    codes_str = request.args.get("codes", "")
    days = request.args.get("days", 60, type=int)
    codes = [c.strip() for c in codes_str.split(",") if c.strip()]

    if len(codes) < 2:
        return jsonify({"error": "请至少选择2只股票进行对比"})
    if len(codes) > 4:
        return jsonify({"error": "最多支持4只股票对比"})
    if days < 10:
        days = 10
    if days > 120:
        days = 120

    colors = ["#ef4444", "#3b82f6", "#22c55e", "#f59e0b"]
    result = {"stocks": [], "dates": [], "error": None}

    all_kline_data = {}
    end_date = datetime.now().strftime("%Y%m%d")
    for code in codes:
        try:
            df = pro.daily(ts_code=code, end_date=end_date, limit=days)
            if df.empty:
                continue
            df = df.sort_values("trade_date")
            all_kline_data[code] = df
        except Exception:
            continue

    if len(all_kline_data) < 2:
        return jsonify({"error": "获取K线数据失败，请检查股票代码"})

    all_dates = sorted(set().union(*[set(df["trade_date"].tolist()) for df in all_kline_data.values()]))
    dates_fmt = [d[4:6] + "/" + d[6:8] for d in all_dates]

    for i, code in enumerate(codes):
        df = all_kline_data.get(code)
        if df is None:
            continue

        info = get_stock_info(code)
        stock_info = {
            "ts_code": code,
            "name": info.get("name", code),
            "industry": info.get("industry", ""),
            "color": colors[i % len(colors)],
        }

        try:
            quotes = get_realtime_quotes_eastmoney([code])
            if code in quotes:
                q = quotes[code]
                stock_info["price"] = q.get("price", 0)
                stock_info["pct_chg"] = q.get("pct_chg", 0)
                stock_info["market_cap"] = q.get("total_mv", 0)
                stock_info["vol"] = q.get("vol", 0)
                stock_info["amount"] = q.get("amount", 0)
                stock_info["turnover_rate"] = q.get("turnover_rate", 0)
        except Exception:
            stock_info["price"] = 0
            stock_info["pct_chg"] = 0

        try:
            latest_trade = df.iloc[-1]
            trade_date = latest_trade["trade_date"]
            basic = pro.daily_basic(ts_code=code, trade_date=trade_date,
                                     fields="pe,pb,ps,total_mv,circ_mv,turnover_rate")
            if not basic.empty:
                row = basic.iloc[0]
                stock_info["pe"] = round(row.get("pe", 0), 2) if row.get("pe") else None
                stock_info["pb"] = round(row.get("pb", 0), 2) if row.get("pb") else None
                stock_info["ps"] = round(row.get("ps", 0), 2) if row.get("ps") else None
                stock_info["circ_mv"] = round(row.get("circ_mv", 0) / 10000, 2)
        except Exception:
            pass

        closes_map = dict(zip(df["trade_date"], df["close"].tolist()))
        close_series = []
        norm_series = []
        first_close = None
        for d in all_dates:
            c = closes_map.get(d)
            if c is not None:
                if first_close is None:
                    first_close = c
                close_series.append(round(c, 2))
                norm_series.append(round(c / first_close * 100, 2) if first_close else 100)
            else:
                close_series.append(None)
                norm_series.append(None)

        close_list = [c for c in close_series if c is not None]
        if len(close_list) >= 5:
            stock_info["pct_5d"] = round((close_list[-1] - close_list[-5]) / close_list[-5] * 100, 2)
        if len(close_list) >= 20:
            stock_info["pct_20d"] = round((close_list[-1] - close_list[-20]) / close_list[-20] * 100, 2)
        stock_info["pct_total"] = round((close_list[-1] - close_list[0]) / close_list[0] * 100, 2) if len(close_list) >= 2 else 0

        stock_info["closes"] = close_series
        stock_info["norm_closes"] = norm_series
        result["stocks"].append(stock_info)

    result["dates"] = dates_fmt
    return jsonify(result)
