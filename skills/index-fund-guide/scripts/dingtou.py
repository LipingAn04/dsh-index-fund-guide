#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
定投工具 —— 规则来自《指数基金投资指南》(银行螺丝钉) 第 5、6 章。

子命令:
  irr      定期定投的年复合收益率(等间隔现金流)
  xirr     日期不固定时的年化收益率
  plan     按书中的四步法生成一份可填写的定投计划表
  surplus  算每月可用于定投的金额(剩余资金的指定比例)
  record   把一笔买卖追加到定投记录 CSV(第 6 章"定期检查"的数据底座)
  report   读记录 CSV: 各品种成本/份额/浮动盈亏 + 整体真实年化(XIRR)

示例:
  # 每月 1 万投 7 个月, 期末市值 76518 -> 书中例子, 应得月均约 2%、年化约 30%
  # 推荐写法(免负号转义):
  python dingtou.py irr --invest 10000,10000,10000,10000,10000,10000,10000 --final 76518

  # 直接给完整现金流(PowerShell 下必须用 = 连接):
  python dingtou.py irr --cashflow=-10000,-10000,-10000,-10000,-10000,-10000,-10000,76518

  python dingtou.py xirr --flows 2026-01-05:-1000,2026-02-05:-1000,2026-07-01:2300

  python dingtou.py plan --income 10000 --expense 6000 --age 35 --ratio 0.5 --out plan.md

  python dingtou.py surplus --income 10000 --expense 6000 --ratio 0.5

  # 记录闭环: 先 record 记账, 再 report 出真实年化
  python dingtou.py record --file 定投记录.csv --date 2026-01-05 --action 买 --code 510300 --name 沪深300ETF --amount 1000 --shares 246.9 --valuation 12.3
  python dingtou.py report --file 定投记录.csv --nav 510300:4.05
"""

import argparse
import csv
import datetime as dt
import json
import os
import sys

# Windows 控制台常为 GBK/CP936, 遇到底层编码放不下的字符不要崩, 退化为替代符即可
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass


# ---------------- 通用 ----------------
def fmt(x, nd=2):
    return ("%." + str(nd) + "f") % x


def npv(rate, cashflows):
    return sum(cf / ((1.0 + rate) ** i) for i, cf in enumerate(cashflows))


def solve_irr(cashflows, lo=-0.999999, hi=10.0, tol=1e-10, max_iter=400):
    """二分法求使 NPV=0 的周期收益率。要求现金流有正有负。"""
    if not any(cf > 0 for cf in cashflows) or not any(cf < 0 for cf in cashflows):
        raise ValueError("现金流必须同时包含正数与负数(投入为负、收回为正)")
    f_lo = npv(lo, cashflows)
    f_hi = npv(hi, cashflows)
    if f_lo * f_hi > 0:
        # 尝试扩展上界
        for _ in range(60):
            hi *= 2
            f_hi = npv(hi, cashflows)
            if f_lo * f_hi <= 0:
                break
        else:
            raise ValueError("在 [-1, %s] 区间内未找到 IRR 解" % fmt(hi, 1))
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid, cashflows)
        if abs(f_mid) < tol or (hi - lo) < tol:
            return mid
        if f_lo * f_mid <= 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2.0


def parse_num_list(text):
    out = []
    for tok in text.replace(" ", "").split(","):
        if not tok:
            continue
        out.append(float(tok))
    if not out:
        raise ValueError("现金流为空")
    return out


# ---------------- irr ----------------
def cmd_irr(a):
    if a.invest:
        if a.cashflow:
            raise SystemExit("--invest 与 --cashflow 只能二选一")
        if a.final is None:
            raise SystemExit("使用 --invest 时必须同时给出 --final(期末持有市值)")
        inv = parse_num_list(a.invest)
        if any(x <= 0 for x in inv):
            raise SystemExit("--invest 只填每期投入的正数金额, 不要带负号")
        cfs = [-x for x in inv] + [float(a.final)]
    else:
        if not a.cashflow:
            raise SystemExit("请提供 --cashflow, 或改用 --invest + --final")
        cfs = parse_num_list(a.cashflow)
    r = solve_irr(cfs)
    periods = {"month": 12, "week": 52, "year": 1, "day": 365}[a.freq]
    ann = ((1.0 + r) ** periods - 1.0) * 100.0
    invested = -sum(cf for cf in cfs if cf < 0)
    final = sum(cf for cf in cfs if cf > 0)
    d = {
        "cashflows": cfs,
        "periods": len(cfs),
        "freq": a.freq,
        "period_return_pct": round(r * 100, 6),
        "annualized_return_pct": round(ann, 4),
        "total_invested": round(invested, 2),
        "final_value": round(final, 2),
        "total_return_pct": round((final / invested - 1.0) * 100, 4) if invested else None,
    }
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return
    unit = {"month": "月", "week": "周", "year": "年", "day": "日"}[a.freq]
    print("定投收益率(IRR)")
    print("-" * 56)
    print("期数           : %d %s" % (len(cfs), unit))
    print("累计投入       : %s" % fmt(invested, 2))
    print("期末收回       : %s" % fmt(final, 2))
    if invested:
        print("累计收益率     : %s%%   (注意: 这个数字不能直接年化!)"
              % fmt((final / invested - 1.0) * 100))
    print("-" * 56)
    print("%s均收益率   : %s%%" % (unit, fmt(r * 100, 4)))
    print("年复合收益率   : %s%%   (= (1 + %s)^%d - 1)"
          % (fmt(ann), fmt(r * 100, 4) + "%", periods))
    print()
    print("口径提醒: 定投不能用 (期末-投入)/投入 直接年化 —— 每笔钱在场时间不同。")
    print("          IRR 适合等间隔投入; 日期不固定请用 xirr 子命令。")


# ---------------- xirr ----------------
def _d(s):
    s = s.strip()
    for f in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return dt.datetime.strptime(s, f).date()
        except ValueError:
            continue
    raise argparse.ArgumentTypeError("日期格式应为 YYYY-MM-DD: %r" % s)


def xirr_rate(pairs):
    """pairs: [(date, cashflow)], 已排序或内部排序。返回小数年化收益率。"""
    pairs = sorted(pairs, key=lambda p: p[0])
    d0 = pairs[0][0]

    def f(r):
        return sum(cf / ((1.0 + r) ** ((d - d0).days / 365.0)) for d, cf in pairs)

    lo, hi = -0.999999, 10.0
    flo, fhi = f(lo), f(hi)
    for _ in range(80):
        if flo * fhi <= 0:
            break
        hi *= 2
        fhi = f(hi)
    else:
        raise SystemExit("未找到 XIRR 解, 请检查现金流方向")
    for _ in range(400):
        mid = (lo + hi) / 2.0
        fm = f(mid)
        if abs(fm) < 1e-10:
            break
        if flo * fm <= 0:
            hi = mid
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2.0


def cmd_xirr(a):
    pairs = []
    for tok in a.flows.replace(" ", "").split(","):
        if not tok:
            continue
        if ":" not in tok:
            raise SystemExit("现金流格式应为 日期:金额 , 例如 2026-01-05:-1000")
        ds, amt = tok.rsplit(":", 1)
        pairs.append((_d(ds), float(amt)))
    if len(pairs) < 2:
        raise SystemExit("至少需要两笔现金流")
    pairs.sort(key=lambda p: p[0])
    rate = xirr_rate(pairs)
    days = (pairs[-1][0] - pairs[0][0]).days
    d = {
        "start": str(pairs[0][0]),
        "end": str(pairs[-1][0]),
        "days": days,
        "xirr_pct": round(rate * 100, 4),
    }
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return
    print("XIRR(年化, 按实际天数)")
    print("-" * 56)
    print("起止           : %s ~ %s (%d 天)" % (pairs[0][0], pairs[-1][0], days))
    print("年化收益率     : %s%%" % fmt(rate * 100, 4))


# ---------------- surplus ----------------
def normalize_ratio(ratio):
    """允许 0.5 与 50 两种写法: 大于 1 视为百分数。

    史上踩过的坑: 直接 --ratio 50 会让"每月可定投"放大 100 倍(50 x 剩余),
    输出 200000 元这种荒唐数字, 所以入口统一归一化。
    """
    if ratio > 1:
        ratio = ratio / 100.0
    if not (0 < ratio <= 1):
        raise SystemExit("定投比例取值范围 0~1, 也可写百分数 0~100; "
                         "例如 --ratio 0.5 或 --ratio 50")
    return ratio


def compute_surplus(income, expense, ratio):
    ratio = normalize_ratio(ratio)
    surplus = income - expense
    if surplus <= 0:
        raise SystemExit("开支 >= 收入, 没有可定投的资金。先梳理现金流。")
    return surplus, surplus * ratio


def cmd_surplus(a):
    a.ratio = normalize_ratio(a.ratio)
    surplus, amount = compute_surplus(a.income, a.expense, a.ratio)
    stock_pct = max(30, 100 - a.age)
    d = {
        "income": a.income,
        "expense": a.expense,
        "surplus": round(surplus, 2),
        "ratio": a.ratio,
        "monthly_invest": round(amount, 2),
        "stock_ratio_pct": stock_pct,
    }
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return
    print("现金流梳理")
    print("-" * 56)
    print("月收入         : %s" % fmt(a.income, 0))
    print("月开支         : %s" % fmt(a.expense, 0))
    print("月剩余         : %s" % fmt(surplus, 0))
    print("定投比例       : %s%%   (书中建议 50%%, 留一半做备用金)" % fmt(a.ratio * 100, 0))
    print("每月可定投     : %s 元" % fmt(amount, 0))
    print()
    print("配置参考       : %d 岁 -> 指数基金建议占流动资产 %d%%"
          % (a.age, stock_pct))
    print("                 家庭按成员平均年龄算; 股票资产占比不应低于 30%。")


# ---------------- plan ----------------
PLAN_TMPL = """# 我的定投计划

制定日期：____　　　家庭成员平均年龄：{age} 岁

## 一、梳理现金流

1. 家庭月收入　{income} 元
   家庭月开支　{expense} 元
   每月剩余资金 {surplus} 元（= 收入 − 开支）
2. 每月剩余资金中拿出 {ratio_pct}% 用于定投（书中建议 50%，留一半做备用金）
   **每月定投金额 = {amount} 元**

## 二、挑选基金（只选当前处于低估的品种）

- [ ] 每月定投少于 1 000 元 → 选 1 只：______________
- [ ] 每月定投 1 000~3 000 元或以上 → 选 2~3 只：

| 序号 | 品种 | 指数代码 | 适用方法 | 每月金额 |
|---|---|---|---|---|
| ① |  |  | 盈利收益率法 / 博格公式 / PB变种 |  |
| ② |  |  |  |  |
| ③ |  |  |  |  |

## 三、构建定投计划

1. 定投渠道：□ 场内股票账户（券商 ________）　□ 场外平台（________）
2. 定投日：每月 ____ 日，原因：____________________（建议：发薪后第一个交易日）
3. 频率：□ 按月　□ 按周（两者长期效果几乎一样，按月更省心）
4. 投资策略（三条写死）：
   1. 指数基金**低估**时，坚持每月定投
   2. 估值**恢复正常**，坚定持有，停止定投，闲钱改投其他低估品种
   3. 进入**高估**区域，分批卖出（份额分 10 份，每月卖 1 份）

## 四、定投记录表

| 日期 | 买/卖 | 品种 | 金额 | 份额 | 估值(PE/PB/盈利收益率) |
|---|---|---|---|---|---|
|  |  |  |  |  |  |
|  |  |  |  |  |  |

> 打印出来贴在显眼处。记录表是后面用 IRR 算真实年化收益的数据来源。

## 五、目标配置参考（(100 − 年龄)% 法则）

- 指数基金占家庭流动资产：**{stock_pct}%**（不低于 30%）
- 其余 {rest_pct}% 放债券基金 / 货币基金作为缓冲
- 长期平均收益参考：货基 2%~3%　债基 6%~7%　指数基金约 14%

## 六、免责

本计划依据《指数基金投资指南》(银行螺丝钉) 的方法框架生成，其中估值阈值与历史数据取自该书 2017 年 5 月口径。
实际投资前必须以**当期**的 PE/PB/盈利收益率复核，并自行承担投资风险。
"""


def cmd_plan(a):
    a.ratio = normalize_ratio(a.ratio)
    surplus, amount = compute_surplus(a.income, a.expense, a.ratio)
    stock_pct = max(30, 100 - a.age)
    rest = 100 - stock_pct
    text = PLAN_TMPL.format(
        age=a.age,
        income=fmt(a.income, 0),
        expense=fmt(a.expense, 0),
        surplus=fmt(surplus, 0),
        ratio_pct=fmt(a.ratio * 100, 0),
        amount=fmt(amount, 0),
        stock_pct=stock_pct,
        rest_pct=rest,
    )
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("已生成定投计划表: %s" % a.out)
        print("每月定投金额: %s 元   指数基金目标占比: %d%%" % (fmt(amount, 0), stock_pct))
    else:
        print(text)


# ---------------- record / report ----------------
RECORD_HEADER = ["date", "action", "code", "name", "amount", "shares", "fee",
                 "valuation", "valuation_type"]
RECORD_REQUIRED = ["date", "action", "code", "amount"]
RECORD_OPTIONAL = ["shares", "fee", "valuation", "valuation_type"]


def _records_check(path):
    """校验表头, 防止用户手改 CSV 后错列导致后面的账全乱。
    只强制 date/action/code/amount; 早期版本没有 fee 列, 缺少时按 0 处理。"""
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        head = next(csv.reader(f), None)
    if not head:
        raise SystemExit("记录文件是空的")
    missing = [c for c in RECORD_REQUIRED if c not in head]
    if missing:
        raise SystemExit("记录文件缺少必需列: %s (至少需要 %s)"
                         % (",".join(missing), ",".join(RECORD_REQUIRED)))
    return head


def _records_read(path):
    if not os.path.exists(path):
        raise SystemExit("记录文件不存在: %s (先用 record 子命令建立第一条记录)" % path)
    _records_check(path)
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def cmd_record(a):
    ACTS = {"买": "买", "buy": "买", "卖": "卖", "sell": "卖",
            "分红": "分红", "div": "分红", "dividend": "分红"}
    if a.action not in ACTS:
        raise SystemExit("--action 只接受 买 / 卖 / 分红 (或 buy/sell/div)")
    act = ACTS[a.action]
    try:
        day = dt.date.fromisoformat(a.date)
    except ValueError:
        raise SystemExit("日期格式应为 YYYY-MM-DD, 收到: %s" % a.date)
    if a.amount <= 0:
        raise SystemExit("--amount 必须为正数(买=付出金额, 卖/分红=收到金额)")
    if a.shares is not None and a.shares <= 0:
        raise SystemExit("--shares 必须为正数")
    if a.fee is not None and a.fee < 0:
        raise SystemExit("--fee 不能为负数(没有费用就填 0 或省略)")
    if a.valuation is not None and a.valuation <= 0:
        raise SystemExit("--valuation 必须为正数")
    if act == "分红" and a.shares is not None:
        raise SystemExit("分红请只填金额(--amount); 若把分红再买入, 再单独记一笔 买, shares 填新增份额")

    path = a.file
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        raise SystemExit("记录文件所在目录不存在: %s" % parent)

    is_new = not os.path.exists(path)
    head = None if is_new else _records_check(path)
    with open(path, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(RECORD_HEADER)
            head = RECORD_HEADER
        row = {
            "date": day.isoformat(), "action": act, "code": a.code,
            "name": a.name or "",
            "amount": "%.2f" % a.amount,
            "shares": "" if a.shares is None else "%.4f" % a.shares,
            "fee": "" if not a.fee else "%.2f" % a.fee,
            "valuation": "" if a.valuation is None else "%.4f" % a.valuation,
            "valuation_type": a.valuation_type or "",
        }
        # 按文件实际表头写, 这样早期没有 fee 列的旧文件也能继续追加
        w.writerow([row.get(c, "") for c in head])

    rows = _records_read(path)
    bought = sum(float(r["amount"]) for r in rows if r["action"] == "买")
    sold = sum(float(r["amount"]) for r in rows if r["action"] == "卖")
    div = sum(float(r["amount"]) for r in rows if r["action"] == "分红")
    fees = sum(float(r["fee"] or 0) for r in rows if "fee" in r)
    n_buy = sum(1 for r in rows if r["action"] == "买")
    n_sell = sum(1 for r in rows if r["action"] == "卖")
    n_div = sum(1 for r in rows if r["action"] == "分红")
    d = {
        "file": os.path.abspath(path),
        "records": len(rows),
        "buys": n_buy, "sells": n_sell, "dividends": n_div,
        "cash_in": round(bought, 2),
        "cash_out": round(sold, 2),
        "dividend_income": round(div, 2),
        "fees": round(fees, 2),
        "net_cash": round(bought - sold - div + fees, 2),
        "appended": {"date": day.isoformat(), "action": act, "code": a.code,
                     "amount": a.amount, "fee": a.fee},
    }
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return
    print("已记录     : %s  %s %s  %s 元" % (day.isoformat(), act, a.code, fmt(a.amount, 2)))
    print("记录文件   : %s" % d["file"])
    print("累计记录   : %d 条 (买 %d / 卖 %d / 分红 %d)" % (len(rows), n_buy, n_sell, n_div))
    print("累计买入   : %s 元   累计卖出: %s 元   累计分红: %s 元   累计费用: %s 元"
          % (fmt(bought, 2), fmt(sold, 2), fmt(div, 2), fmt(fees, 2)))
    print("净投入     : %s 元  (买入 - 卖出 - 分红 + 费用)"
          % fmt(bought - sold - div + fees, 2))
    if n_sell or n_div:
        print("注意       : 净投入不是'成本'。看真实盈亏请用 report(按份额算持仓成本, 并计入已实现盈亏)。")


def cmd_report(a):
    rows = _records_read(a.file)
    if not rows:
        raise SystemExit("记录文件里还没有任何记录")

    nav = {}
    if a.nav:
        for part in a.nav.split(","):
            part = part.strip()
            if not part:
                continue
            if ":" not in part:
                raise SystemExit("--nav 格式应为 代码:最新净值 , 例如 510300:4.05")
            k, v = part.rsplit(":", 1)
            nav[k.strip()] = float(v)

    asof = _d(a.asof) if a.asof else dt.date.today()

    funds = {}
    flows = []
    skipped_rows = 0
    for r in rows:
        code = (r.get("code") or "").strip()
        if not code:
            skipped_rows += 1
            continue
        try:
            day = dt.date.fromisoformat((r["date"] or "").strip())
            amt = float(r["amount"])
        except Exception:
            raise SystemExit("这一行记录格式有误, 请检查: %s" % r)
        act = r["action"]
        if act not in ("买", "卖", "分红"):
            raise SystemExit("不认识的操作 %r, 只能是 买 / 卖 / 分红" % act)
        fee = float(r.get("fee") or 0)
        sh = float((r.get("shares") or "").strip() or 0)

        f = funds.setdefault(code, {
            "code": code, "name": (r.get("name") or "").strip(),
            "bought": 0.0, "sold": 0.0, "shares": 0.0, "cost": 0.0,
            "realized": 0.0, "dividends": 0.0, "fees": 0.0,
            "has_shares": False, "warn": [],
            "last_val": None, "last_val_type": "", "last_val_date": None,
            "last_date": None,
        })
        if f["last_date"] is None or day > f["last_date"]:
            f["last_date"] = day
        f["fees"] += fee

        if act == "买":
            f["bought"] += amt
            f["cost"] += amt + fee
            if sh > 0:
                f["shares"] += sh
                f["has_shares"] = True
            flows.append((day, -(amt + fee)))
        elif act == "卖":
            f["sold"] += amt
            net = amt - fee
            if sh > 0 and f["has_shares"]:
                avg = f["cost"] / f["shares"] if f["shares"] > 0 else 0.0
                cost_out = avg * sh
                f["shares"] -= sh
                f["cost"] -= cost_out
                f["realized"] += net - cost_out
                if f["shares"] <= 1e-9:
                    # 清仓: 把浮点残余成本并入已实现, 保证 成本+已实现 恒等
                    f["realized"] -= f["cost"]
                    f["cost"] = 0.0
                    f["shares"] = 0.0
            else:
                # 没记份额: 只能按"卖出金额 - 买入金额"粗算, 并明确降级
                f["realized"] += net
                f["warn"].append("有卖出记录但没记份额, 已实现盈亏按现金流近似, 不精确")
            flows.append((day, net))
        else:  # 分红
            f["dividends"] += amt
            flows.append((day, amt))

        if (r.get("valuation") or "").strip():
            f["last_val"] = float(r["valuation"])
            f["last_val_type"] = (r.get("valuation_type") or "").strip()
            f["last_val_date"] = day

    total_cost = 0.0
    total_mv = 0.0
    total_realized = 0.0
    total_div = 0.0
    total_fees = 0.0
    items = []
    for code, f in funds.items():
        mv = f["shares"] * nav[code] if (code in nav and f["has_shares"]) else None
        if mv is not None:
            total_mv += mv
        total_cost += f["cost"]
        total_realized += f["realized"]
        total_div += f["dividends"]
        total_fees += f["fees"]
        items.append((code, f, mv))

    overall = None
    if total_mv > 0:
        try:
            overall = xirr_rate(flows + [(asof, total_mv)]) * 100.0
        except SystemExit:
            overall = None

    total_float = (total_mv - total_cost) if total_mv > 0 else None
    total_all = None
    if total_mv > 0:
        total_all = total_mv - total_cost + total_realized + total_div

    d = {
        "file": os.path.abspath(a.file),
        "asof": asof.isoformat(),
        "records": len(rows),
        "skipped_rows": skipped_rows,
        "funds": [],
        "total_holding_cost": round(total_cost, 2),
        "total_market_value": round(total_mv, 2) if total_mv else None,
        "total_float_pnl": None if total_float is None else round(total_float, 2),
        "total_realized_pnl": round(total_realized, 2),
        "total_dividends": round(total_div, 2),
        "total_fees": round(total_fees, 2),
        "total_gain": None if total_all is None else round(total_all, 2),
        "overall_xirr_pct": None if overall is None else round(overall, 4),
    }
    for code, f, mv in items:
        avg = (f["cost"] / f["shares"]) if (f["has_shares"] and f["shares"] > 1e-9) else None
        d["funds"].append({
            "code": code, "name": f["name"],
            "bought": round(f["bought"], 2), "sold": round(f["sold"], 2),
            "shares": round(f["shares"], 4), "has_shares": f["has_shares"],
            "holding_cost": round(f["cost"], 2),
            "avg_cost": None if avg is None else round(avg, 4),
            "nav": nav.get(code),
            "market_value": None if mv is None else round(mv, 2),
            "float_pnl": None if mv is None else round(mv - f["cost"], 2),
            "float_pnl_pct": None if (mv is None or not f["cost"]) else round((mv / f["cost"] - 1) * 100, 2),
            "realized_pnl": round(f["realized"], 2),
            "dividends": round(f["dividends"], 2),
            "fees": round(f["fees"], 2),
            "warnings": f["warn"],
            "last_valuation": f["last_val"],
            "last_valuation_type": f["last_val_type"],
            "last_valuation_date": None if f["last_val_date"] is None else f["last_val_date"].isoformat(),
            "last_date": None if f["last_date"] is None else f["last_date"].isoformat(),
        })
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return

    print("定投账户报告   截至 %s" % asof.isoformat())
    print("-" * 88)
    print("%-10s %-10s %10s %10s %10s %11s %11s" %
          ("代码", "名称", "份额", "成本价", "持仓成本", "市值", "浮动盈亏"))
    for code, f, mv in items:
        avg = (f["cost"] / f["shares"]) if (f["has_shares"] and f["shares"] > 1e-9) else None
        print("%-10s %-10s %10s %10s %10s %11s %11s" % (
            code, (f["name"] or "-")[:10],
            fmt(f["shares"], 2) if f["has_shares"] else "-",
            fmt(avg, 4) if avg is not None else "-",
            fmt(f["cost"], 2),
            fmt(mv, 2) if mv is not None else "缺净值",
            ("%+.2f" % (mv - f["cost"])) if mv is not None else "-"))
    print("-" * 88)
    print("持仓成本   : %s 元" % fmt(total_cost, 2))
    if total_mv > 0:
        print("当前市值   : %s 元" % fmt(total_mv, 2))
        print("浮动盈亏   : %+s 元 (%+.2f%%)"
              % (fmt(total_float, 2), (total_float / total_cost * 100 if total_cost else 0.0)))
    else:
        print("当前市值   : 未提供净值, 加上 --nav 代码:净值 才能算市值与年化")
    if total_realized or total_div:
        print("已实现盈亏 : %+s 元   (卖出时落袋的部分)" % fmt(total_realized, 2))
        print("累计分红   : %+s 元   (持有期间收到的现金)" % fmt(total_div, 2))
    if total_fees:
        print("累计费用   : %s 元" % fmt(total_fees, 2))
    if total_all is not None:
        print("总收益     : %+s 元   (浮动盈亏 + 已实现盈亏 + 累计分红)"
              % fmt(total_all, 2))
    print("真实年化   : %s%%   (XIRR: 按每笔资金的实际在场时间加权, 已含分红现金流)"
          % ("缺数据" if overall is None else fmt(overall, 2)))
    print("-" * 88)

    for code, f, mv in items:
        for w in f["warn"]:
            print("提醒 [%s]: %s" % (code, w))
    stale = [x for x in items if x[1]["last_val_date"] is None]
    if stale:
        print("提醒: 有 %d 个品种没有记录估值, 下次买入时加上 --valuation 才能复盘买点。"
              % len(stale))
    no_shares = [x for x in items if not x[1]["has_shares"]]
    if no_shares:
        print("提醒: %s 从未记过份额, 无法算成本价与浮动盈亏; 补上 --shares 才有意义。"
              % ", ".join(x[0] for x in no_shares))
    print()
    print("口径: 成本 = 买入金额 + 费用 - 卖出时结转的成本; 卖出时的成本按持仓均价结转。")
    print("      分红按现金记入(不再投入)。若分红再买入, 另记一笔 买, 不要重复计入。")
    print("下一步: 用 valuation.py signal 判断各品种当前该买 / 该持 / 该卖。")


def build_parser():
    p = argparse.ArgumentParser(
        description="定投工具 —— 规则来自《指数基金投资指南》(银行螺丝钉)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="cmd")

    i = sub.add_parser("irr", help="定投年复合收益率(等间隔)")
    i.add_argument("--cashflow",
                   help='逗号分隔现金流, 投入为负、期末市值为正。例: -1000,-1000,-1000,3200 '
                        '(PowerShell 下请写成 --cashflow=-1000,-1000,-1000,3200, 或改用 --invest + --final)')
    i.add_argument("--invest",
                   help="每期投入金额(只填正数), 逗号分隔。例: 1000,1000,1000 —— 推荐用法, 免去负号转义")
    i.add_argument("--final", type=float,
                   help="期末持有市值(与 --invest 配套使用)")
    i.add_argument("--freq", choices=["month", "week", "year", "day"], default="month",
                   help="现金流间隔单位(默认 month)")
    i.add_argument("--json", action="store_true")
    i.set_defaults(func=cmd_irr)

    x = sub.add_parser("xirr", help="日期不固定时的年化收益率")
    x.add_argument("--flows", required=True,
                   help="格式 日期:金额，逗号分隔。例: 2026-01-05:-1000,2026-07-01:2300")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_xirr)

    s = sub.add_parser("surplus", help="算每月可定投金额")
    s.add_argument("--income", type=float, required=True, help="家庭月收入")
    s.add_argument("--expense", type=float, required=True, help="家庭月开支")
    s.add_argument("--ratio", type=float, default=0.5, help="剩余资金的定投比例, 默认 0.5")
    s.add_argument("--age", type=float, default=35, help="家庭成员平均年龄(用于配置参考)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_surplus)

    q = sub.add_parser("plan", help="生成定投计划表 markdown")
    q.add_argument("--income", type=float, required=True)
    q.add_argument("--expense", type=float, required=True)
    q.add_argument("--ratio", type=float, default=0.5)
    q.add_argument("--age", type=float, default=35)
    q.add_argument("--out", help="输出文件路径; 省略则打印到屏幕")
    q.set_defaults(func=cmd_plan)

    r = sub.add_parser("record", help="追加一笔买入/卖出/分红到定投记录 CSV")
    r.add_argument("--file", required=True, help="CSV 路径; 不存在则自动新建")
    r.add_argument("--date", required=True, help="交易日期 YYYY-MM-DD")
    r.add_argument("--action", required=True, help="买 / 卖 / 分红 (或 buy/sell/div)")
    r.add_argument("--code", required=True, help="基金或指数代码, 如 510300")
    r.add_argument("--name", help="品种名称")
    r.add_argument("--amount", type=float, required=True,
                   help="买=付出的金额, 卖=收回的金额, 分红=收到的现金")
    r.add_argument("--shares", type=float, help="成交份额(填了才能算成本价与市值; 分红不填)")
    r.add_argument("--fee", type=float, help="这一笔的费用(申购费/佣金/赎回费), 不填视为 0")
    r.add_argument("--valuation", type=float, help="成交时该指数的估值(PE/PB/盈利收益率)")
    r.add_argument("--valuation-type", choices=["PE", "PB", "yield"], help="估值口径")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_record)

    rp = sub.add_parser("report", help="读记录 CSV: 持仓成本 / 份额 / 浮动+已实现盈亏 / 真实年化")
    rp.add_argument("--file", required=True)
    rp.add_argument("--nav", help="最新单位净值, 格式 代码:净值, 逗号分隔。例 510300:4.05,510880:2.81")
    rp.add_argument("--asof", help="计算市值的日期 YYYY-MM-DD, 默认今天")
    rp.add_argument("--json", action="store_true")
    rp.set_defaults(func=cmd_report)

    return p


def main(argv=None):
    p = build_parser()
    a = p.parse_args(argv)
    if not getattr(a, "cmd", None):
        p.print_help()
        return 1
    try:
        a.func(a)
    except BrokenPipeError:
        raise
    except (ValueError, OSError, ZeroDivisionError) as e:
        # 参数/文件层面的可预期错误: 打印人话, 不打 traceback
        print("[错误] %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
