# StockAssistant-SaaS / A股投资助手

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.0+-green.svg)](https://flask.palletsprojects.com)
[![Vue.js](https://img.shields.io/badge/Vue.js-3.0-4FC08D.svg)](https://vuejs.org)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 面向个人 A股投资者的智能投研工具，提供持仓管理、智能选股、策略复盘、K线分析、持仓建议等全流程功能。

![A股投资助手界面预览](./screenshots/preview.png)

## ✨ 核心功能

### 📊 持仓管理
- 持仓 CRUD（创建/编辑/删除/清仓）
- 买入/卖出交易记录
- 实时行情自动填充现价
- 资金管理（总资产/可用资金/持仓市值/浮动盈亏）
- 资产分布可视化（饼图）

### 🎯 智能选股引擎
三套选股策略，适配不同市场环境：

| 策略 | 适用环境 | 核心逻辑 |
|------|---------|---------|
| **趋势突破** | 上升/震荡期 | MA20站稳 + MACD金叉 + 放量 + 板块效应 |
| **板块龙头** | 情绪亢奋期 | 板块涨>2% + 涨幅5-9.5% + 换手5-15% + 主力流入 |
| **超跌反弹** | 下跌末期 | 近20日跌>20% + 止跌信号 + 市值>100亿 |

- 6步筛选流程 + 100分评分体系
- 策略参数可调
- 观察池跟踪

### 📈 策略中心
- 市场环境判断（MA20斜率）
- 仓位建议（0-100%）
- 选股回测（胜率/盈亏比/最大回撤）
- 实时预警（价格/涨跌幅监控）

### 📉 技术分析
- K线图（日K）+ MA5/MA20 均线
- 成交量柱状图
- 支撑/压力位参考（6个关键价位）
- 止损止盈弹窗

### 🧠 智能建议
- **持仓操作建议**：5维评分 → 加仓/持有/观望/减仓/清仓
- **选股策略建议**：策略筛选依据 + 买入策略建议

## 🚀 快速开始

### 环境要求
- Python 3.8+
- Tushare API Token（免费申请：https://tushare.pro）

### 安装步骤

```bash
# 1. 克隆项目
git clone https://github.com/zhangguanglai/StockAssistant-SaaS.git
cd StockAssistant-SaaS

# 2. 创建虚拟环境（推荐）
python -m venv venv

# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置 Tushare Token
# 编辑 config.py，填入你的 Tushare Token
TUSHARE_TOKEN = "your_token_here"

# 5. 启动服务
python server.py
```

### 访问应用
- 本地地址：http://127.0.0.1:5000
- 默认账号：`default` / `default123`

## 📁 项目结构

```
stock-portfolio/
├── server.py              # Flask 应用入口
├── config.py              # 配置文件
├── database.py            # SQLite 数据库操作
├── auth.py                # JWT 认证模块
├── helpers.py             # 工具函数（行情获取等）
├── screener.py            # 选股引擎核心
├── routes/                # API 路由（Blueprint）
│   ├── auth_bp.py         # 认证路由
│   ├── position_bp.py     # 持仓/交易路由
│   ├── screen_bp.py       # 选股路由
│   ├── watch_bp.py        # 观察池路由
│   └── data_bp.py         # 市场数据路由
├── static/                # 静态资源
│   ├── css/
│   └── js/
├── templates/             # HTML 模板
│   └── index.html
├── data/                  # 数据文件（SQLite）
├── screenshots/           # 截图预览
├── requirements.txt       # Python 依赖
└── README.md             # 本文件
```

## 🔌 数据源

| 数据源 | 用途 | 状态 |
|--------|------|------|
| **新浪财经** | 实时行情 | ✅ 可用 |
| **东方财富** | 实时行情 | ⚠️ 网络受限 |
| **Tushare** | 历史日线/基本面 | ✅ 可用（1-2天延迟） |

**降级策略**：东方财富 → 新浪财经 → Tushare

## 🛠️ 技术栈

- **后端**: Python + Flask + SQLite
- **前端**: Vue 3 (CDN) + ECharts
- **数据源**: Tushare API + 新浪财经 + 东方财富
- **认证**: JWT (HS256) + PBKDF2 密码哈希

## 📌 版本记录

| 版本 | 日期 | 主要更新 |
|------|------|---------|
| v3.3 | 2026-04-08 | P0+P1+P2 数据源扩展，市场数据面板 |
| v3.2 | 2026-04-08 | 性能优化（TTL缓存、Gzip压缩） |
| v3.1 | 2026-04-06 | Blueprint 模块化重构 |
| v3.0 | 2026-04-06 | SaaS 多租户认证系统 |
| v2.5 | 2026-04-06 | 策略效果跟踪面板 |
| v2.4 | 2026-04-06 | SQLite 持久化，前端工程化 |
| v2.0 | 2026-04-04 | 三套选股策略架构 |
| v1.0 | 2026-04-01 | 项目初始化 |

## ⚠️ 免责声明

本项目仅供学习研究使用，不构成任何投资建议。股市有风险，投资需谨慎。

## 📄 License

MIT License © 2026 zhangguanglai
