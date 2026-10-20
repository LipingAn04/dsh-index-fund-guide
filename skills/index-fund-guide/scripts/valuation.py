#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
指数基金估值决策器 —— 规则来自《指数基金投资指南》(银行螺丝钉) 第 4 章。

只实现规则的算术, 不抓取行情。所有估值数字必须由使用者提供并注明口径。

子命令:
  signal     给买卖信号。三种方法各管一类指数(见下), 不再用一把尺子量所有品种
  calibrate  用当期无风险利率 / 债基收益 重新推导买卖线, 并对照书中 2017 口径
  bog        博格公式: 拆解预期年复合收益率的三个来源
  pb         博格公式变种 (PB 版): 用于盈利不稳定的周期指数
  amount     定期不定额: 按"越低买越多"放大本月定投金额
  position   当前估值处在历史区间的什么位置(真实分位优先, 线性近似会明确降级)
  portfolio  一笔月度预算分给多个候选品种, 含单只/重叠组上限
  backtest   用"日期+PE+点位"的历史序列检验规则本身(书中回测的本地复现工具)
  sources    取数清单: 每个指标该去哪查, 口径差异在哪

方法分派(书中表 4.2, 这是本工具最重要的一条约束):
  yield  盈利稳定的价值指数(上证50/红利/沪深300/H股) -> 盈利收益率法, 用 PE
  bog    高速成长的指数(创业板/中证500/纳指100)       -> 博格公式, 用 PE 历史分位
  pb     盈利不稳但不会长期亏损的周期指数(证券/能源)  -> 博格公式变种, 用 PB 历史分位
  skip   长期亏损的困境指数(某些资源国股市)          -> 放弃, 不投

关键约定:
  --pe-type 必须说明用的是哪种 PE。静态(LYR)与滚动(TTM)能差 8% 以上,
  足以让信号从"持有"跳到"买入"。同时给出两个口径 (--pe 与 --pe-alt) 时,
  若信号不一致会明确报警。
  分位方法的 30%/70% 分界线是**本工具自定**的, 书中只写"历史较低/较高区域",
  没有给数字。凡是靠它得出的动作, 输出里都会标明 ZONE_SOURCE。

示例:
  python valuation.py signal --pe 9.01 --pe-type ttm
  python valuation.py signal --type grow --pe 31.90 --pe-type ttm --percentile 18.2
  python valuation.py signal --pe 10.70 --pe-type ttm --payout 0.5 --tax 0.2
  python valuation.py calibrate --riskfree 1.65 --bond-avg 3.0
  python valuation.py bog --dividend 1.86 --pe-now 19.71 --pe-future 13.17 --years 10 --earnings-growth 14.4
  python valuation.py position --percentile 55.5
  python valuation.py portfolio --budget 2000 --candidates "000922:9.01:1:yield,399006:31.9:1:bog:18.2"
  python valuation.py backtest --series sh000016.csv --amount 1000
"""

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys

# Windows 控制台常为 GBK/CP936, 遇到底层编码放不下的字符不要崩, 退化为替代符即可
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

# ============ 阈值锚点 ============
# 书中口径: 《指数基金投资指南》2017 年 5 月的数据
BOOK_AS_OF = "2017-05"
BOOK_RISKFREE = 3.5      # 2017-05-31 十年期国债收益率(书中取值)
BOOK_BOND_AVG = 6.4      # 国内债券基金长期平均年化收益(书中取值)
BOOK_BUY = 10.0          # 盈利收益率 > 10% 分批投资
BOOK_SELL = 6.4          # 盈利收益率 < 6.4% 分批卖出

# 校准口径: 2026-10 的当期数据, 由 calibrate 子命令推导, 详见 references/07-calibration.md
# 推导依据:
#   10Y 国债 1.648%(2026-10-16) vs 2017-05 的 3.5%;
#   债基前瞻可持续年化 2.2%~3.0%(近5年已实现 2.6%~3.2%), 取 3.0,
#   而非 22 年均值 4.43%(含 2014~2016 降息牛的不可复制资本利得)。
CAL_AS_OF = "2026-10-16"
CAL_RISKFREE = 1.65      # 中国 10 年期国债收益率 2026-10-19 约 1.649%
CAL_BOND_AVG = 3.0       # 债基前瞻可持续年化, 见 references/07-calibration.md
CAL_BUY = 10.0           # = max(10%, 2 x 1.65% = 3.3%): 10% 这条绝对线仍是最严约束
CAL_SELL = 3.0           # = 债基前瞻年化(教材原义: 股票收益已不高于债券)
CALRP_BUY = 6.6          # 风险溢价平移口径: 3.0 + (书中 10.0 - 6.4) 的 3.6pct 价差
CALRP_SELL = 3.0
CAL_SELL_PE_WARN = 30.0  # 卖出线隐含 PE 高于此值时, 单靠本方法会"永不卖出", 必须叠加分位判断

# 港股折价: 书中直接说"盈利收益率打 9 折"。9 折的来历其实可以推出来:
#   折价 = 分红率 x (分红税 + 换汇损耗)。分红率 50% x 税率 20% = 10% 折价, 正是 0.9。
# 所以不要背 0.9, 用 --payout / --tax 按自家渠道算, 默认值能复现 0.9。
HK_PAYOUT = 0.5
HK_TAX = 0.2
HK_DISCOUNT = 0.9        # = 1 - HK_PAYOUT * HK_TAX, 保留作兼容常量

# 分位法(博格公式路径)的区间分界 —— 本工具自定, 书中没有给数字!
ZONE_BUY_PCT = 30.0
ZONE_SELL_PCT = 70.0
ZONE_SOURCE = "本工具自定分界线(书中只写'历史较低/较高区域', 未给数字)"

METHODS = ("yield", "bog", "pb", "skip")
METHOD_LABEL = {
    "yield": "盈利收益率法(盈利稳定的价值指数)",
    "bog": "博格公式(高速成长指数, 看 PE 分位)",
    "pb": "博格公式变种(周期指数, 看 PB 分位)",
    "skip": "放弃(长期亏损的困境指数)",
}

# 书里表 4.2: 四类指数 → 四种方法。--type 是给使用者的入口, --method 是底层覆盖。
TYPE_METHOD = {
    "value": "yield",
    "grow": "bog",
    "cycle": "pb",
    "distress": "skip",
}
TYPE_LABEL = {
    "value": "盈利稳定的价值指数(银行/白酒/红利/上证50 类)",
    "grow": "高速成长指数(创业板/中证500/纳指 类)",
    "cycle": "强周期指数(证券/能源/钢铁/航运 类)",
    "distress": "长期亏损的困境指数",
}
# 中英文别名 -> 规范类型
TYPE_ALIASES = {
    "value": "value", "价值": "value", "稳定": "value", "盈利稳定": "value",
    "grow": "grow", "成长": "grow", "高速成长": "grow", "增长": "grow",
    "cycle": "cycle", "周期": "cycle", "强周期": "cycle",
    "distress": "distress", "困境": "distress", "亏损": "distress",
}


def normalize_type(token):
    """把 --type 的输入归一到 value/grow/cycle/distress; 不认识就返回 None。"""
    if token is None:
        return None
    t = str(token).strip()
    return TYPE_ALIASES.get(t) or TYPE_ALIASES.get(t.lower())


def resolve_method(a):
    """把 --type / --method 归成一个方法。

    返回 (method, explicit, note):
      explicit=True 表示使用者明确指定过类型或方法;
      explicit=False 表示落到了默认值, 调用方应当打印分类自查提示。
    """
    tkey = normalize_type(getattr(a, "type", None))
    raw_type = getattr(a, "type", None)
    if raw_type is not None and tkey is None:
        raise SystemExit("未知的 --type: %s（可用: value/价值, grow/成长, cycle/周期, "
                         "distress/困境）" % raw_type)
    m = getattr(a, "method", None)
    if tkey is not None:
        mapped = TYPE_METHOD[tkey]
        if m is not None and m != mapped:
            raise SystemExit("--type %s 对应 --method %s, 但你又显式给了 --method %s。"
                             "两者冲突, 请只保留一个。" % (tkey, mapped, m))
        return mapped, True, "--type %s → %s" % (tkey, mapped)
    if m is not None:
        return m, True, None
    return "yield", False, None


TYPE_CHECK_HINT = (
    "[!] 没有指定 --type, 按默认「价值指数 → 盈利收益率法」处理。先过一遍分类自查:\n"
    "    1) 长期盈利稳定、常年分红(银行/白酒/红利/上证50)  -> 就是默认, 保持\n"
    "    2) 高速成长但盈利波动大(创业板/中证500/纳指)      -> 加 --type grow\n"
    "    3) 强周期, 盈利大起大落(证券/能源/钢铁/航运)      -> 加 --type cycle\n"
    "    4) 长期亏损、看不到翻身(困境行业)                 -> 加 --type distress(直接放弃)\n"
    "    分派依据: 书里表 4.2 / references/01-valuation.md 第六节"
)

STALE_MONTHS = 12        # 锚点超过这个月数就该重算


def preset(name):
    if name == "book":
        return {"as_of": BOOK_AS_OF, "riskfree": BOOK_RISKFREE,
                "bond_avg": BOOK_BOND_AVG, "buy": BOOK_BUY, "sell": BOOK_SELL,
                "label": "书中 2017 口径"}
    if name == "cal":
        return {"as_of": CAL_AS_OF, "riskfree": CAL_RISKFREE,
                "bond_avg": CAL_BOND_AVG, "buy": CAL_BUY, "sell": CAL_SELL,
                "label": "当期校准-教材直译 %s" % CAL_AS_OF}
    if name == "cal-rp":
        return {"as_of": CAL_AS_OF, "riskfree": CAL_RISKFREE,
                "bond_avg": CAL_BOND_AVG, "buy": CALRP_BUY, "sell": CALRP_SELL,
                "label": "当期校准-风险溢价平移 %s" % CAL_AS_OF}
    raise SystemExit("未知口径: %s" % name)


def lines_for(a):
    """按命令行参数决定用哪套阈值, 返回 (buy, sell, 来源标签)。"""
    p = preset(getattr(a, "preset", "book"))
    buy = a.buy_line if getattr(a, "buy_line", None) is not None else p["buy"]
    sell = a.sell_line if getattr(a, "sell_line", None) is not None else p["sell"]
    label = p["label"]
    if getattr(a, "buy_line", None) is not None or getattr(a, "sell_line", None) is not None:
        label = "自定义口径"
    return float(buy), float(sell), label


def anchors_for(a):
    """返回 (buy, sell, label, as_of)。"""
    p = preset(getattr(a, "preset", "book"))
    buy, sell, label = lines_for(a)
    return buy, sell, label, p["as_of"]


def fmt(x, nd=2):
    return ("%." + str(nd) + "f") % x


# ============ 日期与过期判断 ============

def parse_as_of(s):
    """'2017-05' / '2026-10-16' / '2026-10-16 收盘' -> date, 取该期最后一天。"""
    s = str(s).strip()
    m = re.match(r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", s)
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    if m.group(3):
        d = int(m.group(3))
    elif mo == 12:
        y, mo, d = y + 1, 1, 1
        d = 0
    else:
        d = 0
    try:
        if d == 0:                      # 只有年月 -> 取月末
            nxt = dt.date(y + (1 if mo == 12 else 0), 1 if mo == 12 else mo + 1, 1)
            return nxt - dt.timedelta(days=1)
        return dt.date(y, mo, d)
    except ValueError:
        return None


def months_since(as_of, today=None):
    d = parse_as_of(as_of)
    if d is None:
        return None
    t = today or dt.date.today()
    return (t.year - d.year) * 12 + (t.month - d.month)


def stale_warning(as_of, who="阈值"):
    m = months_since(as_of)
    if m is None:
        return "[i] %s锚点日期无法解析: %s" % (who, as_of)
    if m > STALE_MONTHS:
        return ("[!] %s锚点 %s, 距今 %d 个月, 已超过 %d 个月。盈利收益率法的两条线都是利率的函数,"
                " 请用 calibrate 重新推导; 分位法的历史区间也会漂移。"
                % (who, as_of, m, STALE_MONTHS))
    return ("[i] %s锚点 %s, 距今 %d 个月(未超过 %d 个月)。利率变动超过 50bp 就该重算,"
            " 别把锚点当常数。" % (who, as_of, m, STALE_MONTHS))


# ============ 三套判定 ============

def earnings_yield(pe, discount=1.0):
    """盈利收益率 = 1/PE * discount, 返回百分数"""
    if pe is None or pe <= 0:
        raise SystemExit("PE 必须为正数(亏损品种无法用市盈率/盈利收益率估值)")
    return (1.0 / pe) * 100.0 * discount


def hk_discount(payout, tax):
    """港股折价系数 = 1 - 分红率 x (分红税 + 换汇损耗, 两者合并为 tax)。"""
    if not (0 <= payout <= 1) or not (0 <= tax <= 1):
        raise SystemExit("--payout / --tax 应为 0~1 之间的小数(如 0.5 / 0.2)")
    return 1.0 - payout * tax


def judge_yield(y, buy, sell):
    if y > buy:
        return "buy", "分批投资", "盈利收益率 %s%% > 买入线 %s%%, 处于低估区, 按月坚持定投" % (fmt(y), fmt(buy, 1))
    if y >= sell:
        edge = " (恰好落在边界上, 按保守方向处理: 等于买入线不算买入)"
        if abs(y - buy) < 1e-9 or abs(y - sell) < 1e-9:
            edge = " (恰好落在边界上, 按保守方向处理: 等于买入线不算买入、等于卖出线不算卖出)"
        return "hold", "坚定持有", "盈利收益率 %s%% 落在 %s%%~%s%% 的持有区, 停止新增定投, 已买份额继续持有%s" % (fmt(y), fmt(sell, 1), fmt(buy, 1), edge)
    return "sell", "分批卖出", "盈利收益率 %s%% < 卖出线 %s%%, 已不如债券基金; 份额分 10 份, 每月卖 1 份" % (fmt(y), fmt(sell, 1))


def zone_signal(pos_pct, buy_pct=None, sell_pct=None):
    """按历史分位给信号。pos_pct 越低越便宜。"""
    bp = ZONE_BUY_PCT if buy_pct is None else float(buy_pct)
    sp = ZONE_SELL_PCT if sell_pct is None else float(sell_pct)
    if not (0 <= bp < sp <= 100):
        raise SystemExit("分位分界线应满足 0 <= 买入分位 < 卖出分位 <= 100")
    if pos_pct < bp:
        return "buy", "历史较低区域", "分位 %s%% < %s%%, 大概率处于低估区, 可开始/继续定投" % (fmt(pos_pct, 1), fmt(bp, 0))
    if pos_pct <= sp:
        return "hold", "历史中位区域", "分位 %s%% 在 %s%%~%s%% 之间, 估值正常, 停止新增, 已买份额持有" % (fmt(pos_pct, 1), fmt(bp, 0), fmt(sp, 0))
    return "sell", "历史较高区域", "分位 %s%% > %s%%, 大概率处于高估区, 分批卖出" % (fmt(pos_pct, 1), fmt(sp, 0))


def judge_method(method, pe=None, pb=None, percentile=None, discount=1.0,
                 buy=None, sell=None, buy_pct=None, sell_pct=None,
                 yield_override=None):
    """统一入口。返回 dict: signal/action/reason, 以及用到的中间量。

    yield_override: 已经算好的盈利收益率(%), 用于港股"只对分红部分扣税"的精确模式。
    """
    out = {"method": method, "signal": None, "action": None, "reason": None,
           "earnings_yield_pct": None, "percentile": percentile}
    if method == "skip":
        out.update(signal="skip", action="放弃",
                   reason="长期亏损的困境指数: 亏损会侵蚀净资产, 盈利收益率与 PB 都会失效, 直接不投")
        return out
    if method == "yield":
        y = yield_override if yield_override is not None else earnings_yield(pe, discount)
        code, action, why = judge_yield(y, buy, sell)
        out.update(signal=code, action=action, reason=why,
                   earnings_yield_pct=round(y, 4))
        return out
    # bog / pb: 用分位
    if percentile is None:
        raise SystemExit("method=%s 必须给出 --percentile(该品种 %s 的历史分位 0~100)。"
                         "没有分位就无法判断高低, 不要退回用绝对 PE/PB 硬套。"
                         % (method, "PE" if method == "bog" else "PB"))
    code, action, why = zone_signal(percentile, buy_pct, sell_pct)
    if method == "pb" and pb is not None:
        out["pb"] = pb
    out.update(signal=code, action=action, reason=why)
    return out


def method_hint(method, pe):
    """方法可能配错的提醒。"""
    if method == "yield" and pe is not None and pe > 25:
        return ("[!] PE %s 偏高。若该指数是高速成长(创业板/中证500/纳指)或强周期(证券/能源/钢铁),"
                " 盈利收益率法本就不适用(书里表 4.2), 请改用 --type grow 或 --type cycle"
                " 并给 --percentile。" % fmt(pe, 2))
    if method in ("bog", "pb") and pe is not None and pe < 12:
        return ("[!] PE %s 很低, 该品种更像'盈利稳定的价值指数', 用 --type value 更合适。"
                % fmt(pe, 2))
    return None


# ============ 子命令 ============

def cmd_signal(a):
    buy, sell, label, as_of = anchors_for(a)
    method, method_explicit, type_note = resolve_method(a)

    discount, disc_note = a.discount, None
    if not a.hk_exact and (a.payout is not None or a.tax is not None):
        payout = HK_PAYOUT if a.payout is None else a.payout
        tax = HK_TAX if a.tax is None else a.tax
        discount = hk_discount(payout, tax)
        disc_note = "折价系数由 分红率 %s x 税率 %s 推出: 1 - %s x %s = %s" % (
            fmt(payout, 2), fmt(tax, 2), fmt(payout, 2), fmt(tax, 2), fmt(discount, 2))

    # 港股"精确模式": 分红税只该打在分红上, 不该对全部盈利打折。
    y_override, hk_note = None, None
    if a.hk_exact:
        if a.dividend is None or a.tax is None or a.pe is None:
            raise SystemExit("--hk-exact 需要同时给 --dividend(股息率%) / --tax(税率) / --pe")
        y_raw0 = (1.0 / a.pe) * 100.0
        if a.dividend > y_raw0:
            raise SystemExit("股息率 %s%% 高于盈利收益率 %s%%, 数据不自洽, 请检查输入"
                             % (fmt(a.dividend, 2), fmt(y_raw0, 2)))
        y_override = y_raw0 - a.dividend * a.tax
        hk_note = ("精确模式: 分红税只作用于分红部分 —— 盈利收益率 %s%% - 股息率 %s%% x 税率 %s"
                   " = %s%%(粗口径整体 9 折会得到 %s%%, 系统性偏低)"
                   % (fmt(y_raw0), fmt(a.dividend, 2), fmt(a.tax, 2), fmt(y_override),
                      fmt(y_raw0 * HK_DISCOUNT)))

    res = judge_method(method, pe=a.pe, pb=a.pb, percentile=a.percentile,
                       discount=discount, buy=buy, sell=sell,
                       buy_pct=a.buy_pct, sell_pct=a.sell_pct,
                       yield_override=y_override)

    raw = None
    if a.pe is not None and a.pe > 0:
        raw = (1.0 / a.pe) * 100.0

    # 交叉验证: 只要输入齐, 就把另一种方法也算一遍
    alt = None
    if method == "yield" and a.percentile is not None:
        c2, ac2, why2 = zone_signal(a.percentile, a.buy_pct, a.sell_pct)
        alt = {"kind": "分位法", "percentile": a.percentile, "signal": c2, "action": ac2}
    elif method in ("bog", "pb") and a.pe is not None and a.pe > 0:
        y2 = earnings_yield(a.pe, discount)
        c2, ac2, why2 = judge_yield(y2, buy, sell)
        alt = {"kind": "盈利收益率法", "earnings_yield_pct": round(y2, 4),
               "signal": c2, "action": ac2}

    d = {
        "method": method,
        "method_label": METHOD_LABEL[method],
        "method_explicit": method_explicit,
        "type_note": type_note,
        "pe": a.pe,
        "pb": a.pb,
        "pe_type": a.pe_type or "未注明",
        "percentile": a.percentile,
        "discount": round(discount, 4),
        "discount_note": disc_note,
        "hk_exact_note": hk_note,
        "earnings_yield_pct": res["earnings_yield_pct"],
        "earnings_yield_raw_pct": round(raw, 4) if raw is not None else None,
        "signal": res["signal"],
        "action": res["action"],
        "reason": res["reason"],
        "thresholds": {"buy_above": buy, "sell_below": sell, "source": label,
                       "anchor_as_of": as_of},
        "zone": {"buy_below_pct": a.buy_pct if a.buy_pct is not None else ZONE_BUY_PCT,
                 "sell_above_pct": a.sell_pct if a.sell_pct is not None else ZONE_SELL_PCT,
                 "source": ZONE_SOURCE},
        "alt": alt,
        "signals_conflict": bool(alt and alt["signal"] != res["signal"]),
        "hint": method_hint(method, a.pe),
        "stale": stale_warning(as_of),
    }
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return

    if not method_explicit:
        print(TYPE_CHECK_HINT)
        print()
    if type_note:
        print("品种类型           : %s   (%s)" % (TYPE_LABEL[normalize_type(a.type)], type_note))
    print("品种方法           : %s" % METHOD_LABEL[method])
    if a.pe is not None:
        print("市盈率 PE          : %s   (口径: %s)" % (fmt(a.pe, 3), a.pe_type or "未注明"))
    if a.pb is not None:
        print("市净率 PB          : %s   (口径: %s)" % (fmt(a.pb, 3), a.pb_type or "未注明"))
    if a.percentile is not None:
        print("历史分位           : %s%%" % fmt(a.percentile, 1))
    if disc_note:
        print("折价系数           : %s" % disc_note)
    elif discount != 1.0:
        print("折价系数           : %s (手工覆盖; 港股 0.9 的来历见 --payout/--tax)" % fmt(discount, 2))
    if hk_note:
        print("港股口径           : %s" % hk_note)
    if res["earnings_yield_pct"] is not None:
        print("盈利收益率(原始)   : %s%%" % fmt(raw))
        if y_override is not None:
            print("盈利收益率(税后)   : %s%%" % fmt(res["earnings_yield_pct"]))
        elif discount != 1.0:
            print("盈利收益率(折后)   : %s%%" % fmt(res["earnings_yield_pct"]))
        else:
            print("盈利收益率         : %s%%" % fmt(res["earnings_yield_pct"]))
    print()
    print("信号               : %s  (信号码 %s)" % (res["action"], res["signal"]))
    print("依据               : %s" % res["reason"])
    if method in ("yield",):
        print("阈值口径           : %s" % label)
        print("参考阈值           : 买入线 > %s%%   持有区 %s%%~%s%%   卖出线 < %s%%"
              % (fmt(buy, 1), fmt(sell, 1), fmt(buy, 1), fmt(sell, 1)))
    else:
        print("分位分界线         : 买入 <%s%%  持有 %s%%~%s%%  卖出 >%s%%"
              % (fmt(a.buy_pct if a.buy_pct is not None else ZONE_BUY_PCT, 0),
                 fmt(a.buy_pct if a.buy_pct is not None else ZONE_BUY_PCT, 0),
                 fmt(a.sell_pct if a.sell_pct is not None else ZONE_SELL_PCT, 0),
                 fmt(a.sell_pct if a.sell_pct is not None else ZONE_SELL_PCT, 0)))
        print("注意               : %s —— 动作结论的可信度低于 yield 方法" % ZONE_SOURCE)

    if method == "yield" and sell > 0 and (100.0 / sell) > CAL_SELL_PE_WARN:
        print()
        print("[!] 卖出线 %s%% 隐含 PE > %s 倍, 高于 A 股历史上绝大多数指数的高点。"
              % (fmt(sell, 1), fmt(100.0 / sell, 0)))
        print("    低利率把卖出线压到很低, 结果是本方法在当前环境几乎永远发不出'卖出'信号。")
        print("    不要把它理解为'可以一直不卖': 卖出判断请用 --type grow --percentile <分位>,")
        print("    或给 --sell-line 自定义。")

    if alt:
        print()
        if alt["kind"] == "分位法":
            print("交叉验证(分位法)   : 分位 %s%% -> %s" % (fmt(alt["percentile"], 1), alt["action"]))
        else:
            print("交叉验证(盈利收益率): %s%% -> %s"
                  % (fmt(alt["earnings_yield_pct"]), alt["action"]))
        if alt["signal"] != res["signal"]:
            print("[!] 两种方法给出的信号不一致。这通常说明该品种被错了方法:")
            print("    盈利收益率法适合盈利稳定的价值指数, 分位法适合成长/周期指数。")
            print("    先确认它属于哪一类(见 references/02-catalog.md 的分类表), 再采信对应方法的结论。")
        else:
            print("两种方法结论一致, 信号较可靠。")

    if d["hint"]:
        print()
        print(d["hint"])
    print()
    print(stale_warning(as_of))


def cmd_calibrate(a):
    if a.riskfree <= 0:
        raise SystemExit("--riskfree 必须为正数(百分数, 如 1.65)")
    if a.bond_avg <= 0:
        raise SystemExit("--bond-avg 必须为正数(百分数, 如 3.0)")
    buy = max(a.buy_floor, 2.0 * a.riskfree)
    sell = a.bond_avg + a.risk_premium
    book_buy, book_sell = BOOK_BUY, BOOK_SELL
    d = {
        "as_of": a.as_of,
        "riskfree_pct": a.riskfree,
        "bond_avg_pct": a.bond_avg,
        "risk_premium_pct": a.risk_premium,
        "buy_line_pct": round(buy, 4),
        "sell_line_pct": round(sell, 4),
        "buy_pe_below": round(100.0 / buy, 4),
        "sell_pe_above": round(100.0 / sell, 4) if sell > 0 else None,
        "book": {"buy_line_pct": book_buy, "sell_line_pct": book_sell,
                 "buy_pe_below": round(100.0 / book_buy, 4),
                 "sell_pe_above": round(100.0 / book_sell, 4)},
        "anchor_2017": {"riskfree_pct": BOOK_RISKFREE, "bond_avg_pct": BOOK_BOND_AVG},
        "variants": {
            "A_教材直译": {"buy": CAL_BUY, "sell": CAL_BOND_AVG},
            "B_风险溢价平移": {"buy": CALRP_BUY, "sell": CALRP_SELL},
        },
    }
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return
    print("阈值校准   数据日期 %s" % a.as_of)
    print("-" * 70)
    print("当期无风险利率(10年期国债) : %s%%   (2017 年书中用 %s%%)"
          % (fmt(a.riskfree, 2), fmt(BOOK_RISKFREE, 1)))
    print("当期债基跨周期平均收益     : %s%%   (2017 年书中用 %s%%)"
          % (fmt(a.bond_avg, 2), fmt(BOOK_BOND_AVG, 1)))
    print()
    print("买入线 = max(10%, 2 x 无风险利率)")
    print("       = max(10%%, %s%%) = %s%%   -> 对应 PE < %s"
          % (fmt(2 * a.riskfree, 2), fmt(buy, 2), fmt(100.0 / buy, 2)))
    print("卖出线 = 债基长期平均收益 + 风险溢价(%s%%)" % fmt(a.risk_premium, 2))
    print("       = %s%%            -> 对应 PE > %s"
          % (fmt(sell, 2), fmt(100.0 / sell, 2) if sell > 0 else "-"))
    print()
    print("对照书中 2017 口径: 买入线 %s%% (PE<%s)   卖出线 %s%% (PE>%s)"
          % (fmt(book_buy, 1), fmt(100.0 / book_buy, 2),
             fmt(book_sell, 1), fmt(100.0 / book_sell, 2)))
    print("-" * 70)
    print("两条线为什么不同步下降:")
    print("  买入线锚定的是'绝对安全边际'(格雷厄姆的 10% 硬门槛), 利率再低这条也不该松;")
    print("  卖出线锚定的是'持有股票的收益已不高于债券', 随债基收益一起下移。")
    print()
    sell_pe = (100.0 / sell) if sell > 0 else None
    if sell_pe and sell_pe > CAL_SELL_PE_WARN:
        print("校准后的直接后果: 持有区被拉成 PE %s~%s 的巨幅区间。"
              % (fmt(100.0 / buy, 1), fmt(sell_pe, 0)))
        print("  PE > %s 在 A 股历史上只出现在极少数泡沫时刻, 等于这套规则在低利率下'永不卖出'。"
              % fmt(sell_pe, 0))
        print("  结论: 卖出必须换判据 —— 用分位法 --type grow/cycle --percentile(>%s 分批卖出),"
              % fmt(ZONE_SELL_PCT, 0))
        print("        或把卖出线改成'债基收益 + 2~3pct 风险补偿'(见下方变体 B)。")
    print()
    print("两种校准变体, 取哪个是判断题:")
    print("  变体 A 教材直译: 买入线 %s%% (PE<%s)  卖出线 = 债基前瞻 %s%% (PE>%s)"
          % (fmt(CAL_BUY, 1), fmt(100.0 / CAL_BUY, 1), fmt(CAL_BOND_AVG, 1),
             fmt(100.0 / CAL_SELL, 0) if CAL_SELL > 0 else "-"))
    print("     → 保住格雷厄姆的 10% 绝对底线, 但卖出条件失效, 需靠分位兜底。")
    print("  变体 B 风险溢价平移: 买入线 %s%% (PE<%s)  卖出线 %s%% (PE>%s)"
          % (fmt(CALRP_BUY, 1), fmt(100.0 / CALRP_BUY, 1), fmt(CALRP_SELL, 1),
             fmt(100.0 / CALRP_SELL, 0)))
    print("     → 保持书中'股票盈利收益率高于债基 3.6pct'的价差不变, 承认利率下移抬高了估值中枢;")
    print("       代价是买入线从 PE<10 放宽到 PE<%s, 更激进, 也放弃了对整体泡沫的防护。"
          % fmt(100.0 / CALRP_BUY, 1))
    print("  默认仍用 book 口径(最保守); cal / cal-rp 只是把'当期环境'摆出来, 不替你选。")


def cmd_bog(a):
    if a.years <= 0:
        raise SystemExit("years 必须为正")
    pe_cagr = ((a.pe_future / a.pe_now) ** (1.0 / a.years) - 1.0) * 100.0 if a.pe_now > 0 else 0.0
    div = a.dividend
    earn = a.earnings_growth
    simple = div + pe_cagr + earn                        # 书中口径: 三项直接相加
    compound = ((1 + div / 100.0) * (1 + pe_cagr / 100.0) * (1 + earn / 100.0) - 1.0) * 100.0
    d = {
        "years": a.years,
        "dividend_yield_pct": div,
        "pe_now": a.pe_now,
        "pe_future": a.pe_future,
        "pe_cagr_pct": round(pe_cagr, 4),
        "earnings_growth_pct": earn,
        "expected_cagr_simple_pct": round(simple, 4),
        "expected_cagr_compound_pct": round(compound, 4),
    }
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return
    print("博格公式: 年复合收益率 = 初始股息率 + 每年PE变化率 + 每年盈利增长率")
    print("-" * 62)
    print("① 初始股息率        : %+8.2f%%" % div)
    print("② 每年PE变化率      : %+8.2f%%   (PE %s -> %s, %s 年)"
          % (pe_cagr, fmt(a.pe_now, 2), fmt(a.pe_future, 2), a.years))
    print("③ 每年盈利增长率    : %+8.2f%%" % earn)
    print("-" * 62)
    print("书中口径(三项相加)  : %+8.2f%%" % simple)
    print("乘法合成(精确复利)  : %+8.2f%%" % compound)
    print()
    print("注: 书中沪深300 十年实例 1.86% + (-4%) + 14.4% = 12.26%, 用加法口径;")
    print("    两者差异随年限与幅度放大, 长期推算用乘法结果更稳。")


def cmd_pb(a):
    if a.years <= 0:
        raise SystemExit("years 必须为正")
    pb_cagr = ((a.pb_future / a.pb_now) ** (1.0 / a.years) - 1.0) * 100.0
    simple = pb_cagr + a.book_growth
    compound = ((1 + pb_cagr / 100.0) * (1 + a.book_growth / 100.0) - 1.0) * 100.0
    d = {
        "years": a.years,
        "pb_now": a.pb_now,
        "pb_future": a.pb_future,
        "pb_cagr_pct": round(pb_cagr, 4),
        "book_growth_pct": a.book_growth,
        "expected_cagr_simple_pct": round(simple, 4),
        "expected_cagr_compound_pct": round(compound, 4),
    }
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return
    print("博格公式变种(PB版): 年复合收益率 = 每年PB变化率 + 每年净资产变化率")
    print("适用: 盈利不稳定/周期性, 但即便最差年份也不亏损的品种(如证券、能源)")
    print("-" * 62)
    print("① 每年PB变化率      : %+8.2f%%   (PB %s -> %s, %s 年)"
          % (pb_cagr, fmt(a.pb_now, 2), fmt(a.pb_future, 2), a.years))
    print("② 每年净资产增长率  : %+8.2f%%" % a.book_growth)
    print("-" * 62)
    print("书中口径(两项相加)  : %+8.2f%%" % simple)
    print("乘法合成(精确复利)  : %+8.2f%%" % compound)
    print()
    print("注: 该变种不计初始股息率 —— 盈利不稳定则难以分红。")


def cmd_amount(a):
    if a.n < 0:
        raise SystemExit("n 不能为负")
    if a.first <= 0 or a.now <= 0:
        raise SystemExit("first / now 必须为正")
    if a.base <= 0:
        raise SystemExit("基准金额必须为正数(收到 %s)" % a.base)
    if a.mode == "pe":
        ratio = a.first / a.now
        label = "首次PE / 当前PE"
    elif a.mode == "pb":
        ratio = a.first / a.now
        label = "首次PB / 当前PB"
    else:
        ratio = a.now / a.first
        label = "当前盈利收益率 / 首次定投时盈利收益率"
    mult = ratio ** a.n
    amount = a.base * mult
    d = {
        "mode": a.mode,
        "base": a.base,
        "first": a.first,
        "now": a.now,
        "n": a.n,
        "ratio": round(ratio, 6),
        "multiplier": round(mult, 6),
        "amount": round(amount, 2),
    }
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return
    print("定期不定额: 本月金额 = 基准金额 x (%s) ^ n" % label)
    print("-" * 62)
    print("基准金额      : %s" % fmt(a.base, 0))
    print("首次估值      : %s" % fmt(a.first, 4))
    print("当前估值      : %s" % fmt(a.now, 4))
    print("估值比值      : %s" % fmt(ratio, 4))
    print("放大器 n      : %s" % a.n)
    print("放大倍数      : %s" % fmt(mult, 4))
    print("-" * 62)
    print("本月定投金额  : %s 元" % fmt(amount, 2))
    print()
    print("提醒: n 越大, 低估时所需资金呈几何级增长。作者建议 n=1 已不错, 资金充裕最多 n=2。")
    if amount > a.base * 3:
        print("[!] 本月金额是基准的 %.1f 倍, 请确认现金流承受得住。" % (amount / a.base))


def load_series(path):
    """读一列历史估值(每行一个数, 或逗号/空格/分号分隔)。跳过非数字行(如表头)。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            text = f.read()
    except OSError as e:
        raise SystemExit("读不了历史序列文件: %s (%s)" % (path, e))
    vals = []
    for tok in re.split(r"[,\s;]+", text):
        tok = tok.strip()
        if not tok:
            continue
        try:
            vals.append(float(tok))
        except ValueError:
            continue
    if len(vals) < 30:
        raise SystemExit("历史序列只有 %d 个数据点, 太少算不出有意义的分位(建议至少 30 个)" % len(vals))
    return vals


def cmd_position(a):
    bp = ZONE_BUY_PCT if a.buy_pct is None else a.buy_pct
    sp = ZONE_SELL_PCT if a.sell_pct is None else a.sell_pct

    if a.percentile is not None:
        if not (0 <= a.percentile <= 100):
            raise SystemExit("--percentile 应在 0~100 之间")
        pos = a.percentile
        source = "真实历史分位(使用者提供)"
        exact = True
    elif a.series:
        if a.value is None:
            raise SystemExit("用 --series 时必须同时给 --value(当前值)")
        vals = load_series(a.series)
        pos = sum(1 for v in vals if v <= a.value) * 100.0 / len(vals)
        source = "真实历史分位(由 %d 个历史点现算, 文件 %s)" % (len(vals), a.series)
        exact = True
    else:
        if a.value is None or a.low is None or a.high is None:
            raise SystemExit("要么给 --percentile, 要么同时给 --value --low --high")
        if a.high <= a.low:
            raise SystemExit("high 必须大于 low")
        pos = (a.value - a.low) / (a.high - a.low) * 100.0
        pos = max(0.0, min(100.0, pos))
        source = "线性近似(不是真实分位: 假设区间内均匀分布)"
        exact = False

    code, action, why = zone_signal(pos, bp, sp)

    d = {
        "value": a.value,
        "low": a.low,
        "high": a.high,
        "percentile": a.percentile,
        "series": a.series,
        "position_pct": round(pos, 2),
        "source": source,
        "exact_percentile": exact,
        "signal": code,
        "zone": action,
        "reason": why,
        "zone_thresholds": {"buy_below_pct": bp, "sell_above_pct": sp, "source": ZONE_SOURCE},
    }
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return

    if exact:
        if a.value is not None:
            print("当前值         : %s" % fmt(a.value, 2))
        print("历史分位       : %s%%  (%s)" % (fmt(pos, 1), source))
        print("区间判定       : %s" % action)
        print("建议           : %s" % why)
    else:
        print("历史区间       : %s ~ %s" % (fmt(a.low, 2), fmt(a.high, 2)))
        print("当前值         : %s" % fmt(a.value, 2))
        print("所处位置(线性) : %s%%  (%s)" % (fmt(pos, 1), source))
        print("区间判定       : %s   [仅供参考: 当前是线性近似, 不是真实分位]" % action)
        print("理由(近似)     : %s" % why)
        print()
        print("[!] 线性近似假设'区间内均匀分布', 而真实估值分布是右偏的:")
        print("    高估值区停留时间远短于低估值区, 同一个位置在真实分位里往往更靠前。")
        print("    要做真正的卖出判断, 用 --percentile 直接给真实分位, 或 --series 给历史序列。")
    print()
    print("分界线来源     : %s" % ZONE_SOURCE)
    print("  当前用: 买入 <%s%%  持有 %s%%~%s%%  卖出 >%s%%   (可用 --buy-pct/--sell-pct 改)"
          % (fmt(bp, 0), fmt(bp, 0), fmt(sp, 0), fmt(sp, 0)))
    print("  书中只写'处于历史较低区域/较高区域', 没有给具体数字; 这两个数不是书里的。")


def parse_candidates(spec):
    """候选格式: 代码:数值[:折价系数[:方法或类型[:分位]]], 逗号分隔。
    数值对 yield/bog 是 PE, 对 pb 是 PB, 对 skip 可省略。
    第 4 段既能写方法(yield/bog/pb/skip), 也能写类型(value/grow/cycle/distress, 含中文),
    写类型时自动映射, 例如 399006:31.9:1:grow:18.2。"""
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        bits = [b.strip() for b in part.split(":")]
        if len(bits) < 1 or not bits[0]:
            raise SystemExit("候选格式应为 代码:数值[:折价[:方法或类型[:分位]]], "
                             "例如 000922:9.01:1:yield 或 399006:31.9:1:grow:18.2")
        code = bits[0]
        method = bits[3] if len(bits) > 3 and bits[3] else "yield"
        tkey = normalize_type(method)
        if tkey is not None:
            method = TYPE_METHOD[tkey]
        if method not in METHODS:
            raise SystemExit("候选 %s 的方法/类型 %s 无效, 方法只能是 %s, 类型只能是 %s"
                             % (code, bits[3], "/".join(METHODS), "/".join(TYPE_METHOD)))
        val = None
        if len(bits) > 1 and bits[1]:
            try:
                val = float(bits[1])
            except ValueError:
                raise SystemExit("候选 %s 的数值不是数字: %s" % (code, bits[1]))
        elif method != "skip":
            raise SystemExit("候选 %s 缺少数值(PE 或 PB)" % code)
        disc = 1.0
        if len(bits) > 2 and bits[2]:
            try:
                disc = float(bits[2])
            except ValueError:
                raise SystemExit("候选 %s 的折价系数不是数字" % code)
        pct = None
        if len(bits) > 4 and bits[4]:
            try:
                pct = float(bits[4])
            except ValueError:
                raise SystemExit("候选 %s 的分位不是数字" % code)
        out.append({"code": code, "value": val, "discount": disc,
                    "method": method, "percentile": pct})
    if not out:
        raise SystemExit("没有解析到任何候选品种")
    return out


def _cap_weights(items, max_single=None, groups=(), max_group=None):
    """按上限压缩权重, 并把腾出来的份额按原始目标比例转给"还没到上限"的品种。

    items: [{'code','w'}]; groups: [(名, [code...])]。
    若约束本身不可行(例如 2 只候选 + 单只上限 40%, 40%x2 < 100%),
    不做静默违反: 退回原始比例并明确报告, 让使用者自己决定放宽哪一条。
    返回 (items, notes, infeasible_bool)。
    """
    n = len(items)
    if n == 0:
        return items, [], False
    target = {it["code"]: it["w"] for it in items}
    s0 = sum(target.values())
    if s0 <= 0:
        s0 = float(n)
    target = {k: v / s0 for k, v in target.items()}
    w = dict(target)
    notes = []
    infeasible = False

    def group_sums():
        d = {}
        for gname, members in groups:
            mem = [c for c in members if c in w]
            d[gname] = (mem, sum(w[c] for c in mem))
        return d

    for _ in range(200):
        changed = False
        # 1) 单只上限
        if max_single is not None:
            for c in list(w):
                if w[c] > max_single + 1e-12:
                    w[c] = max_single
                    changed = True
        # 2) 重叠组上限
        if max_group is not None and groups:
            for gname, (mem, tot) in group_sums().items():
                if tot > max_group + 1e-12:
                    sc = max_group / tot
                    for c in mem:
                        w[c] *= sc
                    changed = True
        if not changed:
            break
        # 3) 把损失的部分按目标比例补给"还有空间"的品种
        lost = 1.0 - sum(w.values())
        if lost <= 1e-12:
            break
        free = [c for c in w if not (max_single is not None and w[c] >= max_single - 1e-12)]
        if max_group is not None and groups:
            at_cap = set()
            for gname, (mem, tot) in group_sums().items():
                if abs(tot - max_group) < 1e-9:
                    at_cap.update(mem)
            free = [c for c in free if c not in at_cap]
        tp = sum(target[c] for c in free)
        if tp <= 0:
            infeasible = True
            break
        for c in free:
            w[c] += lost * target[c] / tp

    total = sum(w.values())
    if abs(total - 1.0) > 1e-6:
        infeasible = True
    if infeasible:
        # 退回原始目标比例, 并说清是哪条约束自己打架了
        w = dict(target)
        why = []
        if max_single is not None:
            cap_sum = max_single * n
            if cap_sum < 1.0 - 1e-9:
                why.append("单只上限 %s%% x %d 只 = %s%%, 凑不满 100%%"
                           % (fmt(max_single * 100, 0), n, fmt(cap_sum * 100, 0)))
        if max_group is not None and groups:
            for gname, members in groups:
                mem = [c for c in members if c in target]
                if mem and len(mem) == n and max_group < 1.0 - 1e-9:
                    why.append("组 %s 已包含全部 %d 只候选, 组上限 %s%% 与总和 100%% 矛盾"
                               % (gname, n, fmt(max_group * 100, 0)))
        notes.append("上限不可行(%s), 已退回按目标比例分配, 未强行满足该上限"
                     % ("; ".join(why) if why else "约束相互冲突"))
    else:
        if max_single is not None:
            hit = [c for c in w if abs(w[c] - max_single) < 1e-9]
            if hit:
                notes.append("单只上限 %s%% 生效: %s" % (fmt(max_single * 100, 0), ", ".join(hit)))
        if max_group is not None and groups:
            for gname, (mem, tot) in group_sums().items():
                if not mem:
                    notes.append("组 %s 的品种都没进入买入区, 本次未参与分配" % gname)
                elif tot < max_group - 1e-9:
                    notes.append("组 %s 合计 %s%%, 上限 %s%% 未触及"
                                 % (gname, fmt(tot * 100, 1), fmt(max_group * 100, 0)))
                else:
                    notes.append("组 %s 合计 %s%%, 已触及上限 %s%%, 超出部分转给了组外品种"
                                 % (gname, fmt(tot * 100, 1), fmt(max_group * 100, 0)))
    for it in items:
        it["w"] = w[it["code"]]
    return items, notes, infeasible


def cmd_portfolio(a):
    buy, sell, label, as_of = anchors_for(a)
    cands = parse_candidates(a.candidates)
    for c in cands:
        r = judge_method(c["method"], pe=c["value"] if c["method"] in ("yield", "bog") else None,
                         pb=c["value"] if c["method"] == "pb" else None,
                         percentile=c["percentile"], discount=c["discount"],
                         buy=buy, sell=sell, buy_pct=a.buy_pct, sell_pct=a.sell_pct)
        c["earnings_yield"] = r["earnings_yield_pct"]
        c["signal"] = r["signal"]
        c["action"] = r["action"]
        c["reason"] = r["reason"]

    eligible = [c for c in cands if c["signal"] == "buy"]
    note = []
    skipped = [c for c in cands if c["signal"] == "skip"]
    if skipped:
        note.append("按表 4.2 放弃: %s" % ", ".join(c["code"] for c in skipped))

    groups = []
    if a.group:
        for g in a.group:
            if "=" not in g:
                raise SystemExit("--group 格式应为 名称=代码,代码, 例如 银行=510050,510900")
            gname, codes = g.split("=", 1)
            groups.append((gname.strip(), [x.strip() for x in codes.split(",") if x.strip()]))

    def dump_all(to_bond):
        d = {
            "budget": a.budget,
            "thresholds": {"buy_above": buy, "sell_below": sell, "source": label,
                           "anchor_as_of": as_of},
            "zone": {"buy_below_pct": a.buy_pct if a.buy_pct is not None else ZONE_BUY_PCT,
                     "sell_above_pct": a.sell_pct if a.sell_pct is not None else ZONE_SELL_PCT,
                     "source": ZONE_SOURCE},
            "candidates": cands,
            "allocation": [],
            "to_bond_fund": round(to_bond, 2),
            "note": "; ".join(note),
            "stale": stale_warning(as_of),
        }
        return d

    if not eligible:
        d = dump_all(a.budget)
        d["note"] = "; ".join(note + ["没有品种处于低估区; 按书中第 7 章, 这笔钱应改投债券基金(债基也不合适则货币基金/银行理财)"])
        if a.json:
            print(json.dumps(d, ensure_ascii=False, indent=2))
            return
        print("本月预算       : %s 元" % fmt(a.budget, 0))
        print("阈值口径       : %s" % label)
        print("-" * 62)
        for c in cands:
            print("  %-12s %-6s %-9s -> %s" % (c["code"], c["method"],
                                               fmt(c["value"], 2) if c["value"] is not None else "-",
                                               c["action"]))
        print("-" * 62)
        print("结论: 一个都没进入低估区, 本月不投指数基金。")
        print("去向: 全部 %s 元 -> 债券基金(利率高位选长期, 低位选短期);" % fmt(a.budget, 0))
        print("      债基也不合适(利率极低) -> 货币基金 / 银行理财。")
        print("      空仓等低估不算失败, 书里把这叫'没有好机会时就拿现金'。")
        for n in note:
            print("[!] %s" % n)
        return

    # 书里的品种数约束: 月投 1000 以下只投 1 只, 1000~3000 投 2~3 只
    if a.budget < 1000 and len(eligible) > 1:
        eligible.sort(key=lambda c: (c["percentile"] if c["percentile"] is not None
                                     else -(c["earnings_yield"] or 0)))
        note.append("月预算 < 1000 元, 按书中建议只投 1 只, 已保留最便宜的一只")
        eligible = eligible[:1]
    elif a.budget < 3000 and len(eligible) > 3:
        eligible.sort(key=lambda c: (c["percentile"] if c["percentile"] is not None
                                     else -(c["earnings_yield"] or 0)))
        note.append("月预算 < 3000 元, 按书中建议最多 3 只, 已截断")
        eligible = eligible[:3]

    items = [{"code": c["code"], "c": c} for c in eligible]
    for it in items:
        it["w"] = 1.0 / len(items)
    if a.weight == "depth":
        # 归一化"低估深度": 不同方法的深度单位不同(盈利收益率的百分点 vs 分位百分点),
        # 直接相加会让分位法的品种系统性压过盈利率法的品种。所以各自归一:
        #   yield: (盈利收益率 - 买入线) / 买入线        -> 买入线处为 0, 达到买入线两倍时为 1
        #   分位 : (买入分位 - 当前分位) / 买入分位        -> 买入分位处为 0, 分位 0 时为 1
        # 两者都表示"深入便宜区多少", 量纲一致(0~1), 才敢放在一起比较。
        depths = []
        mixed = False
        for it in items:
            c = it["c"]
            if c["method"] == "yield" and c["earnings_yield"] is not None:
                base = buy if buy and buy > 0 else 1.0
                depths.append(max((c["earnings_yield"] - buy) / base, 1e-9))
            elif c["percentile"] is not None:
                mixed = True
                bp = ZONE_BUY_PCT if a.buy_pct is None else a.buy_pct
                base = bp if bp > 0 else 1.0
                depths.append(max((bp - c["percentile"]) / base, 1e-9))
            else:
                depths.append(1e-9)
        tot = sum(depths)
        for it, dv in zip(items, depths):
            it["w"] = dv / tot
        if mixed and len(items) > 1:
            note.append("深度已按各自方法的门槛归一(盈利率按买入线的倍数, 分位按买入分位的比例), "
                        "否则分位法的品种会系统性压过盈利率法的品种。")

    items, cap_notes, cap_infeasible = _cap_weights(items, a.max_single, groups, a.max_group)
    note.extend(cap_notes)

    alloc = []
    for it in items:
        c = it["c"]
        alloc.append({"code": c["code"], "method": c["method"], "value": c["value"],
                      "percentile": c["percentile"],
                      "earnings_yield_pct": c["earnings_yield"],
                      "weight_pct": round(it["w"] * 100, 2),
                      "amount": round(a.budget * it["w"], 2)})
    s = sum(x["amount"] for x in alloc)
    if alloc:
        alloc[-1]["amount"] = round(alloc[-1]["amount"] + (a.budget - s), 2)

    d = dump_all(0.0)
    d["allocation"] = alloc
    d["weight_method"] = a.weight
    d["caps"] = {"max_single": a.max_single, "max_group": a.max_group,
                 "groups": [{"name": g[0], "codes": g[1]} for g in groups],
                 "infeasible": cap_infeasible}
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return
    print("本月预算       : %s 元" % fmt(a.budget, 0))
    print("阈值口径       : %s" % label)
    print("分配方式       : %s" % ("等权" if a.weight == "equal" else "按低估深度加权"))
    if a.max_single is not None:
        print("单只上限       : %s%%" % fmt(a.max_single * 100, 0))
    print("-" * 62)
    print("%-12s %-6s %8s %9s %8s %10s" % ("代码", "方法", "数值", "盈利收益率", "权重", "金额"))
    for x in alloc:
        print("%-12s %-6s %8s %8s%% %7s%% %9s 元"
              % (x["code"], x["method"],
                 fmt(x["value"], 2) if x["value"] is not None else "-",
                 fmt(x["earnings_yield_pct"]) if x["earnings_yield_pct"] is not None else "-",
                 fmt(x["weight_pct"]), fmt(x["amount"])))
    print("-" * 62)
    for n in note:
        print("[!] %s" % n)
    if groups:
        print("重叠组已计入权重上限: %s" % ", ".join("%s(%s)" % (g[0], ",".join(g[1])) for g in groups))
    print("提醒: 买到重复持仓不算分散 —— 上证50 / 基本面50 / H股 / 50AH优选 里有大量同一批银行股;")
    print("      沪深300 与 中证100 / 上证50 也高度重叠。重叠的品种用 --group 归到一组设上限。")


# ============ 回测: 检验规则本身 ============

def _xirr(pairs):
    """pairs: [(date, cashflow)]; 返回年化收益率(小数)。"""
    pairs = sorted(pairs)
    if len(pairs) < 2:
        return None
    t0 = pairs[0][0]
    if not any(cf > 0 for _, cf in pairs) or not any(cf < 0 for _, cf in pairs):
        return None

    def npv(rate):
        s = 0.0
        for d, cf in pairs:
            s += cf / ((1.0 + rate) ** ((d - t0).days / 365.0))
        return s

    lo, hi = -0.9999, 10.0
    flo, fhi = npv(lo), npv(hi)
    if flo * fhi > 0:
        return None
    for _ in range(300):
        mid = (lo + hi) / 2.0
        fm = npv(mid)
        if abs(fm) < 1e-9:
            return mid
        if flo * fm <= 0:
            hi = mid
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2.0


def load_bt_series(path, price_col=None):
    """读回测序列: CSV, 表头含 date, pe, close(点位或净值)。"""
    rows = []
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            rd = csv.DictReader(f)
            if not rd.fieldnames:
                raise SystemExit("回测文件没有表头, 应为 date,pe,close")
            names = [n.strip().lower() for n in rd.fieldnames]
            def pick(*cands):
                for c in cands:
                    if c in names:
                        return rd.fieldnames[names.index(c)]
                return None
            c_date = pick("date", "日期", "trade_date")
            c_pe = pick("pe", "市盈率", "pe_ttm")
            c_px = price_col or pick("close", "price", "nav", "点位", "收盘")
            if not (c_date and c_pe and c_px):
                raise SystemExit("回测文件需要 date / pe / close 三列, 例如 date,pe,close")
            for r in rd:
                try:
                    d = dt.date.fromisoformat((r[c_date] or "").strip()[:10])
                    pe = float(r[c_pe])
                    px = float(r[c_px])
                except Exception:
                    continue
                if pe > 0 and px > 0:
                    rows.append((d, pe, px))
    except OSError as e:
        raise SystemExit("读不了回测文件: %s (%s)" % (path, e))
    if len(rows) < 12:
        raise SystemExit("回测数据只有 %d 行, 至少需要 12 个月" % len(rows))
    rows.sort()
    return rows


def _sample_monthly(rows):
    """按自然月取每月最后一条记录(定投日=每月最后一个交易日)。"""
    out = {}
    for d, pe, px in rows:
        out[(d.year, d.month)] = (d, pe, px)
    return [out[k] for k in sorted(out)]


def simulate(rows, amount, buy, sell, discount=1.0, mode="value"):
    """mode: value=只在盈利收益率>买入线时定投; dumb=每期无条件定投。"""
    flows = []
    shares = 0.0
    invested = 0.0
    times = 0
    for d, pe, px in rows:
        take = False
        if mode == "dumb":
            take = True
        else:
            y = earnings_yield(pe, discount)
            take = y > buy
        if take:
            shares += amount / px
            invested += amount
            times += 1
            flows.append((d, -amount))
    if not rows or shares == 0:
        return None
    last_d, last_pe, last_px = rows[-1]
    mv = shares * last_px
    flows.append((last_d, mv))
    rate = _xirr(flows)
    return {"mode": mode, "times": times, "invested": round(invested, 2),
            "market_value": round(mv, 2), "multiple": round(mv / invested, 4) if invested else None,
            "annual_pct": round(rate * 100, 4) if rate is not None else None,
            "start": rows[0][0].isoformat(), "end": last_d.isoformat(),
            "months": len(rows)}


def cmd_backtest(a):
    buy, sell, label, as_of = anchors_for(a)
    rows = load_bt_series(a.series, a.price_col)
    if a.monthly:
        rows = _sample_monthly(rows)
    res_v = simulate(rows, a.amount, buy, sell, a.discount, "value")
    res_d = simulate(rows, a.amount, buy, sell, a.discount, "dumb")
    d = {"file": os.path.abspath(a.series), "thresholds": {"buy_above": buy, "sell_below": sell,
                                                         "source": label},
         "rows": len(rows), "amount_per_period": a.amount,
         "value_weighted": res_v, "dumb": res_d}
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return
    if res_v is None:
        raise SystemExit("按当前买入线一次都没投, 无法回测(买入线 %s%% 太低或数据未覆盖低估值期)" % fmt(buy, 1))
    print("回测: 只在盈利收益率 > %s%% 时定投   (阈值口径 %s)" % (fmt(buy, 1), label))
    print("数据 %s ~ %s, 共 %d 个定投时点(按%s)"
          % (res_v["start"], res_v["end"], len(rows), "月" if a.monthly else "记录"))
    print("-" * 66)
    print("%-22s %10s %10s" % ("", "估值定投", "无脑定投"))
    for k, name in [("times", "投入次数"), ("invested", "累计投入"),
                    ("market_value", "期末市值"), ("multiple", "市值/投入"),
                    ("annual_pct", "年化(IRR %)")]:
        vv = res_v.get(k)
        dd = (res_d or {}).get(k)
        print("%-22s %10s %10s" % (name,
                                   fmt(vv, 2) if isinstance(vv, (int, float)) else "-",
                                   fmt(dd, 2) if isinstance(dd, (int, float)) else "-"))
    print("-" * 66)
    if res_d and res_v.get("annual_pct") is not None and res_d.get("annual_pct") is not None:
        gap = res_v["annual_pct"] - res_d["annual_pct"]
        print("估值定投年化比无脑定投 %s %.2f 个百分点。" % ("高" if gap >= 0 else "低", abs(gap)))
    print()
    print("读这个结果时的三条纪律:")
    print("  1) 单一样本不构成证明。书中 2004~2015 上证50 回测年化 29.27%, 那是中国经济高增长期,")
    print("     作者自己也说'未来大概率 15%~20%'。换区间、换品种, 结论可能完全不同。")
    print("  2) 没有算分红、费用、税。红利类指数每年 3%~4% 的股息若不计入, 年化会被系统性低估。")
    print("  3) 这份数据是你自己提供的, 口径(静态/TTM)、频率、是否复权都会改变结果。")
    print("     换一个数据源再跑一次, 若结论翻转, 说明它不稳健, 别拿它当依据。")
    print()
    print(stale_warning(as_of))


SOURCE_ROWS = [
    ("指数 PE / PB / 股息率", "中证指数官网 csindex.com.cn(官方, 免费, 最权威)",
     "上证/中证系列指数的官方估值; 港股去恒生指数官网 hsi.com.hk"),
    ("指数 PE / PB 历史分位", "蛋卷基金指数估值页 / 韭圈儿 App / 乐咕乐股",
     "分位是分位法(bog/pb)的唯一输入; 没有分位就别用分位法"),
    ("指数历史 PE 序列(做回测)", "乐咕乐股图表导出 / 韭圈儿 / Wind / Choice / 中证官网历史估值表",
     "backtest 子命令需要 date,pe,close 三列; 免费可编程接口目前都不开放, 需手工导出"),
    ("全部 A 股整体估值", "乐咕乐股 全部A股市盈率 / 中位数", "判断市场整体温度, 不用于单只基金决策"),
    ("10 年期国债收益率", "中债信息网 chinabond.com.cn 收益率曲线",
     "calibrate 的 --riskfree 输入; 也可用聚金数据 jujindata.com/bond/detail/cn10y"),
    ("债券基金长期平均收益", "中证纯债债基指数 / Wind 中长期纯债型基金指数",
     "calibrate 的 --bond-avg 输入; 要用跨周期平均, 不要用最近一年"),
    ("货币基金 7 日年化", "各平台 App 直接看", "判断'卖出后钱放哪'的相对吸引力"),
    ("基金规模与是否清盘", "天天基金 fund.eastmoney.com 基金档案页",
     "规模长期低于 1 亿要警惕清盘; 清盘=强制赎回, 定投被迫中断"),
    ("场内基金实时折溢价", "券商 App 的 IOPV 对比", "溢价 >1% 时场外申购通常比场内买入划算"),
    ("QDII 是否暂停申购", "基金公司公告 / 天天基金申购状态",
     "外汇额度用尽会暂停申购(只能赎回), 这是 QDII 的额外风险, 不是估值问题"),
    ("分级基金 A/B 份额", "已于 2020 年底前完成整改, 全部退出",
     "书中第 3 章相关段落只作历史理解; 现在市场上已无分级基金可买"),
]


def cmd_sources(a):
    if a.json:
        print(json.dumps([{"indicator": r[0], "where": r[1], "note": r[2]} for r in SOURCE_ROWS],
                         ensure_ascii=False, indent=2))
        return
    print("取数清单(每项都给了可执行的查询位置)")
    print("=" * 78)
    for ind, where, note in SOURCE_ROWS:
        print("· %s" % ind)
        print("    去哪查: %s" % where)
        print("    为什么: %s" % note)
    print("=" * 78)
    print("口径陷阱:")
    print("  1) 静态 PE(上年度报告净利润) / 滚动 PE(最近四个季度) / 动态 PE(预测下一年)。")
    print("     同一指数三者能差 8%~15%, 足以让信号在买入线与持有线之间横跳。")
    print("     引用任何 PE 都必须写明口径, 并用 signal --pe/--pe-alt 交叉验证。")
    print("  2) 指数官网给的是'总市值加权'整体 PE; 乐咕另有'中位数''等权平均'两种统计,")
    print("     它们回答的是不同问题: 整体估值高低看加权, 大多数股票贵不贵看中位数。")
    print("  3) 股息率要用'最近 12 个月已分红'口径, 不要用预测股息率。")
    print("  4) 分位由'历史区间'决定: 起算年份不同, 同一个 PE 的分位能差 20 个点以上。")
    print("     用分位时必须知道它是从哪一年算起的。")


def build_parser():
    p = argparse.ArgumentParser(
        description="指数基金估值决策器 —— 规则来自《指数基金投资指南》(银行螺丝钉)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="cmd")

    def add_thresholds(sp):
        sp.add_argument("--preset", choices=["book", "cal", "cal-rp"], default="book",
                        help="阈值口径: book=书中2017口径(默认), cal=当期校准-教材直译, "
                             "cal-rp=当期校准-风险溢价平移")
        sp.add_argument("--buy-line", type=float, default=None,
                        help="自定义买入线(盈利收益率%%, 覆盖 preset)")
        sp.add_argument("--sell-line", type=float, default=None,
                        help="自定义卖出线(盈利收益率%%, 覆盖 preset)")

    def add_zones(sp):
        sp.add_argument("--buy-pct", type=float, default=None,
                        help="分位法买入分界, 默认 %s(%s)" % (ZONE_BUY_PCT, ZONE_SOURCE))
        sp.add_argument("--sell-pct", type=float, default=None,
                        help="分位法卖出分界, 默认 %s(%s)" % (ZONE_SELL_PCT, ZONE_SOURCE))

    s = sub.add_parser("signal", help="买卖信号(三种方法各管一类指数)")
    s.add_argument("--type", dest="type", default=None,
                   help="品种类型(推荐这样用): value/价值=盈利稳定, grow/成长=高速成长, "
                        "cycle/周期=强周期, distress/困境=长期亏损放弃。会自动选方法")
    s.add_argument("--method", choices=list(METHODS), default=None,
                   help="底层覆盖: yield / bog / pb / skip。一般改用它上面的 --type")
    s.add_argument("--pe", type=float, help="当前市盈率 PE(yield / bog 用)")
    s.add_argument("--pe-type", choices=["lyr", "ttm", "dynamic"],
                   help="PE 口径: lyr=静态 ttm=滚动 dynamic=动态。强烈建议总是填写")
    s.add_argument("--pb", type=float, help="当前市净率 PB(pb 方法用)")
    s.add_argument("--pb-type", choices=["mrq", "lyr"], help="PB 口径: mrq=最新季报 lyr=年报")
    s.add_argument("--percentile", type=float,
                   help="该品种 PE/PB 的历史分位 0~100(bog/pb 必填)")
    s.add_argument("--pe-alt", type=float, help="另一口径的 PE, 用于交叉验证信号是否稳健")
    s.add_argument("--pe-alt-type", choices=["lyr", "ttm", "dynamic"], help="--pe-alt 的口径")
    s.add_argument("--discount", type=float, default=1.0,
                   help="盈利收益率折价系数; 港股常用 0.9(默认 1.0)")
    s.add_argument("--payout", type=float, default=None,
                   help="港股分红率(0~1), 与 --tax 一起推出折价系数, 默认 0.5")
    s.add_argument("--tax", type=float, default=None,
                   help="港股分红税+换汇损耗合计(0~1), 与 --payout 一起用, 默认 0.2")
    s.add_argument("--hk-exact", action="store_true",
                   help="港股精确模式: 分红税只打在分红上(需同时给 --dividend 与 --tax), "
                        "比书中整体 9 折更准")
    s.add_argument("--dividend", type=float, default=None,
                   help="当前股息率%%, 仅 --hk-exact 用")
    add_thresholds(s)
    add_zones(s)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_signal)

    c = sub.add_parser("calibrate", help="用当期利率推导买卖线")
    c.add_argument("--riskfree", type=float, required=True, help="10 年期国债收益率, 百分数")
    c.add_argument("--bond-avg", type=float, required=True, help="债基跨周期平均年化, 百分数")
    c.add_argument("--buy-floor", type=float, default=10.0, help="买入线绝对下限, 默认 10")
    c.add_argument("--risk-premium", type=float, default=0.0,
                   help="卖出线之上再加的股权风险溢价, 百分数, 默认 0(忠于教材)")
    c.add_argument("--as-of", default="today", help="数据日期")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_calibrate)

    b = sub.add_parser("bog", help="博格公式年复合收益率拆解")
    b.add_argument("--dividend", type=float, required=True, help="初始股息率, 百分数(如 1.86)")
    b.add_argument("--pe-now", type=float, required=True, help="买入时 PE")
    b.add_argument("--pe-future", type=float, required=True, help="期末 PE")
    b.add_argument("--years", type=float, required=True, help="投资年数")
    b.add_argument("--earnings-growth", type=float, required=True, help="每年盈利增长率, 百分数")
    b.add_argument("--json", action="store_true")
    b.set_defaults(func=cmd_bog)

    v = sub.add_parser("pb", help="博格公式变种(PB版)")
    v.add_argument("--pb-now", type=float, required=True)
    v.add_argument("--pb-future", type=float, required=True)
    v.add_argument("--years", type=float, required=True)
    v.add_argument("--book-growth", type=float, required=True, help="每年净资产增长率, 百分数")
    v.add_argument("--json", action="store_true")
    v.set_defaults(func=cmd_pb)

    m = sub.add_parser("amount", help="定期不定额: 本月该投多少")
    m.add_argument("--base", type=float, required=True, help="首次定投(进入低估)时的基准金额")
    m.add_argument("--mode", choices=["yield", "pe", "pb"], default="pe",
                   help="yield=盈利收益率法; pe=博格公式; pb=PB 变种")
    m.add_argument("--first", type=float, required=True, help="首次定投时的估值")
    m.add_argument("--now", type=float, required=True, help="当前估值")
    m.add_argument("--n", type=float, default=1.0, help="放大器, 建议 1, 最多 2")
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_amount)

    q = sub.add_parser("position", help="当前估值在历史区间的位置")
    q.add_argument("--percentile", type=float, help="直接给真实历史分位(0~100), 优先于线性近似")
    q.add_argument("--series", help="历史估值序列文件(一列数字), 用它现算真实分位; 需配 --value")
    q.add_argument("--value", type=float, help="当前 PE 或 PB")
    q.add_argument("--low", type=float, help="历史区间下限")
    q.add_argument("--high", type=float, help="历史区间上限")
    add_zones(q)
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_position)

    o = sub.add_parser("portfolio", help="月度预算分配到多个候选品种")
    o.add_argument("--budget", type=float, required=True, help="本月可投金额")
    o.add_argument("--candidates", required=True,
                   help="候选: 代码:数值[:折价[:方法[:分位]]], 逗号分隔。"
                        "例 000922:9.01:1:yield,399006:31.9:1:bog:18.2")
    o.add_argument("--weight", choices=["equal", "depth"], default="depth",
                   help="equal=等权, depth=按低估深度加权(默认)")
    o.add_argument("--max-single", type=float, default=None,
                   help="单只权重上限(0~1), 如 0.4; 默认不设限但会提示")
    o.add_argument("--group", action="append", default=None,
                   help="重叠组 名称=代码,代码; 可重复。与 --max-group 配合限制重叠持仓")
    o.add_argument("--max-group", type=float, default=None,
                   help="每个重叠组的权重上限(0~1), 如 0.5")
    add_thresholds(o)
    add_zones(o)
    o.add_argument("--json", action="store_true")
    o.set_defaults(func=cmd_portfolio)

    bt = sub.add_parser("backtest", help="用历史序列检验规则本身")
    bt.add_argument("--series", required=True, help="CSV 文件, 表头含 date, pe, close 三列")
    bt.add_argument("--price-col", default=None, help="价格列名, 默认自动找 close/price/nav")
    bt.add_argument("--amount", type=float, default=1000.0, help="每期投入金额, 默认 1000")
    bt.add_argument("--discount", type=float, default=1.0, help="盈利收益率折价系数")
    bt.add_argument("--no-monthly", dest="monthly", action="store_false",
                    help="不按月抽样, 用原始每行(默认按自然月取最后一条)")
    bt.set_defaults(monthly=True)
    add_thresholds(bt)
    bt.add_argument("--json", action="store_true")
    bt.set_defaults(func=cmd_backtest)

    src = sub.add_parser("sources", help="取数清单与口径陷阱")
    src.add_argument("--json", action="store_true")
    src.set_defaults(func=cmd_sources)

    return p


def main(argv=None):
    p = build_parser()
    a = p.parse_args(argv)
    if not getattr(a, "cmd", None):
        p.print_help()
        return 1
    if getattr(a, "cmd", None) == "signal":
        m, _explicit, _note = resolve_method(a)
        if m in ("yield", "bog") and a.pe is None:
            raise SystemExit("method=%s 需要 --pe" % m)
        if m == "pb" and a.pb is None:
            raise SystemExit("method=pb 需要 --pb")
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
