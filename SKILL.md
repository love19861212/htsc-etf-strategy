---
name: htsc-etf-strategy
description: 华泰柏瑞杯 ETF AI 交易巅峰赛参赛策略。4 ETF 组合（黄金+纳指+半导体+货币）按权重 + 动态市场信号 自动调仓。配合 a-share-paper-trading skill 提交市价单完成 rebalance。
user-invocable: true
metadata:
  openclaw:
    emoji: 📊
    skillKey: htsc-etf-strategy
    author: GoldenLotus
    version: "1.1"
    requires:
      bins: ["python3"]
      skills: ["a-share-paper-trading"]
    contest: 华泰柏瑞杯·全国首届ETF AI交易巅峰赛
    contestEnd: 2026-07-20T15:00:00+08:00
---

# htsc-etf-strategy

华泰柏瑞杯·全国首届ETF AI 交易巅峰赛 **Agent 开发者榜** 参赛策略。

## 策略核心

**4 ETF 组合 + 三档 profile + 动态市场信号**：

| ETF | 代码 | balanced | offense | defense | 性质 | T+0 |
|---|---|---|---|---|---|---|
| 黄金 ETF | 518880.SH | 35% | 25% | 50% | 避险 / 抗回撤 | ✅ |
| 纳指 ETF | 513100.SH | 33% | 32% | 20% | 美元资产 / 美科技 | ✅ |
| 半导体 ETF | 512760.SH | 25% | 35% | — | A 股核心高 beta | ❌ |
| 货币 ETF | 511880.SH | 7% | 8% | 30% | 现金等价物 / 待机会 | ✅ |

> 510300 沪深300 已于 2026-06-30 退出 (用户认为收益不好, 不再配置)

**操作原则**：
- 每个交易日 14:30 自动 rebalance（可调）
- 单一品种最大权重 40%
- 跌破 8% 止损线自动减仓 (软 -5% 减半 / 硬 -8% 全清)
- 现金底仓不低于 5%
- T+0 品种可日内反复（黄金/纳指/货币）
- T+1 品种次日才能卖（半导体）
- **不在 active targets 的持仓自动清理**（一次性, T+1 锁定顺延）

## 动态攻防切换 (3 层信号)

### 第 0 层 — Top10 集中押 leader（2026-06-30 起默认）

**目标**：华泰柏瑞杯 top 10 需 32%+ 收益, 20 天, 日均 1.5%+。仅靠动态调 profile 不够, 必须集中持仓。

`dynamic.top10_mode: true` 时启用。每次 rebalance:
1. 拿 5 ETF 当日 `getQuote().change%`
2. 按涨幅排序, leader 拿 `top10_leader_weight` (默认 55%), 2nd 拿 `top10_second_weight` (默认 30%), 3rd 拿 `top10_third_weight` (默认 10%), 剩下补货币 ETF
3. 若 leader 涨幅 < `top10_min_leader_change` (默认 0.5%) → 信号弱, 退回 offense
4. 若所有 ETF 都跌/平 → 退回 defense
5. **阶段 0 补卖**：cash 缺口时自动卖非 leader 持仓释放现金（前提：top10_mode）

**激进配置**（top 10 专用）：
- `maxSinglePositionPct: 60`（从 40 上调, 允许单品种 60%）
- `stopLossPct: 6` / `softStopLossPct: 3`（从 8/5 收紧）
- `defenseTriggerPct: -1` / `offenseTriggerPct: 2`（从 -2/5 收紧）
- `minCashPct: 3`（从 5 降低）

### 第 1 层 — 累计 P&L 阈值（静态基础）

在 `risk` 里：
- `pnl_pct ≤ defenseTriggerPct (-1%)` → defense
- `pnl_pct ≥ offenseTriggerPct (+2%)` → offense
- else → balanced

### 第 2 层 — 动态市场信号（启用后 override 基础）

`dynamic.enabled: true` 时，每次 rebalance 调 `getQuote()` 拿 5 ETF 当日涨跌幅，算：

- `breadth_pct` = 5 ETF 中今日上涨比例 (0-100)
- `offensive_avg` = (纳指 + 半导体) 今日均涨幅
- `defensive_avg` = (黄金 + 货币) 今日均涨幅
- `cross_asset` = 进攻均值 - 防御均值 (正=风险偏好, 负=避险)

**Override 规则**：

| 条件 | 动作 |
|---|---|
| `cross > strong_risk_on_cross` AND `breadth ≥ strong_risk_on_breadth` AND P&L 没深度亏 | 升 offense |
| `cross < strong_risk_off_cross` AND `breadth ≤ strong_risk_off_breadth` AND P&L 没大涨 | 降 defense |
| balanced 时 `offense_avg > aggressive_offense_trigger_offense_avg` | 升 offense |
| balanced 时 `defense_avg > 1%` AND `offense_avg < 0` | 降 defense |

**激进默认值**（排名 207 → top 10 需要 30+%）：
- `strong_risk_on_cross: 0.5` (放宽, 容易进 offense)
- `strong_risk_off_cross: -2.0` (放宽, 难出 offense)
- `aggressive_offense_trigger_offense_avg: 0.5` (进攻 +0.5% 就升档)

### 第 3 层 — 持仓清理

- 每次 rebalance 检查持仓, 不在 active targets (defense/balanced/offense/top10 三个的并集) 的品种
- 一次性市价清仓, T+1 锁定的顺延到下一日

## 工具

### run-strategy

跑一次完整的「查账户 → 查持仓 → 算市场信号 → 选 profile → 算目标 vs 实际 → 卖单先执行 → 买单 cash-aware 执行 → 清理 stale 持仓」流程。

**参数**：
- `mode`（可选）：`rebalance`（默认，下单）/ `analyze`（仅打印计划不下单）

**执行**：
```bash
python3 rebalance.py [rebalance|analyze]
```

**输出（rebalance 模式）**：
- 账户/持仓快照
- 市场信号（广度/进攻均值/防御均值/cross）
- 选用的 profile + 选择原因链
- 各 ETF 的目标价/目标股数/当前股数/调仓量
- 卖单先执行（释放 cash），买单后执行（按 cash 减量）
- Stale 持仓清理（T+0 当日, T+1 顺延）
- 下单结果含 orderId
- `last_report.json` 完整 plan + signals + reasons 供 cron LLM 引用

**典型错误**：
- `auth` 类别 → HT_APIKEY 失效，重新配置
- `business` 类别 → 非交易时段/停牌/涨跌停，脚本会跳过该品种
- `validation` 类别 → 资金不够（cash-aware 会自动减量, 不会 100 股就跳过）

## 配置示例

```json
{
  "targets": {
    "518880": { "weight": 35, "tPlus0": true },
    "513100": { "weight": 33, "tPlus0": true },
    "512760": { "weight": 25, "tPlus0": false },
    "511880": { "weight": 7,  "tPlus0": true }
  },
  "offenseTargets": {
    "512760": { "weight": 35, "tPlus0": false },
    "513100": { "weight": 32, "tPlus0": true },
    "518880": { "weight": 25, "tPlus0": true },
    "511880": { "weight": 8,  "tPlus0": true }
  },
  "defenseTargets": {
    "518880": { "weight": 50, "tPlus0": true },
    "511880": { "weight": 30, "tPlus0": true },
    "513100": { "weight": 20, "tPlus0": true }
  },
  "risk": {
    "defenseTriggerPct": -2,
    "offenseTriggerPct": 5,
    "softStopLossPct": 5,
    "stopLossPct": 8
  },
  "dynamic": {
    "enabled": true,
    "strong_risk_on_cross": 1.0,
    "strong_risk_on_breadth": 50,
    "strong_risk_off_cross": -1.5,
    "strong_risk_off_breadth": 30,
    "aggressive_offense_trigger_offense_avg": 1.0
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
