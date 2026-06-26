# htsc-etf-strategy 📊

> 华泰柏瑞杯·全国首届ETF AI交易巅峰赛 **Agent 开发者榜** 参赛策略
> 5 ETF 组合 + 按权重自动 rebalance | OpenClaw skill

[![Contest](https://img.shields.io/badge/Contest-华泰柏瑞杯-c8102e)](#)
[![Track](https://img.shields.io/badge/Track-Agent开发者榜-blue)](#)
[![Status](https://img.shields.io/badge/Status-Active-success)](#)

## 🏆 参赛信息

| 项 | 内容 |
|---|---|
| **赛事** | 华泰柏瑞杯·全国首届ETF AI交易巅峰赛 |
| **主办** | 华泰证券 × 华泰柏瑞基金 |
| **赛道** | 自带 Agent 参赛 → Agent 开发者榜 |
| **比赛期** | 2026-06-11 ~ 2026-07-20 |
| **报名截止** | 2026-07-12 24:00 |
| **初始资金** | 100 万（虚拟） |
| **本策略参赛 ID** | 见 `logs/participation.log` |

## 🎯 策略思路

### 核心逻辑：**攻守兼备 + 趋势跟随**

25 天比赛窗口，**单一指标（总收益率）排名** → 既要攻也要防。本策略在最大化收益和最小化回撤之间取平衡：

- **30% 黄金 ETF（518880）** — 避险底仓，与 A 股负相关性强，T+0 灵活
- **30% 纳指 ETF（513100）** — 美元资产 + 美科技龙头，T+0 跟美股夜盘
- **25% 半导体 ETF（512760）** — A 股核心高 beta 进攻仓位
- **10% 货币 ETF（511880）** — 现金等价物，待机会抄底
- **5% 沪深 300 ETF（510300）** — 底仓对冲

### 风险控制

| 参数 | 阈值 | 说明 |
|---|---|---|
| 单一品种最大权重 | 40% | 防止黑天鹅集中暴露 |
| 单品止损线 | -8% | 跌破自动减仓 |
| 现金底仓 | ≥ 5% | 保持灵活 |
| T+0 / T+1 处理 | 区分 | 避免 T+1 当日不可卖 |

## 📦 安装

### 前置

- Python 3.9+（系统 Python 需 `requests`）
- OpenClaw runtime
- 同装 5 个华泰官方 skill（`a-share-paper-trading` 等）

### 步骤

```bash
# 1. 安装华泰官方 skill 包（含 a-share-paper-trading）
# 见 https://d.zhangle.com 文档

# 2. 装本策略 skill
mkdir -p ~/.openclaw/skills/
cp -r htsc-etf-strategy ~/.openclaw/skills/

# 3. 配置 HT_APIKEY
export HT_APIKEY="ht_your_api_key"
# 或写入 ~/.htsc-skills/config
echo "HT_APIKEY=ht_your_api_key" > ~/.htsc-skills/config
chmod 600 ~/.htsc-skills/config

# 4. 验证
python3 ~/.openclaw/skills/htsc-etf-strategy/rebalance.py analyze
```

## 🚀 使用

### 单次分析（不下单）

```bash
python3 rebalance.py analyze
```

输出：
- 账户/持仓快照
- 5 个 ETF 的目标股数 / 调仓量 / 估算金额

### 实际调仓

```bash
python3 rebalance.py
# 或 python3 rebalance.py rebalance
```

### Cron 自动每日 rebalance

```bash
# 每个交易日 14:30（A股收盘前 30 分钟）
30 14 * * 1-5 cd /root/.openclaw/skills/htsc-etf-strategy && python3 rebalance.py >> logs/rebalance-$(date +\%F).log 2>&1
```

## 🛠 自定义调参

编辑 `config.json`：

```json
{
  "targets": {
    "518880": { "name": "黄金ETF", "weight": 30, "tPlus0": true }
  },
  "risk": {
    "maxSinglePositionPct": 40,
    "stopLossPct": 8,
    "minCashPct": 5
  }
}
```

可改：
- `targets.<code>.weight` — 权重（5 个加起来必须 100）
- `risk.maxSinglePositionPct` — 单一品种最大权重
- `risk.stopLossPct` — 止损线
- `risk.minCashPct` — 现金底仓

## 📊 业绩跟踪

每次 rebalance 都会写日志到 `logs/rebalance-<date>.log`，记录：
- 调仓时间
- 每个 ETF 调仓量 / orderId
- 成交价格 / 状态
- 当日总盈亏

## 🏅 参赛激励

- **Agent 策略激励计划**：提交本 GitHub 链接到大赛入口，前 200 名各 100 元 Token
- **Agent 开发者榜冠军**：2 万 Token
- **Agent 开发者榜 2-5 名**：6000 元奖品
- **6-10 名**：3000 元奖品

详见 [华泰柏瑞杯官方规则](https://m.jiniutech.com/qs/htbr/hd/index.html?id=rhSWgiy9)

## 📁 目录结构

```
htsc-etf-strategy/
├── SKILL.md          # OpenClaw skill 描述
├── README.md         # 本文件（GitHub/参赛展示用）
├── rebalance.py      # 主策略脚本
├── config.json       # 权重/风险参数
└── logs/             # 运行日志（提交后建）
```

## 🤝 致谢

- 主办方：华泰证券 / 华泰柏瑞基金
- skill 框架：OpenClaw
- 模拟盘 API：华泰 ai.zhangle.com

## 📄 许可

MIT License — 自由使用、修改、分发，欢迎二次创作。

---

**⚠️ 免责声明**：本策略仅用于华泰柏瑞杯模拟盘比赛，所有交易均为虚拟资金，不构成任何投资建议。
