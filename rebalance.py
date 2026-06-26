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


def selectProfile(pnl_pct, cfg):
    """#3 动态攻防切换 — 根据累计盈亏% 选目标配置

    Returns: (profile_name, targets_dict)
      - pnl_pct <= defenseTrigger: 'defense'
      - pnl_pct >= offenseTrigger: 'offense'
      - else: 'balanced'
    """
    risk = cfg["risk"]
    if pnl_pct <= risk["defenseTriggerPct"]:
        return "defense", cfg.get("defenseTargets", cfg["targets"])
    elif pnl_pct >= risk["offenseTriggerPct"]:
        return "offense", cfg.get("offenseTargets", cfg["targets"])
    else:
        return "balanced", cfg["targets"]


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
    mode = sys.argv[1] if len(sys.argv) > 1 else "rebalance"
    with open(LOG, "a") as fh:
        log(f"=== mode={mode} 启动 ===", fh)

        # 1. 拿账户 + 持仓 + 行情
        bal = cli("getAccountBalance")["data"]
        pos = cli("getPositions")["data"]
        quotes = {code: cli("getQuote", "--stock-code", code, "--exchange", CFG["exchange"][code])["data"]
                  for code in CFG["targets"]}
        log(f"账户: 总资产={bal['totalAssets']:.2f} 可用={bal['availableBalance']:.2f} 冻结={bal['frozenAmount']:.2f}", fh)
        log(f"持仓: {len(pos.get('positions', []))} 只, 总市值={pos.get('totalMarketValue', 0):.2f}", fh)
        for p in pos.get("positions", []):
            log(f"  - {p['stockName']}({p['stockCode']}) {p['quantity']}股 成本={p['costPrice']:.3f} 现价={p['currentPrice']:.3f} 盈亏={p['profitPct']:.2f}% 可卖={p.get('availableQuantity', 'N/A')}", fh)

        # 2. #3 动态攻防切换 — 根据累计 P&L 选 profile
        pnl_abs, pnl_pct = computeCumulativePnlPct(bal)
        log(f"📊 累计 P&L: {pnl_abs:+.2f} 元 ({pnl_pct:+.3f}%) [避开 API buggy pct, 用 totalAssets-initial]", fh)
        profile_name, targets = selectProfile(pnl_pct, CFG)
        log(f"🎯 选用 profile: {profile_name} (defense ≤ {CFG['risk']['defenseTriggerPct']}% / offense ≥ +{CFG['risk']['offenseTriggerPct']}%)", fh)

        # 3. #2 动态止损 — 先看现有持仓是否需要强制减仓
        pos_list = pos.get("positions", [])
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
            log(f"  {x['name']}({x['code']}): 现 {x['currentQty']} → 目标 {x['targetQty']} (Δ {x['delta']:+}) @ {x['price']:.3f}", fh)

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

        # 5. 调仓执行 (T+1 跟踪: 卖单卖不动时记 deferred, 明天的 rebalance 会接)
        deferred_t1 = predicted_deferred  # 复用预判, 实际下单后补实际订单号
        for x in plan:
            if x["delta"] == 0:
                continue
            direction = "buy" if x["delta"] > 0 else "sell"
            qty = abs(x["delta"])
            # T+1 限制: 今天买的不能今天卖
            if direction == "sell":
                current = next((p for p in pos["positions"] if p["stockCode"] == x["code"]), None)
                if not current:
                    log(f"  ⚠️ {x['name']} 无持仓可卖", fh)
                    continue
                # availableQuantity 是当前可卖数
                avail = current.get("availableQuantity", current["quantity"])
                if qty > avail:
                    deferred_t1.append({
                        "code": x["code"], "name": x["name"],
                        "wanted_to_sell": qty, "available_today": avail,
                        "deferred_amount": qty - avail
                    })
                    qty = avail
                    log(f"  ⏳ {x['name']} T+1 锁定: 想卖 {x['delta']} 可卖 {avail} → 今日卖 {qty}, 明日补 {qty - avail}", fh)
            qty = int(qty)  # 强制转 int,避免 float 字符串 '10000.0' 被 argparse 拒收
            if qty == 0:
                continue
            log(f"  → {direction} {x['name']}({x['code']}) {qty}股 @ 市价", fh)
            r = cli("submitOrder", "--direction", direction,
                    "--stock-code", x["code"], "--exchange", x["exchange"],
                    "--quantity", str(qty), "--order-type", "market")
            log(f"    结果: {r}", fh)
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
