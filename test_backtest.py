#!/usr/bin/env python3
"""测试选股回测API"""
import requests
import json

# 测试回测API
url = 'http://127.0.0.1:5000/api/backtest?days=15&hold=5'
headers = {'Authorization': 'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoxLCJ1c2VybmFtZSI6ImRlZmF1bHQiLCJleHAiOjE3NDQ2MjY1Njh9.test'}

try:
    r = requests.get(url, headers=headers, timeout=60)
    print(f'Status: {r.status_code}')
    if r.status_code == 200:
        data = r.json()
        print(f'\n回测结果:')
        print(f'  信号数: {data.get("total_signals", 0)}')
        print(f'  胜率: {data.get("win_rate", 0)}%')
        print(f'  平均收益: {data.get("avg_return", 0)}%')
        print(f'  盈亏比: {data.get("profit_loss_ratio", 0)}')
        print(f'  扫描标的: {data.get("screened_total", 0)}')
        print(f'\n结论: {data.get("summary", "")}')
        print(f'\n交易明细数: {len(data.get("trades", []))}')
    else:
        print(f'Error: {r.text}')
except Exception as e:
    print(f'Error: {e}')
