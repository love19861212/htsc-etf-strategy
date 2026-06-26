---
name: htsc-etf-strategy
description: 华泰柏瑞杯 ETF AI 交易巅峰赛参赛策略。5 ETF 组合（黄金+纳指+半导体+货币+沪深300）按权重自动调仓，支持模拟盘运行。配合 a-share-paper-trading skill 提交市价单完成 rebalance。
user-invocable: true
metadata:
  openclaw:
    emoji: 📊
    skillKey: htsc-etf-strategy
    author: GoldenLotus
    version: "1.0"
    requires:
      bins: ["python3"]
      skills: ["a-share-paper-trading"]
    contest: "华泰柏瑞杯·全国首届ETF AI交易巅峰赛"
    contestEnd: "2026-07-20T15:00:00+08:00"
---

# htsc-etf-strategy

华泰柏瑞杯·全国首届ETF AI交易巅峰赛 **Agent 开发者榜** 参赛策略。

## 策略核心

**5 ETF 组合 + 按权重自动 rebalance**：

| ETF | 代码 | 权重 | 性质 | T+0 |
|---|---|---|---|---|
| 黄金 ETF | 518880.SH | 30% | 避险 / 抗回撤 | ✅ |
| 纳指 ETF | 513100.SH | 30% | 美元资产 / 美科技 | ✅ |
| 半导体 ETF | 512760.SH | 25% | A 股核心高 beta | ❌ |
| 货币 ETF | 511880.SH | 10% | 现金等价物 / 待机会 | ✅ |
| 沪深 300 ETF | 510300.SH | 5% | 底仓对冲 | ❌ |

**操作原则**：
- 每个交易日 14:30 自动 rebalance（可调）
- 单一品种最大权重 40%
- 跌破 8% 止损线自动减仓
- 现金底仓不低于 5%
- T+0 品种可日内反复（黄金/纳指/货币）
- T+1 品种次日才能卖

## 工具

### run-strategy
跑一次完整的"查账户 → 查持仓 → 算目标 vs 实际 → 下单调仓"流程。

**参数**：
- `mode`（可选）: `rebalance`（默认，下单）/ `analyze`（仅打印计划不下单）

**执行**：
```bash
python3 rebalance.py [rebalance|analyze]
```

**输出**：
- 账户/持仓快照
- 5 个 ETF 的目标价 / 目标股数 / 当前股数 / 调仓量
- 下单结果（rebalance 模式）含 orderId

**典型错误**：
- `auth` 类别 → HT_APIKEY 失效，重新配置
- `business` 类别 → 非交易时段/停牌/涨跌停，脚本会跳过该品种
- `validation` 类别 → 资金不够/可卖数不够，脚本会按 availableQuantity 自动调整

## 配置

`config.json` 包含所有可调参数：

```json
{
  "targets": {
    "518880": { "name": "黄金ETF",   "weight": 30, "tPlus0": true  },
    "513100": { "name": "纳指ETF",   "weight": 30, "tPlus0": true  },
    "512760": { "name": "半导体ETF", "weight": 25, "tPlus0": false },
    "511880": { "name": "货币ETF",   "weight": 10, "tPlus0": true  },
    "510300": { "name": "沪深300ETF","weight": 5,  "tPlus0": false }
  },
  "risk": {
    "maxSinglePositionPct": 40,
    "stopLossPct": 8,
    "minCashPct": 5
  }
}
```

## 依赖

- Python 3.9+（系统 Python，需 `requests`）
- a-share-paper-trading skill（同仓已装）
- HT_APIKEY 环境变量（或 `~/.htsc-skills/config`）

## 参赛提交

本 skill 已开源，可作为 **Agent策略激励计划** 提交材料：
- 截止 2026-07-12 23:59:59
- 前 200 名各 100 元 Token
- 比赛期间累计收益必须为正
