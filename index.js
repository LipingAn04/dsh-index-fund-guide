// ============================================================================
// dsh-index-fund-guide — DSH 插件入口
// ============================================================================
// 标准 Cordis 插件（导出 name + inject + apply(ctx, config)）。
// apply() 把本包自带的 skills/index-fund-guide/ 注册成运行时技能。
//
// 为什么用「插件注册」而不是「丢进 ~/.dsh/skills 让 skill-filesystem 扫描」：
// 部分 deployment（例如 desktop/web 的 preset 形态）会把 host 层的
// `skill-filesystem` 行 disabled 掉，改由 preset 自己挂载，此时磁盘目录
// 扫描并不生效，只有 ctx.skills.register() 进来的技能会出现在 catalog 里。
// 本插件不依赖任何扫描根，装到哪个 profile 就在哪个 profile 可见。
// ============================================================================

const { existsSync, readFileSync } = require('node:fs');
const path = require('node:path');

const SKILL_NAME = 'index-fund-guide';
const SKILL_DIR = path.join(__dirname, 'skills', SKILL_NAME);
const SKILL_FILE = path.join(SKILL_DIR, 'SKILL.md');

module.exports.name = 'dsh-index-fund-guide';
module.exports.inject = ['skills'];

/**
 * 解析 SKILL.md 的 YAML frontmatter —— 只取本插件需要的单行标量键。
 * 技能内容本身由 SKILL.md 承载，这里解析是为了让 name/description/whenToUse
 * 只有一处真源（frontmatter），不会和 index.js 里的副本漂移。
 */
function parseFrontmatter(text) {
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(text);
  if (!m) return { attrs: {}, body: text };
  const attrs = {};
  for (const line of m[1].split(/\r?\n/)) {
    const kv = /^([A-Za-z_][\w-]*):[ \t]*(.*)$/.exec(line);
    if (!kv) continue;
    let value = kv[2].trim();
    if (
      (value.startsWith('"') && value.endsWith('"') && value.length > 1) ||
      (value.startsWith("'") && value.endsWith("'") && value.length > 1)
    ) {
      value = value.slice(1, -1);
    }
    if (value !== '') attrs[kv[1]] = value;
  }
  return { attrs, body: text.slice(m[0].length) };
}

/** 读取技能正文与元数据；文件缺失或元数据不全时给出可诊断的错误。 */
function loadSkill() {
  if (!existsSync(SKILL_FILE)) {
    throw new Error(
      '[dsh-index-fund-guide] 缺少技能文件: ' + SKILL_FILE +
        '（安装不完整？package 的 files 字段必须包含 "skills"）'
    );
  }
  const raw = readFileSync(SKILL_FILE, 'utf8');
  const { attrs } = parseFrontmatter(raw);
  const missing = ['name', 'description', 'whenToUse'].filter((k) => !attrs[k]);
  if (missing.length > 0) {
    throw new Error(
      '[dsh-index-fund-guide] SKILL.md frontmatter 缺少字段: ' + missing.join(', ')
    );
  }
  return {
    name: attrs.name || SKILL_NAME,
    version: attrs.version || null,
    description: attrs.description,
    whenToUse: attrs.whenToUse,
    content: raw.trim(),
    resourceBase: { kind: 'directory', path: SKILL_DIR }
  };
}

/** Cordis 插件入口：注册技能，使 `skill` 工具可按名加载。 */
function apply(ctx) {
  const skill = loadSkill();
  return ctx.skills.register({
    name: skill.name,
    description: skill.description,
    whenToUse: skill.whenToUse,
    content: skill.content,
    // resourceBase 让技能里的相对引用（references/…、scripts/…）在运行时能定位到包内文件。
    resourceBase: skill.resourceBase,
    // source 是技能的「来源桶」（runtime / bundled / user-dsh …），本技能随插件包分发。
    source: 'bundled'
  });
}

/** 兼容旧式加载。 */
function load() {
  return { name: module.exports.name, skill: loadSkill() };
}

module.exports.apply = apply;
module.exports.load = load;
module.exports.SKILL_NAME = SKILL_NAME;
module.exports.SKILL_DIR = SKILL_DIR;
module.exports.loadSkill = loadSkill;
module.exports.parseFrontmatter = parseFrontmatter;
