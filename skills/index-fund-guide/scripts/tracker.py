#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""指数基金每日跟踪器 —— 关注清单 / 每日快照 / 投资判断 / 条件推算

设计三原则
----------
1. 数据落在自己盘上：每天抓一次存进 snapshots.csv，从今天起积累**自己的**历史序列，
   不再依赖任何供应商的历史 PE 接口（那些接口要么收费、要么不给）。
2. 判断逻辑不复制：阈值、方法分派、分位判据全部 import 自 valuation.py，
   只有一处定义，不会两处漂移。
3. 预测只做条件推算：给定估值与盈利增长假设，算未来 N 年的年化区间；
   不预测点位、不预测涨跌、不给概率。

子命令
------
  init      建目录与默认关注清单
  watch     增删查关注品种
  collect   抓当日估值快照（--fixtures 可离线）
  analyze   出信号 / 建议金额 / 三情景预期 / 跨线提醒
  report    生成 markdown 日报
  show      打印最近 N 日快照

快速开始
--------
  python tracker.py init --dir ./fund-track
  python tracker.py collect --dir ./fund-track
  python tracker.py analyze --dir ./fund-track --budget 2000
  python tracker.py report  --dir ./fund-track

免责：这是研究工具，不是投资建议；数据来自公开接口且无 SLA。
"""

import argparse
import csv
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.request

try:  # 让 GBK 控制台不至于因个别字符崩溃
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.join(os.getcwd(), "fund-track")

SNAP_HEADER = ["date", "key", "name", "kind", "type", "close", "pe", "pb",
               "pe_pct", "pb_pct", "dividend", "roe", "source", "fetched_at"]
WATCH_HEADER_KEYS = ("key", "name", "type", "fund", "target", "note")

# 默认关注清单：四类指数各取代表，覆盖"价值 / 成长 / 周期 / 港股"
DEFAULT_WATCH = [
    {"key": "000300", "name": "沪深300",   "type": "value", "fund": "", "target": 0.30, "note": "A股核心宽基"},
    {"key": "000016", "name": "上证50",    "type": "value", "fund": "", "target": 0.20, "note": "大盘蓝筹"},
    {"key": "000922", "name": "中证红利",  "type": "value", "fund": "", "target": 0.20, "note": "高股息，熊市抗跌"},
    {"key": "000905", "name": "中证500",   "type": "grow",  "fund": "", "target": 0.10, "note": "中盘成长"},
    {"key": "399006", "name": "创业板指",  "type": "grow",  "fund": "", "target": 0.05, "note": "高波动，只宜小仓"},
    {"key": "HSCEI",  "name": "恒生国企",  "type": "value", "fund": "", "target": 0.10, "note": "港股，估值折价"},
    {"key": "HSI",    "name": "恒生指数",  "type": "value", "fund": "", "target": 0.05, "note": "港股宽基"},
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


# ---------------------------------------------------------------- 小工具

def ei(v):
    """转成 int，失败返回 None。"""
    try:
        if v is None or v == "":
            return None
        return int(float(v))
    except (TypeError, ValueError):
        return None


def ef(v):
    """转成 float，失败返回 None。"""
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def fmt(v, nd=2, dash="—"):
    if v is None:
        return dash
    try:
        return ("%." + str(nd) + "f") % float(v)
    except (TypeError, ValueError):
        return dash


def today_str():
    return datetime.date.today().isoformat()


def dj_code(key, kind="index"):
    """把用户写的代码转成蛋卷接口用的代码。

    000300 -> SH000300 ; 399006 -> SZ399006 ; HSI -> HKHSI
    也接受已经带前缀的写法（SH000300 / HKHSI）。
    """
    k = str(key).strip().upper()
    if k.startswith("HK") or k.startswith("SH") or k.startswith("SZ"):
        return k
    if k in ("HSI", "HSCEI", "HSTECH"):
        return "HK" + k
    if re.fullmatch(r"\d{6}", k):
        return ("SH" if k[0] == "0" else "SZ") + k
    return k


def load_valuation():
    """import 同目录的 valuation.py，复用同一套阈值与判据。"""
    import importlib.util
    path = os.path.join(HERE, "valuation.py")
    spec = importlib.util.spec_from_file_location("ifg_valuation", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- 抓数

def http_json(url, headers=None, timeout=12, retries=2):
    last = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as e:
            last = e
    raise RuntimeError("%s 抓取失败: %s" % (url.split("?")[0], last))


def http_text(url, headers=None, timeout=12, retries=2):
    last = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            for enc in ("gbk", "utf-8"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", "replace")
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            last = e
    raise RuntimeError("%s 抓取失败: %s" % (url, last))


def fetch_eva(code, fixtures=None):
    """取一个指数的估值快照。返回 dict（已统一成百分数口径）。"""
    dj = dj_code(code)
    if fixtures:
        path = os.path.join(fixtures, dj + ".json")
        if not os.path.exists(path):
            raise RuntimeError("缺少离线样本 %s" % path)
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    else:
        url = "https://danjuanfunds.com/djapi/index_eva/detail/%s" % dj
        payload = http_json(url, headers={
            "User-Agent": UA,
            "Referer": "https://danjuanfunds.com/djmodule/value-center",
        })
    data = payload.get("data") if isinstance(payload, dict) else None
    if not data:
        raise RuntimeError("%s 返回空数据（接口可能改了字段）" % dj)

    def pct(v):
        f = ef(v)
        return None if f is None else f * 100.0

    return {
        "key": str(code).upper(),
        "pe": ef(data.get("pe")),
        "pb": ef(data.get("pb")),
        "pe_pct": pct(data.get("pe_percentile")),
        "pb_pct": pct(data.get("pb_percentile")),
        "dividend": pct(data.get("yeild")),
        "roe": pct(data.get("roe")),
        "ts": ei(data.get("ts")),
    }


def fetch_close(code):
    """取指数收盘/最新点位（腾讯行情）。失败返回 None，不阻塞。"""
    k = str(code).upper()
    if k.startswith("HK") or k in ("HSI", "HSCEI", "HSTECH"):
        sym = k if k.startswith("HK") else "hk" + k
        sym = sym.replace("HK", "hk")
    elif re.fullmatch(r"\d{6}", k):
        sym = ("sh" if k[0] == "0" else "sz") + k
    else:
        return None
    try:
        txt = http_text("https://qt.gtimg.cn/q=%s" % sym)
    except RuntimeError:
        return None
    m = re.search(r'="([^"]*)"', txt)
    if not m:
        return None
    parts = m.group(1).split("~")
    if len(parts) < 4:
        return None
    return ef(parts[3])


# ---------------------------------------------------------------- 存储

def paths(d):
    return {
        "dir": d,
        "watch": os.path.join(d, "watchlist.json"),
        "snap": os.path.join(d, "data", "snapshots.csv"),
        "reports": os.path.join(d, "reports"),
    }


def ensure_dirs(d):
    p = paths(d)
    os.makedirs(os.path.dirname(p["snap"]), exist_ok=True)
    os.makedirs(p["reports"], exist_ok=True)
    return p


def load_watch(d):
    p = paths(d)
    if not os.path.exists(p["watch"]):
        raise SystemExit("[!] 还没有关注清单：先跑 tracker.py init --dir %s" % d)
    with open(p["watch"], "r", encoding="utf-8") as f:
        obj = json.load(f)
    items = obj.get("items") or []
    for it in items:
        it.setdefault("type", "value")
        it.setdefault("target", 0.0)
        it.setdefault("fund", "")
        it.setdefault("note", "")
    return obj, items


def save_watch(d, obj):
    p = paths(d)
    obj["updated"] = today_str()
    with open(p["watch"], "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def read_snaps(d):
    p = paths(d)
    rows = []
    if not os.path.exists(p["snap"]):
        return rows
    with open(p["snap"], "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def write_snaps(d, rows):
    p = paths(d)
    os.makedirs(os.path.dirname(p["snap"]), exist_ok=True)
    with open(p["snap"], "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SNAP_HEADER)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in SNAP_HEADER})


def latest_by_key(rows):
    """每个 key 取日期最新的一条；同一天取最后写入的。"""
    best = {}
    for r in rows:
        k = r.get("key")
        cur = best.get(k)
        if cur is None or (r.get("date") or "") >= (cur.get("date") or ""):
            best[k] = r
    return best


def history_for(rows, key, n=None):
    rs = [r for r in rows if r.get("key") == key]
    rs.sort(key=lambda r: r.get("date") or "")
    return rs[-n:] if n else rs


# ---------------------------------------------------------------- 命令：init / watch

def cmd_init(a):
    d = os.path.abspath(a.dir)
    p = ensure_dirs(d)
    if os.path.exists(p["watch"]) and not a.force:
        raise SystemExit("[!] 关注清单已存在：%s（要重建加 --force）" % p["watch"])
    obj = {
        "version": 1,
        "created": today_str(),
        "note": "type 决定用哪套估值方法: value(盈利收益率) / grow(博格+分位) / cycle(PB变种+分位) / skip(放弃)。target 是目标仓位占比，用于组合分配。",
        "items": [dict(x) for x in DEFAULT_WATCH],
    }
    save_watch(d, obj)
    print("已初始化  : %s" % d)
    print("关注清单  : %s（%d 只）" % (p["watch"], len(obj["items"])))
    print("快照文件  : %s" % p["snap"])
    print("")
    for it in obj["items"]:
        print("  %-8s %-10s type=%-5s target=%3d%%  %s"
              % (it["key"], it["name"], it["type"], round(it["target"] * 100), it["note"]))
    print("")
    print("下一步: python tracker.py collect --dir %s" % a.dir)
    return 0


def cmd_watch(a):
    d = os.path.abspath(a.dir)
    obj, items = load_watch(d)

    if a.list or (not a.add and not a.remove):
        print("关注清单  %s" % paths(d)["watch"])
        print("-" * 78)
        print("%-8s %-10s %-6s %-8s %-8s %s" % ("代码", "名称", "类型", "目标仓位", "基金", "备注"))
        for it in items:
            print("%-8s %-10s %-6s %6d%%   %-8s %s"
                  % (it["key"], it["name"], it["type"],
                     round(it.get("target", 0) * 100), it.get("fund") or "—", it.get("note") or ""))
        print("-" * 78)
        print("共 %d 只；类型 value/grow/cycle/skip 决定估值方法" % len(items))
        return 0

    changed = 0
    if a.remove:
        for k in [x.strip().upper() for x in a.remove.split(",") if x.strip()]:
            before = len(items)
            items = [it for it in items if str(it["key"]).upper() != k]
            if len(items) < before:
                changed += 1
                print("已移除    : %s" % k)
            else:
                print("[!] 不在清单里: %s" % k)

    if a.add:
        for spec in [x for x in a.add.split(";") if x.strip()]:
            # 代码:名称[:类型[:基金代码[:目标仓位]]]
            parts = spec.split(":")
            key = parts[0].strip().upper()
            if not key:
                raise SystemExit("[!] 关注项缺少代码: %s" % spec)
            name = parts[1].strip() if len(parts) > 1 and parts[1].strip() else key
            typ = (parts[2].strip() if len(parts) > 2 and parts[2].strip() else "value").lower()
            if typ not in ("value", "grow", "cycle", "skip"):
                raise SystemExit("[!] 类型只能是 value/grow/cycle/skip，收到 %s" % typ)
            fund = parts[3].strip() if len(parts) > 3 else ""
            tgt = ef(parts[4]) if len(parts) > 4 else None
            exist = [it for it in items if str(it["key"]).upper() == key]
            if exist:
                exist[0]["name"] = name
                exist[0]["type"] = typ
                if fund:
                    exist[0]["fund"] = fund
                if tgt is not None:
                    exist[0]["target"] = tgt
                print("已更新    : %-8s %s (type=%s)" % (key, name, typ))
            else:
                items.append({"key": key, "name": name, "type": typ,
                              "fund": fund, "target": tgt or 0.0, "note": ""})
                print("已添加    : %-8s %s (type=%s)" % (key, name, typ))
            changed += 1

    obj["items"] = items
    save_watch(d, obj)
    tot = sum(ef(it.get("target")) or 0.0 for it in items)
    if abs(tot - 1.0) > 0.02 and tot > 0:
        print("[!] 提示: 目标仓位合计 %.0f%%，不是 100%%（分配时会按比例归一）" % (tot * 100))
    return 0


# ---------------------------------------------------------------- 命令：collect

def cmd_collect(a):
    d = os.path.abspath(a.dir)
    p = ensure_dirs(d)
    obj, items = load_watch(d)
    date = a.date or today_str()
    stamp = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    if a.fixtures:
        print("[离线模式] 从 %s 读取样本" % a.fixtures)

    rows = read_snaps(d)
    keep = [r for r in rows if not (r.get("date") == date)]

    ok = fail = 0
    failures = []
    for it in items:
        key = it["key"]
        try:
            ev = fetch_eva(key, fixtures=a.fixtures)
        except RuntimeError as e:
            fail += 1
            failures.append((it, str(e)))
            print("  [失败] %-8s %-10s %s" % (key, it["name"], e))
            continue
        close = None if a.no_close else fetch_close(key)
        row = {
            "date": date, "key": key, "name": it["name"],
            "kind": "index", "type": it["type"],
            "close": "" if close is None else "%.4f" % close,
            "pe": "" if ev["pe"] is None else "%.4f" % ev["pe"],
            "pb": "" if ev["pb"] is None else "%.4f" % ev["pb"],
            "pe_pct": "" if ev["pe_pct"] is None else "%.4f" % ev["pe_pct"],
            "pb_pct": "" if ev["pb_pct"] is None else "%.4f" % ev["pb_pct"],
            "dividend": "" if ev["dividend"] is None else "%.4f" % ev["dividend"],
            "roe": "" if ev["roe"] is None else "%.4f" % ev["roe"],
            "source": "fixture" if a.fixtures else "danjuan",
            "fetched_at": stamp,
        }
        keep.append(row)
        ok += 1
        print("  [ok]   %-8s %-10s PE %-7s 分位 %-7s 股息 %-6s"
              % (key, it["name"], fmt(ev["pe"]), fmt(ev["pe_pct"], 2, "") + "%",
                 fmt(ev["dividend"], 2, "") + "%"))

    keep.sort(key=lambda r: (r.get("date") or "", r.get("key") or ""))
    write_snaps(d, keep)
    print("")
    print("写入 %d 条（%s）→ %s" % (ok, date, p["snap"]))
    if fail:
        print("[!] %d 只抓取失败。多半是接口改名或限流；15 分钟后重试，或用 --fixtures 离线。" % fail)
        for it, msg in failures:
            print("    %-8s %s" % (it["key"], msg))
    print("下一步: python tracker.py analyze --dir %s" % a.dir)
    return 0 if ok else 1


# ---------------------------------------------------------------- 判断与推算

def pick_method(mod, typ, pe, pb, pct, a):
    """按用户声明的类型选方法；没声明就按 PE 粗判并提示。"""
    typ = (typ or "").lower()
    mp = {"value": "yield", "grow": "bog", "cycle": "pb", "skip": "skip"}
    explicit = typ in mp
    method = mp.get(typ, "yield")
    note = ""
    if not explicit:
        if pe and pe > 25:
            method = "bog"
            note = "未声明类型且 PE>25，按成长指数用博格公式（建议在关注清单里写明 type）"
        else:
            note = "未声明类型，默认按价值指数用盈利收益率法（建议在关注清单里写明 type）"
    if method in ("bog", "pb") and pct is None:
        return "skip", note + "；但缺少分位数据，无法用博格/PB 法 → 放弃判断"
    return method, note


def estimate_growth(div, pe, roe, override):
    """盈利增长 g 的估计：ROE × 留存率；无数据时退回名义增长假设。"""
    if override is not None:
        return override, "用户指定"
    if roe and roe > 0 and pe and div is not None:
        y = 100.0 / pe
        payout = min(max(div / y, 0.0), 1.0) if y else 0.0
        g = roe * (1.0 - payout)
        g = min(max(g, -5.0), 15.0)
        return g, "ROE %.2f%% × 留存率 %.0f%%" % (roe, (1 - payout) * 100)
    return 4.0, "默认假设（缺少 ROE/股息数据，取名义增长 4%）"


def scenarios(div, pe, g, years, pe_buy, use_reversion=True):
    """三情景年化收益率（%）。这是条件推算：先假设，再算结果。

    flat     估值不变，只赚盈利增长 + 分红
    to_buy   估值回落到买入线（只对价值指数有意义；成长/周期指数分位与 PE
             非线性，不给这一档，返回 None 而不是硬编一个数）
    down30   估值再跌 30%，看下限
    """
    d = div or 0.0
    out = {}
    out["flat"] = d + g
    if use_reversion and pe and pe_buy:
        out["to_buy"] = d + ((pe_buy / pe) ** (1.0 / years) - 1.0) * 100.0 + g
    else:
        out["to_buy"] = None
    out["down30"] = d + (0.7 ** (1.0 / years) - 1.0) * 100.0 + g
    return out


METHOD_LABEL = {"yield": "盈收", "bog": "博格", "pb": "PB", "skip": "放弃", "—": "—"}


def analyse_one(mod, it, row, a, anchors):
    buy, sell = anchors
    pe = ef(row.get("pe"))
    pb = ef(row.get("pb"))
    pct = ef(row.get("pe_pct"))
    pbp = ef(row.get("pb_pct"))
    div = ef(row.get("dividend"))
    roe = ef(row.get("roe"))
    method, mnote = pick_method(mod, it.get("type"), pe, pb, pct, a)

    hk = str(it["key"]).upper().startswith("HK") or str(it["key"]).upper() in ("HSI", "HSCEI", "HSTECH")
    disc = 0.9 if hk else 1.0

    signal, action, reason = "unknown", "数据不足", ""
    cash_half = False
    y = None
    if pe and pe > 0:
        y = 100.0 / pe * disc

    if method == "skip":
        signal, action, reason = "skip", "放弃", mnote or "长期亏损或数据不足，不参与判断"
    elif method == "yield":
        if y is None:
            reason = "缺少 PE"
        else:
            signal, action, reason = mod.judge_yield(y, buy, sell)
            # 绝对值与历史分位打架时不静默买入：信号保留，但仓位减半并明说
            if pct is not None and signal == "buy" and pct > a.sell_pct:
                reason += ("；但 PE 分位 %.1f%% 已在历史高位（>%.0f%%），与盈利收益率矛盾："
                           "盈利稳定则以盈利率为准，但仓位减半、分批入手"
                           % (pct, a.sell_pct))
                action = "分批投资（减半）"
                cash_half = True
        fix = mod.method_hint("yield", pe) if pe else ""
        if fix:
            reason = (reason + "；" + fix) if reason else fix
    elif method in ("bog", "pb"):
        use_pct = pbp if (method == "pb" and pbp is not None) else pct
        label = "PB" if method == "pb" else "PE"
        if use_pct is None:
            signal, action, reason = "skip", "数据不足", "缺少%s历史分位" % label
        else:
            z, zact, zr = mod.zone_signal(use_pct, a.buy_pct, a.sell_pct)
            signal, action = z, zact
            reason = "%s历史分位 %.1f%%（%s）；%s" % (label, use_pct, zr,
                                                   "分位越低越便宜，注意成长股高 PE 未必贵")
        if y is not None and pe:
            ys, ya, _ = mod.judge_yield(y, buy, sell)
            if ys != signal and signal != "skip":
                reason += "；交叉验证: 盈利收益率 %.2f%% 给出「%s」，两法冲突时以分位为准并降低仓位" % (y, ya)

    g, gsrc = estimate_growth(div, pe, roe, a.growth)
    pe_buy = 100.0 / buy if buy else None
    sc = scenarios(div, pe, g, a.years, pe_buy, use_reversion=(method == "yield"))

    # 建议金额：买入区按"越低估投越多"，其余为 0
    amount = 0.0
    if signal == "buy" and a.base and y:
        amount = a.base * (y / buy) ** (a.n if a.n is not None else 1.0)
    elif signal == "buy" and a.base:
        amount = a.base
    if cash_half:
        amount *= 0.5

    return {
        "key": it["key"], "name": it["name"], "type": it.get("type"),
        "method": method, "pe": pe, "pb": pb, "pe_pct": pct, "pb_pct": pbp,
        "dividend": div, "roe": roe, "earnings_yield": y,
        "close": ef(row.get("close")),
        "signal": signal, "action": action, "reason": reason,
        "growth": g, "growth_note": gsrc, "scenarios": sc,
        "amount": round(amount, 2), "target": ef(it.get("target")) or 0.0,
        "note": mnote, "date": row.get("date"),
        "source": row.get("source"), "fetched_at": row.get("fetched_at"),
    }


def cross_alerts(mod, cur, prev, a, anchors):
    """跨线提醒：只在真的越界时说话。"""
    buy, sell = anchors
    out = []
    if not prev:
        return out
    y0 = ef(prev.get("pe"))
    y1 = ef(cur.get("pe"))
    if y0 and y1:
        chg = (y1 / y0 - 1.0) * 100.0
        if abs(chg) >= 3.0:
            out.append("PE 由 %.2f 变为 %.2f（%+.1f%%）" % (y0, y1, chg))
    p0, p1 = ef(prev.get("pe_pct")), ef(cur.get("pe_pct"))
    if p0 is not None and p1 is not None:
        if p0 < a.buy_pct <= p1:
            out.append("PE 分位 %+0.1f 个百分点，跌出低估区（>%.0f%%）" % (p1 - p0, a.buy_pct))
        elif p0 >= a.buy_pct > p1:
            out.append("PE 分位 %+0.1f 个百分点，进入低估区（<%.0f%%）" % (p1 - p0, a.buy_pct))
        if p0 < a.sell_pct <= p1:
            out.append("PE 分位 %+0.1f 个百分点，进入高估区（>%.0f%%）" % (p1 - p0, a.sell_pct))
    return out


def cmd_analyze(a):
    d = os.path.abspath(a.dir)
    p = ensure_dirs(d)
    obj, items = load_watch(d)
    rows = read_snaps(d)
    if not rows:
        raise SystemExit("[!] 还没有任何快照：先跑 collect")
    mod = load_valuation()
    preset = mod.preset(a.preset)
    buy, sell = mod.lines_for(a)[:2]
    anchors = (buy, sell)

    latest = latest_by_key(rows)
    dates = sorted({r.get("date") for r in rows if r.get("date")})
    last_date = dates[-1]
    prev_all = latest_by_key([r for r in rows if (r.get("date") or "") < last_date])

    results = []
    for it in items:
        row = latest.get(it["key"])
        if not row:
            results.append({"key": it["key"], "name": it["name"], "signal": "missing",
                            "action": "无数据", "reason": "清单里但没抓到数据，重跑 collect",
                            "amount": 0.0, "target": ef(it.get("target")) or 0.0,
                            "alerts": [], "type": it.get("type"), "method": "—"})
            continue
        r = analyse_one(mod, it, row, a, anchors)
        r["alerts"] = cross_alerts(mod, row, prev_all.get(it["key"]), a, anchors)
        results.append(r)

    # 组合层：目标仓位分配 + 单只上限（水位填充：封顶后的余量继续分给未封顶的）
    budget = a.budget
    alloc = []
    if budget:
        cand = [r for r in results if r["signal"] == "buy" and r["amount"] > 0]
        tot_tgt = sum((r["target"] or 0.0) for r in cand) or 0.0
        weights = {}
        for r in cand:
            weights[r["key"]] = ((r["target"] or 0.0) / tot_tgt) if tot_tgt else (
                1.0 / len(cand) if cand else 0.0)
        cap_each = budget * a.max_single
        given = {k: 0.0 for k in weights}
        for _ in range(50):
            rem = budget - sum(given.values())
            if rem <= 0.005:
                break
            act = [k for k in weights if given[k] < cap_each - 0.005]
            if not act:
                break
            tot_w = sum(weights[k] for k in act) or 0.0
            if tot_w <= 0:
                break
            added = 0.0
            for k in act:
                give = min(rem * weights[k] / tot_w, cap_each - given[k])
                given[k] += give
                added += give
            if added <= 0.005:
                break
        for r in cand:
            amt = given.get(r["key"], 0.0)
            if amt <= 0.005:
                continue
            alloc.append({"key": r["key"], "name": r["name"], "amount": round(amt, 2),
                          "share": round(amt / budget * 100, 1),
                          "capped": abs(amt - cap_each) < 0.01})
        used = sum(x["amount"] for x in alloc)
        if alloc and used < budget - 0.01:
            alloc.append({"key": "—", "name": "留作债基/货基",
                          "amount": round(budget - used, 2),
                          "share": round((budget - used) / budget * 100, 1), "capped": False})
        if not alloc:
            alloc.append({"key": "—", "name": "全转债基/货基（无低估品种）",
                          "amount": round(budget, 2), "share": 100.0, "capped": False})

    out = {
        "as_of": last_date, "preset": a.preset,
        "anchors": {"buy_line_pct": round(buy, 4), "sell_line_pct": round(sell, 4),
                    "buy_pe": round(100.0 / buy, 2) if buy else None,
                    "sell_pe": round(100.0 / sell, 2) if sell else None},
        "counts": {k: sum(1 for r in results if r["signal"] == k)
                   for k in ("buy", "hold", "sell", "skip", "missing", "unknown")},
        "items": results, "allocation": alloc,
        "budget": budget, "years": a.years, "growth_assumption": a.growth,
    }
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    # ---- 人类可读
    print("每日跟踪分析   数据日 %s   口径 preset=%s" % (last_date, a.preset))
    print("阈值: 买入线 %.2f%%（PE<%.1f）  卖出线 %.2f%%（PE>%.1f）"
          % (buy, 100.0 / buy if buy else 0, sell, 100.0 / sell if sell else 0))
    warn = mod.stale_warning(preset.get("as_of"), "阈值")
    if warn:
        print(warn)
    print("-" * 100)
    print("%-8s %-10s %-5s %-7s %-7s %-7s %-8s %-6s %s"
          % ("代码", "名称", "方法", "PE", "分位%", "股息%", "盈利率%", "动作", "推算年化(不回归/回归/再跌30%)"))
    print("-" * 100)
    for r in results:
        if r.get("signal") == "missing":
            print("%-8s %-10s %s" % (r["key"], r["name"], r.get("reason", "")))
            continue
        sc = r.get("scenarios") or {}
        seg = "/".join(fmt(sc.get(k), 1) for k in ("flat", "to_buy", "down30"))
        print("%-8s %-10s %-5s %-7s %-7s %-7s %-8s %-6s %s"
              % (r["key"], r["name"], METHOD_LABEL.get(r["method"], r["method"]), fmt(r["pe"]),
                 fmt(r["pe_pct"], 1), fmt(r["dividend"], 2),
                 fmt(r["earnings_yield"], 2), r["action"], seg))
    print("-" * 100)

    alerts = [(r["name"], m) for r in results for m in (r.get("alerts") or [])]
    if alerts:
        print("[提醒] 与上次快照相比有变化：")
        for nm, m in alerts:
            print("    %-10s %s" % (nm, m))
        print("")

    if budget:
        print("本月/本期预算 %.0f 元，按买入信号与目标仓位分配：" % budget)
        for x in alloc:
            print("    %-10s %8.2f 元  (占 %.1f%%)%s"
                  % (x["name"], x["amount"], x["share"], "  ← 触及单只上限" if x["capped"] else ""))
        print("")

    print("判断依据（逐条）：")
    for r in results:
        if r.get("signal") == "missing":
            continue
        print("  %s %s → %s" % (r["key"], r["action"], r.get("reason") or "—"))
        print("      盈利增长假设 %.2f%%（%s）" % (r.get("growth") or 0, r.get("growth_note") or ""))
        if r.get("note"):
            print("      方法提示: %s" % r["note"])
    print("")
    print("说明: 「推算年化」是条件推算——先固定估值与盈利增长假设再算结果，不是预测；")
    print("      三档 = 估值不变 / 估值回落到买入线 / 估值再跌 30%%，年限 %d 年。" % a.years)
    print("      卖出侧请看分位是否 >%.0f%%（盈利收益率法在低利率环境几乎不触发卖出）。" % a.sell_pct)
    return 0


# ---------------------------------------------------------------- 命令：report / show

def cmd_show(a):
    d = os.path.abspath(a.dir)
    rows = read_snaps(d)
    if not rows:
        raise SystemExit("[!] 还没有快照")
    dates = sorted({r.get("date") for r in rows})[-a.days:]
    keys = [it["key"] for _, items in [load_watch(d)] for it in items]
    by = {(r.get("date"), r.get("key")): r for r in rows}
    print("最近 %d 个数据日：" % len(dates))
    head = "%-8s" % "代码" + "".join("%11s" % x[5:] for x in dates)
    print(head)
    for k in keys:
        line = "%-8s" % k
        for dt in dates:
            r = by.get((dt, k))
            line += "%11s" % (fmt(ef(r.get("pe")), 2) if r else "—")
        print(line)
    print("（单元格为该日 PE；缺格表示当天没抓到）")
    return 0


def cmd_report(a):
    d = os.path.abspath(a.dir)
    p = ensure_dirs(d)
    # 复用 analyze 的机器输出
    ns = argparse.Namespace(dir=d, preset=a.preset, buy_line=None, sell_line=None,
                            buy_pct=a.buy_pct, sell_pct=a.sell_pct, budget=a.budget,
                            base=a.base, n=a.n, growth=a.growth, years=a.years,
                            max_single=a.max_single, json=True)
    import io
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        cmd_analyze(ns)
    finally:
        sys.stdout = old
    data = json.loads(buf.getvalue())

    rows = read_snaps(d)
    hist = {}
    for it in data["items"]:
        hist[it["key"]] = history_for(rows, it["key"], a.days)

    L = []
    L.append("# 指数基金每日跟踪 · %s" % data["as_of"])
    L.append("")
    L.append("- 阈值口径：`%s`（买入线 %.2f%% = PE<%.1f；卖出线 %.2f%% = PE>%.1f）"
             % (data["preset"], data["anchors"]["buy_line_pct"], data["anchors"]["buy_pe"] or 0,
                data["anchors"]["sell_line_pct"], data["anchors"]["sell_pe"] or 0))
    L.append("- 统计：买 %d / 持 %d / 卖 %d / 放弃 %d / 无数据 %d"
             % (data["counts"]["buy"], data["counts"]["hold"], data["counts"]["sell"],
                data["counts"]["skip"], data["counts"]["missing"]))
    L.append("")
    L.append("## 一、今日信号")
    L.append("")
    L.append("| 代码 | 名称 | 方法 | PE | PE分位 | 股息率 | 盈利收益率 | 动作 | 推算年化(不回归/回归/再跌30%) |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for it in data["items"]:
        if it.get("signal") == "missing":
            L.append("| %s | %s | — | — | — | — | — | 无数据 | 重跑 collect |" % (it["key"], it["name"]))
            continue
        sc = it.get("scenarios") or {}
        seg = " / ".join(fmt(sc.get(k), 1) for k in ("flat", "to_buy", "down30"))
        L.append("| %s | %s | %s | %s | %s%% | %s%% | %s%% | **%s** | %s |"
                 % (it["key"], it["name"], METHOD_LABEL.get(it["method"], it["method"]), fmt(it["pe"]),
                    fmt(it["pe_pct"], 1), fmt(it["dividend"], 2),
                    fmt(it["earnings_yield"], 2), it["action"], seg))
    L.append("")

    al = [(it["name"], m) for it in data["items"] for m in (it.get("alerts") or [])]
    L.append("## 二、变化与提醒")
    L.append("")
    if al:
        for nm, m in al:
            L.append("- **%s**：%s" % (nm, m))
    else:
        L.append("- 与上次快照相比无跨线变化。")
    L.append("")

    if data["budget"]:
        L.append("## 三、本期资金分配（预算 %.0f 元）" % data["budget"])
        L.append("")
        L.append("| 去向 | 金额 | 占比 | 说明 |")
        L.append("|---|---|---|---|")
        for x in data["allocation"]:
            L.append("| %s | %.2f 元 | %.1f%% | %s |"
                     % (x["name"], x["amount"], x["share"],
                        "触及单只上限" if x["capped"] else ""))
        L.append("")

    L.append("## 四、判断依据")
    L.append("")
    for it in data["items"]:
        if it.get("signal") == "missing":
            continue
        L.append("**%s %s —— %s**" % (it["key"], it["action"], it["reason"] or "—"))
        L.append("")
        L.append("- 盈利增长假设 %.2f%%（%s），年限 %d 年"
                 % (it.get("growth") or 0, it.get("growth_note") or "", data["years"]))
        if it.get("note"):
            L.append("- 方法提示：%s" % it["note"])
        h = hist.get(it["key"]) or []
        if len(h) >= 2:
            seq = " → ".join(fmt(ef(r.get("pe")), 2) for r in h[-7:])
            L.append("- 最近 PE：%s" % seq)
        L.append("")

    L.append("## 五、数据与方法声明")
    L.append("")
    L.append("- 数据源：蛋卷 `djapi/index_eva/detail`（估值/分位/股息/ROE）+ 腾讯行情（点位），抓取时间见快照 CSV。")
    L.append("- 阈值：`valuation.py` 的 preset，与 skill 正文完全一致；低利率环境下卖出线可能长期不触发，卖出侧以分位 >%.0f%% 为主判据。" % a.sell_pct)
    L.append("- 「推算年化」是**条件推算**：固定估值与盈利增长后算结果，不是价格预测，也不是收益承诺。")
    L.append("- 分红税、换汇、港股通折价、赎回费均未计入本表。")
    L.append("")
    L.append("---")
    L.append("本报告由 `tracker.py` 自动生成，仅供研究，不构成投资建议。")

    text = "\n".join(L) + "\n"
    if a.out:
        out = os.path.abspath(a.out)
    else:
        out = os.path.join(p["reports"], "%s.md" % data["as_of"])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("日报已生成: %s" % out)
    print("  信号: 买 %d / 持 %d / 卖 %d / 放弃 %d"
          % (data["counts"]["buy"], data["counts"]["hold"],
             data["counts"]["sell"], data["counts"]["skip"]))
    return 0


# ---------------------------------------------------------------- CLI

def build_parser():
    ap = argparse.ArgumentParser(
        prog="tracker.py",
        description="指数基金每日跟踪：关注清单 → 每日快照 → 投资判断 → 条件推算",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    def common(sp, with_dir=True):
        if with_dir:
            sp.add_argument("--dir", default=DEFAULT_DIR, help="跟踪数据目录（默认 ./fund-track）")

    p = sub.add_parser("init", help="建目录与默认关注清单")
    common(p)
    p.add_argument("--force", action="store_true", help="覆盖已有清单")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("watch", help="增删查关注品种")
    common(p)
    p.add_argument("--add", help='新增/更新，格式 "代码:名称[:类型[:基金代码[:目标仓位]]]"，多条用分号')
    p.add_argument("--remove", help="移除，多个代码用逗号")
    p.add_argument("--list", action="store_true", help="打印清单")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("collect", help="抓当日估值快照")
    common(p)
    p.add_argument("--date", help="快照日期（默认今天），用于补录")
    p.add_argument("--fixtures", help="离线样本目录（<SH000300>.json），不联网")
    p.add_argument("--no-close", action="store_true", help="不抓点位，只取估值")
    p.set_defaults(func=cmd_collect)

    def add_judge_args(sp):
        sp.add_argument("--preset", default="cal", choices=["book", "cal", "cal-rp"],
                        help="阈值口径（默认 cal=2026 校准；book 为书里 2017 原值）")
        sp.add_argument("--buy-line", type=float, help="覆盖买入线（盈利收益率%%，如 10）")
        sp.add_argument("--sell-line", type=float, help="覆盖卖出线")
        sp.add_argument("--buy-pct", type=float, default=None, help="分位买入线，默认 30")
        sp.add_argument("--sell-pct", type=float, default=None, help="分位卖出线，默认 70")
        sp.add_argument("--base", type=float, default=0.0, help="单只基准金额，用于算建议投入")
        sp.add_argument("--n", type=float, default=1.0, help="定期不定额指数（默认 1）")
        sp.add_argument("--growth", type=float, default=None, help="盈利增长假设%%，默认 ROE×留存率")
        sp.add_argument("--years", type=float, default=3.0, help="推算年限（默认 3 年）")

    p = sub.add_parser("analyze", help="出信号 / 建议金额 / 三情景 / 提醒")
    common(p)
    add_judge_args(p)
    p.add_argument("--budget", type=float, default=0.0, help="本期预算，按目标仓位分配")
    p.add_argument("--max-single", type=float, default=0.4, help="单只上限占预算比例，默认 0.4")
    p.add_argument("--json", action="store_true", help="机器可读输出")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("report", help="生成 markdown 日报")
    common(p)
    add_judge_args(p)
    p.add_argument("--budget", type=float, default=0.0)
    p.add_argument("--max-single", type=float, default=0.4)
    p.add_argument("--days", type=int, default=7, help="报告中回看的快照天数")
    p.add_argument("--out", help="输出路径（默认 reports/<日期>.md）")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("show", help="打印最近 N 日 PE 快照")
    common(p)
    p.add_argument("--days", type=int, default=7)
    p.set_defaults(func=cmd_show)

    return ap


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = build_parser()
    if not argv:
        ap.print_help()
        return 1
    a = ap.parse_args(argv)
    if not getattr(a, "func", None):
        ap.print_help()
        return 1
    if getattr(a, "buy_pct", 0.0) is None:
        a.buy_pct = 30.0
    if getattr(a, "sell_pct", 0.0) is None:
        a.sell_pct = 70.0
    try:
        return a.func(a)
    except BrokenPipeError:
        raise
    except (ValueError, OSError, ZeroDivisionError, RuntimeError) as e:
        print("[错误] %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
