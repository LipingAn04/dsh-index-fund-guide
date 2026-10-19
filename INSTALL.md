# 安装与维护

本文件放 README 里不适合展开的工程细节：安装的三种方式、两个已知的坑、目录结构、数据来源与报告期规则、发布流程。

---

## 一、安装

### 方式一：从 GitHub 装（推荐）

```bash
pnpm add https://github.com/LipingAn04/dsh-index-fund-guide.git
```

然后把包名追加到 profile 的 `package.json`：

```json
{
  "dsh": {
    "profile": {
      "bundles": ["...", "dsh-index-fund-guide"]
    }
  }
}
```

最后重启 DSH。装好后 `skill({ name: "index-fund-guide" })` 应能加载。

也可以直接在 DSH GUI 的插件管理器里，用 `file:` 地址安装本地目录。

### 方式二：从本地目录装

```bash
git clone https://github.com/LipingAn04/dsh-index-fund-guide.git
pnpm add file:C:\path\to\dsh-index-fund-guide
```

### 方式三：只取脚本（不用 DSH）

`skills/index-fund-guide/scripts/` 下 4 个 Python 文件**零第三方依赖**（仅标准库），Python 3.6+ 直接跑：

```bash
python valuation.py signal --method yield --pe 9.01 --pe-type ttm --preset cal
python dingtou.py irr --invest 1000,1000,1000,6000 --final 8600
```

---

## 二、两个安装坑（都已踩过）

1. **改完源文件要重装才生效。** pnpm 的 `file:` 依赖是**拷贝**进 `node_modules`，不是软链接；`package.json` 的 `version` 不变时 pnpm 认为无需更新（输出 `added 0`）。必须 `remove_bundle` → `install_bundle`，或者先提升版本号。

2. **不要用 PowerShell 的 `Set-Content -Encoding UTF8` 改这些中文文件。** PS 5.1 会按 GBK 读、按 UTF-8 写，中文变乱码（本项目踩过一次：`SKILL.md` 从 14456 字节膨胀成 19264 字节，`package.json` 还带上了 BOM 导致 pnpm 报 `Unexpected token '\ufeff'`）。用编辑器、或用 Python 的 `encoding="utf-8"` 写。

   > 同一个原因还会让 `.ps1` 脚本报语法错：**含中文的 PowerShell 脚本必须存成 UTF-8 with BOM**，否则 PS 5.1 按 GBK 解析，中文字节里冒出的 `"` 会提前终止字符串。

---

## 三、目录结构

```text
.
├── package.json          DSH bundle 元信息（dsh.bundle.patch 指向 cordis.patch.yml）
├── cordis.patch.yml      插件 loader 行（必须用 insert 块，不能写顶层行）
├── index.js              ctx.skills.register() 注册技能，从 SKILL.md 读 frontmatter
├── README.md             能力与核心功能
├── INSTALL.md            本文件
├── DISCLAIMER.md         风险提示与已知问题清单（必读）
├── CHANGELOG.md          版本变更
├── LICENSE               MIT
├── publish.ps1           一键发布脚本
└── skills/index-fund-guide/
    ├── SKILL.md          技能入口：决策树、阈值表、方法分派、最小必读集、回答纪律
    ├── references/
    │   ├── 00-why-index.md        为什么是指数基金（第 1~2 章）
    │   ├── 01-valuation.md        四个估值指标 + 盈利收益率法 + 博格公式 + 四类指数决策表
    │   ├── 02-catalog.md          宽基/行业指数档案（含代码）+ 买前存活检查 + 行业估值表
    │   ├── 03-dingtou.md          定投原理、场内场外、费率、五个提收益技巧、IRR
    │   ├── 04-plan.md             定投四步法 + 可填空计划表 + 三个实例
    │   ├── 05-allocation.md       货币/债券基金 + (100−年龄)% 配置 + 2026 收益现实
    │   ├── 06-qa.md               复利三来源、为何低估还跌、12 条禁止清单
    │   ├── 07-calibration.md      2017 阈值 → 2026 的校准全过程、敏感性、当期信号
    │   └── 08-raw-data.md         自己算 PE/PB：原料、TTM 相减法、三层真实性证据
    └── scripts/
        ├── valuation.py  判断信号 / 校准 / 组合分配 / 回测 / 取数清单
        ├── rawdata.py    抓原料（成分股/行情/财报）→ 自算 PE/PB → 逐只对账
        ├── dingtou.py    定投算术（IRR/XIRR）、计划表、记账、报告
        └── selftest.py   36 项保真断言（离线可跑）
```

---

## 四、数据来源与时间

| 数据 | 来源 | 时间 |
|---|---|---|
| 成分股名单 | 东方财富 `RPT_INDEX_COMPONENT` | 每次现抓 |
| 收盘价 / 总市值 / 流通市值 | 腾讯行情 `qt.gtimg.cn`（返回 `quote_time` 到秒） | 每次现抓 |
| 归母净利润 / 每股净资产 | 东方财富 `RPT_LICO_FN_CPD` | 按**法定披露截止日**推断最近一期 |
| 第三方 PE/PB（仅对账） | 蛋卷 `index_eva`、乐咕乐股 | 2026-10-16 |
| 指数估值快照（分位/股息率） | 蛋卷 `index_eva`（`pe_percentile`） | 2026-10-16 |
| 10Y 国债收益率 | 中国货币网 / 聚金数据 | 2026-10-16：1.648% |
| 债基收益 | 中证 930609、Wind 885008 | 近十年 2.75% / 近五年 2.59% |

**报告期不是"上一个季末"。** 法定披露截止日：一季报 4-30、中报 8-31、三季报 10-31、年报次年 4-30。早期版本用「季末 + 35 天」推断，会在中报/年报窗口抓到只披露了部分公司的半成品，导致 **PE 被大幅高估**；现已改为按截止日判定，且在截止日前抓数据直接拒绝（`--allow-partial` 才放行）。财报缓存带元信息，**半成品永不命中缓存**。

---

## 五、脚本运行环境

- 只用 Python 3 标准库，无第三方依赖；只有 `rawdata.py` 需要联网
- Windows 控制台是 GBK，脚本已把 stdout 设成 `errors="replace"`：中文可能显示乱码但数值正确；需要原样中文时设 `PYTHONIOENCODING=utf-8`
- PowerShell 下以负号开头的参数值会被当成选项，现金流优先用 `--invest ... --final ...`，或用等号形式 `--cashflow=-1000,-1000,6000`
- 需要离线校验时：`python selftest.py`（36 项断言，不联网）

---

## 六、发布与更新

仓库自带 `publish.ps1`，把「填元信息 → 提交 → 建仓库 → 认证 → 推送」一次做完：

```powershell
# 首次发布（自动建仓库，需要 Fine-grained PAT：Contents + Administration 均为 Read and write）
$env:GITHUB_TOKEN = "github_pat_xxx"
.\publish.ps1 -Owner <你的GitHub用户名> -CreateRepo

# 后续更新（仓库已存在，走浏览器登录即可，不需要 token）
.\publish.ps1 -RepoUrl https://github.com/<用户名>/dsh-index-fund-guide.git
```

推送完成后记得去 GitHub 删除本次使用的 PAT。脚本推送成功后会立刻把可能内嵌 token 的 remote 地址还原成干净地址。

日常改完代码的流程：

```powershell
git add -A
git commit -m "fix: 改了什么"
git push
# 本机要生效还需在插件管理器里 remove_bundle → install_bundle
```
