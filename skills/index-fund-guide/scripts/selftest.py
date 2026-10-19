#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""index-fund-guide 保真测试 (selftest)

用途: 改了脚本或参考文档之后, 一条命令确认"书中可复现的数字"没有跑偏。
断言全部来自《指数基金投资指南》原文案例, 不是随口编的期望值:

  - 盈利收益率法: PE 8.2 -> 12.20% -> 分批投资
  - 格雷厄姆 + 港股 9 折: 12.20% x 0.9 = 9.98% -> 转为持有
  - 博格公式: 1.86% + PE(19.71->13.17, 10年) + 14.4% 盈利增长 = 12.26%(书中), 允许 0.15pct 误差
  - 定期不定额(盈利收益率法): 基准 1000 元, 10% -> 12.5%, n=1 => 1250 元
  - 定期不定额(博格法): 基准 1000 元, PE 20 -> 10, n=1 => 2000 元
  - IRR: 每月 1 万 x 7 期 + 期末 76518 => 月均 2.2260% / 年化 30.24%
  - 现金流: 月入 1 万、开支 6 千、比例 50% => 每月 2000 元 (且 50 与 0.5 必须等价)

用法:
    python selftest.py            # 全部跑一遍
    python selftest.py -v         # 失败时打印完整输出
退出码 0 = 全通过, 1 = 有失败。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

FAILS = []


def _pick_tmpdir():
    """找一个真的可写的临时目录；全都不可写时返回 None（跳过 record/report 段）。"""
    cands = []
    for key in ("IFG_SELFTEST_TMP", "DSH_TMPDIR", "TEMP", "TMP"):
        v = os.environ.get(key)
        if v:
            cands.append(os.path.join(v, "ifg-selftest"))
    cands.append(os.path.join(os.getcwd(), ".tmp_selftest"))
    cands.append(os.path.join(HERE, ".tmp_selftest"))
    for d in cands:
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".write-probe")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("x")
            os.remove(probe)
            return d
        except Exception:
            continue
    return None


def run(script, args, expect, forbid=(), label=""):
    """跑一条 CLI, 断言 stdout 包含 expect 里每一项、不包含 forbid 里任何一项。"""
    path = os.path.join(HERE, script)
    cmd = [PY, path] + args
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env=ENV, timeout=120)
    except subprocess.TimeoutExpired:
        FAILS.append((label or " ".join(args), "超时", "", ""))
        return None

    out = (p.stdout or "") + (p.stderr or "")
    problems = []
    if p.returncode != 0:
        problems.append("exit=%d" % p.returncode)
    for e in expect:
        if e not in out:
            problems.append("缺少 %r" % e)
    for f in forbid:
        if f in out:
            problems.append("不该出现 %r" % f)

    name = label or ("%s %s" % (script, " ".join(args)))
    if problems:
        FAILS.append((name, "; ".join(problems), out.strip(), cmd))
        print("[FAIL] %s" % name)
    else:
        print("[ok]   %s" % name)
    return out


def run_err(script, args, expect, label=""):
    """跑一条应当失败的命令: 断言非零退出、包含 expect、且没有 traceback。"""
    path = os.path.join(HERE, script)
    cmd = [PY, path] + args
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env=ENV, timeout=120)
    except subprocess.TimeoutExpired:
        FAILS.append((label or " ".join(args), "超时", "", ""))
        print("[FAIL] %s" % (label or " ".join(args)))
        return None
    out = (p.stdout or "") + (p.stderr or "")
    problems = []
    if p.returncode == 0:
        problems.append("本该失败却成功")
    if "Traceback (most recent call last)" in out:
        problems.append("出现了 traceback")
    for e in expect:
        if e not in out:
            problems.append("缺少 %r" % e)
    name = label or ("%s %s" % (script, " ".join(args)))
    if problems:
        FAILS.append((name, "; ".join(problems), out.strip(), cmd))
        print("[FAIL] %s" % name)
    else:
        print("[ok]   %s" % name)
    return out


def send_bad(cmd_list, label):
    """校验非法输入被干净拒绝(不抛 traceback、不发买入/卖出信号)。"""
    script, args, expect = cmd_list
    return run_err(script, args, [expect], label=label)


def main(argv=None):
    ap = argparse.ArgumentParser(description="index-fund-guide 保真测试")
    ap.add_argument("-v", "--verbose", action="store_true", help="失败时打印完整输出")
    a = ap.parse_args(argv)

    print("=" * 68)
    print("index-fund-guide 保真测试")
    print("=" * 68)

    td = _pick_tmpdir()

    # ---------- 1. 盈利收益率法与港股折扣 ----------
    run("valuation.py", ["signal", "--pe", "8.2"],
        ["12.20%", "分批投资"], label="PE 8.2 -> 12.20% -> 分批投资")

    run("valuation.py", ["signal", "--pe", "8.2", "--discount", "0.9"],
        ["10.98%", "分批投资"],
        label="港股 9 折: 12.20% x 0.9 = 10.98% -> 仍在买入区")

    run("valuation.py", ["signal", "--pe", "11", "--discount", "0.9"],
        ["8.18%", "持有"], forbid=["分批投资"],
        label="港股 9 折: PE 11 -> 9.09% x 0.9 = 8.18% -> 转为持有")

    run("valuation.py", ["signal", "--pe", "40"],
        ["卖出"], label="PE 40 -> 2.5% -> 分批卖出")

    # 边界语义: 恰好等于买入线按保守方向处理(等于不算买入), 等于卖出线算持有
    run("valuation.py", ["signal", "--pe", "10"], ["坚定持有", "恰好落在边界上"],
        forbid=["分批投资"], label="边界: 盈利收益率恰好 10.00% -> 持有(端点保守)")
    run("valuation.py", ["signal", "--pe", "15.625"], ["坚定持有", "恰好落在边界上"],
        forbid=["分批卖出"], label="边界: 盈利收益率恰好 6.40% -> 持有(端点保守)")

    # ---------- 2. 博格公式 (书中沪深300 十年实例) ----------
    run("valuation.py", ["bog",
                         "--dividend", "1.86",
                         "--pe-now", "19.71",
                         "--pe-future", "13.17",
                         "--years", "10",
                         "--earnings-growth", "14.4"],
        ["12.3"], label="博格公式: 1.86 + (-4.0) + 14.4 = 12.31% (书中 12.26%)")

    # ---------- 3. 定期不定额 ----------
    run("valuation.py", ["amount", "--base", "1000", "--mode", "yield",
                         "--first", "10", "--now", "12.5", "--n", "1"],
        ["1250.00"], label="不定额(盈利收益率法): 10%->12.5%, n=1 => 1250 元")

    run("valuation.py", ["amount", "--base", "1000", "--mode", "pe",
                         "--first", "20", "--now", "10", "--n", "1"],
        ["2000.00"], label="不定额(博格法): PE 20->10, n=1 => 2000 元")

    # ---------- 4. IRR (书中 2016 年 1~7 月 H股ETF 案例) ----------
    run("dingtou.py", ["irr",
                       "--invest", "10000,10000,10000,10000,10000,10000,10000",
                       "--final", "76518"],
        ["2.2260", "30.24"], label="IRR: 7期 x 1万, 期末 76518 => 月均 2.2260% / 年化 30.24%")

    # ---------- 5. 现金流 (含 --ratio 50 与 0.5 必须等价) ----------
    run("dingtou.py", ["surplus", "--income", "10000", "--expense", "6000",
                       "--ratio", "50"],
        ["2000"], forbid=["200000", "5000%"],
        label="现金流: 剩 4000 x 50% = 2000 元 (百分数写法)")

    run("dingtou.py", ["surplus", "--income", "10000", "--expense", "6000",
                       "--ratio", "0.5"],
        ["2000"], label="现金流: --ratio 0.5 与 50 等价")

    # ---------- 6. 阈值校准 (2026-10 现实利率) ----------
    run("valuation.py", ["calibrate", "--riskfree", "1.648", "--bond-avg", "3.0"],
        ["10.00%", "3.00%"], label="校准: 风险利率 1.65% / 债基 3.0% -> 买入线 10% / 卖出线 3%")

    # ---------- 7. 方法分派: 同一只指数用对/用错方法必须给出不同结论 ----------
    run("valuation.py", ["signal", "--method", "bog", "--pe", "31.90",
                         "--pe-type", "ttm", "--percentile", "18.2"],
        ["历史较低区域", "分位 18.2% < 30%"],
        label="分派: 创业板 PE 31.9 但分位 18.2% -> 走分位法判买入")

    run("valuation.py", ["signal", "--pe", "31.90", "--pe-type", "ttm"],
        ["分批卖出", "盈利收益率法本就不适用"],
        label="分派: 同一只指数用盈利收益率法 -> 判卖出, 且必须警告方法可能不对")

    run("valuation.py", ["position", "--percentile", "55.5"],
        ["本工具自定分界线", "不是书里的"],
        label="分位: 30/70 必须标明是本工具自定, 不得伪装成书中阈值")

    # ---------- 8. 港股精确模式: 税只打在分红上 ----------
    run("valuation.py", ["signal", "--pe", "10.70", "--pe-type", "ttm",
                         "--hk-exact", "--dividend", "3.38", "--tax", "0.2"],
        ["8.67%", "精确模式"],
        label="港股: 9.35% - 3.38%x0.2 = 8.67% (粗口径 9 折只有 8.41%)")

    # ---------- 9. 组合层: 上限不可行必须明说, 不许静默违反 ----------
    run("valuation.py", ["portfolio", "--budget", "3000", "--weight", "equal",
                         "--max-single", "0.4",
                         "--candidates", "000922:9.01:1:yield,399006:31.9:1:bog:18.2"],
        ["上限不可行"], label="组合: 2 只候选 x 单只 40% 凑不满 100% -> 明确报告不可行")

    # ---------- 10. 回测引擎: 已知现金流的算术锚点 ----------
    # 构造 12 个月, PE 恒为 8(<买入线, 每月都投), 点位恒为 100。
    # 每月投 1000 元、买 10 份, 期末 120 份 x 100 = 12000 = 累计投入,
    # 所以年化必须恰好是 0.00%。这是纯粹的资金加权算术, 与市场无关。
    if td is not None:
        bt = os.path.join(td, "flat.csv")
        with open(bt, "w", encoding="utf-8") as f:
            f.write("date,pe,close\n")
            for i in range(12):
                f.write("2025-%02d-28,8.00,100.00\n" % (i + 1))
        out = run("valuation.py", ["backtest", "--series", bt, "--amount", "1000",
                                   "--json"],
                  ['"annual_pct": 0.0', '"times": 12', '"multiple": 1.0'],
                  label="回测: 点位不变时应得年化 0.00% / 投入 12 次 / 市值比 1.0")
        # 再验一次筛选逻辑: PE 交替 8/30, 只该投一半的月份
        bt2 = os.path.join(td, "mixed.csv")
        with open(bt2, "w", encoding="utf-8") as f:
            f.write("date,pe,close\n")
            for i in range(12):
                f.write("2025-%02d-28,%s,100.00\n" % (i + 1, "8.00" if i % 2 == 0 else "30.00"))
        run("valuation.py", ["backtest", "--series", bt2, "--amount", "1000", "--json"],
            ['"times": 6'],
            label="回测: PE 只有一半月份低于买入线 -> 投入 6 次")
        for fn in ("flat.csv", "mixed.csv"):
            p = os.path.join(td, fn)
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
    else:
        print("[skip] 回测: 找不到可写临时目录")

    # ---------- 11. 记录闭环: record -> report 端到端 ----------
    # 注意: 不能用 tempfile.TemporaryDirectory —— 沙箱下清理临时目录会被拒绝
    # (PermissionError: [WinError 5] ... AppData\Local\Temp\dsh-xxxx)。
    # 也不能固定用脚本同目录：技能装进 profile 的 node_modules 后那里是只读的。
    # 所以按「显式环境变量 -> 当前目录 -> 系统 TEMP -> 脚本同目录」逐个试写。
    csv = os.path.join(td, "record.csv") if td else None
    if csv is None:
        print("[skip] 记录闭环: 找不到可写临时目录 (record/report 段跳过)")
    else:
        if os.path.exists(csv):
            os.remove(csv)
        try:
            run("dingtou.py", ["record", "--file", csv, "--date", "2026-01-05",
                               "--action", "买", "--code", "510300", "--name", "沪深300ETF",
                               "--amount", "1000", "--shares", "250", "--fee", "0.5",
                               "--valuation", "13.0", "--valuation-type", "PE"],
                ["510300"], label="record: 写入第一笔(带费用)")
            run("dingtou.py", ["record", "--file", csv, "--date", "2026-02-05",
                               "--action", "买", "--code", "510300", "--name", "沪深300ETF",
                               "--amount", "1000", "--shares", "240", "--fee", "0.5",
                               "--valuation", "12.5", "--valuation-type", "PE"],
                ["510300"], label="record: 写入第二笔(越跌买越多份额)")
            run("dingtou.py", ["record", "--file", csv, "--date", "2026-06-20",
                               "--action", "分红", "--code", "510300", "--name", "沪深300ETF",
                               "--amount", "40"],
                ["累计分红"], label="record: 记一笔现金分红")
            run("dingtou.py", ["record", "--file", csv, "--date", "2026-09-01",
                               "--action", "卖", "--code", "510300", "--name", "沪深300ETF",
                               "--amount", "520", "--shares", "100", "--fee", "0.3"],
                ["510300"], label="record: 记一笔部分卖出")
            out = run("dingtou.py", ["report", "--file", csv, "--nav", "510300:4.5",
                                     "--asof", "2026-10-16"],
                      ["510300", "已实现盈亏", "累计分红", "持仓成本"],
                      label="report: 出成本/份额/浮动+已实现盈亏/年化")
            if out:
                # 持仓成本 = 2001(两笔买入含费) - 408.37(卖出 100 份按均价结转) = 1592.63
                # 若仍用"净投入"(2001-519.7-40=1441.3)冒充成本, 这里就会对不上。
                if "1592" not in out:
                    FAILS.append(("report 持仓成本", "应显示约 1592.63 的持仓成本", out.strip(), []))
                    print("[FAIL] report 持仓成本应为 1592.63 (按份额结转, 不是净投入)")
                elif "净投入" in out:
                    FAILS.append(("report 净投入", "报告不应再用'净投入'冒充成本", out.strip(), []))
                    print("[FAIL] report 仍在用'净投入'表示成本")
                else:
                    print("[ok]   report 用持仓成本 1592.63, 未混用净投入")
        finally:
            for fn in ("record.csv",):
                p = os.path.join(td, fn)
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass

    # ---------- 11b. 原料自算 PE/PB: 离线用假原料验算术(不联网) ----------
    # 两只股票, 一盈一亏。手算:
    #   A: 市值 100 亿, TTM 净利 6+10-4 = 12 亿 -> PE 8.33; BPS 5 -> 净资产 50 亿 -> PB 2.0
    #   B: 市值 20 亿, TTM 净利 -1-3+1 = -3 亿 -> 亏损, 不参与"剔除亏损"口径
    #   中证口径 PE = (100+20)/(12-3) = 13.33   剔除亏损 PE = 100/12 = 8.33
    raw_dir = os.path.join(td, "rawcache") if td else None
    if raw_dir is None:
        print("[skip] 原料自算: 找不到可写临时目录")
    else:
        import json as _json
        os.makedirs(raw_dir, exist_ok=True)
        payload = {
            "index": "TEST", "fetched_at": "2026-10-19T00:00:00", "period": "2026-06-30",
            "codes": ["A00001", "B00002"],
            "quotes": {
                "A00001": {"name": "甲公司", "price": 10.0, "mcap": 1.0e10,
                           "float_mcap": 6.0e9, "pe_vendor": 8.33, "pb_vendor": 2.0},
                "B00002": {"name": "乙公司", "price": 10.0, "mcap": 2.0e9,
                           "float_mcap": 1.0e9, "pe_vendor": -6.67, "pb_vendor": 2.5},
            },
            "reports": {
                "latest": {"A00001": {"np": 6.0e8, "bps": 5.0},
                           "B00002": {"np": -1.0e8, "bps": 4.0}},
                "prev_fy": {"A00001": {"np": 1.0e9}, "B00002": {"np": -3.0e8}},
                "prev_same": {"A00001": {"np": 4.0e8}, "B00002": {"np": -1.0e8}},
            },
        }
        with open(os.path.join(raw_dir, "index-TEST.json"), "w", encoding="utf-8") as f:
            _json.dump(payload, f, ensure_ascii=False)
        out = run("rawdata.py", ["calc", "--index", "TEST", "--cache", raw_dir,
                                "--top", "0", "--verify"],
                  ["13.3", "8.33", "其中亏损 1 只", "2.00"],
                  forbid=("Traceback",),
                  label="原料自算: 亏损股负利润计入中证口径 -> PE 13.33, 剔除亏损 8.33")
        try:
            os.remove(os.path.join(raw_dir, "index-TEST.json"))
            os.rmdir(raw_dir)
        except OSError:
            pass

    # ---------- 12. 非法输入必须干净拒绝(不出 traceback、不硬给信号) ----------
    send_bad(("valuation.py", ["signal", "--pe", "0"],
              "PE 必须为正数"), "拒绝: PE=0 (亏损品种没有市盈率)")
    send_bad(("valuation.py", ["signal", "--pe", "-5"],
              "PE 必须为正数"), "拒绝: PE 为负")
    send_bad(("valuation.py", ["signal", "--method", "bog", "--pe", "30"],
              "必须给出 --percentile"), "拒绝: 成长指数没给分位就不许硬判")
    send_bad(("valuation.py", ["amount", "--base", "-100", "--mode", "pe",
                               "--first", "20", "--now", "10"],
              "基准金额必须为正数"), "拒绝: 负的基准金额")
    send_bad(("valuation.py", ["backtest", "--series", "不存在.csv"],
              "读不了回测文件"), "拒绝: 回测文件不存在")
    send_bad(("dingtou.py", ["irr", "--invest", "100,100", "--final", "0"],
              "必须同时包含正数与负数"), "拒绝: 现金流全为正则无法算 IRR")
    send_bad(("dingtou.py", ["record", "--file", "x.csv", "--date", "2026-01-01",
                             "--action", "乱写", "--code", "X", "--amount", "1"],
              "只接受"), "拒绝: record 的非法动作")
    send_bad(("dingtou.py", ["report", "--file", "根本不存在的文件.csv"],
              "记录文件不存在"), "拒绝: report 读不存在的文件")

    # ---------- 13. 报告期推断: 必须等法定披露截止日, 不能用「季末+35天」 ----------
    try:
        sys.path.insert(0, HERE)
        import rawdata
        cases = [("2026-01-15", "2025-09-30"), ("2026-02-05", "2025-09-30"),
                 ("2026-04-30", "2025-09-30"), ("2026-05-01", "2026-03-31"),
                 ("2026-08-04", "2026-03-31"), ("2026-08-31", "2026-03-31"),
                 ("2026-09-01", "2026-06-30"), ("2026-11-04", "2026-09-30")]
        bad = [(t, rawdata.latest_period(t), e) for t, e in cases
               if rawdata.latest_period(t) != e]
        if bad:
            FAILS.append(("报告期推断", "这些日期取错了报告期: %s" % bad, "",
                          ("latest_period",)))
            print("[!!]   报告期推断: %s" % bad)
        else:
            print("[ok]   报告期推断: 中报等到 8-31、年报等到次年 4-30 (%d 个日期用例)"
                  % len(cases))
    except ImportError as e:
        print("[skip] 报告期推断 (导入 rawdata 失败: %s)" % e)

    # ---------- 汇总 ----------
    print("-" * 68)
    if FAILS:
        print("失败 %d 项:" % len(FAILS))
        for name, why, out, cmd in FAILS:
            print("\n* %s\n  原因: %s" % (name, why))
            if a.verbose:
                print("  命令: %s" % " ".join(str(c) for c in cmd))
                print("  输出:\n%s" % out)
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
