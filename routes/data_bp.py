# -*- coding: utf-8 -*-
"""
routes/data_bp.py - 市场数据接口路由（P1/P2）
包含：
  P1: 财务数据(fina_indicator/income/forecast)、回购(repurchase)、
      涨停(limit_list/limit_step/limit_cpt)、板块资金流(moneyflow_ind_ths/dc)
  P2: 北向资金(moneyflow_hsgt/hsgt_top10)、龙虎榜(top_list/top_inst)、
      筹码分布(cyq_chips)、神奇九转(stk_nineturn)、机构调研(stk_surv)、
      板块轮动(sw_daily/dc_daily)
"""

from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request

from auth import login_required
from helpers import (
    pro,
    get_fina_indicator, get_income_trend, get_forecast, get_repurchase,
    get_limit_list, get_limit_step, get_limit_cpt_list,
    get_moneyflow_ind_ths, get_moneyflow_ind_dc, get_ths_members,
    get_st_stocks, get_suspended_stocks,
)

data_bp = Blueprint("data", __name__)


# ============================================================
# P1: 财务数据
# ============================================================

@data_bp.route("/api/stock/<ts_code>/finance")
@login_required
def stock_finance(ts_code):
    """获取个股财务数据（指标 + 营收趋势 + 业绩预告 + 回购）"""
    try:
        fina = get_fina_indicator(ts_code)
        income = get_income_trend(ts_code)
        forecast = get_forecast(ts_code)
        repurchase = get_repurchase(ts_code)

        return jsonify({
            "ts_code": ts_code,
            "fina_indicator": fina,
            "income_trend": income,
            "forecast": forecast,
            "repurchase": repurchase,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@data_bp.route("/api/stock/<ts_code>/fina-indicator")
@login_required
def stock_fina_indicator(ts_code):
    """获取个股财务指标（ROE/毛利率/净利率等）"""
    try:
        periods = request.args.get("periods", 4, type=int)
        data = get_fina_indicator(ts_code, periods=periods)
        return jsonify({"ts_code": ts_code, "data": data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@data_bp.route("/api/stock/<ts_code>/income")
@login_required
def stock_income(ts_code):
    """获取个股营收趋势"""
    try:
        periods = request.args.get("periods", 8, type=int)
        data = get_income_trend(ts_code, periods=periods)
        return jsonify({"ts_code": ts_code, "data": data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@data_bp.route("/api/stock/<ts_code>/forecast")
@login_required
def stock_forecast(ts_code):
    """获取个股业绩预告"""
    try:
        data = get_forecast(ts_code)
        return jsonify({"ts_code": ts_code, "data": data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@data_bp.route("/api/stock/<ts_code>/repurchase")
@login_required
def stock_repurchase(ts_code):
    """获取个股回购记录"""
    try:
        data = get_repurchase(ts_code)
        return jsonify({"ts_code": ts_code, "data": data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# P1: 涨停数据
# ============================================================

@data_bp.route("/api/market/limit-list")
@login_required
def market_limit_list():
    """获取涨停股票列表"""
    try:
        trade_date = request.args.get("trade_date")
        data = get_limit_list(trade_date)
        return jsonify({
            "trade_date": trade_date or datetime.now().strftime("%Y%m%d"),
            "count": len(data),
            "data": data,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@data_bp.route("/api/market/limit-step")
@login_required
def market_limit_step():
    """获取连板梯队"""
    try:
        trade_date = request.args.get("trade_date")
        data = get_limit_step(trade_date)
        # 按连板天数分组
        step_groups = {}
        for item in data:
            days = item.get("days", 1)
            if days not in step_groups:
                step_groups[days] = []
            step_groups[days].append(item)
        return jsonify({
            "trade_date": trade_date or datetime.now().strftime("%Y%m%d"),
            "total": len(data),
            "step_groups": step_groups,
            "data": data,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@data_bp.route("/api/market/limit-cpt")
@login_required
def market_limit_cpt():
    """获取涨停概念分布"""
    try:
        trade_date = request.args.get("trade_date")
        data = get_limit_cpt_list(trade_date)
        # 统计每个概念的涨停家数
        concept_count = {}
        for item in data:
            concepts = item.get("concept", "")
            if concepts:
                for c in str(concepts).split("+"):
                    c = c.strip()
                    if c:
                        concept_count[c] = concept_count.get(c, 0) + 1
        # 按涨停家数降序排列
        sorted_concepts = sorted(concept_count.items(), key=lambda x: x[1], reverse=True)
        return jsonify({
            "trade_date": trade_date or datetime.now().strftime("%Y%m%d"),
            "concept_rank": [{"concept": k, "count": v} for k, v in sorted_concepts],
            "data": data,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# P1: 板块资金流
# ============================================================

@data_bp.route("/api/market/sector-flow")
@login_required
def market_sector_flow():
    """获取板块资金流（同花顺 + 东财双口径）"""
    try:
        trade_date = request.args.get("trade_date")
        ths_data = get_moneyflow_ind_ths(trade_date)
        dc_data = get_moneyflow_ind_dc(trade_date)
        return jsonify({
            "trade_date": trade_date or datetime.now().strftime("%Y%m%d"),
            "ths": ths_data[:30],  # TOP 30
            "dc": dc_data[:30],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@data_bp.route("/api/ths-members/<ts_code>")
@login_required
def ths_members(ts_code):
    """获取同花顺概念板块成分股"""
    try:
        data = get_ths_members(ts_code)
        return jsonify({"ts_code": ts_code, "count": len(data), "data": data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# P0: ST / 停牌查询
# ============================================================

@data_bp.route("/api/market/st-stocks")
@login_required
def market_st_stocks():
    """获取当前ST股票列表"""
    try:
        trade_date = request.args.get("trade_date")
        stocks = get_st_stocks(trade_date)
        return jsonify({
            "trade_date": trade_date or datetime.now().strftime("%Y%m%d"),
            "count": len(stocks),
            "stocks": list(stocks),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@data_bp.route("/api/market/suspended-stocks")
@login_required
def market_suspended_stocks():
    """获取当前停牌股票列表"""
    try:
        trade_date = request.args.get("trade_date")
        stocks = get_suspended_stocks(trade_date)
        return jsonify({
            "trade_date": trade_date or datetime.now().strftime("%Y%m%d"),
            "count": len(stocks),
            "stocks": list(stocks),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# P2: 北向资金
# ============================================================

@data_bp.route("/api/market/northbound-flow")
@login_required
def market_northbound_flow():
    """获取北向资金流向（沪深港通）"""
    try:
        trade_date = request.args.get("trade_date")
        days = request.args.get("days", 30, type=int)

        if trade_date:
            end_date = trade_date
        else:
            end_date = datetime.now().strftime("%Y%m%d")

        start_date = (datetime.strptime(end_date, "%Y%m%d") -
                      timedelta(days=days + 10)).strftime("%Y%m%d")

        import pandas as pd
        
        df = pro.moneyflow_hsgt(
            start_date=start_date, end_date=end_date,
            fields="trade_date,ggt_ss,ggt_sz,hgt,sgt,north_money,south_money"
        )
        if df.empty:
            return jsonify({"trade_date": end_date, "data": []})

        df = df.sort_values("trade_date", ascending=False).head(days)
        # 转换为 float（接口返回的可能是字符串）
        for col in ["ggt_ss", "ggt_sz", "hgt", "sgt", "north_money", "south_money"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        result = df.to_dict("records")

        latest = result[0] if result else {}
        return jsonify({
            "latest": latest,
            "data": result,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@data_bp.route("/api/stock/<ts_code>/northbound-top10")
@login_required
def stock_northbound_top10(ts_code):
    """获取个股北向资金买卖TOP10"""
    try:
        trade_date = request.args.get("trade_date")
        days = request.args.get("days", 10, type=int)

        if trade_date:
            end_date = trade_date
        else:
            end_date = datetime.now().strftime("%Y%m%d")

        start_date = (datetime.strptime(end_date, "%Y%m%d") -
                      timedelta(days=days + 5)).strftime("%Y%m%d")

        df = pro.hsgt_top10(
            ts_code=ts_code, start_date=start_date, end_date=end_date,
            fields="ts_code,trade_date,name,close,change,rank,market_type,amount,net_amount,buy,sell"
        )
        if df.empty:
            return jsonify({"ts_code": ts_code, "data": []})

        df = df.sort_values("trade_date", ascending=False)
        result = df.to_dict("records")
        return jsonify({"ts_code": ts_code, "data": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# P2: 龙虎榜
# ============================================================

@data_bp.route("/api/market/top-list")
@login_required
def market_top_list():
    """获取龙虎榜数据"""
    try:
        trade_date = request.args.get("trade_date")
        if not trade_date:
            trade_date = datetime.now().strftime("%Y%m%d")

        df = pro.top_list(
            trade_date=trade_date,
            fields="ts_code,trade_date,name,close,pct_chg,turnover_rate,amount,"
                   "l_sell,l_buy,net_amount,net_rate,amount_rate,declare_date"
        )
        if df.empty:
            return jsonify({"trade_date": trade_date, "count": 0, "data": []})

        result = df.to_dict("records")
        return jsonify({"trade_date": trade_date, "count": len(result), "data": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@data_bp.route("/api/market/top-inst")
@login_required
def market_top_inst():
    """获取龙虎榜机构席位详情"""
    try:
        trade_date = request.args.get("trade_date")
        ts_code = request.args.get("ts_code")
        if not trade_date:
            trade_date = datetime.now().strftime("%Y%m%d")

        params = {"trade_date": trade_date}
        if ts_code:
            params["ts_code"] = ts_code

        df = pro.top_inst(
            **params,
            fields="ts_code,trade_date,name,exalter,buy_amount,sell_amount,net_amount,"
                   "buy_rate,sell_rate,net_rate,amount_rate"
        )
        if df.empty:
            return jsonify({"trade_date": trade_date, "count": 0, "data": []})

        result = df.to_dict("records")
        return jsonify({"trade_date": trade_date, "count": len(result), "data": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# P2: 筹码分布
# ============================================================

@data_bp.route("/api/stock/<ts_code>/chips")
@login_required
def stock_chips(ts_code):
    """获取个股筹码分布"""
    try:
        trade_date = request.args.get("trade_date")
        if not trade_date:
            trade_date = datetime.now().strftime("%Y%m%d")

        df = pro.cyq_chips(
            ts_code=ts_code, trade_date=trade_date,
            fields="ts_code,trade_date,price,percent,change,hl_rate"
        )
        if df.empty:
            return jsonify({"ts_code": ts_code, "data": []})

        df = df.sort_values("price")
        result = df.to_dict("records")

        # 计算套牢盘/获利盘
        total_pct = sum(item.get("percent", 0) for item in result)
        profit_pct = sum(item.get("percent", 0) for item in result if item.get("change", 0) > 0)
        loss_pct = total_pct - profit_pct

        return jsonify({
            "ts_code": ts_code,
            "trade_date": trade_date,
            "profit_pct": round(profit_pct, 2),
            "loss_pct": round(loss_pct, 2),
            "data": result,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# P2: 神奇九转
# ============================================================

@data_bp.route("/api/stock/<ts_code>/nineturn")
@login_required
def stock_nineturn(ts_code):
    """获取个股神奇九转（TD序列）"""
    try:
        df = pro.stk_nineturn(
            ts_code=ts_code,
            fields="ts_code,trade_date,close,close_1,close_2,close_3,close_4,"
                   "close_5,close_6,close_7,close_8,td_type,td_count"
        )
        if df.empty:
            return jsonify({"ts_code": ts_code, "data": []})

        df = df.sort_values("trade_date", ascending=False).head(30)
        result = df.to_dict("records")
        return jsonify({"ts_code": ts_code, "data": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# P2: 机构调研
# ============================================================

@data_bp.route("/api/stock/<ts_code>/research")
@login_required
def stock_research(ts_code):
    """获取个股机构调研记录"""
    try:
        df = pro.stk_surv(
            ts_code=ts_code,
            fields="ts_code,name,surv_date,fund_visitors,rece_place,rece_mode,rece_org,org_type,comp_rece"
        )
        if df.empty:
            return jsonify({"ts_code": ts_code, "data": []})

        df = df.sort_values("surv_date", ascending=False).head(20)
        result = df.to_dict("records")
        return jsonify({"ts_code": ts_code, "data": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# P2: 板块轮动（申万 + 东财行业指数）
# ============================================================

@data_bp.route("/api/market/sector-rotation")
@login_required
def market_sector_rotation():
    """获取板块轮动数据（申万行业 + 东财行业）"""
    try:
        trade_date = request.args.get("trade_date")
        if not trade_date:
            trade_date = datetime.now().strftime("%Y%m%d")

        # 申万行业
        sw_data = []
        try:
            df_sw = pro.sw_daily(
                trade_date=trade_date,
                fields="ts_code,trade_date,close,pct_chg,amount,vol"
            )
            if not df_sw.empty:
                df_sw = df_sw.sort_values("pct_chg", ascending=False)
                sw_data = df_sw.head(50).to_dict("records")
        except Exception as e:
            print(f"[WARN] 申万行业数据获取失败: {e}")

        # 东财行业
        dc_data = []
        try:
            df_dc = pro.dc_daily(
                trade_date=trade_date,
                fields="ts_code,trade_date,close,pct_chg,amount,vol"
            )
            if not df_dc.empty:
                df_dc = df_dc.sort_values("pct_chg", ascending=False)
                dc_data = df_dc.head(50).to_dict("records")
        except Exception as e:
            print(f"[WARN] 东财行业数据获取失败: {e}")

        return jsonify({
            "trade_date": trade_date,
            "shenwan": sw_data,
            "eastmoney": dc_data,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
