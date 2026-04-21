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

def _clean_for_json(obj):
    """递归清理对象，确保其可JSON序列化（处理numpy类型）"""
    try:
        import numpy as np
        if isinstance(obj, (np.bool_, bool)):
            return bool(obj)
        if isinstance(obj, (np.integer, int)):
            return int(obj)
        if isinstance(obj, (np.floating, float)):
            return float(obj)
        if isinstance(obj, (np.ndarray, list)):
            return [_clean_for_json(item) for item in obj]
        if isinstance(obj, dict):
            return {key: _clean_for_json(value) for key, value in obj.items()}
    except ImportError:
        pass  # numpy not installed
    return obj

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
            c["result"] = None  # 清除旧结果
        
        import traceback
        try:
            from screener import run_strategy, save_screen_result
            # 执行策略
            result = run_strategy(strategy=strategy, top_n=top_n, silent=True, force=force, params=params)
            
            # 保存结果到数据库
            try:
                save_screen_result(result)
            except Exception as save_err:
                print(f"[WARN] 保存结果失败（不影响主流程）: {save_err}")
            
            # 更新缓存
            with _screen_lock:
                c["result"] = result
                c["last_run"] = datetime.now().strftime("%H:%M:%S")
                print(f"[INFO] {strategy} 策略执行完成，找到 {len(result.get('results', []))} 只股票")
                
        except Exception as e:
            error_msg = str(e)
            error_trace = traceback.format_exc()
            print(f"[ERROR] 选股执行失败: {error_msg}")
            print(f"[ERROR] 异常堆栈: {error_trace}")
            
            # 保存错误信息到缓存
            with _screen_lock:
                c["result"] = {"error": error_msg, "trace": error_trace[:500]}
        finally:
            # 确保 running 状态被清除
            with _screen_lock:
                c["running"] = False
                print(f"[INFO] {strategy} 策略线程结束，running=False")

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
    
    # 如果缓存中没有结果，尝试从历史记录中加载最新的
    if not result:
        try:
            from screener import load_screen_history
            history_all = load_screen_history(days=30)
            # 找到该策略的最新历史记录
            strategy_history = [h for h in history_all if h.get("strategy") == strategy]
            if strategy_history:
                latest = strategy_history[0]  # 按时间倒序，第一个是最新的
                
                # 从top5字段构建results数组（历史记录中没有完整的results）
                top5_results = latest.get("top5", [])
                # 将top5格式转换为完整的results格式
                results = []
                for r in top5_results:
                    # 基本字段转换
                    result_item = {
                        "ts_code": r.get("ts_code", ""),
                        "name": r.get("name", ""),
                        "price": r.get("price", 0),
                        "pct_chg": r.get("pct_chg", 0),
                        "total_score": r.get("total_score", 0),
                        # 添加一些默认字段，避免前端报错
                        "max_board_name": "",
                        "max_board_pct": 0,
                        "trend_score": 0,
                        "board_score": 0,
                        "money_score": 0,
                        "bonus_score": 0,
                        "bonus_tags": [],
                        "match_audit": {},
                    }
                    results.append(result_item)
                
                # 构建与run_strategy返回格式兼容的结果对象
                result = {
                    "results": results,
                    "stats": latest.get("stats", {}),
                    "market": {
                        "status": latest.get("market_status", "unknown"),
                        "description": latest.get("market_desc", ""),
                    },
                    "run_time": latest.get("run_time", 0),
                    "screen_date": latest.get("date", ""),
                    "screen_time": latest.get("time", ""),
                    "strategy": latest.get("strategy", strategy),
                    "strategy_meta": {},  # 历史记录中没有strategy_meta
                }
                # 同时更新缓存，避免下次重复查询
                with _screen_lock:
                    cache["result"] = result
                    cache["last_run"] = latest.get("time", "")
                print(f"[INFO] 从历史记录加载 {strategy} 结果: {len(results)} 只股票 (从top5恢复)")
            else:
                # 没有历史记录
                return jsonify({"running": False, "results": [], "market": None, "stats": None, "history": [], "strategy": strategy})
        except Exception as e:
            print(f"[WARN] 从历史记录加载失败: {e}")
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

    response_data = {
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
    }
    # 清理数据确保JSON序列化
    response_data = _clean_for_json(response_data)
    return jsonify(response_data)


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


@screen_bp.route("/api/screen/all-summaries")
@login_required
def all_strategy_summaries():
    """获取所有策略的最新执行摘要（供策略卡片内嵌展示）"""
    global _screen_result_cache
    summaries = {}
    running_list = []

    for strategy_key in ["trend_break", "sector_leader", "oversold_bounce"]:
        cache = _screen_result_cache.get(strategy_key, {})
        result = cache.get("result") if cache else None
        is_running = cache.get("running", False) if cache else False
        last_run_time = cache.get("last_run") if cache else None

        if is_running:
            running_list.append(strategy_key)

        # 有有效结果时提取摘要
        if result and "error" not in result:
            stats = result.get("stats") or {}
            results = result.get("results") or []
            top_score = max((r.get("total_score", 0) for r in results), default=0) if results else 0
            market_info = result.get("market") or {}

            summaries[strategy_key] = {
                "count": len(results),
                "last_run": result.get("screen_time") or last_run_time or "-",
                "duration": round(result.get("run_time", 0), 1),
                "top_score": int(top_score),
                "market_status": market_info.get("status", ""),
                "has_result": True,
            }
        else:
            # 尝试从历史记录中找最近一次（缓存可能被清空但DB还有）
            try:
                from screener import load_screen_history
                history = load_screen_history(days=30)
                strategy_history = [h for h in history if h.get("strategy") == strategy_key]
                if strategy_history:
                    latest = strategy_history[0]
                    summaries[strategy_key] = {
                        "count": latest.get("final_count", 0),
                        "last_run": latest.get("screen_time", latest.get("date", "-")),
                        "duration": round(latest.get("run_time", 0), 1),
                        "top_score": 0,
                        "market_status": "",
                        "has_result": True,
                        "from_history": True,
                    }
                else:
                    summaries[strategy_key] = None
            except Exception:
                summaries[strategy_key] = None

    return jsonify({
        "summaries": summaries,
        "running": running_list,
    })
