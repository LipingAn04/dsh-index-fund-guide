#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rawdata.py — 用「原料」自己算指数 PE/PB，不采信任何已算好的 PE/PB。

设计原则
--------
1. **只取原料**：总市值、净利润（财报）、净资产（每股净资产×总股本）、收盘价。
   供应商已算好的 PE/PB 只用于**校验**，绝不参与计算。
2. **每一步可复现**：每个数字都带来源 URL、抓取时间、字段名，写进 CSV 供手工抽查。
3. **算完再对账**：公司级 PE 与供应商 PE 逐只比对（中位数误差），指数级 PE 与
   公开口径（蛋卷/乐咕/中证官网）比对，差在哪要能说清。

公式（与《指数基金投资指南》一致）
--------------------------------
PE = Σ市值 / Σ净利润(TTM)        PB = Σ市值 / Σ净资产(归母)
净利润 TTM = 本期累计 + 上年全年 − 上年同期累计
净资产     = 每股净资产 × 总股本，总股本 = 总市值 ÷ 收盘价

三种加权口径（结论会不同，必须标明）
--------------------------------
total   总市值加权   —— 大盘股主导
float   自由流通加权 —— 接近中证官方「调整市值」口径
median  个股 PE 中位数 —— 等权视角，反映「典型公司」

用法
----
  rawdata.py fetch --index 000300                 # 抓原料，缓存到 ./_ifg_cache/
  rawdata.py calc  --index 000300                 # 算 PE/PB + 三口径 + 覆盖度
  rawdata.py calc  --index 000300 --verify        # 加上逐只对账
  rawdata.py calc  --index 000300 --csv out.csv   # 导出逐只明细供手工核对
  rawdata.py list                                 # 已缓存哪些指数
"""
import argparse
import csv
import datetime
import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

UA_EM = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
UA_TX = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"
TX = "https://qt.gtimg.cn/q="
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

SRC = {
    "constituents": "东财数据中心 RPT_INDEX_COMPONENT（指数成分股名单）",
    "quotes": "腾讯行情 qt.gtimg.cn（收盘价/总市值/流通市值；PE、PB 仅用于对账）",
    "reports": "东财数据中心 RPT_LICO_FN_CPD（归母净利润、每股净资产）",
    "indexperf": "中证指数官网 index-perf（指数点位，仅用于交叉核对）",
}

REPORT_COLS = "SECURITY_CODE,REPORTDATE,PARENT_NETPROFIT,BPS,BASIC_EPS"


# ---------------------------------------------------------------- 基础工具
def http_get(url, headers=None, timeout=25, tries=3):
    last = ""
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers or UA_EM)
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
                return r.read()
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, e)
            if i < tries - 1:
                time.sleep(0.5 * (i + 1))
    raise SystemExit("网络请求失败 %s\n  %s" % (url.split("?")[0], last))


def cache_dir(explicit=None):
    cands = [explicit, os.environ.get("IFG_CACHE"), os.path.join(os.getcwd(), "_ifg_cache"),
             os.path.join(os.path.dirname(os.path.abspath(__file__)), "_ifg_cache")]
    for d in cands:
        if not d:
            continue
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".w")
            with open(probe, "w") as f:
                f.write("x")
            os.remove(probe)
            return d
        except Exception:
            continue
    raise SystemExit("找不到可写目录，请用 --cache 指定")


def today():
    return datetime.date.today().isoformat()


# ---------------------------------------------------------------- 1. 成分股
def fetch_constituents(index_code):
    url = (DC + "?reportName=RPT_INDEX_COMPONENT&columns=ALL&filter=" +
           urllib.parse.quote('(INDEX_CODE="%s")' % index_code, safe="") +
           "&pageSize=1000&source=WEB&client=WEB")
    raw = http_get(url)
    j = json.loads(raw.decode("utf-8", "replace"))
    rows = (j.get("result") or {}).get("data") or []
    codes = [r["SECURITY_CODE"] for r in rows]
    if not codes:
        raise SystemExit("成分股为空：%s（指数代码可能不存在）" % index_code)
    return codes, url


# ---------------------------------------------------------------- 2. 行情
def _tx_prefix(code):
    if code[0] == "6":
        return "sh"
    if code[0] in "03":
        return "sz"
    if code[0] in "48":
        return "bj"
    return "sh"


def fetch_quotes(codes, batch=60):
    """腾讯行情，返回 {code: {name, price, mcap, float_mcap, pe_vendor, pb_vendor}}。"""
    out = {}
    for i in range(0, len(codes), batch):
        chunk = codes[i:i + batch]
        url = TX + ",".join(_tx_prefix(c) + c for c in chunk)
        txt = http_get(url, headers=UA_TX).decode("gbk", "replace")
        for line in txt.split(";"):
            line = line.strip()
            if not line.startswith("v_") or "=" not in line:
                continue
            body = line.split("=", 1)[1].strip().strip('"')
            f = body.split("~")
            if len(f) < 48:
                continue
            code = f[2].strip()

            def num(idx, scale=1.0):
                try:
                    return float(f[idx]) * scale
                except Exception:
                    return None

            out[code] = {
                "name": f[1].strip(),
                "price": num(3),
                # 腾讯以「亿元」为单位：44=流通市值 45=总市值
                "mcap": num(45, 1e8),
                "float_mcap": num(44, 1e8),
                "pe_vendor": num(39),      # 仅用于对账
                "pb_vendor": num(46),      # 仅用于对账
                "quote_time": f[30].strip(),
            }
    return out


# ---------------------------------------------------------------- 3. 财报
# 法定披露截止日：一季报 04-30 / 中报 08-31 / 三季报 10-31 / 年报 次年 04-30。
# 注意不能统一用「季末 + 35 天」：那样在中报窗口(8/4~8/31)和年报窗口(2/4~4/30)
# 会抓到只披露了部分公司的半成品，Σ净利润被低估，PE 被大幅高估。
_DISCLOSE = {"03-31": (0, "04-30"), "06-30": (0, "08-31"),
             "09-30": (0, "10-31"), "12-31": (1, "04-30")}


def disclosure_deadline(period):
    """某一报告期的法定披露截止日(date)。"""
    p = datetime.date.fromisoformat(period)
    add_years, md = _DISCLOSE["%02d-%02d" % (p.month, p.day)]
    return datetime.date(p.year + add_years, int(md[:2]), int(md[3:]))


def latest_period(today_str=None):
    """最近一期「已过法定披露截止日」的报告期。

    判定基准是「发起计算的今天」；返回的是今天为止已经披露完整、可以安全使用的
    最近一期。今天 = 2026-10-19 → 2026-06-30（三季报要到 10-31 才截止）。
    """
    d = datetime.date.fromisoformat(today_str) if today_str else datetime.date.today()
    cands = []
    for y in (d.year, d.year - 1):
        for md in ("12-31", "09-30", "06-30", "03-31"):
            end = datetime.date.fromisoformat("%d-%s" % (y, md))
            if d > disclosure_deadline(end.isoformat()):
                cands.append(end)
    return max(cands).isoformat()


def ttm_dates(period):
    """返回 (本期, 上年全年, 上年同期)。"""
    p = datetime.date.fromisoformat(period)
    if p.month == 12:
        return period, None, None
    prev_fy = "%d-12-31" % (p.year - 1)
    same = "%d-%02d-%02d" % (p.year - 1, p.month, p.day)
    return period, prev_fy, same


def fetch_report_period(report_date, cache, pages_max=30, refresh=False):
    """抓某一报告期全市场归母净利润，缓存到磁盘。

    缓存带元信息（抓取时间 / 条数 / 是否已过披露截止）。**没过披露截止就抓的
    半成品不会被命中**，下次自动重抓 —— 否则一次抓早会永久污染后面的计算。
    """
    p = os.path.join(cache, "report-%s.json" % report_date)
    if os.path.exists(p) and not refresh:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "_meta" in data:
            meta = data["_meta"]
            hit = data.get("rows") or {}
            if meta.get("complete") and hit:
                return hit
            print("  报告期 %s 的缓存是半成品（抓于 %s，披露截止 %s），重抓"
                  % (report_date, meta.get("fetched_at"), meta.get("deadline")))
        else:
            print("  报告期 %s 的缓存是旧格式（无元信息），重抓" % report_date)
    out = {}
    for pn in range(1, pages_max + 1):
        url = (DC + "?reportName=RPT_LICO_FN_CPD&columns=" + REPORT_COLS +
               "&filter=" + urllib.parse.quote("(REPORTDATE='%s')" % report_date, safe="") +
               "&pageSize=500&pageNumber=%d&source=WEB&client=WEB"
               "&sortColumns=SECURITY_CODE&sortTypes=1" % pn)
        j = json.loads(http_get(url).decode("utf-8", "replace"))
        res = j.get("result") or {}
        rows = res.get("data") or []
        if not rows:
            break
        for r in rows:
            code = r.get("SECURITY_CODE")
            if not code:
                continue
            out[code] = {
                "np": r.get("PARENT_NETPROFIT"),
                "bps": r.get("BPS"),
                "eps": r.get("BASIC_EPS"),
            }
        sys.stdout.write("\r  报告期 %s: %d 家" % (report_date, len(out)))
        sys.stdout.flush()
        if pn >= (res.get("pages") or 1):
            break
    print()
    now = datetime.datetime.now()
    dl = disclosure_deadline(report_date)
    complete = now.date() > dl
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"_meta": {"fetched_at": now.isoformat(timespec="seconds"),
                             "period": report_date, "n": len(out),
                             "deadline": dl.isoformat(), "complete": complete},
                   "rows": out}, f, ensure_ascii=False)
    return out


# ---------------------------------------------------------------- 抓取主流程
def cmd_fetch(a):
    cache = cache_dir(a.cache)
    idx = a.index
    print("== 抓原料: 指数 %s ==" % idx)
    # 报告期先定下来再联网：半成品不该白白消耗请求
    period = a.period or latest_period()
    dl = disclosure_deadline(period)
    if datetime.date.today() <= dl:
        msg = ("报告期 %s 的法定披露截止日 %s 还没到，此刻抓到的只是已披露的那部分公司，"
               "Σ净利润会偏小、PE 会偏高" % (period, dl))
        if not getattr(a, "allow_partial", False):
            raise SystemExit("[!] " + msg + "\n    如确实要用半成品，加 --allow-partial")
        print("  [!] " + msg + "（按 --allow-partial 继续）")
    print("  报告期 %s   法定披露截止 %s" % (period, dl))
    d1, d2, d3 = ttm_dates(period)
    codes, url_cons = fetch_constituents(idx)
    print("  成分股 %d 只  来源: %s" % (len(codes), SRC["constituents"]))
    time.sleep(0.2)

    quotes = fetch_quotes(codes, batch=a.batch)
    miss = [c for c in codes if c not in quotes]
    print("  行情 %d 只  缺 %d  来源: %s" % (len(quotes), len(miss), SRC["quotes"]))
    qts = sorted({v.get("quote_time") for v in quotes.values() if v.get("quote_time")})
    if qts:
        print("  行情时间 %s%s（盘中跑是实时价，收盘后跑是当日收盘价）"
              % (qts[0], "" if len(qts) == 1 else " ~ " + qts[-1]))

    period = a.period or latest_period()
    d1, d2, d3 = ttm_dates(period)
    print("  报告期 TTM: %s + %s − %s"
          % (d1, d2 or "(不适用: 年报即全年)", d3 or "(不适用)"))
    rf = getattr(a, "refresh", False)
    r1 = fetch_report_period(d1, cache, refresh=rf)
    r2 = fetch_report_period(d2, cache, refresh=rf) if d2 else {}
    r3 = fetch_report_period(d3, cache, refresh=rf) if d3 else {}

    payload = {
        "index": idx,
        "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "period": period,
        "periods": [d1, d2, d3],
        "sources": dict(SRC, constituents_url=url_cons),
        "codes": codes,
        "quotes": quotes,
        "reports": {"latest": r1, "prev_fy": r2, "prev_same": r3},
    }
    out = os.path.join(cache, "index-%s.json" % idx)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print("  已缓存: %s" % out)
    return 0


# ---------------------------------------------------------------- 计算
def load_payload(idx, cache):
    p = os.path.join(cache, "index-%s.json" % idx)
    if not os.path.exists(p):
        raise SystemExit("没有缓存 %s，先跑: rawdata.py fetch --index %s" % (p, idx))
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def compute(payload):
    """返回 (明细列表, 汇总)。明细里每个字段都是原料或由原料算出的中间量。"""
    q = payload["quotes"]
    r1 = payload["reports"]["latest"]
    r2 = payload["reports"]["prev_fy"]
    r3 = payload["reports"]["prev_same"]
    rows = []
    for code in payload["codes"]:
        qq = q.get(code)
        if not qq or not qq.get("mcap"):
            continue
        a1 = r1.get(code) or {}
        np1, bps = a1.get("np"), a1.get("bps")
        if np1 is None or not bps:
            continue
        # ---- 净利润 TTM（原料相加减，不采信供应商的 PE）----
        if not r2:
            np_ttm = np1                      # 最新期就是年报
        else:
            np2 = (r2.get(code) or {}).get("np")
            np3 = (r3.get(code) or {}).get("np")
            if np2 is None or np3 is None:
                continue
            np_ttm = np1 + np2 - np3
        if not np_ttm:                       # 净利润 TTM 缺失 -> 无法算 PE
            continue
        # 总股本 = 总市值 / 收盘价（与交易所披露股本一致，脚本会做恒等式自检）
        shares = qq["mcap"] / qq["price"] if qq.get("price") else None
        equity = bps * shares if shares and bps > 0 else None
        pos = np_ttm > 0                     # 亏损股：不进 PE 加权，但市值要计入合计
        rows.append({
            "code": code, "name": qq.get("name"), "price": qq.get("price"),
            "mcap": qq["mcap"], "float_mcap": qq.get("float_mcap"),
            "np_ttm": np_ttm, "bps": bps, "shares": shares, "equity": equity,
            "loss": not pos,
            "pe_ours": (qq["mcap"] / np_ttm) if pos else None,
            "pb_ours": (qq["mcap"] / equity) if equity else None,
            "pe_vendor": qq.get("pe_vendor"), "pb_vendor": qq.get("pb_vendor"),
            "weight": qq["mcap"],
        })
    ok = [r for r in rows if r["pe_ours"]]
    allr = [r for r in rows if r["np_ttm"] is not None]
    mcap_all = sum(r["mcap"] for r in allr)
    np_all = sum(r["np_ttm"] for r in allr)          # 含亏损股（负值参与合计）
    ff_all = sum(r["float_mcap"] or 0 for r in allr)
    tot_mcap = sum(r["mcap"] for r in ok)
    tot_np = sum(r["np_ttm"] for r in ok)
    tot_eq = sum(r["equity"] for r in ok if r["equity"])
    ff_pos = sum(r["float_mcap"] or 0 for r in ok)
    pes = sorted(r["pe_ours"] for r in ok)
    median = pes[len(pes) // 2] if pes else None
    summary = {
        "n_constituents": len(payload["codes"]),
        "n_priced": len(rows),
        "n_used": len(ok),
        "n_agg": len(allr),
        "n_loss": sum(1 for r in allr if r["loss"]),
        "cover_mcap_pct": 100.0 * mcap_all / sum(r["mcap"] for r in rows) if rows else 0,
        # 中证口径：Σ总市值 / Σ净利润，亏损股的负利润一起计入
        "pe_total": mcap_all / np_all if np_all > 0 else None,
        # 剔除亏损股口径：只把盈利公司加权
        "pe_pos": tot_mcap / tot_np if tot_np > 0 else None,
        "pe_float": ff_all / np_all if np_all > 0 and ff_all else None,
        "pe_float_pos": ff_pos / tot_np if tot_np > 0 and ff_pos else None,
        "pe_median": median,
        "pb_total": tot_mcap / tot_eq if tot_eq else None,
        "sum_mcap": mcap_all, "sum_float": ff_all, "sum_np": np_all,
        "sum_np_pos": tot_np, "sum_equity": tot_eq,
    }
    return rows, summary


def _fmt(x, n=2):
    return "-" if x is None else ("%.*f" % (n, x))


def _clean_name(name):
    """去掉除息/除权前缀，便于核对。"""
    s = (name or "").strip()
    for pre in ("XD", "XR", "DR", "N", "C"):
        if s.startswith(pre) and len(s) > len(pre) + 1:
            s = s[len(pre):]
            break
    return s


def _age_days(iso):
    """距今天数；解析失败返回 None。"""
    try:
        t = datetime.datetime.fromisoformat(iso)
    except Exception:
        return None
    return (datetime.datetime.now() - t).days


def cmd_calc(a):
    cache = cache_dir(a.cache)
    payload = load_payload(a.index, cache)
    rows, s = compute(payload)
    age = _age_days(payload.get("fetched_at"))
    qts = sorted({v.get("quote_time") for v in payload["quotes"].values()
                  if v.get("quote_time")})
    print("=" * 78)
    print("指数 %s  抓取时间 %s%s  报告期 TTM %s"
          % (payload["index"], payload["fetched_at"],
             "" if age is None else "（%d 天前）" % age, payload["period"]))
    if qts:
        print("行情时间 %s%s" % (qts[0], "" if len(qts) == 1 else " ~ " + qts[-1]))
    dl = disclosure_deadline(payload["period"])
    print("报告期披露截止 %s%s"
          % (dl.isoformat(),
             "" if datetime.date.today() > dl else "   [!] 尚未截止，数据可能不全"))
    print("=" * 78)
    if age:
        print("[!] 这是 %d 天前的快照，市值与 PE 已不是当前值。要最新值请重跑:"
              % age)
        print("    rawdata.py fetch --index %s" % payload["index"])
        print()
    print("成分股 %d 只 → 取到行情 %d 只 → 进入合计 %d 只（市值覆盖 %.2f%%；其中亏损 %d 只）"
          % (s["n_constituents"], s["n_priced"], s["n_agg"], s["cover_mcap_pct"], s["n_loss"]))
    print()
    print("原料合计（亿元）：")
    print("  总市值      %15.2f" % (s["sum_mcap"] / 1e8))
    print("  自由流通市值 %15.2f" % (s["sum_float"] / 1e8))
    print("  净利润 TTM   %15.2f" % (s["sum_np"] / 1e8))
    print("  净资产       %15.2f" % (s["sum_equity"] / 1e8))
    print()
    print("自己算出来（不是抄的）：")
    print("  PE(中证口径)     %8s   = Σ总市值 / Σ净利润（亏损股的负利润计入；共 %d 只亏损）"
          % (_fmt(s["pe_total"]), s["n_loss"]))
    print("  PE(剔除亏损)     %8s   = 只把盈利公司加权，通常比中证口径低" % _fmt(s["pe_pos"]))
    print("  PE(自由流通加权) %8s   = Σ自由流通市值 / Σ净利润（权重偏小盘）" % _fmt(s["pe_float"]))
    print("  PE(个股中位数)   %8s   = 等权视角，不受巨头影响" % _fmt(s["pe_median"]))
    print("  PB(总市值加权)   %8s   = Σ总市值 / Σ净资产" % _fmt(s["pb_total"]))
    miss = [r for r in rows if r["np_ttm"] is None and r["mcap"]]
    if miss:
        miss.sort(key=lambda r: -r["mcap"])
        print()
        print("  真的缺数据的 %d 只（没有可用的净利润，占指数 %.2f%%）：%s"
              % (len(miss), 100.0 * sum(r["mcap"] for r in miss) / s["sum_mcap"],
                 "、".join("%s %s" % (r["code"], _clean_name(r["name"])) for r in miss[:6])))
        print("  它们多半是上市不足一年或该期未披露，剔除后 PE 会略偏低，幅度在 0.1~0.5 个点。")
    print()
    if a.verify:
        print("逐只对账（我们的 PE = 市值/净利润TTM，对比供应商公布的 PE）：")
        pairs = [(r["pe_ours"], r["pe_vendor"], r) for r in rows
                 if r["pe_ours"] and r["pe_vendor"] and r["pe_vendor"] > 0]
        if pairs:
            errs = sorted(abs(o - v) / v * 100 for o, v, _ in pairs)
            within1 = sum(1 for e in errs if e <= 1.0)
            print("  样本 %d 只；中位误差 %.3f%%；90 分位 %.2f%%；最大 %.2f%%；误差≤1%% 的占 %.1f%%"
                  % (len(pairs), errs[len(errs) // 2], errs[int(len(errs) * 0.9)],
                     errs[-1], 100.0 * within1 / len(errs)))
            bad = sorted(pairs, key=lambda p: -abs(p[0] - p[1]) / p[1])
            bad = [p for p in bad if abs(p[0] - p[1]) / p[1] * 100 > 5]
            if bad:
                print("  误差 >5%% 的 %d 只（多数是当年重组/换股/股本变动，TTM 相减法会失真）："
                      % len(bad))
                for o, v, r in bad[:5]:
                    print("     %-8s %-8s 我们 %.2f vs 公布 %.2f  市值 %.1f 亿（占指数 %.2f%%）"
                          % (r["code"], _clean_name(r["name"]), o, v,
                             r["mcap"] / 1e8, 100.0 * r["mcap"] / s["sum_mcap"]))
        pbp = [(r["pb_ours"], r["pb_vendor"]) for r in rows
               if r["pb_ours"] and r["pb_vendor"] and r["pb_vendor"] > 0]
        if pbp:
            perrs = sorted(abs(o - v) / v * 100 for o, v in pbp)
            print("  PB 对账 %d 只；中位误差 %.3f%%；误差≤1%% 占 %.1f%%"
                  % (len(perrs), perrs[len(perrs) // 2],
                     100.0 * sum(1 for e in perrs if e <= 1.0) / len(perrs)))
        print()
    top = sorted([r for r in rows if r["pe_ours"]], key=lambda r: -r["mcap"])[:a.top]
    if top:
        print("权重最大的 %d 只（可逐个去行情软件核对）：" % len(top))
        print("  %-8s %-10s %10s %8s %14s %14s %9s %9s"
              % ("代码", "名称", "总市值亿", "收盘价", "净利润TTM亿", "净资产亿", "PE(算)", "PE(公布)"))
        for r in top:
            print("  %-8s %-10s %10.1f %8.2f %14.1f %14.1f %9s %9s"
                  % (r["code"], _clean_name(r["name"])[:10], r["mcap"] / 1e8, r["price"],
                     r["np_ttm"] / 1e8, (r["equity"] or 0) / 1e8,
                     _fmt(r["pe_ours"]), _fmt(r["pe_vendor"])))
        print()
    print("口径提醒：中证官网公布的指数 PE 用「调整市值」（按自由流通比例分档加权），")
    print("          介于总市值与自由流通之间。经验上 PE(总市值) 最接近公开的指数 PE，")
    print("          自由流通口径权重偏向小盘、偏差更大；三个数一起看，别只挑一个。")
    if a.published_pe or a.published_pb:
        print()
        print("与公开数据对账：")
        if a.published_pe:
            pe_dev = (s["pe_total"] - a.published_pe) / a.published_pe * 100
            print("  公开 PE %8.4f  vs  我们(总市值) %8.4f   偏差 %+.2f%%"
                  % (a.published_pe, s["pe_total"], pe_dev))
        if a.published_pb:
            pb_dev = (s["pb_total"] - a.published_pb) / a.published_pb * 100
            print("  公开 PB %8.4f  vs  我们(总市值) %8.4f   偏差 %+.2f%%"
                  % (a.published_pb, s["pb_total"], pb_dev))
        print("  偏差主要来自加权方式（调整市值 vs 总市值）与 PE 口径（静态/TTM/动态），")
        print("  低于 1% 属正常口径差；超过 3% 说明有一方的数据不是同一天或口径不同。")
    if a.csv:
        with open(a.csv, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["code", "name", "price", "mcap_yi", "float_mcap_yi", "np_ttm_yi",
                        "bps", "equity_yi", "pe_ours", "pb_ours", "pe_vendor", "pb_vendor",
                        "source", "fetched_at"])
            for r in rows:
                w.writerow([r["code"], r["name"], r["price"],
                            round(r["mcap"] / 1e8, 4),
                            round((r["float_mcap"] or 0) / 1e8, 4),
                            round(r["np_ttm"] / 1e8, 4) if r["np_ttm"] else "",
                            r["bps"], round((r["equity"] or 0) / 1e8, 4),
                            _fmt(r["pe_ours"], 6), _fmt(r["pb_ours"], 6),
                            r["pe_vendor"], r["pb_vendor"],
                            "东财成分+RPT_LICO_FN_CPD / 腾讯行情",
                            payload["fetched_at"]])
        print("逐只明细已导出: %s（%d 行，可手工抽查任意一只）" % (a.csv, len(rows)))
    if a.json:
        print(json.dumps({"index": payload["index"], "summary": s,
                          "fetched_at": payload["fetched_at"]},
                         ensure_ascii=False, indent=2))
    if s.get("pe_total"):
        print()
        print("下一步（把算出来的数直接喂给买卖信号）：")
        print("  valuation.py signal --pe %.4f --pe-type ttm --method yield"
              % s["pe_total"])
        print("  （真要下判断，再补 --percentile 用分位交叉验证；PE 只是其中一把尺子）")
    return 0


def cmd_list(a):
    cache = cache_dir(a.cache)
    files = sorted(f for f in os.listdir(cache) if f.startswith("index-"))
    if not files:
        print("缓存目录 %s 里还没有指数数据" % cache)
        return 0
    for fn in files:
        with open(os.path.join(cache, fn), encoding="utf-8") as f:
            p = json.load(f)
        print("%-10s 成分 %-5d 抓取 %s  报告期 %s"
              % (p["index"], len(p["codes"]), p["fetched_at"], p["period"]))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(
        description="用原料（市值/净利润/净资产）自己算指数 PE/PB，并与公开数据对账")
    sub = ap.add_subparsers(dest="cmd")

    f = sub.add_parser("fetch", help="抓成分股、行情、财报原料")
    f.add_argument("--index", default="000300", help="指数代码，如 000300 / 000016 / 000922")
    f.add_argument("--period", help="报告期 YYYY-MM-DD，默认按今天推断")
    f.add_argument("--batch", type=int, default=60, help="行情每批只数（默认 60）")
    f.add_argument("--cache", help="缓存目录")
    f.add_argument("--refresh", action="store_true",
                   help="忽略缓存，强制重抓财报（行情每次都是现抓）")
    f.add_argument("--allow-partial", action="store_true",
                   help="允许使用尚未到披露截止日的半成品报告期（默认拒绝）")
    f.set_defaults(func=cmd_fetch)

    c = sub.add_parser("calc", help="用原料算 PE/PB，可选对账与导出")
    c.add_argument("--index", default="000300")
    c.add_argument("--cache", help="缓存目录")
    c.add_argument("--verify", action="store_true", help="逐只对比供应商公布的 PE/PB")
    c.add_argument("--csv", help="导出逐只明细 CSV")
    c.add_argument("--json", action="store_true", help="输出 JSON 汇总")
    c.add_argument("--top", type=int, default=8, help="打印权重最大的前 N 只（默认 8）")
    c.add_argument("--published-pe", type=float, help="第三方公布的指数 PE，用于对账")
    c.add_argument("--published-pb", type=float, help="第三方公布的指数 PB，用于对账")
    c.set_defaults(func=cmd_calc)

    l = sub.add_parser("list", help="列出已缓存的指数")
    l.add_argument("--cache", help="缓存目录")
    l.set_defaults(func=cmd_list)
    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    if not getattr(a, "func", None):
        ap.print_help()
        return 1
    return a.func(a)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
