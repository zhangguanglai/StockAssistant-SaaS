# -*- coding: utf-8 -*-
"""Tushare API 诊断测试脚本"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from helpers import _get_pro, get_realtime_quotes, get_index_quotes
from datetime import datetime, timedelta

pro = _get_pro()

def test(name, fn):
    print(f"\n=== {name} ===")
    try:
        result = fn()
        print(f"  OK: {result}")
    except Exception as e:
        import traceback
        print(f"  ERROR: {e}")
        traceback.print_exc()

today = datetime.now().strftime("%Y%m%d")
yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
last_trade = "20260413"  # 上个已知交易日

print(f"今天: {today}, 上一交易日: {last_trade}")

# 1. daily
test("daily(000001.SZ)", lambda: pro.daily(ts_code="000001.SZ", start_date=last_trade, end_date=today).shape)

# 2. daily_basic
test("daily_basic(last_trade)", lambda: pro.daily_basic(ts_code="000001.SZ", trade_date=last_trade).shape)

# 3. sw_daily - 今天
test("sw_daily(今天)", lambda: pro.sw_daily(trade_date=today, fields="ts_code,name,pct_chg").shape)

# 4. sw_daily - 上一交易日
test("sw_daily(last_trade)", lambda: pro.sw_daily(trade_date=last_trade, fields="ts_code,name,pct_chg").shape)

# 5. sw_daily - 用代码
test("sw_daily(index_code)", lambda: pro.sw_daily(index_code="801010.SI", start_date=last_trade, end_date=today).shape)

# 6. moneyflow
test("moneyflow(last_trade)", lambda: pro.moneyflow(ts_code="000001.SZ", trade_date=last_trade).shape)

# 7. moneyflow_hsgt
test("moneyflow_hsgt(last_trade)", lambda: pro.moneyflow_hsgt(trade_date=last_trade).shape)

# 8. limit_list
test("limit_list(today)", lambda: pro.limit_list(trade_date=today).shape)
test("limit_list(last_trade)", lambda: pro.limit_list(trade_date=last_trade).shape)

# 9. stk_nineturn
test("stk_nineturn(000001.SZ)", lambda: pro.stk_nineturn(ts_code="000001.SZ", start_date=last_trade).shape)

# 10. cyq_chips
test("cyq_chips(000001.SZ)", lambda: pro.cyq_chips(ts_code="000001.SZ", trade_date=last_trade).shape)

# 11. top_list
test("top_list(last_trade)", lambda: pro.top_list(trade_date=last_trade).shape)

# 12. fina_indicator
test("fina_indicator(000001.SZ)", lambda: pro.fina_indicator(ts_code="000001.SZ", fields="ts_code,end_date,roe,netprofit_margin").shape)

# 13. 实时行情
test("get_realtime_quotes", lambda: {k: v.get("_source") for k, v in get_realtime_quotes(["000001.SZ", "600519.SH"]).items()})

# 14. 指数行情
test("get_index_quotes", lambda: {k: v.get("_source") for k, v in get_index_quotes().items()})

print("\n=== 测试完成 ===")
