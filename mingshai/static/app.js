/* 所以然前端：无构建步骤的原生 JS。所有数字来自后端确定性引擎，前端只负责展示和收集用户的修改。 */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtN = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toLocaleString("zh-CN", { minimumFractionDigits: 0, maximumFractionDigits: d }));
const SKIP = "__skip__";
const REPO = "https://github.com/flukier1016/nl-stock-screener";

/* 线性图标：24 网格、1.7 描边、圆角端点，和应用图标同一种笔触 */
const ICONS = {
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  sparkles: '<path d="M12 3.5 13.9 8.6 19 10.5l-5.1 1.9L12 17.5l-1.9-5.1L5 10.5l5.1-1.9z"/><path d="M19 3v3.5M17.25 4.75h3.5"/>',
  funnel: '<path d="M3.5 5h17l-6.5 7.8V19l-4 2v-8.2z"/>',
  check: '<circle cx="12" cy="12" r="8.5"/><path d="m8.5 12.3 2.4 2.4 4.6-4.9"/>',
  x: '<path d="M17.5 6.5 6.5 17.5M6.5 6.5l11 11"/>',
  alert: '<path d="M12 3.8 2.8 19.5h18.4z"/><path d="M12 10v4.2M12 17v.01"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8v.01"/>',
  database: '<ellipse cx="12" cy="5.5" rx="7.5" ry="2.5"/><path d="M4.5 5.5v13c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5v-13M4.5 12c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  sliders: '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
  columns: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M9.2 4.5v15M14.8 4.5v15"/>',
  bookmark: '<path d="M6.5 3.5h11v17l-5.5-3.8-5.5 3.8z"/>',
  bell: '<path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 1.5h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
  download: '<path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M4.5 19.5h15"/>',
  chevron: '<path d="m9.5 6 6 6-6 6"/>',
  arrow: '<path d="M5 12h14M13.5 6.5 19 12l-5.5 5.5"/>',
  shield: '<path d="M12 3.2 4.8 6v5.8c0 4.4 3 7.7 7.2 9 4.2-1.3 7.2-4.6 7.2-9V6z"/><path d="m9 12 2.1 2.1 4-4.2"/>',
  external: '<path d="M14 4.5h5.5V10M19.5 4.5 11 13M17.5 14v4.5a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1v-11a1 1 0 0 1 1-1H10"/>',
  refresh: '<path d="M19.5 11.5a7.5 7.5 0 1 0-2.2 5.3M19.5 5v6.5H13"/>',
  trash: '<path d="M4.5 7h15M10 11v5.5M14 11v5.5M6.5 7l1 12.5h9L17.5 7M9.5 7V4.5h5V7"/>',
  target: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r=".8"/>',
  layers: '<path d="m12 3.5 8.5 4.8-8.5 4.8-8.5-4.8z"/><path d="m3.5 12.6 8.5 4.8 8.5-4.8"/>',
  book: '<path d="M5 4.5A1.5 1.5 0 0 1 6.5 3H19v15.5H6.5A1.5 1.5 0 0 0 5 20z"/><path d="M5 20a1.5 1.5 0 0 0 1.5 1.5H19"/>',
  message: '<path d="M4.5 5h15v10.5H10l-5.5 4.5z"/><path d="M8.5 9.5h7M8.5 12h4"/>',
  minus: '<circle cx="12" cy="12" r="8.5"/><path d="M8.5 12h7"/>',
  help: '<circle cx="12" cy="12" r="8.5"/><path d="M9.8 9.6a2.3 2.3 0 1 1 3.2 2.1c-.6.3-1 .8-1 1.4v.6M12 16.8v.01"/>',
  pie: '<path d="M12 3.5v8.5h8.5A8.5 8.5 0 1 1 12 3.5z"/><path d="M15 3.8A8.5 8.5 0 0 1 20.2 9H15z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4 6 18M18 6l1.4-1.4"/>',
  moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
  github: '<path d="M9 19c-4.3 1.4-4.3-2.5-6-3m12 5v-3.5c0-1 .1-1.4-.5-2 2.8-.3 5.5-1.4 5.5-6a4.6 4.6 0 0 0-1.3-3.2 4.2 4.2 0 0 0-.1-3.2s-1.1-.3-3.5 1.3a12.3 12.3 0 0 0-6.2 0C6.5 2.8 5.4 3.1 5.4 3.1a4.2 4.2 0 0 0-.1 3.2A4.6 4.6 0 0 0 4 9.5c0 4.6 2.7 5.7 5.5 6-.6.6-.6 1.2-.5 2V21"/>',
};
const icon = (n, cls = "") => `<svg class="i ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[n] || ""}</svg>`;

const EXAMPLES = [
  "经营改善、估值合理、走势相对稳定",
  "高股息、低波动的大盘股",
  "银行股里市净率低于1倍、股息率5%以上的",
  "ROE大于15%、负债率不超过40%、现金流好的公司",
  "高成长但估值又低的小盘股",
  "明天会涨停的股票",
];
const CAT = { passed: "入选", near_miss: "差一点", excluded: "被排除", missing: "数据缺失" };
const CAT_CLS = { passed: "pass", near_miss: "near", excluded: "fail", missing: "miss" };
const CAT_HELP = {
  passed: "满足全部条件。按代码排序，不代表推荐。",
  near_miss: "只差一个条件，且差距在 20% 以内。",
  excluded: "至少一个条件不满足，红色数字就是卡住它的那条。",
  missing: "缺数据，无法判定，所以不算通过。",
};
const SEV = { conflict: ["冲突", "alert"], applicability: ["口径不适用", "alert"], tension: ["张力", "info"], coverage: ["缺数据较多", "info"], error: ["错误", "alert"] };

const S = {
  client: (() => { try { let id = localStorage.getItem("ms_client"); if (!id) { id = crypto.randomUUID(); localStorage.setItem("ms_client", id); } return id; } catch { return "anon-" + Math.random().toString(36).slice(2); } })(),
  data: new URLSearchParams(location.search).get("data"),
  meta: null, query: "", parsed: null, segments: [], choices: {}, strategy: null, assumptions: [], notes: [],
  check: null, result: null, resultKey: null, tab: "passed", sort: null, selected: new Set(), showN: 150, filter: "",
};

async function api(path, body, method) {
  const url = path + (S.data ? (path.includes("?") ? "&" : "?") + "data=" + encodeURIComponent(S.data) : "");
  const res = await fetch(url, {
    method: method || (body ? "POST" : "GET"),
    headers: { "Content-Type": "application/json", "X-Client-Id": S.client },
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* 非 JSON */ }
  if (!res.ok) {
    const d = (data && (data.detail || data)) || {};
    const err = new Error(d.message || (d.issues && d.issues.map((i) => i.message).join("；")) || `请求失败（${res.status}）`);
    err.status = res.status; err.detail = d;
    throw err;
  }
  return data;
}

function toast(msg, ms = 2600) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("on");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("on"), ms);
}
const metric = (id) => S.meta?.catalog.metrics.find((m) => m.id === id);
const concept = (id) => S.meta?.catalog.concepts.find((c) => c.id === id);
const opText = { "<=": "≤", ">=": "≥", "<": "<", ">": ">", between: "介于" };
const notice = (cls, ic, title, body) => `<div class="notice ${cls}">${icon(ic)}<div>${title ? `<b>${esc(title)}</b>` : ""}${body}</div></div>`;

function condText(c) {
  const m = metric(c.metric); if (!m) return c.metric;
  if (c.basis === "industry_median") return `${m.name} ${opText[c.op]} 行业中位数${c.value != 1 ? " × " + c.value : ""}`;
  if (c.basis === "market_median") return `${m.name} ${opText[c.op]} 全市场中位数${c.value != 1 ? " × " + c.value : ""}`;
  if (c.op === "between") return `${m.name} 介于 ${c.value[0]}～${c.value[1]}${m.unit}`;
  return `${m.name} ${opText[c.op]} ${c.value}${m.unit}`;
}

/* ── 主题：默认跟随系统，手动切换后记在本浏览器 ───────────────────── */
function currentTheme() {
  const t = document.documentElement.dataset.theme;
  if (t) return t;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
(function initTheme() { try { const t = localStorage.getItem("ms_theme"); if (t) document.documentElement.dataset.theme = t; } catch { /* 隐私模式 */ } })();
function toggleTheme(next) {
  next = next || (currentTheme() === "dark" ? "light" : "dark");
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("ms_theme", next); } catch { /* 隐私模式 */ }
  renderTop();
}

/* ── 顶栏与横幅 ─────────────────────────────────────────── */
function renderTop() {
  const m = S.meta; const el = $("#topStatus");
  if (!m) { el.innerHTML = `<span class="chip"><span class="spin"></span> 连接中</span>`; return; }
  const snap = m.snapshot; const src = snap?.sources || {};
  const llm = m.llm; const chips = [];
  if (snap) {
    chips.push(`<span class="chip click" data-act="data-panel" title="查看数据状态与覆盖率"><span class="dot ${snap.mode === "demo" ? "warn" : "ok"}"></span>${snap.mode === "demo" ? "演示数据" : `扶摇快照 <span class="mono">${esc(snap.snapshot_id)}</span>`}</span>`);
    if (src.prices?.last_date) chips.push(`<span class="chip opt-chip" title="日 K 行情截止日">${icon("clock", "sm")}行情 <span class="mono">${esc(src.prices.last_date)}</span></span>`);
    if (src.valuation?.as_of) chips.push(`<span class="chip opt-chip" title="估值快照时点">${icon("pie", "sm")}估值 <span class="mono">${esc(src.valuation.as_of)}</span></span>`);
    chips.push(`<span class="chip opt-chip" title="财务口径">${icon("book", "sm")}财务 2026 中报</span>`);
  } else {
    chips.push(`<span class="chip click" data-act="data-panel"><span class="dot bad"></span>真实数据未就绪</span>`);
  }
  const bud = llm.budget;
  const llmTip = llm.available ? `意图解析用 ${llm.model || "大模型"}${bud ? `；AI 额度剩 $${bud.remaining_usd.toFixed(2)}，总额 $${bud.limit_usd}` : ""}`
    : (bud?.disabled ? bud.reason : "未配置大模型，按规则解析");
  chips.push(`<span class="chip" title="${esc(llmTip)}">${icon("sparkles", "sm")}${llm.available ? "AI 解析" : "规则解析"}</span>`);
  chips.push(`<a class="toplink" href="${REPO}" target="_blank" rel="noopener">${icon("github", "sm")}源码</a>`);
  const dark = currentTheme() === "dark";
  $("#themeBox").innerHTML = `<span class="theme-seg" role="group" aria-label="显示模式"><button class="${dark ? "" : "on"}" data-act="theme" data-theme="light">${icon("sun", "sm")}日间</button><button class="${dark ? "on" : ""}" data-act="theme" data-theme="dark">${icon("moon", "sm")}夜间</button></span>`;
  el.innerHTML = chips.join("");
  $("#footVersion").textContent = ` 版本 ${m.version}。`;
  const b = $("#banner");
  if (snap?.mode === "demo") {
    b.innerHTML = `<div class="bar demo">${icon("alert")}<b>演示数据</b>股票名带“演示”，数字不是真实公司。<button class="btn tiny" data-act="go-live">切回真实数据</button></div>`;
  } else if (!m.data_ready) {
    const bs = m.build; const prog = bs && bs.total ? `（${bs.step || ""} ${bs.done || 0}/${bs.total}）` : bs?.step ? `（${bs.step}）` : "";
    b.innerHTML = `<div class="bar down">${icon("database")}<b>真实数据还在准备</b>${bs?.status === "running" ? "正在从扶摇取数" + esc(prog) + "，" : bs?.status === "failed" ? "上次取数失败：" + esc(bs.error || "") + "。" : ""}准备好之前不给任何结论。<button class="btn tiny" data-act="go-demo">先用演示数据看看</button></div>`;
  } else b.innerHTML = "";
}

/* ── 第 1 步：输入 ──────────────────────────────────────── */
function renderInput() {
  $("#stepInput").innerHTML = `
    <span class="hero-kicker">${icon("funnel", "sm")}一句话选股</span>
    <h1>不只给名单，<em>也讲清为什么</em></h1>
    <p class="lead">说一句模糊的想法，先确认每个词的意思，再用同花顺真实数据逐条筛选。</p>
    <div class="ask">
      ${icon("search", "lg")}
      <textarea id="q" maxlength="300" rows="1" placeholder="例如：经营改善、估值合理、走势相对稳定">${esc(S.query)}</textarea>
      <button class="btn primary big" data-act="parse" id="parseBtn">${icon("sparkles")}解析</button>
    </div>
    <div class="examples"><span class="muted small">试试：</span>${EXAMPLES.map((e, i) => `<button class="ex ${i === EXAMPLES.length - 1 ? "warn" : ""}" data-act="example" data-q="${esc(e)}">${i === EXAMPLES.length - 1 ? icon("shield", "sm") : ""}${esc(e)}</button>`).join("")}</div>
    <div class="principle">
      <div><span class="pi a">${icon("sparkles", "lg")}</span><div><b>AI 只翻译</b><span>把你的话变成条件，不碰数字</span></div></div>
      <div><span class="pi b">${icon("target", "lg")}</span><div><b>程序出结果</b><span>同样的条件，永远同样的结果</span></div></div>
      <div><span class="pi c">${icon("database", "lg")}</span><div><b>数字有出处</b><span>每个数都能查到来源和时点</span></div></div>
    </div>`;
}

async function doParse(q) {
  q = (q ?? $("#q").value).trim();
  if (!q) { toast("先写一句选股想法"); return; }
  S.query = q; const btn = $("#parseBtn"); btn.disabled = true; btn.innerHTML = `<span class="spin"></span> 解析中`;
  try {
    const r = await api("/api/parse", { query: q });
    S.parsed = r.parse; S.segments = r.parse.segments; S.choices = {};
    S.strategy = r.strategy; S.assumptions = r.assumptions; S.notes = r.notes; S.result = null; S.selected.clear();
    renderUnderstand(r); renderConditions(); $("#stepResults").classList.add("hidden");
    $("#stepUnderstand").scrollIntoView({ behavior: "smooth", block: "start" });
    runCheck();
  } catch (e) { handleErr(e); } finally { btn.disabled = false; btn.innerHTML = `${icon("sparkles")}解析`; }
}

function handleErr(e) {
  if (e.status === 503) { toast("真实数据还没准备好，可先用演示数据体验"); loadMeta(); return; }
  toast(e.message || "出错了");
}

/* ── 第 2 步：确认理解 ──────────────────────────────────── */
function renderUnderstand(r) {
  const p = S.parsed; const el = $("#stepUnderstand"); el.classList.remove("hidden");
  const modeBadge = p.mode === "llm" ? `<span class="badge ai">${icon("sparkles", "sm")}AI 解析 · ${esc(p.model || "")}${p.latency_ms ? " · " + (p.latency_ms / 1000).toFixed(1) + "s" : ""}</span>`
    : `<span class="badge rule">规则解析</span><span class="muted small">${esc(p.fallback_reason || "")}</span>`;
  const refused = S.segments.filter((s) => s.kind === "refused");
  const blocked = p.blocked || [];
  const paraphrase = (r?.assumptions || S.assumptions).filter((a) => a.startsWith("AI 复述"));
  const pending = S.assumptions.filter((a) => a.startsWith("待确认"));
  el.innerHTML = `
    <div class="step-h"><span class="step-n">2</span><h2>确认理解</h2><span class="sub">每个词选一种口径，默认已选推荐</span><span class="right">${modeBadge}</span></div>
    ${paraphrase.map((a) => `<div class="paraphrase">${icon("message")}<div>${esc(a)}${pending.length ? `<div class="small muted" style="margin-top:4px">${pending.map(esc).join("　")}</div>` : ""}</div></div>`).join("")}
    ${refused.length ? notice("refused", "shield", "已拒绝", `${refused.map((s) => `“${esc(s.text)}”（${esc(s.reason)}）`).join("；")}。${esc(r?.refuse_hint || "")}`) : ""}
    ${r?.only_refused ? notice("unsup", "info", "", "没有能转成数据条件的部分。试试“股息率 3% 以上”“近 60 日涨幅”这类说法。") : ""}
    <div class="segs">${S.segments.map((s, i) => segCard(s, i)).join("") || `<div class="empty">没有识别出可执行的片段</div>`}</div>
    ${blocked.length ? `<details class="fold"><summary>${icon("shield", "sm")}拦下 AI 的 ${blocked.length} 条越界输出</summary><ul class="assume">${blocked.map((b) => `<li><code>${esc(JSON.stringify(b.segment).slice(0, 160))}</code> → ${esc(b.reason)}</li>`).join("")}</ul></details>` : ""}
    ${p.rules_shadow ? `<details class="fold"><summary>对照：规则解析结果</summary><ul class="assume">${p.rules_shadow.map((s) => `<li>${esc(s.text)} → ${esc(segLabel(s))}</li>`).join("") || "<li>规则没有识别出片段</li>"}</ul></details>` : ""}
    <details class="fold" open><summary>默认假设</summary><ul class="assume">${S.assumptions.filter((a) => !a.startsWith("AI 复述") && !a.startsWith("待确认")).map((a) => `<li>${esc(a)}</li>`).join("")}</ul></details>`;
}

function segLabel(s) {
  if (s.kind === "concept") { const c = concept(s.concept_id); const o = c?.options.find((x) => x.id === s.option_id); return `${c?.label || s.concept_id}：${o?.label || ""}`; }
  if (s.kind === "explicit") return condText({ metric: s.metric, op: s.op, value: s.value, basis: s.basis || "absolute" });
  if (s.kind === "universe") return "股票池：" + (S.meta.catalog.universes.find((u) => u.id === s.universe_id)?.label || s.universe_id);
  if (s.kind === "industry") return "行业：" + (s.industry || "");
  if (s.kind === "exclude") return { st: "剔除 ST", new: "剔除次新股", bj: "剔除北交所" }[s.metric] || s.metric;
  if (s.kind === "refused") return "拒绝：" + s.reason;
  return "未识别：" + (s.reason || "");
}

function segCard(s, i) {
  const by = s.by === "llm" ? `<span class="badge ai">AI</span>` : `<span class="badge rule">规则</span>`;
  const skip = S.choices[i] === SKIP;
  if (s.kind === "refused") return "";
  if (s.kind === "unsupported") return `<div class="seg"><div class="seg-head"><span class="quote">${esc(s.text)}</span><span class="arrow">${icon("arrow", "sm")}</span><span class="badge miss">没有对应数据</span>${by}</div><div class="small muted">${esc(s.reason)}</div></div>`;
  const head = `<div class="seg-head"><span class="quote">${esc(s.text)}</span><span class="arrow">${icon("arrow", "sm")}</span><b>${esc(segLabel(s))}</b>${by}<span style="flex:1"></span><label class="small muted"><input type="checkbox" data-act="skip" data-i="${i}" ${skip ? "checked" : ""}> 不用</label></div>`;
  if (s.kind !== "concept") return `<div class="seg ${skip ? "skip" : ""}">${head}${s.reason ? `<div class="small muted">${esc(s.reason)}</div>` : ""}</div>`;
  const c = concept(s.concept_id); const chosen = S.choices[i] && S.choices[i] !== SKIP ? S.choices[i] : s.option_id;
  const opts = c.options.map((o) => `
    <label class="opt ${o.id === chosen ? "on" : ""}">
      <input type="radio" name="seg${i}" data-act="choose" data-i="${i}" value="${esc(o.id)}" ${o.id === chosen ? "checked" : ""} ${skip ? "disabled" : ""}>
      <div><div class="t">${esc(o.label)}${o.recommended ? '<span class="badge brand">推荐</span>' : ""}</div><div class="d">${esc(o.summary)}</div>
      <div class="c">${o.conds.map((x) => esc(condText(x))).join("<br>")}</div>${o.note ? `<div class="d">${esc(o.note)}</div>` : ""}</div>
    </label>`).join("");
  const unavail = (c.unavailable || []).map((u) => `<div class="opt off" title="数据做不到">${icon("minus")}<div><div class="t">${esc(u.label)}<span class="badge rule">做不到</span></div><div class="d">${esc(u.reason)}</div></div></div>`).join("");
  return `<div class="seg ${skip ? "skip" : ""}">${head}<div class="seg-q">${esc(c.question)}${s.reason && s.by === "llm" ? ` <span class="muted">（AI：${esc(s.reason)}）</span>` : ""}</div><div class="opts">${opts}${unavail}</div></div>`;
}

async function recompile() {
  try {
    const r = await api("/api/compile", { query: S.query, segments: S.segments, choices: S.choices });
    S.strategy = r.strategy; S.assumptions = [...S.assumptions.filter((a) => a.startsWith("AI 复述") || a.startsWith("待确认")), ...r.assumptions]; S.notes = r.notes;
    renderUnderstand(); renderConditions(); runCheck(); markStale();
  } catch (e) { handleErr(e); }
}

/* ── 第 3 步：检查条件 ──────────────────────────────────── */
function renderConditions() {
  const el = $("#stepConditions"); el.classList.remove("hidden");
  const s = S.strategy; const cat = S.meta.catalog; const inds = S.meta.industries || [];
  const indName = (code) => (S.meta.industryCodes || {})[code] || code;
  const metricsByCat = {};
  cat.metrics.forEach((m) => { (metricsByCat[m.category] ||= []).push(m); });
  const metricOptions = (sel) => Object.entries(metricsByCat).map(([k, ms]) => `<optgroup label="${esc(k)}">${ms.map((m) => `<option value="${m.id}" ${m.id === sel ? "selected" : ""}>${esc(m.name)}</option>`).join("")}</optgroup>`).join("");
  const cov = S.meta.coverage || {};
  const rows = s.conditions.map((c, i) => {
    const m = metric(c.metric); const rel = c.basis !== "absolute";
    const valInput = c.op === "between"
      ? `<input type="number" step="any" data-act="val0" data-i="${i}" value="${c.value[0]}"> ～ <input type="number" step="any" data-act="val1" data-i="${i}" value="${c.value[1]}">`
      : `<input type="number" step="any" data-act="val" data-i="${i}" value="${c.value}"> <span class="muted">${rel ? "倍" : esc(m.unit)}</span>`;
    const issue = (S.check?.issues || []).find((x) => x.condition_id === c.id);
    const cv = cov[c.metric];
    return `<tr>
      <td><div class="grp ${c.origin === "explicit" ? "explicit" : c.origin === "user" ? "user" : ""}">${esc(c.label || "条件")}</div>${c.source_text ? `<div class="orig">来自“${esc(c.source_text)}”</div>` : ""}</td>
      <td><select data-act="metric" data-i="${i}">${metricOptions(c.metric)}</select></td>
      <td><select data-act="op" data-i="${i}">${cat.ops.map((o) => `<option value="${o}" ${o === c.op ? "selected" : ""} ${rel && o === "between" ? "disabled" : ""}>${opText[o]}</option>`).join("")}</select></td>
      <td>${valInput}</td>
      <td><select data-act="basis" data-i="${i}">${Object.entries(cat.bases).map(([k, v]) => `<option value="${k}" ${k === c.basis ? "selected" : ""} ${k !== "absolute" && (!m.relative_ok || c.op === "between") ? "disabled" : ""}>${esc(v)}</option>`).join("")}</select></td>
      <td class="cov num">${cv !== undefined ? `<span class="cov-bar ${cv < 0.8 ? "low" : ""}"><i style="width:${Math.round(cv * 100)}%"></i></span>${Math.round(cv * 100)}%` : "—"}</td>
      <td><button class="btn ghost tiny" data-act="del" data-i="${i}" title="删除这条">${icon("trash", "sm")}</button></td>
    </tr>${issue ? `<tr><td colspan="7">${notice("error", "alert", "", esc(issue.message))}</td></tr>` : ""}`;
  }).join("");
  const u = s.universe;
  el.innerHTML = `
    <div class="step-h"><span class="step-n">3</span><h2>检查条件</h2><span class="sub">程序只执行这张表，可以直接改</span></div>
    <div class="uni">
      <span class="uni-title">${icon("layers")}股票池</span>
      <label><select data-act="u-base">${cat.universes.map((x) => `<option value="${x.id}" ${x.id === u.base ? "selected" : ""}>${esc(x.label)}</option>`).join("")}</select></label>
      <span>行业 ${u.industries.map((code) => `<span class="ind-chip">${esc(indName(code))}<button data-act="u-ind-del" data-code="${esc(code)}" title="移除">${icon("x", "sm")}</button></span>`).join(" ")}
        <select data-act="u-ind-add"><option value="">＋ 加行业</option>${inds.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("")}</select></span>
      <label><input type="checkbox" data-act="u-st" ${u.exclude_st ? "checked" : ""}> 剔除 ST</label>
      <label>上市满 <select data-act="u-days">${[0, 180, 365, 730].map((d) => `<option value="${d}" ${d === u.min_listed_days ? "selected" : ""}>${d} 天</option>`).join("")}</select></label>
      <label><input type="checkbox" data-act="u-bj" ${u.exclude_bj ? "checked" : ""}> 剔除北交所</label>
    </div>
    <div class="ctable-wrap"><table class="ctable"><thead><tr><th>来自</th><th>指标</th><th>比较</th><th>阈值</th><th>阈值基准</th><th title="全市场有数据的比例">有数据</th><th></th></tr></thead><tbody>${rows || `<tr><td colspan="7" class="empty">还没有条件</td></tr>`}</tbody></table></div>
    <div id="checkBox">${checkHtml()}</div>
    <div class="actions">
      <button class="btn" data-act="add">${icon("sliders")}添加条件</button>
      <span class="muted small">${icon("info", "sm")} 同样的条件和数据，结果永远一样</span>
      <span class="spacer"></span>
      <button class="btn primary big" data-act="screen" id="screenBtn">${icon("funnel")}运行筛选</button>
    </div>`;
}

function checkHtml() {
  const c = S.check; const parts = [];
  (S.notes || []).forEach((n) => parts.push(notice(n.severity, "alert", "提示", esc(n.message))));
  if (!c) return parts.join("");
  c.issues.filter((i) => !i.condition_id).forEach((i) => parts.push(notice("error", "alert", "", esc(i.message))));
  c.conflicts.forEach((x) => { const [t, ic] = SEV[x.severity] || ["提示", "info"]; parts.push(notice(x.severity, ic, t, esc(x.message))); });
  return parts.join("");
}

let checkTimer = null;
function runCheck() {
  clearTimeout(checkTimer);
  checkTimer = setTimeout(async () => {
    try { S.check = await api("/api/check", { strategy: S.strategy }); } catch (e) { S.check = null; }
    const box = $("#checkBox"); if (box) box.innerHTML = checkHtml();
    if ((S.check?.issues || []).some((i) => i.condition_id)) renderConditions();
  }, 250);
}

function markStale() {
  if (!S.result) return;
  const box = $("#staleBox");
  if (box) box.innerHTML = JSON.stringify(S.strategy) !== S.resultKey
    ? `<div class="notice stale"><span>${icon("refresh")} <b>结果已过期</b>条件改过了，请重新筛选。</span><button class="btn tiny primary" data-act="screen">重新筛选</button></div>` : "";
}

function editCond(i, patch) {
  const c = S.strategy.conditions[i]; Object.assign(c, patch);
  const m = metric(c.metric);
  if (c.op === "between" && !Array.isArray(c.value)) c.value = [Number(c.value) || 0, (Number(c.value) || 0) + 10];
  if (c.op !== "between" && Array.isArray(c.value)) c.value = c.value[1];
  if (c.basis !== "absolute" && (!m.relative_ok || c.op === "between")) c.basis = "absolute";
  c.origin = c.origin === "concept" ? "concept" : "user";
  renderConditions(); runCheck(); markStale();
}

/* ── 第 4 步：结果 ──────────────────────────────────────── */
async function doScreen() {
  const btn = $("#screenBtn"); if (btn) { btn.disabled = true; btn.innerHTML = `<span class="spin"></span> 筛选中`; }
  try {
    const r = await api("/api/screen", { strategy: S.strategy });
    S.result = r; S.resultKey = JSON.stringify(S.strategy); S.tab = r.counts.passed ? "passed" : r.counts.near_miss ? "near_miss" : "excluded";
    S.sort = null; S.selected.clear(); S.showN = 150; S.filter = "";
    renderResults(); $("#stepResults").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    if (e.status === 422) { S.check = { issues: e.detail.issues || [], conflicts: [] }; renderConditions(); toast("条件有错误，先修正再运行"); }
    else handleErr(e);
  } finally { if (btn) { btn.disabled = false; btn.innerHTML = `${icon("funnel")}运行筛选`; } }
}

function kpi(cls, label, ic, v, sub, tab) {
  return `<div class="kpi ${cls} ${tab ? "click" : ""} ${tab && S.tab === tab ? "on" : ""}" ${tab ? `data-act="tab" data-tab="${tab}"` : ""}>
    <div class="kpi-l">${icon(ic, "sm")}${esc(label)}</div><div class="kpi-v">${fmtN(v, 0)}</div><div class="kpi-s">${esc(sub)}</div></div>`;
}

function renderResults() {
  const r = S.result; const el = $("#stepResults"); el.classList.remove("hidden");
  const src = r.data_sources || {}; const n = Math.max(r.universe_count, 1); const c = r.counts;
  const pct = (x) => `占 ${((x / n) * 100).toFixed(1)}%`;
  const funnelRows = [{ label: "股票池", count: r.universe_count }, ...r.funnel];
  const funnel = funnelRows.map((f, k) => `
    <div class="funnel-row ${k === 0 ? "first" : k === funnelRows.length - 1 ? "last" : ""}"><div class="funnel-bar"><i style="width:${(f.count / n) * 100}%"></i><span>${k ? `<span class="mono">${k}</span>　` : ""}${esc(f.label)}</span></div><div class="num">${fmtN(f.count, 0)}</div></div>`).join("");
  const sens = r.impact.map((imp, i) => sensHtml(imp, r.conditions[i], i)).join("");
  const relax = (r.relax || []).map((x) => `<li>“${esc(x.label)}”去掉会多出 ${x.removed_adds} 只${x.nearest_relax ? `；放到 ${x.sweep_kind === "multiplier" ? x.nearest_relax.x + " 倍中位数" : fmtN(x.nearest_relax.x)} 时可筛出 ${x.nearest_relax.count} 只` : ""}</li>`).join("");
  el.innerHTML = `
    <div class="step-h"><span class="step-n">4</span><h2>结果与理由</h2><span class="sub">缺数据的单独列出，不算通过</span></div>
    <div id="staleBox"></div>
    <div class="kpis">
      ${kpi("", "股票池", "layers", r.universe_count, `${r.conditions.length} 个条件`, null)}
      ${kpi("pass", "入选", "check", c.passed, pct(c.passed), "passed")}
      ${kpi("near", "差一点", "target", c.near_miss, "差一个条件", "near_miss")}
      ${kpi("fail", "被排除", "x", c.excluded, pct(c.excluded), "excluded")}
      ${kpi("miss", "数据缺失", "minus", c.missing, "不算通过", "missing")}
    </div>
    <p class="summary">${esc(r.summary.text)}</p>
    <div class="meta-line">
      <span>运行编号 <code>${esc(r.run_id)}</code></span><span>${icon("database", "sm")}快照 <code>${esc(r.snapshot_id)}</code>${r.data_mode === "demo" ? ' <span class="badge miss">演示数据</span>' : ""}</span>
      ${src.prices?.last_date ? `<span>${icon("clock", "sm")}行情截至 <code>${esc(src.prices.last_date)}</code></span>` : ""}${src.valuation?.as_of ? `<span>${icon("pie", "sm")}估值快照 <code>${esc(src.valuation.as_of)}</code></span>` : ""}
      <span>${icon("book", "sm")}财务 2026 中报</span><span>耗时 <code>${r.elapsed_ms} ms</code></span>
    </div>
    ${(r.data_notes || []).map((x) => notice("coverage", "info", "", esc(x))).join("")}
    ${r.conflicts.filter((x) => x.severity !== "coverage").map((x) => { const [t, ic] = SEV[x.severity] || ["提示", "info"]; return notice(x.severity, ic, t, esc(x.message)); }).join("")}
    ${relax ? notice("tension", "sliders", "结果偏少", `<ul class="assume">${relax}</ul>`) : ""}
    <div class="unisteps">${r.universe_steps.map((u, i) => `${i ? icon("chevron", "sm") : ""}<span class="st">${esc(u.step)}<span class="n">${fmtN(u.count, 0)}</span></span>`).join("")}</div>
    <div class="grid2">
      <div class="panel"><h3><span class="t">${icon("funnel")}逐条漏斗</span><span class="sub">每加一个条件还剩几只</span></h3>${funnel}</div>
      <div class="panel"><h3><span class="t">${icon("sliders")}条件变化的影响</span><span class="sub">点曲线可直接改阈值</span></h3><div class="sens">${sens}</div></div>
    </div>
    <div class="tabs">${Object.entries(CAT).map(([k, v]) => `<button class="tab ${S.tab === k ? "on" : ""}" data-act="tab" data-tab="${k}">${v}<span class="tab-n">${fmtN(r.counts[k], 0)}</span></button>`).join("")}</div>
    <div class="toolbar">
      <label class="search">${icon("search")}<input type="search" id="lookup" placeholder="查任一只股票：代码或简称"></label>
      <button class="btn" data-act="lookup">查理由</button>
      <span class="spacer"></span>
      <button class="btn" data-act="compare" ${S.selected.size >= 2 ? "" : "disabled"}>${icon("columns")}对比（${S.selected.size}）</button>
      <button class="btn" data-act="save">${icon("bookmark")}保存</button>
      <button class="btn" data-act="save-monitor">${icon("bell")}监控</button>
      <button class="btn" data-act="csv">${icon("download")}导出</button>
      <button class="btn" data-act="explain">${icon("message")}AI 解读</button>
    </div>
    <div id="aiBox"></div>
    <p class="tab-help">${icon("info", "sm")}<span>${esc(CAT_HELP[S.tab])}</span></p>
    <div id="tableBox">${tableHtml()}</div>`;
}

function sensHtml(imp, c, i) {
  const sw = imp.sweep; const ex = imp.added_examples.map((x) => esc(x.name)).join("、");
  let svg = `<div class="muted small">该条件没有可扫描的阈值</div>`;
  if (sw && sw.points.length > 1) {
    const W = 320, H = 66, P = 6; const xs = sw.points.map((p) => p.x), ys = sw.points.map((p) => p.count);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y1 = Math.max(...ys, 1);
    const X = (x) => P + ((x - x0) / (x1 - x0 || 1)) * (W - 2 * P), Y = (y) => H - 14 - (y / y1) * (H - 22);
    const d = sw.points.map((p, k) => `${k ? "L" : "M"}${X(p.x).toFixed(1)},${Y(p.count).toFixed(1)}`).join("");
    const cx = X(Math.min(Math.max(sw.current, x0), x1));
    svg = `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
      <path class="a" d="${d}L${X(x1)},${H - 14}L${X(x0)},${H - 14}Z"></path><path class="l" d="${d}"></path>
      <line class="cur" x1="${cx}" x2="${cx}" y1="2" y2="${H - 14}"></line>
      ${sw.points.map((p) => `<circle class="hit" cx="${X(p.x)}" cy="${Y(p.count)}" r="5" data-act="sweep" data-i="${i}" data-x="${p.x}" data-n="${p.count}"></circle>`).join("")}
      <text x="${P}" y="${H - 2}">${fmtN(x0)}</text><text x="${W - P}" y="${H - 2}" text-anchor="end">${fmtN(x1)}</text></svg>
      <div class="spark-tip" id="tip${i}">横轴 ${esc(sw.x_label)} · 纵轴 入选数 · 红线 当前值</div>`;
  }
  return `<div class="sens-item"><div class="sens-top"><b>${esc(c.text)}</b>${imp.removed_adds > 0 ? `<span class="add">去掉它 +${imp.removed_adds} 只</span>` : `<span class="zero">去掉它不影响</span>`}</div>
    ${imp.removed_adds > 0 && ex ? `<div class="small muted">会多出：${ex}${imp.removed_adds > imp.added_examples.length ? " 等" : ""}</div>` : ""}${svg}</div>`;
}

function currentRows() {
  const r = S.result; let rows = r.rows.filter((x) => x.k === S.tab);
  if (S.sort) {
    const { col, dir } = S.sort;
    const key = col === "name" ? (x) => x.c : col === "ind" ? (x) => x.i || "" : col === "gap" ? (x) => x.g ?? -1 : (x) => x.v[col] ?? -Infinity;
    rows = [...rows].sort((a, b) => (key(a) > key(b) ? 1 : key(a) < key(b) ? -1 : 0) * dir);
  } else if (S.tab === "near_miss") rows = [...rows].sort((a, b) => (a.g ?? 9) - (b.g ?? 9));
  return rows;
}

function cellHtml(v, s, t) {
  if (s === "m") return `<span class="cell v-m"><span class="mk">?</span>缺失</span>`;
  const mk = s === "p" ? `<span class="mk">✓</span>` : `<span class="mk">✕</span>`;
  return `<span class="cell v-${s}" title="阈值 ${fmtN(t)}">${fmtN(v)}${mk}</span>`;
}

function tableHtml() {
  const r = S.result; const rows = currentRows();
  if (!rows.length) return `<div class="empty">${S.tab === "passed" ? "没有股票满足全部条件。看看上面哪个条件最卡人，放宽它试试。" : "这一组没有股票"}</div>`;
  const arrow = (c) => (S.sort?.col === c ? (S.sort.dir > 0 ? " ↑" : " ↓") : "");
  const gapCol = S.tab === "near_miss" || S.tab === "excluded";
  const head = `<tr><th class="l"></th><th class="l" data-act="sort" data-col="name">股票${arrow("name")}</th><th class="l" data-act="sort" data-col="ind">行业${arrow("ind")}</th>
    ${r.conditions.map((c, i) => `<th data-act="sort" data-col="${i}" title="${esc(c.text)}">${esc(c.metric_name)}<br><span class="muted">${esc(opText[c.op])} ${c.basis === "absolute" ? (c.op === "between" ? c.value.join("～") : c.value) : c.basis === "industry_median" ? "行业中位" : "市场中位"}</span>${arrow(i)}</th>`).join("")}
    ${gapCol ? `<th data-act="sort" data-col="gap">最大差距${arrow("gap")}</th>` : ""}</tr>`;
  const body = rows.slice(0, S.showN).map((x) => `<tr data-act="row" data-code="${esc(x.c)}">
    <td class="l" data-act="noop"><input type="checkbox" data-act="pick" data-code="${esc(x.c)}" ${S.selected.has(x.c) ? "checked" : ""}></td>
    <td class="l"><span class="nm">${esc(x.n)}</span><span class="code">${esc(x.c)}</span></td><td class="l">${esc(x.i || "—")}</td>
    ${x.v.map((v, i) => `<td class="num">${cellHtml(v, x.s[i], x.t[i])}</td>`).join("")}
    ${gapCol ? `<td class="num">${x.g == null ? "—" : Math.round(x.g * 100) + "%"}</td>` : ""}</tr>`).join("");
  return `<div class="rtable-wrap"><table class="rtable"><thead>${head}</thead><tbody>${body}</tbody></table></div>
    ${rows.length > S.showN ? `<div class="more"><button class="btn" data-act="more">再显示 ${Math.min(150, rows.length - S.showN)} 只（共 ${rows.length}）</button></div>` : ""}`;
}

/* ── 证据抽屉 ─────────────────────────────────────────── */
async function openEvidence(q) {
  openDrawer(`<div class="muted"><span class="spin"></span> 加载证据…</div>`);
  try {
    const e = await api("/api/evidence", { strategy: S.strategy, query: q });
    const cls = { passed: "pass", near_miss: "near", excluded: "fail", missing: "miss", out_of_universe: "rule" }[e.category];
    const items = e.items.map((it) => `<div class="ev-item ${it.status}">
        <div class="row1"><span class="cond">${esc(it.label)}｜${esc(it.condition)}</span><span class="badge ${{ pass: "pass", fail: "fail", missing: "miss" }[it.status]}">${icon({ pass: "check", fail: "x", missing: "minus" }[it.status], "sm")}${esc(it.status_text)}</span></div>
        <div class="val ${it.status === "fail" ? "v-f" : ""}">${esc(it.value_text)}</div>
        <div class="thr">阈值：${esc(it.threshold_text)}${it.gap_text ? `　<b class="v-f">${esc(it.gap_text)}</b>` : ""}${it.missing_reason ? `　<span class="v-m">${esc(it.missing_reason)}</span>` : ""}</div>
        ${it.trace ? `<div class="trace">${esc(it.trace)}</div>` : ""}
        <div class="src"><span>来源</span><span>${esc(it.source.provider)}</span><span>字段</span><span><code>${esc(it.source.fields)}</code></span>
          <span>口径期</span><span>${esc(it.source.period)}　单位 ${esc(it.source.unit)}</span>
          ${it.source.as_of ? `<span>时点</span><span>${esc(it.source.as_of)}</span>` : ""}${it.source.request_id ? `<span>request_id</span><span><code>${esc(it.source.request_id)}</code></span>` : ""}</div>
      </div>`).join("");
    const list = (a) => (a.length ? `<ul>${a.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<div class="muted">无</div>`);
    openDrawer(`
      <div class="ev-head"><div><h3>${esc(e.name)}<span class="code">${esc(e.thscode)}</span></h3><div class="muted small">${esc(e.industry_l1 && e.industry_l1 !== e.industry ? e.industry_l1 + " / " : "")}${esc(e.industry || "")}${e.data_mode === "demo" ? " · 演示数据" : ""}</div></div>
      <div style="display:flex;gap:6px;align-items:center"><span class="badge ${cls}">${esc(e.category_text)}</span><button class="btn ghost tiny" data-act="close" title="关闭">${icon("x")}</button></div></div>
      <div class="verdict">${icon("target")}<div>${esc(e.verdict)}</div></div>
      ${items}
      ${e.items.length ? `<div class="three"><div class="f"><h4>${icon("database", "sm")}事实</h4>${list(e.facts)}</div><div class="i2"><h4>${icon("funnel", "sm")}推断（按你选的口径）</h4>${list(e.inferences)}</div><div class="u"><h4>${icon("help", "sm")}不确定</h4>${list(e.uncertain)}</div></div>` : ""}
      <p class="muted small" style="margin-top:16px">快照 <code>${esc(e.snapshot_id)}</code> · 不构成投资建议</p>`);
  } catch (err) { openDrawer(`${notice("error", "alert", "", esc(err.message))}<button class="btn" data-act="close">关闭</button>`); }
}
function openDrawer(html) { $("#drawerInner").innerHTML = html; $("#drawer").classList.add("open"); $("#scrim").classList.add("on"); }
function closeDrawer() { $("#drawer").classList.remove("open"); $("#scrim").classList.remove("on"); $("#modal").classList.add("hidden"); }

/* ── 对比、导出、AI 解读、保存、监控 ──────────────────────── */
async function doCompare() {
  try {
    const r = await api("/api/compare", { strategy: S.strategy, thscodes: [...S.selected].slice(0, 5) });
    const head = `<tr><th class="l">指标</th>${r.stocks.map((s) => `<th>${esc(s.name)}<br><span class="muted small">${esc(CAT[s.category] || "不在股票池")}</span></th>`).join("")}</tr>`;
    const body = r.metrics.map((m) => `<tr><td class="l">${esc(m.name)} <span class="muted small">${esc(m.unit)}</span></td>${r.stocks.map((s) => {
      const p = r.percentile[m.id][s.thscode];
      return `<td class="num">${fmtN(s.values[m.id])}${p == null ? "" : `<span class="pct">分位 ${p}%</span><span class="pbar"><i style="width:${p}%"></i></span>`}</td>`;
    }).join("")}</tr>`).join("");
    $("#modalInner").innerHTML = `<div class="ev-head"><h3>${icon("columns", "lg")} 对比 ${r.stocks.length} 只股票</h3><button class="btn ghost" data-act="close">${icon("x")}</button></div><p class="muted small">${esc(r.note)}</p><div class="rtable-wrap"><table class="cmp"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
    $("#modal").classList.remove("hidden");
  } catch (e) { handleErr(e); }
}

function doCsv() {
  const r = S.result; const cols = ["代码", "名称", "行业", "分组", ...r.conditions.map((c) => c.text), "运行编号", "数据快照"];
  const lines = [cols, ...r.rows.map((x) => [x.c, x.n, x.i || "", CAT[x.k], ...x.v.map((v, i) => (x.s[i] === "m" ? "缺失" : v)), r.run_id, r.snapshot_id])];
  const csv = "﻿" + lines.map((l) => l.map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`).join(",")).join("\n");
  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" })); a.download = `所以然_${r.run_id}.csv`; a.click();
}

async function doExplain() {
  const box = $("#aiBox"); box.innerHTML = `<div class="ai-box"><span class="spin"></span> AI 解读中，写完会核对每个数字…</div>`;
  try {
    const r = await api("/api/explain", { strategy: S.strategy });
    if (r.ok) box.innerHTML = `<div class="ai-box"><div class="hd">${icon("sparkles", "sm")}AI 解读 · <span class="mono">${esc(r.model || "")}</span><span class="badge pass">${icon("shield", "sm")}数字已核对</span></div><div style="white-space:pre-wrap;margin-top:8px">${esc(r.text)}</div></div>`;
    else if (r.blocked) box.innerHTML = `<div class="ai-box blocked"><div class="hd">${icon("shield", "sm")}<b>AI 解读被拦下</b>${esc(r.reason)}</div><div style="margin-top:6px">${r.unsupported_numbers?.length ? "找不到出处的数字：" + esc(r.unsupported_numbers.join("、")) + "。" : ""}${r.compliance_hits?.length ? "合规问题：" + esc(r.compliance_hits.join("、")) + "。" : ""}</div><details class="fold"><summary>被拦下的草稿</summary><div style="white-space:pre-wrap">${esc(r.draft)}</div></details><div class="small" style="margin-top:6px">改用模板摘要：${esc(r.fallback)}</div></div>`;
    else box.innerHTML = `<div class="ai-box blocked">AI 解读不可用（${esc(r.reason)}）。模板摘要：${esc(r.fallback)}</div>`;
  } catch (e) { box.innerHTML = ""; handleErr(e); }
}

async function doSave(monitor) {
  const name = prompt("给这个策略起个名字", S.query.slice(0, 30)); if (name === null) return;
  try {
    const s = await api("/api/strategies", { strategy: S.strategy, name });
    if (monitor) { await api("/api/monitors", { strategy_id: s.id }); toast("已开始监控：每次数据更新后自动重跑，记录进出名单"); }
    else toast("已保存");
    renderSaved();
  } catch (e) { handleErr(e); }
}

async function renderSaved() {
  const el = $("#stepSaved");
  let list = [], mons = [];
  try { [list, mons] = await Promise.all([api("/api/strategies"), api("/api/monitors")]); } catch { /* 数据未就绪时忽略 */ }
  const monBy = Object.fromEntries(mons.map((m) => [m.strategy_id, m]));
  el.innerHTML = `<div class="step-h"><span class="step-n">${icon("bookmark", "sm")}</span><h2>我的策略</h2><span class="sub">存在本浏览器，无需登录</span></div>
    ${list.length ? `<div class="saved-list">${list.map((s) => {
      const m = monBy[s.id];
      return `<div class="saved-item"><div class="grow"><div class="nm">${esc(s.name)}${m ? `<span class="badge pass">${icon("bell", "sm")}监控中</span>` : ""}${s.data_mode === "demo" ? `<span class="badge miss">演示</span>` : ""}</div>
        <div class="small muted">${esc(s.created_at.slice(0, 16).replace("T", " "))} · 入选 <span class="mono">${s.counts.passed}</span> 只 · 快照 <span class="mono">${esc(s.snapshot_id)}</span></div>
        ${m ? `<div class="small">${esc(m.last_run?.note || "")}</div>` : ""}</div>
        <button class="btn tiny" data-act="load" data-id="${s.id}">${icon("refresh", "sm")}载入</button>
        ${m ? `<button class="btn tiny" data-act="mon-run" data-id="${m.id}">立即重跑</button><button class="btn tiny" data-act="mon-hist" data-id="${m.id}">历史</button>` : `<button class="btn tiny" data-act="mon-new" data-id="${s.id}">${icon("bell", "sm")}转为监控</button>`}
        <button class="btn ghost tiny" data-act="sdel" data-id="${s.id}" title="删除">${icon("trash", "sm")}</button><div class="runs" id="runs-${m ? m.id : ""}"></div></div>`;
    }).join("")}</div>` : `<div class="muted small">还没有保存的策略。</div>`}`;
}

async function monHistory(id) {
  try {
    const runs = await api(`/api/monitors/${id}/runs`);
    $(`#runs-${id}`).innerHTML = `<ul>${runs.map((r) => `<li><span class="mono">${esc(r.run_at.slice(0, 16).replace("T", " "))}</span>　${esc(r.note)}${r.entered.length ? `　新进入：${r.entered.map((x) => esc(x.name)).join("、")}` : ""}${r.exited.length ? `　移出：${r.exited.map((x) => esc(x.name) + "（" + esc(x.why) + "）").join("；")}` : ""}</li>`).join("")}</ul>`;
  } catch (e) { handleErr(e); }
}

function dataPanel() {
  const m = S.meta; const snap = m.snapshot; const src = snap?.sources || {};
  const covRows = Object.entries(m.coverage || {}).map(([k, v]) => `<tr><td>${esc(metric(k)?.name || k)}</td><td class="num"><span class="cov-bar ${v < 0.8 ? "low" : ""}"><i style="width:${Math.round(v * 100)}%"></i></span>${Math.round(v * 100)}%</td><td class="muted">${esc(metric(k)?.period || "")}</td></tr>`).join("");
  const miss = src.financials?.missing ? Object.entries(src.financials.missing).map(([k, v]) => `${k}：${Object.entries(v).map(([c, n]) => `code ${c} × ${n}`).join("，") || "无"}`).join("；") : "";
  $("#modalInner").innerHTML = `<div class="ev-head"><h3>${icon("database", "lg")} 数据状态</h3><button class="btn ghost" data-act="close">${icon("x")}</button></div>
    ${snap ? `<div class="kv" style="margin-top:12px">
      <div>快照编号</div><div class="mono">${esc(snap.snapshot_id)}（${esc(snap.mode === "demo" ? "演示" : "真实")}）</div>
      <div>构建时间</div><div class="mono">${esc(snap.built_at)}</div>
      <div>股票数</div><div class="mono">${fmtN(m.universe_size, 0)}</div>
      <div>估值快照</div><div>${esc(src.valuation?.as_of || "—")}</div>
      <div>财务</div><div>${esc(src.financials?.endpoint || "")}；报告期 ${esc((src.financials?.reports || []).join("、"))}</div>
      ${miss ? `<div>财务缺失返回</div><div>${esc(miss)}</div>` : ""}
      <div>行情</div><div>截至 ${esc(src.prices?.last_date || "—")}；交易日历最新交易日 ${esc(src.prices?.calendar_last_trading_day || "—")}</div>
      <div>行业分组</div><div>第 ${esc(String(src.industry?.level_chosen ?? src.industry?.level ?? ""))} 级，${esc(String(src.industry?.groups ?? src.industry?.count ?? ""))} 组</div>
      <div>取数统计</div><div class="mono small">${esc(JSON.stringify(snap.fetch_stats || {}))}</div>
    </div>
    ${(snap.notes || []).map((x) => notice("coverage", "info", "", esc(x))).join("")}
    <h4 class="sec">各指标覆盖率（全市场有数据的比例）</h4><table class="covtable"><tbody>${covRows}</tbody></table>` : notice("error", "alert", "", esc(m.load_error || "没有快照"))}
    ${m.build ? `<h4 class="sec">最近一次构建</h4><pre class="small mono" style="white-space:pre-wrap">${esc(JSON.stringify({ status: m.build.status, step: m.build.step, done: m.build.done, total: m.build.total, error: m.build.error, stats: m.build.stats }, null, 1))}</pre>` : ""}`;
  $("#modal").classList.remove("hidden");
}

/* ── 事件 ─────────────────────────────────────────────── */
// 字体样式表先按 print 加载、不阻塞首屏，加载完再启用（不用内联 onload，配合内容安全策略）
for (const l of document.querySelectorAll("link[data-async-css]")) {
  const on = () => { l.media = "all"; };
  if (l.sheet) on(); else l.addEventListener("load", on, { once: true });
}
document.addEventListener("click", async (ev) => {
  const t = ev.target.closest("[data-act]"); if (!t) { if (ev.target.id === "scrim" || ev.target.id === "modal") closeDrawer(); return; }
  const a = t.dataset.act; const i = Number(t.dataset.i);
  if (a === "parse") doParse();
  else if (a === "example") { $("#q").value = t.dataset.q; doParse(t.dataset.q); }
  else if (a === "screen") doScreen();
  else if (a === "add") { S.strategy.conditions.push({ id: "u" + Date.now().toString(36), metric: "pe_ttm", op: "<=", value: 30, basis: "absolute", label: "我加的条件", origin: "user", source_text: "" }); renderConditions(); runCheck(); markStale(); }
  else if (a === "del") { S.strategy.conditions.splice(i, 1); renderConditions(); runCheck(); markStale(); }
  else if (a === "u-ind-del") { S.strategy.universe.industries = S.strategy.universe.industries.filter((c) => c !== t.dataset.code); renderConditions(); runCheck(); markStale(); }
  else if (a === "tab") { S.tab = t.dataset.tab; S.sort = null; S.showN = 150; renderResults(); }
  else if (a === "sort") { const col = t.dataset.col; const key = isNaN(Number(col)) ? col : Number(col); S.sort = { col: key, dir: S.sort?.col === key ? -S.sort.dir : 1 }; $("#tableBox").innerHTML = tableHtml(); }
  else if (a === "more") { S.showN += 150; $("#tableBox").innerHTML = tableHtml(); }
  else if (a === "row") openEvidence(t.dataset.code);
  else if (a === "lookup") { const q = $("#lookup").value.trim(); if (q) openEvidence(q); }
  else if (a === "close") closeDrawer();
  else if (a === "compare") doCompare();
  else if (a === "csv") doCsv();
  else if (a === "explain") doExplain();
  else if (a === "save") doSave(false);
  else if (a === "save-monitor") doSave(true);
  else if (a === "sweep") {
    const c = S.strategy.conditions[i]; const x = Number(t.dataset.x);
    const v = Math.abs(x) >= 10 ? Math.round(x * 10) / 10 : Math.round(x * 100) / 100;
    if (c.basis !== "absolute") c.value = v; else if (c.op === "between") c.value = [c.value[0], v]; else c.value = v;
    renderConditions(); toast(`阈值改为 ${v}，重新筛选中`); doScreen();
  }
  else if (a === "load") { const s = (await api("/api/strategies")).find((x) => x.id === t.dataset.id); if (s) { S.strategy = s.strategy; S.query = s.query; renderInput(); renderConditions(); runCheck(); doScreen(); } }
  else if (a === "sdel") { await api(`/api/strategies/${t.dataset.id}`, null, "DELETE"); renderSaved(); }
  else if (a === "mon-new") { await api("/api/monitors", { strategy_id: t.dataset.id }); toast("已转为监控"); renderSaved(); }
  else if (a === "mon-run") { try { const r = await api(`/api/monitors/${t.dataset.id}/run`, {}); toast(r.note, 4000); renderSaved(); } catch (e) { handleErr(e); } }
  else if (a === "mon-hist") monHistory(t.dataset.id);
  else if (a === "data-panel") dataPanel();
  else if (a === "theme") toggleTheme(t.dataset.theme);
  else if (a === "go-demo") { S.data = "demo"; history.replaceState(null, "", "?data=demo"); await loadMeta(); }
  else if (a === "go-live") { S.data = null; history.replaceState(null, "", location.pathname); await loadMeta(); }
});

document.addEventListener("change", (ev) => {
  const t = ev.target; const a = t.dataset.act; if (!a) return; const i = Number(t.dataset.i);
  if (a === "choose") { S.choices[i] = t.value; recompile(); }
  else if (a === "skip") { if (t.checked) S.choices[i] = SKIP; else delete S.choices[i]; recompile(); }
  else if (a === "metric") editCond(i, { metric: t.value });
  else if (a === "op") editCond(i, { op: t.value });
  else if (a === "basis") editCond(i, { basis: t.value, value: t.value === "absolute" ? (metric(S.strategy.conditions[i].metric).unit === "倍" ? 20 : 0) : 1 });
  else if (a === "val") editCond(i, { value: Number(t.value) });
  else if (a === "val0") editCond(i, { value: [Number(t.value), S.strategy.conditions[i].value[1]] });
  else if (a === "val1") editCond(i, { value: [S.strategy.conditions[i].value[0], Number(t.value)] });
  else if (a === "u-base") { S.strategy.universe.base = t.value; renderConditions(); runCheck(); markStale(); }
  else if (a === "u-ind-add" && t.value) { const code = S.meta.industryByName[t.value]; if (code && !S.strategy.universe.industries.includes(code)) S.strategy.universe.industries.push(code); renderConditions(); runCheck(); markStale(); }
  else if (a === "u-st") { S.strategy.universe.exclude_st = t.checked; runCheck(); markStale(); }
  else if (a === "u-bj") { S.strategy.universe.exclude_bj = t.checked; runCheck(); markStale(); }
  else if (a === "u-days") { S.strategy.universe.min_listed_days = Number(t.value); runCheck(); markStale(); }
  else if (a === "pick") { if (t.checked) S.selected.add(t.dataset.code); else S.selected.delete(t.dataset.code); const b = document.querySelector('[data-act="compare"]'); if (b) { b.disabled = S.selected.size < 2; b.innerHTML = `${icon("columns")}对比（${S.selected.size}）`; } }
});

document.addEventListener("mouseover", (ev) => {
  const t = ev.target.closest('[data-act="sweep"]'); if (!t) return;
  const tip = $("#tip" + t.dataset.i); if (tip) tip.textContent = `阈值 ${fmtN(Number(t.dataset.x))} → 入选 ${t.dataset.n} 只（点一下改成这个阈值）`;
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") closeDrawer();
  if (ev.key === "Enter" && ev.target.id === "q" && !ev.shiftKey) { ev.preventDefault(); doParse(); }
  if (ev.key === "Enter" && ev.target.id === "lookup") { const q = ev.target.value.trim(); if (q) openEvidence(q); }
});
$("#scrim").addEventListener("click", closeDrawer);

async function loadMeta() {
  try {
    S.meta = await api("/api/meta");
    S.meta.industryByName = {}; S.meta.industryCodes = {};
  } catch (e) { toast("服务连接失败"); return; }
  renderTop(); renderInput(); renderSaved();
  if (S.strategy) renderConditions();
  if (S.meta.data_ready) {
    try { const r = await api("/api/industries"); S.meta.industryByName = r.by_name; S.meta.industryCodes = r.by_code; } catch { /* 可选 */ }
  }
  if (!S.meta.data_ready) setTimeout(loadMeta, 15000);
  autoRunFromUrl();
}

/* 链接里带问题：/?q=一句话 自动解析；再加 &run=1 自动筛选。方便分享和演示 */
let autoRan = false;
async function autoRunFromUrl() {
  const p = new URLSearchParams(location.search); const q = p.get("q");
  if (autoRan || !q || !S.meta?.data_ready) return;
  autoRan = true;
  $("#q").value = q;
  await doParse(q);
  if (p.get("run") === "1" && S.strategy) { await doScreen(); $("#stepResults").scrollIntoView({ block: "start" }); }
}
loadMeta();
