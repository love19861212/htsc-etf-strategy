#!/usr/bin/env python3
"""ETF 策略 rebalance 脚本。
读取 config.json → 查持仓/账户 → 算目标 vs 实际 → 下单补/减仓。
"""
import json
import os
import sys
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
CFG = json.load(open(ROOT / "config.json"))
PY = str(Path(__file__).parent.parent / "a-share-paper-trading" / "a_share_paper_trading.py")
LOG = ROOT / f"rebalance-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"


def cli(*args):
    """调 paper-trading skill CLI, 返 JSON"""
    res = subprocess.run(["python3", PY, *args], capture_output=True, text=True, timeout=30)
    try:
        return json.loads(res.stdout)
    except Exception:
        return {"ok": False, "error": {"message": res.stdout + res.stderr}}


def log(msg, fh):
    line = f"[{datetime.now().isoformat()}] {msg}"
    print(line)
    fh.write(line + "\n")
    fh.flush()


def computeCumulativePnlPct(bal):
    """真实累计盈亏% (避开 API buggy totalProfitPct, 它用剩余现金当分母)

    Returns: (pnl_abs, pnl_pct)
    """
    initial = CFG["initialCapital"]
    pnl_abs = bal["totalAssets"] - initial
    pnl_pct = pnl_abs / initial * 100
    return pnl_abs, pnl_pct


def computeMarketSignals(quotes, cfg):
    """#4 动态市场信号 — 5 ETF 当日盘面快照

    输入: quotes dict[code] = {currentPrice, prevClose, change, ...}
    返回: dict 含:
      - breadth_pct: 5 ETF 中今日上涨比例 (0-100)
      - offensive_avg: 进攻类 (纳指+半导体) 今日均涨幅
      - defensive_avg: 防御类 (黄金+货币) 今日均涨幅
      - cross_asset: 进攻 - 防御 (正=风险偏好, 负=避险)
      - is_strong_risk_on / is_strong_risk_off: bool
      - per_etf: 各 ETF 当日 change%
    """
    signals = {"per_etf": {}}
    for code, q in quotes.items():
        signals["per_etf"][code] = q.get("change", 0.0)

    # 进攻类 vs 防御类
    offensive_codes = [c for c, t in cfg["targets"].items() if t["name"] in ("纳指ETF", "半导体ETF")]
    defensive_codes = [c for c, t in cfg["targets"].items() if t["name"] in ("黄金ETF", "货币ETF")]
    # 兜底: 如果改名了找不到, 用 config 中的进攻组
    if not offensive_codes:
        offensive_codes = ["513100", "512760"]
    if not defensive_codes:
        defensive_codes = ["518880", "511880"]

    signals["offensive_avg"] = sum(signals["per_etf"].get(c, 0) for c in offensive_codes) / len(offensive_codes) if offensive_codes else 0
    signals["defensive_avg"] = sum(signals["per_etf"].get(c, 0) for c in defensive_codes) / len(defensive_codes) if defensive_codes else 0
    signals["cross_asset"] = signals["offensive_avg"] - signals["defensive_avg"]
    signals["breadth_pct"] = sum(1 for v in signals["per_etf"].values() if v > 0) / max(len(signals["per_etf"]), 1) * 100

    # 阈值在 cfg["dynamic"] 里
    dyn = cfg.get("dynamic", {})
    cross_on = dyn.get("strong_risk_on_cross", 1.5)  # cross > 1.5% 算强风险偏好
    cross_off = dyn.get("strong_risk_off_cross", -1.5)
    breadth_on = dyn.get("strong_risk_on_breadth", 50)
    breadth_off = dyn.get("strong_risk_off_breadth", 30)

    signals["is_strong_risk_on"] = signals["cross_asset"] > cross_on and signals["breadth_pct"] >= breadth_on
    signals["is_strong_risk_off"] = signals["cross_asset"] < cross_off and signals["breadth_pct"] <= breadth_off
    return signals


def selectProfile(pnl_pct, cfg, signals=None):
    """#3 动态攻防切换 — P&L + 市场信号综合选 profile

    Returns: (profile_name, targets_dict, reason_list)
    """
    risk = cfg["risk"]
    dyn = cfg.get("dynamic", {})
    dyn_enabled = dyn.get("enabled", False)
    reasons = []

    # === 基础 P&L 选 profile (静态逻辑) ===
    if pnl_pct <= risk["defenseTriggerPct"]:
        base = "defense"
        reasons.append(f"P&L {pnl_pct:+.2f}% ≤ defense trigger {risk['defenseTriggerPct']}%")
    elif pnl_pct >= risk["offenseTriggerPct"]:
        base = "offense"
        reasons.append(f"P&L {pnl_pct:+.2f}% ≥ offense trigger +{risk['offenseTriggerPct']}%")
    else:
        base = "balanced"
        reasons.append(f"P&L {pnl_pct:+.2f}% 在 ({risk['defenseTriggerPct']}%, +{risk['offenseTriggerPct']}%) 区间, 默认 balanced")

    if not dyn_enabled or signals is None:
        return base, cfg.get(f"{base}Targets", cfg["targets"]), reasons

    # === 动态 override: 市场信号调节 ===
    final = base

    # 强风险偏好 + P&L 没深度亏损 → 升级 offense
    if base != "offense" and pnl_pct > risk["defenseTriggerPct"]:
        if signals["is_strong_risk_on"]:
            final = "offense"
            reasons.append(
                f"市场强风险偏好 → 升 offense: cross={signals['cross_asset']:+.2f}%, breadth={signals['breadth_pct']:.0f}%, offense_avg={signals['offensive_avg']:+.2f}%"
            )

    # 强避险 + P&L 没大涨 → 降到 defense
    if base != "defense" and pnl_pct < risk["offenseTriggerPct"]:
        if signals["is_strong_risk_off"]:
            final = "defense"
            reasons.append(
                f"市场强避险 → 降 defense: cross={signals['cross_asset']:+.2f}%, breadth={signals['breadth_pct']:.0f}%, defense_avg={signals['defensive_avg']:+.2f}%"
            )

    # 进攻类极强 (offense_avg > 阈值, 激进默认 1.0%) 且 P&L 中性 → 升半档
    off_aggressive = dyn.get("aggressive_offense_trigger_offense_avg", 1.0)
    if final == "balanced" and signals["offensive_avg"] > off_aggressive:
        final = "offense"
        reasons.append(f"进攻类强势 (offense_avg={signals['offensive_avg']:+.2f}% > {off_aggressive}%) → 升 offense")

    # 防御类极强 (defense_avg > 1%) 且 P&L 中性 → 降半档
    if final == "balanced" and signals["defensive_avg"] > 1.0 and signals["offensive_avg"] < 0:
        final = "defense"
        reasons.append(f"防御类强势 (defense_avg={signals['defensive_avg']:+.2f}% > 1% 且 offense_avg<0) → 降 defense")

    return final, cfg.get(f"{final}Targets", cfg["targets"]), reasons


def getActiveTargets(cfg):
    """所有 profile 的 target code 集合 (用来 detect 需要清理的持仓)"""
    codes = set(cfg["targets"].keys())
    for k in ("defenseTargets", "offenseTargets"):
        if k in cfg:
            codes |= set(cfg[k].keys())
    return codes


def cleanupStalePositions(positions, active_targets, fh, log):
    """清理不在任何 profile targets 里的持仓 (一次性全清, 不计入 rebalance)

    Returns: list of cleanup plans [{code, name, qty, tPlus0}]
    """
    cleanup = []
    for p in positions:
        code = p["stockCode"]
        if code in active_targets:
            continue
        avail = p.get("availableQuantity", p["quantity"])
        if avail <= 0:
            log(f"  🧹 {p['stockName']}({code}) 不在 targets 但今日不可卖 (T+1 锁定, 持仓={p['quantity']}, 可卖=0) → 顺延明天", fh)
            continue
        log(f"  🧹 {p['stockName']}({code}) 不在 targets → 清理 {avail} 股 (持仓 {p['quantity']})", fh)
        cleanup.append({
            "code": code, "name": p["stockName"], "exchange": p.get("exchange", "SH"),
            "qty": avail, "tPlus0": True
        })
    return cleanup


def checkStopLoss(positions, cfg, fh, log):
    """#2 动态止损 — 14:30 窗口触发

    - 软止损 (softStopLossPct%): 减半仓 (向下取整到 100)
    - 硬止损 (stopLossPct%): 全清

    Returns: dict[code] = override_qty (可能为 0 = 全清, 原数量 = 不动)
    """
    overrides = {}
    soft = cfg["risk"]["softStopLossPct"]
    hard = cfg["risk"]["stopLossPct"]
    triggered_any = False
    for p in positions:
        code = p["stockCode"]
        profit_pct = p["profitPct"]
        if profit_pct <= -hard:
            log(f"  🛑 硬止损 {p['stockName']}({code}) {profit_pct:.2f}% ≤ -{hard}% → 全清", fh)
            overrides[code] = 0
            triggered_any = True
        elif profit_pct <= -soft:
            new_qty = int(p["quantity"] * 0.5 / 100) * 100
            log(f"  ⚠️ 软止损 {p['stockName']}({code}) {profit_pct:.2f}% ≤ -{soft}% → 减半 (→ {new_qty}股)", fh)
            overrides[code] = new_qty
            triggered_any = True
    if not triggered_any:
        log(f"  ✅ 无止损触发 (软 -{soft}% / 硬 -{hard}%)", fh)
    return overrides


def main():
    # === 交易日判断：周六/周日/法定节假日不交易 ===
    try:
        from chinese_calendar import is_workday
        from datetime import date
        today = date.today()
        if not is_workday(today):
            from chinese_calendar import is_holiday
            reason = "法定节假日" if is_holiday(today) else "周末"
            print(f"[{datetime.now().isoformat()}] ⚠️ 今日({today})为{reason}，跳过交易。")
            return
    except ImportError:
        pass  # 库未装时跳过检查，兼容旧环境

    mode = sys.argv[1] if len(sys.argv) > 1 else "rebalance"
    with open(LOG, "a") as fh:
        log(f"=== mode={mode} 启动 ===", fh)

        # 1. 拿账户 + 持仓 + 行情
        bal = cli("getAccountBalance")["data"]
        pos = cli("getPositions")["data"]
        # 行情: 取所有 active target + 持仓里有的 code (清理路径需要)
        all_codes_needed = set(CFG["targets"].keys())
        for k in ("defenseTargets", "offenseTargets"):
            if k in CFG:
                all_codes_needed |= set(CFG[k].keys())
        for p in pos.get("positions", []):
            all_codes_needed.add(p["stockCode"])
        quotes = {code: cli("getQuote", "--stock-code", code, "--exchange", CFG["exchange"].get(code, "SH"))["data"]
                  for code in all_codes_needed}
        log(f"账户: 总资产={bal['totalAssets']:.2f} 可用={bal['availableBalance']:.2f} 冻结={bal['frozenAmount']:.2f}", fh)
        log(f"持仓: {len(pos.get('positions', []))} 只, 总市值={pos.get('totalMarketValue', 0):.2f}", fh)
        for p in pos.get("positions", []):
            log(f"  - {p['stockName']}({p['stockCode']}) {p['quantity']}股 成本={p['costPrice']:.3f} 现价={p['currentPrice']:.3f} 盈亏={p['profitPct']:.2f}% 可卖={p.get('availableQuantity', 'N/A')}", fh)

        # 2. #3 动态攻防切换 — P&L + 市场信号综合
        pnl_abs, pnl_pct = computeCumulativePnlPct(bal)
        log(f"📊 累计 P&L: {pnl_abs:+.2f} 元 ({pnl_pct:+.3f}%) [避开 API buggy pct, 用 totalAssets-initial]", fh)

        # #4 市场信号
        signals = computeMarketSignals(quotes, CFG)
        log(f"📈 市场信号: 广度={signals['breadth_pct']:.0f}% 进攻均值={signals['offensive_avg']:+.2f}% 防御均值={signals['defensive_avg']:+.2f}% cross={signals['cross_asset']:+.2f}% {'[强风险偏好]' if signals['is_strong_risk_on'] else '[强避险]' if signals['is_strong_risk_off'] else ''}", fh)
        for code, ch in signals["per_etf"].items():
            log(f"  - {code} {ch:+.2f}%", fh)

        profile_name, targets, profile_reasons = selectProfile(pnl_pct, CFG, signals)
        log(f"🎯 选用 profile: {profile_name}", fh)
        for r in profile_reasons:
            log(f"  · {r}", fh)

        # 2.5 #5 清理不在 active targets 的持仓 (一次性清仓)
        pos_list = pos.get("positions", [])
        active_targets = getActiveTargets(CFG)
        cleanup_list = cleanupStalePositions(pos_list, active_targets, fh, log)
        if cleanup_list:
            log(f"⚠️ 发现 {len(cleanup_list)} 个待清理品种, 将于 rebalance 后立即平仓", fh)

        # 3. #2 动态止损 — 先看现有持仓是否需要强制减仓
        stoploss_overrides = checkStopLoss(pos_list, CFG, fh, log)

        # 4. 算目标仓位
        total = bal["totalAssets"]
        plan = []
        for code, t in targets.items():
            price = quotes[code]["currentPrice"]
            target_amount = total * t["weight"] / 100
            target_qty = int(target_amount / price / 100) * 100  # 整百
            current = next((p for p in pos["positions"] if p["stockCode"] == code), None)
            current_qty = current["quantity"] if current else 0
            # #2 止损覆盖: 如果该品种触发止损, 改写 target_qty
            if code in stoploss_overrides:
                target_qty = stoploss_overrides[code]
                log(f"  🚨 {t['name']}({code}) 触发止损, target_qty 改写为 {target_qty}", fh)
            delta = target_qty - current_qty
            plan.append({
                "code": code, "name": t["name"], "exchange": CFG["exchange"][code],
                "price": price, "currentQty": current_qty, "targetQty": target_qty,
                "delta": delta, "tPlus0": t["tPlus0"]
            })
        log("调仓计划:", fh)
        for x in plan:
            delta_str = f"{x['delta']:+.0f}"
            log(f"  {x['name']}({x['code']}): 现 {x['currentQty']:.0f} → 目标 {x['targetQty']} (Δ {delta_str}) @ {x['price']:.3f}", fh)

        # === 预判 T+1 deferred (基于 current availableQuantity, 不依赖实际下单) ===
        predicted_deferred = []
        for x in plan:
            if x["delta"] < 0:  # 卖单
                current = next((p for p in pos["positions"] if p["stockCode"] == x["code"]), None)
                if current:
                    avail = current.get("availableQuantity", current["quantity"])
                    if abs(x["delta"]) > avail:
                        predicted_deferred.append({
                            "code": x["code"], "name": x["name"],
                            "wanted_to_sell": abs(x["delta"]),
                            "available_today": avail,
                            "deferred_amount": abs(x["delta"]) - avail,
                            "reason": "T+1 锁定 (今天买入的不能今天卖)"
                        })
        if predicted_deferred:
            log(f"⏳ 预判 T+1 deferred (明天 rebalance 补):", fh)
            for d in predicted_deferred:
                log(f"  - {d['name']}({d['code']}): 想卖 {d['wanted_to_sell']} 可卖 {d['available_today']} 明日补 {d['deferred_amount']}", fh)
        else:
            log("✅ 无 T+1 deferred", fh)

        # === 输出 REPORT_JSON (analyze 和 rebalance 模式都生成, 供 cron LLM 引用) ===
        from datetime import datetime, timezone, timedelta
        contest_end = datetime.fromisoformat(CFG["contestEnd"])
        now_bj = datetime.now(timezone(timedelta(hours=8)))
        days_left = (contest_end - now_bj).days

        report = {
            "timestamp": now_bj.isoformat(),
            "mode": mode,
            "pnl_abs": round(pnl_abs, 2),
            "pnl_pct": round(pnl_pct, 3),
            "total_assets": bal["totalAssets"],
            "available_balance": bal["availableBalance"],
            "profile": profile_name,
            "profile_reasons": profile_reasons,
            "market_signals": {
                "breadth_pct": round(signals["breadth_pct"], 1),
                "offensive_avg": round(signals["offensive_avg"], 3),
                "defensive_avg": round(signals["defensive_avg"], 3),
                "cross_asset": round(signals["cross_asset"], 3),
                "is_strong_risk_on": signals["is_strong_risk_on"],
                "is_strong_risk_off": signals["is_strong_risk_off"],
                "per_etf_change_pct": signals["per_etf"],
            },
            "cleanup_pending": [{"code": c["code"], "name": c["name"], "qty": c["qty"]} for c in cleanup_list],
            "triggers": {
                # 暴露的是"触发阈值" (profitPct 到达该值则触发), 带符号, LLM 不会再误读
                "soft_stoploss_trigger": -CFG["risk"]["softStopLossPct"],
                "hard_stoploss_trigger": -CFG["risk"]["stopLossPct"],
                "defense_trigger": CFG["risk"]["defenseTriggerPct"],
                "offense_trigger": CFG["risk"]["offenseTriggerPct"],
            },
            "stoploss_triggered": list(stoploss_overrides.keys()),
            "plan": plan,
            "predicted_deferred_t1": predicted_deferred,
            "days_to_contest_end": days_left,
            "log_path": str(LOG),
        }
        with open(ROOT / f"last_report.json", "w") as rfh:
            json.dump(report, rfh, ensure_ascii=False, indent=2)
        log(f"📦 REPORT_JSON 写到 last_report.json", fh)

        if mode == "analyze":
            log("=== analyze 模式，不下单 ===", fh)
            return

        # 5. 调仓执行 — 分两阶段: 先卖释放现金, 再买 (cash-aware)
        deferred_t1 = predicted_deferred
        current_cash = bal["availableBalance"]  # 跟踪动态 cash

        # 阶段 1: 卖单 (释放 cash)
        sell_orders = [x for x in plan if x["delta"] < 0]
        for x in sell_orders:
            qty = abs(x["delta"])
            current = next((p for p in pos["positions"] if p["stockCode"] == x["code"]), None)
            if not current:
                log(f"  ⚠️ {x['name']} 无持仓可卖", fh)
                continue
            avail = current.get("availableQuantity", current["quantity"])
            if qty > avail:
                deferred_t1.append({
                    "code": x["code"], "name": x["name"],
                    "wanted_to_sell": qty, "available_today": avail,
                    "deferred_amount": qty - avail
                })
                qty = avail
                log(f"  ⏳ {x['name']} T+1 锁定: 想卖 {abs(x['delta'])} 可卖 {avail} → 今日卖 {qty}, 明日补 {qty - avail}", fh)
            qty = int(qty)
            if qty == 0:
                continue
            log(f"  [SELL] {x['name']}({x['code']}) {qty}股 @ {x['price']:.3f}", fh)
            r = cli("submitOrder", "--direction", "sell",
                    "--stock-code", x["code"], "--exchange", x["exchange"],
                    "--quantity", str(qty), "--order-type", "market")
            log(f"    结果: {r}", fh)
            # 假设成交, 累加 cash (实际可能 T+1 才能用, 但 sell 是 T+0 入账)
            if isinstance(r, dict) and r.get("ok"):
                current_cash += qty * x["price"]
                log(f"    → cash 释放 +{qty * x['price']:.2f} ≈ {current_cash:.2f}", fh)

        # 阶段 2: 买单 (扣 cash)
        buy_orders = sorted([x for x in plan if x["delta"] > 0], key=lambda x: -x["delta"] * x["price"])  # 大单优先
        for x in buy_orders:
            qty = int(x["delta"])
            cost = qty * x["price"]
            if cost > current_cash:
                # cash 不够, 减量
                affordable = int((current_cash / x["price"]) / 100) * 100
                if affordable < 100:
                    log(f"  💸 {x['name']} 需要 {cost:.0f} cash, 现有 {current_cash:.0f} → 跳过 (不够买 100 股)", fh)
                    continue
                log(f"  💸 {x['name']} 需要 {cost:.0f} cash, 现有 {current_cash:.0f} → 减量到 {affordable} 股", fh)
                qty = affordable
                cost = qty * x["price"]
            log(f"  [BUY] {x['name']}({x['code']}) {qty}股 @ {x['price']:.3f} (需 {cost:.2f})", fh)
            r = cli("submitOrder", "--direction", "buy",
                    "--stock-code", x["code"], "--exchange", x["exchange"],
                    "--quantity", str(qty), "--order-type", "market")
            log(f"    结果: {r}", fh)
            if isinstance(r, dict) and r.get("ok"):
                current_cash -= cost
                log(f"    → cash 扣减 -{cost:.2f} ≈ {current_cash:.2f}", fh)

        # 5.5 #5 清理不在 targets 的持仓 (一次性, T+0 可卖的今天清)
        if mode == "rebalance" and cleanup_list:
            for c in cleanup_list:
                r = cli("submitOrder", "--direction", "sell",
                        "--stock-code", c["code"], "--exchange", c["exchange"],
                        "--quantity", str(int(c["qty"])), "--order-type", "market")
                log(f"  🧹 清理 {c['name']}({c['code']}) {int(c['qty'])} 股 → {r}", fh)

        log("=== 完成 ===", fh)

        # === 6. 输出 REPORT_JSON + REPORT_MD (供 cron isolated agent 引用, 避免 LLM 瞎编) ===
        # 计算距比赛结束还有几天
        from datetime import datetime, timezone, timedelta
        contest_end = datetime.fromisoformat(CFG["contestEnd"])
        now_bj = datetime.now(timezone(timedelta(hours=8)))
        days_left = (contest_end - now_bj).days

        # 整理已成交操作 (从 log 解析, 简化: 重跑太重, 直接从 balances/orders 算)
        report = {
            "timestamp": now_bj.isoformat(),
            "pnl_abs": round(pnl_abs, 2),
            "pnl_pct": round(pnl_pct, 3),
            "total_assets": bal["totalAssets"],
            "available_balance": bal["availableBalance"],
            "profile": profile_name,
            "triggers": {
                "soft_stoploss_trigger": -CFG["risk"]["softStopLossPct"],
                "hard_stoploss_trigger": -CFG["risk"]["stopLossPct"],
                "defense_trigger": CFG["risk"]["defenseTriggerPct"],
                "offense_trigger": CFG["risk"]["offenseTriggerPct"],
            },
            "stoploss_triggered": list(stoploss_overrides.keys()),
            "plan": plan,
            "deferred_t1": deferred_t1,
            "days_to_contest_end": days_left,
            "log_path": str(LOG),
        }
        # 写 JSON (可被 LLM parse)
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n=== REPORT_JSON_END ===\n")
        fh.flush()
        # 也写到独立文件, 方便程序读
        with open(ROOT / f"last_report.json", "w") as rfh:
            json.dump(report, rfh, ensure_ascii=False, indent=2)
        log(f"📦 REPORT_JSON 写到 {LOG.name} 和 last_report.json", fh)


if __name__ == "__main__":
    main()
