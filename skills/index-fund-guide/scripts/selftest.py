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
    run("valuation.py", ["signal", "--pe", "10"], ["坚定持有", "恰好等于买入线"],
        forbid=["分批投资"], label="边界: 盈利收益率恰好 10.00% -> 持有(端点保守)")
    run("valuation.py", ["signal", "--pe", "15.625"], ["坚定持有", "恰好等于卖出线"],
        forbid=["分批卖出"], label="边界: 盈利收益率恰好 6.40% -> 持有(端点保守)")
    # 反向断言: 不在边界上就不许自称"恰好落在边界" (曾经每个持有信号都这么写)
    run("valuation.py", ["signal", "--pe", "31.57", "--preset", "cal"], ["坚定持有"],
        forbid=["恰好等于", "恰好落在边界"], label="普通持有信号不得自称处于边界")

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

    # ---------- 11c. 自由流通加权 PE: 分母必须同权重(否则大市值股会把 PE 压低) ----------
    # 两只股票, 自由流通比例差别很大:
    #   A: 总市值 100 亿, 自由流通 30 亿, 净利 10 亿  (比例 0.3)
    #   B: 总市值 100 亿, 自由流通 100 亿, 净利  2 亿  (比例 1.0)
    #   中证口径 PE = (100+100)/(10+2) = 16.67
    #   自由流通 PE = 130 / (10x0.3 + 2x1.0) = 130/5 = 26.00   <-- 正确
    #   错误算法(Σ自由流通/Σ净利) = 130/12 = 10.83             <-- 必须不出现
    raw_dir2 = os.path.join(td, "rawcache2") if td else None
    if raw_dir2 is None:
        print("[skip] 自由流通加权: 找不到可写临时目录")
    else:
        import json as _json2
        os.makedirs(raw_dir2, exist_ok=True)
        payload2 = {
            "index": "TESTFF", "fetched_at": "2026-10-19T00:00:00", "period": "2026-06-30",
            "codes": ["C00003", "D00004"],
            "quotes": {
                "C00003": {"name": "丙公司", "price": 10.0, "mcap": 1.0e10,
                           "float_mcap": 3.0e9, "pe_vendor": 10.0, "pb_vendor": 1.0},
                "D00004": {"name": "丁公司", "price": 10.0, "mcap": 1.0e10,
                           "float_mcap": 1.0e10, "pe_vendor": 50.0, "pb_vendor": 2.0},
            },
            "reports": {
                "latest": {"C00003": {"np": 10.0e8, "bps": 5.0},
                           "D00004": {"np": 2.0e8, "bps": 2.5}},
                "prev_fy": {"C00003": {"np": 0.0}, "D00004": {"np": 0.0}},
                "prev_same": {"C00003": {"np": 0.0}, "D00004": {"np": 0.0}},
            },
        }
        with open(os.path.join(raw_dir2, "index-TESTFF.json"), "w", encoding="utf-8") as f:
            _json2.dump(payload2, f, ensure_ascii=False)
        run("rawdata.py", ["calc", "--index", "TESTFF", "--cache", raw_dir2, "--top", "0"],
            ["26.00", "16.6"], forbid=("10.83",),
            label="自由流通加权: 分母同权重 -> 26.00 (旧的 Σ自由流通/Σ净利 会得 10.83)")
        try:
            os.remove(os.path.join(raw_dir2, "index-TESTFF.json"))
            os.rmdir(raw_dir2)
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

    # ---------- 14. 按品种类型自动换算法 (--type -> --method) ----------
    # 书里表 4.2: 四类指数四种方法。这一步错了, 后面每个减法都精确地错。
    run("valuation.py", ["signal", "--type", "grow", "--pe", "31.62",
                         "--percentile", "17.4", "--preset", "cal"],
        ["高速成长指数", "历史较低区域", "--type grow → bog"],
        forbid=["分类自查"], label="类型分派: --type grow -> 博格公式看分位")

    run("valuation.py", ["signal", "--type", "cycle", "--pb", "1.2",
                         "--percentile", "2", "--preset", "cal"],
        ["强周期指数", "历史较低区域", "--type cycle → pb"],
        label="类型分派: --type cycle -> 博格公式变种看 PB 分位")

    run("valuation.py", ["signal", "--type", "distress", "--pe", "20"],
        ["困境指数", "放弃", "--type distress → skip"],
        label="类型分派: --type distress -> 直接放弃")

    run("valuation.py", ["signal", "--type", "成长", "--pe", "31.62",
                         "--percentile", "17.4", "--preset", "cal"],
        ["高速成长指数"], label="类型分派: 中文别名 --type 成长 也认")

    # 不指定类型时必须先让使用者过一遍分类自查, 不能默默套默认方法
    run("valuation.py", ["signal", "--pe", "31.62", "--percentile", "17.4",
                         "--preset", "cal"],
        ["分类自查", "2) 高速成长", "3) 强周期", "4) 长期亏损"],
        label="默认路径: 没给 --type 时打印分类自查四问")

    run_err("valuation.py", ["signal", "--type", "value", "--method", "bog",
                             "--pe", "31", "--percentile", "10"],
            ["冲突"], label="拒绝: --type 与 --method 自相矛盾")
    run_err("valuation.py", ["signal", "--type", "乱七八糟", "--pe", "10"],
            ["未知的 --type"], label="拒绝: 不认识的类型")

    # 组合层: 候选用类型写法, 且混合方法时深度必须归一(否则分位法压过盈利率法)
    run("valuation.py", ["portfolio", "--budget", "3000", "--preset", "cal",
                         "--candidates",
                         "000922:9.12:1:value:30,399006:31.62:1:grow:17.4,399975:1.20:1:cycle:2.0"],
        ["yield", "bog", "pb", "深度已按各自方法的门槛归一"],
        label="组合: 候选可写类型, 混合方法时深度归一后分配")

    # ---------- 15. 每日跟踪器 (全离线: 用 fixtures 造两天的数据) ----------
    if not td:
        print("[skip] 每日跟踪器 (找不到可写临时目录)")
    else:
        import json as _json
        import shutil as _sh

        tdir = os.path.join(td, "track")
        fx1 = os.path.join(td, "fx1")
        fx2 = os.path.join(td, "fx2")
        for x in (tdir, fx1, fx2):
            _sh.rmtree(x, ignore_errors=True)
            os.makedirs(x, exist_ok=True)

        def _wfx(where, code, pe, pb, pct, div, roe):
            """写一份假的蛋卷 index_eva 响应（份额制：分位/股息/ROE 传百分数）。"""
            with open(os.path.join(where, code + ".json"), "w", encoding="utf-8") as f:
                _json.dump({"data": {
                    "pe": pe, "pb": pb,
                    "pe_percentile": pct / 100.0, "pb_percentile": pct / 100.0,
                    "yeild": div / 100.0, "roe": roe / 100.0, "ts": 1792080000000,
                }}, f)

        # 第 1 天: 红利 PE 11（盈利率 9.09% -> 持有），分位 50
        _wfx(fx1, "SH000922", 11.0, 1.00, 50.0, 3.0, 10.0)
        _wfx(fx1, "SZ399006", 30.0, 4.00, 15.0, 1.0, 14.0)
        # 第 2 天: 红利 PE 9（盈利率 11.11% -> 买入），分位 20（进入低估区）
        _wfx(fx2, "SH000922", 9.0, 0.90, 20.0, 4.0, 10.0)
        _wfx(fx2, "SZ399006", 32.0, 4.20, 18.0, 1.0, 14.0)

        run("tracker.py", ["init", "--dir", tdir, "--force"], ["已初始化"],
            label="跟踪: init 建清单")
        # type 的来历必须显式交代：它来自书第3章的描述性分类，不是当期数据结论
        run("tracker.py", ["init", "--dir", tdir + "-type", "--force"],
            ["类型自查", "描述性分类", "不是当期数据"],
            label="跟踪: init 交代 type 的来历（第3章描述 ≠ 当期结论）")
        run("tracker.py", ["watch", "--dir", tdir, "--remove", "000300,000016,000905,HSCEI,HSI"],
            ["已移除"], label="跟踪: 移除不需要的品种")
        run("tracker.py", ["watch", "--dir", tdir, "--add",
                           "000922:中证红利:value:100032:0.5;399006:创业板指:grow::0.5"],
            ["type=value", "type=grow"], label="跟踪: 写回类型与目标仓位")

        run("tracker.py", ["collect", "--dir", tdir, "--date", "2026-10-19",
                           "--fixtures", fx1, "--no-close"],
            ["[ok]", "写入 2 条"], forbid=["[失败]"], label="跟踪: 第 1 天抓数（离线）")
        run("tracker.py", ["collect", "--dir", tdir, "--date", "2026-10-20",
                           "--fixtures", fx2, "--no-close"],
            ["[ok]", "写入 2 条"], forbid=["[失败]"], label="跟踪: 第 2 天抓数（离线）")

        out = run("tracker.py", ["analyze", "--dir", tdir, "--preset", "cal",
                                 "--base", "1000", "--budget", "1000"],
                  ["分批投资", "历史较低区域", "进入低估区", "PE 由 11.00 变为 9.00",
                   "留作债基/货基", "判据分层", "数据日 2026-10-20"],
                  label="跟踪: 出信号 + 跨线提醒 + 资金分配 + 判据分层交代")

        # 用旧快照出信号必须被点名：拿几年前的 PE 当今天的价格做决定，是这个工具最危险的用法。
        # 单独开一个只装了旧快照的目录（否则 max(数据日) 仍是今天，永远不触发）。
        stale = tdir + "-stale"
        run("tracker.py", ["init", "--dir", stale, "--force"], ["已初始化"],
            label="跟踪: 建旧数据目录")
        run("tracker.py", ["watch", "--dir", stale, "--remove", "000300,000016,000905,399006,HSCEI,HSI"],
            ["已移除"], label="跟踪: 旧数据目录只留一只")
        run("tracker.py", ["collect", "--dir", stale, "--date", "2020-01-02",
                           "--fixtures", fx2, "--no-close"],
            ["[ok]"], label="跟踪: 抓一份 2020 年的旧快照")
        run("tracker.py", ["analyze", "--dir", stale, "--preset", "cal", "--max-age", "3"],
            ["数据不是今天的", "再判断"], label="跟踪: 旧数据必须报警（--max-age）")

        # 分配上限必须服从「本期建议金额」，不能被目标仓位二次覆盖
        # 红利 PE 9 -> 盈利率 11.11%，建议金额 = 1000 × 11.11/10 = 1111.11（> 预算，全额给 1000 前先封顶）
        # 创业板 分位 18 < 30 -> 博格法买入，建议金额 = 1000 × 30/18 = 1666.67
        # 两只都超过预算 1000，各封 40% = 400 -> 余 200
        if out and "400.00" not in out:
            FAILS.append(("跟踪分配上限", "两只都应封在单只上限 400 元", out[:300], []))
            print("[FAIL] 跟踪分配应受单只上限 400 约束")
        else:
            print("[ok]   跟踪分配: 单只上限 400 元生效, 余量留债基")

        # 分位法建议金额必须用「买入分位/当前分位」，不能拿 3% 的盈利率比 10% 的买入线
        jout2 = run("tracker.py", ["analyze", "--dir", tdir, "--preset", "cal",
                                   "--base", "1000", "--json"],
                    ['"amount"'], label="跟踪: 建议金额按方法分派")
        if jout2:
            try:
                obj2 = _json.loads(jout2[jout2.index("{"):jout2.rindex("}") + 1])
                growth = [x for x in obj2["items"] if x["method"] == "bog"][0]
                if abs(growth["amount"] - 1000.0 * 30.0 / 18.0) > 1.0:
                    FAILS.append(("跟踪分位法金额", "创业板应按 30/18 放大到约 1667",
                                  str(growth["amount"]), []))
                    print("[FAIL] 分位法建议金额用了错误的量纲")
                else:
                    print("[ok]   跟踪分位法金额: 30%%/18%% -> 约 1667 元（不是按盈利率算的 292）")
            except Exception as e:
                FAILS.append(("跟踪分位法金额", "JSON 解析失败", repr(e), []))
                print("[FAIL] 跟踪分位法金额 JSON 解析失败: %r" % e)

        # 港股精确口径必须能跨过买入线（粗 9 折 9.62% vs 精确 10.06%）
        _wfx(fx2, "HKHSCEI", 9.35, 0.94, 40.0, 3.14, 10.05)
        run("tracker.py", ["watch", "--dir", tdir, "--add", "HSCEI:恒生国企:value::0.2"],
            ["type=value"], label="跟踪: 加入港股品种")
        run("tracker.py", ["collect", "--dir", tdir, "--date", "2026-10-20",
                           "--fixtures", fx2, "--no-close"],
            ["[ok]"], forbid=["[失败]"], label="跟踪: 港股抓数（离线）")
        hk1 = run("tracker.py", ["analyze", "--dir", tdir, "--preset", "cal"],
                  ["港股粗口径", "坚定持有"], label="跟踪: 港股粗口径 9 折")
        hk2 = run("tracker.py", ["analyze", "--dir", tdir, "--preset", "cal", "--hk-exact"],
                  ["港股精确口径", "分批投资", "10.0"], label="跟踪: 港股精确口径跨过买入线")

        # 机器输出要能被程序吃下去（JSON 合法且计数自洽）
        jout = run("tracker.py", ["analyze", "--dir", tdir, "--preset", "cal",
                                  "--base", "1000", "--budget", "1000", "--json"],
                   ['"counts"'], label="跟踪: --json 机器输出")
        if jout:
            try:
                obj = _json.loads(jout[jout.index("{"):jout.rindex("}") + 1])
                nbuy = obj["counts"]["buy"]
                n_alloc = len(obj["allocation"])
                if nbuy != 2 or n_alloc < 1:
                    FAILS.append(("跟踪: JSON 自洽",
                                  "预期买 2 只、有分配行，实际买 %s / 分配 %s 行"
                                  % (nbuy, n_alloc), "", ("--json",)))
                    print("[FAIL] 跟踪: JSON 自洽")
                else:
                    print("[ok]   跟踪: JSON 自洽 (买 %d 只 / 分配 %d 行)" % (nbuy, n_alloc))
            except (ValueError, KeyError) as e:
                FAILS.append(("跟踪: JSON 自洽", "解析失败: %s" % e, jout[:300], ("--json",)))
                print("[FAIL] 跟踪: JSON 自洽")

        rout = run("tracker.py", ["report", "--dir", tdir, "--preset", "cal",
                                 "--budget", "1000", "--base", "1000"],
                   ["日报已生成"], label="跟踪: 生成日报")
        if rout:
            import re as _re
            m = _re.search(r"日报已生成:\s*(.+\.md)", rout)
            if m and os.path.exists(m.group(1).strip()):
                with open(m.group(1).strip(), "r", encoding="utf-8") as f:
                    body = f.read()
                need = ["## 一、今日信号", "## 五、数据与方法声明", "条件推算"]
                if all(x in body for x in need):
                    print("[ok]   跟踪: 日报落盘且含五个小节")
                else:
                    FAILS.append(("跟踪: 日报内容", "缺少段落 %s" % need, body[:300], ("report",)))
                    print("[FAIL] 跟踪: 日报内容")
            else:
                FAILS.append(("跟踪: 日报内容", "报告文件没生成", rout[:300], ("report",)))
                print("[FAIL] 跟踪: 日报内容")

        run("tracker.py", ["show", "--dir", tdir], ["最近", "000922"],
            label="跟踪: show 打印多日 PE")
        run_err("tracker.py", ["collect", "--dir", os.path.join(td, "nope")],
                ["还没有关注清单"], label="拒绝: 未初始化就跑 collect")
        run_err("tracker.py", ["watch", "--dir", tdir, "--add", "000300:沪深300:乱写类型"],
                ["类型只能是"], label="拒绝: 不认识的品种类型")

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
