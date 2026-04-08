# -*- coding: utf-8 -*-
"""
routes/screen_bp.py - 选股引擎相关路由
包含：执行选股、策略列表、参数获取、状态查询、结果获取、历史记录
"""

import threading
from datetime import datetime
from flask import Blueprint, jsonify, request

from auth import login_required
from helpers import pro, get_realtime_quotes

screen_bp = Blueprint("screen", __name__)

# 选股结果缓存
_screen_result_cache = {
    "trend_break": {"result": None, "running": False, "last_run": None},
    "sector_leader": {"result": None, "running": False, "last_run": None},
    "oversold_bounce": {"result": None, "running": False, "last_run": None},
}
_screen_current_strategy = "trend_break"
_screen_lock = threading.Lock()


@screen_bp.route("/api/screen/run", methods=["POST"])
@login_required
def run_screen():
    """执行选股筛选（异步）"""
    global _screen_result_cache, _screen_current_strategy

    top_n = request.json.get("top_n", 20) if request.json else 20
    top_n = min(max(int(top_n), 5), 50)
    force = bool(request.json.get("force", False)) if request.json else False
    strategy = request.json.get("strategy", "trend_break") if request.json else "trend_break"
    params = request.json.get("params", None) if request.json else None

    valid_strategies = ["trend_break", "sector_leader", "oversold_bounce"]
    if strategy not in valid_strategies:
        strategy = "trend_break"

    cache = _screen_result_cache[strategy]
    with _screen_lock:
        if cache["running"]:
            return jsonify({"error": "选股正在进行中，请稍候..."}), 429

    def _run():
        global _screen_result_cache, _screen_current_strategy
        _screen_current_strategy = strategy
        c = _screen_result_cache[strategy]
        with _screen_lock:
            c["running"] = True
        try:
            from screener import run_strategy, save_screen_result
            result = run_strategy(strategy=strategy, top_n=top_n, silent=True, force=force, params=params)
            save_screen_result(result)
            with _screen_lock:
                c["result"] = result
                c["last_run"] = datetime.now().strftime("%H:%M:%S")
        except Exception as e:
            print(f"[ERROR] 选股执行失败: {e}")
            with _screen_lock:
                c["running"] = False
                c["result"] = {"error": str(e)}
        finally:
            with _screen_lock:
                c["running"] = False

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    from screener import STRATEGY_META
    meta = STRATEGY_META.get(strategy, {})
    name = meta.get("name", strategy)
    msg = f"{name}已启动（强制模式，忽略大盘风控），预计需要3-5分钟" if force else f"{name}已启动，预计需要3-5分钟"
    return jsonify({"message": msg, "running": True, "strategy": strategy})


@screen_bp.route("/api/screen/strategies")
def screen_strategies():
    """获取可用策略列表 + 推荐策略"""
    try:
        from screener import STRATEGY_META, check_market_environment, get_market_sentiment
        recommended = "trend_break"
        reason = "默认推荐"
        market = None
        try:
            market = check_market_environment()
            sentiment = get_market_sentiment()
            limit_up_count = sentiment.get("limit_up_count", 0)
            status = market.get("status", "unknown")

            if status == "上升":
                recommended = "trend_break"
                reason = "大盘上升，趋势突破策略最稳健"
            elif status == "震荡":
                if limit_up_count > 50:
                    recommended = "sector_leader"
                    reason = f"震荡市+涨停{limit_up_count}家，市场情绪亢奋，板块龙头策略更优"
                else:
                    recommended = "trend_break"
                    reason = "大盘震荡，趋势突破策略适合捕捉结构性机会"
            elif status == "下降":
                ma20_slope = market.get("ma20_slope", 0)
                if ma20_slope > -0.005:
                    recommended = "oversold_bounce"
                    reason = "大盘下降趋缓，超跌反弹策略可博取反弹"
                else:
                    recommended = "oversold_bounce"
                    reason = "大盘持续下降，超跌反弹策略逆向布局"
        except Exception as e:
            reason = f"环境判断失败({str(e)[:30]})，默认趋势突破"

        return jsonify({
            "strategies": STRATEGY_META,
            "recommended": recommended,
            "reason": reason,
            "market": market,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@screen_bp.route("/api/screen/params")
@login_required
def screen_params():
    """获取指定策略的可调参数列表"""
    strategy = request.args.get("strategy", "")
    try:
        from screener import STRATEGY_PARAMS, get_strategy_params
        if strategy:
            params = get_strategy_params(strategy)
        else:
            params = STRATEGY_PARAMS
        return jsonify({"params": params})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@screen_bp.route("/api/screen/status")
@login_required
def screen_status():
    """查询选股状态"""
    strategy = request.args.get("strategy", _screen_current_strategy)
    cache = _screen_result_cache.get(strategy, _screen_result_cache["trend_break"])
    with _screen_lock:
        result = cache["result"]
        running = cache["running"]
        last_run = cache["last_run"]

    return jsonify({
        "running": running,
        "last_run": last_run,
        "has_result": result is not None and "error" not in result,
        "error": result.get("error") if result and "error" in result else None,
        "strategy": strategy,
    })


@screen_bp.route("/api/screen/result")
@login_required
def screen_result():
    """获取选股结果"""
    strategy = request.args.get("strategy", _screen_current_strategy)
    cache = _screen_result_cache.get(strategy, _screen_result_cache["trend_break"])
    with _screen_lock:
        result = cache["result"]
        running = cache["running"]

    if running:
        return jsonify({"running": True, "message": "选股进行中...", "strategy": strategy})
    if not result:
        return jsonify({"running": False, "results": [], "market": None, "stats": None, "history": [], "strategy": strategy})

    if "error" in result:
        return jsonify({"running": False, "error": result["error"], "strategy": strategy})

    try:
        from screener import load_screen_history
        history = load_screen_history(days=7)
    except Exception:
        history = []

    # 用实时行情更新选股结果中的现价和涨跌幅
    results = result.get("results", [])
    if results:
        ts_codes = [r["ts_code"] for r in results if "ts_code" in r]
        if ts_codes:
            try:
                realtime_quotes = get_realtime_quotes(ts_codes)
                for r in results:
                    ts_code = r.get("ts_code")
                    if ts_code and ts_code in realtime_quotes:
                        quote = realtime_quotes[ts_code]
                        realtime_price = quote.get("price", 0)
                        realtime_pct = quote.get("pct_chg", 0)
                        if realtime_price > 0:
                            r["price"] = realtime_price
                        if realtime_pct != 0:
                            r["pct_chg"] = round(realtime_pct, 2)
                        r["_source"] = quote.get("_source", "tushare")
                        r["_time"] = quote.get("_time", "")
            except Exception as e:
                print(f"[WARN] 选股结果实时行情更新失败: {e}")

    return jsonify({
        "running": False,
        "market": result.get("market"),
        "results": results,
        "stats": result.get("stats"),
        "run_time": result.get("run_time"),
        "screen_date": result.get("screen_date"),
        "screen_time": result.get("screen_time"),
        "strategy": result.get("strategy", strategy),
        "strategy_meta": result.get("strategy_meta"),
        "history": history,
    })


@screen_bp.route("/api/screen/history")
@login_required
def screen_history():
    """获取历史选股记录"""
    days = request.args.get("days", 7, type=int)
    try:
        from screener import load_screen_history
        history = load_screen_history(days=days)
    except Exception as e:
        history = []
    return jsonify({"history": history})
