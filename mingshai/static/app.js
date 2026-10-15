/* 明筛前端：无构建步骤的原生 JS。所有数字来自后端确定性引擎，前端只负责展示和收集用户的修改。 */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtN = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toLocaleString("zh-CN", { minimumFractionDigits: 0, maximumFractionDigits: d }));
const SKIP = "__skip__";
const EXAMPLES = [
  "经营改善、估值合理、走势相对稳定",
  "高股息、低波动的大盘股",
  "银行股里市净率低于1倍、股息率5%以上的",
  "ROE大于15%、负债率不超过40%、现金流好的公司",
  "高成长但估值又低的小盘股",
  "明天会涨停的股票",
];
const CAT = { passed: "入选", near_miss: "差一点", excluded: "被排除", missing: "数据缺失" };
const CAT_HELP = {
  passed: "满足你设定的全部条件。排序默认按代码，不代表优劣；点表头可按任一条件排序。",
  near_miss: "只差一个条件，且差距不超过阈值的 20%（阈值为 0 时按该指标四分位距的 1/4 计）。适合看看条件是不是定得太死。",
  excluded: "至少有一个条件不满足。红色数字就是卡住它的条件；点一行看完整的排除依据。",
  missing: "已有数据的条件都满足，但至少一个条件缺数据，所以无法判定。缺失不按通过处理。",
};

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

function condText(c) {
  const m = metric(c.metric); if (!m) return c.metric;
  if (c.basis === "industry_median") return `${m.name} ${opText[c.op]} 行业中位数${c.value != 1 ? " × " + c.value : ""}`;
  if (c.basis === "market_median") return `${m.name} ${opText[c.op]} 全市场中位数${c.value != 1 ? " × " + c.value : ""}`;
  if (c.op === "between") return `${m.name} 介于 ${c.value[0]}～${c.value[1]}${m.unit}`;
  return `${m.name} ${opText[c.op]} ${c.value}${m.unit}`;
}

/* ── 顶栏与横幅 ─────────────────────────────────────────── */
function renderTop() {
  const m = S.meta; const el = $("#topStatus");
  if (!m) { el.innerHTML = `<span class="chip"><span class="spin"></span> 连接中</span>`; return; }
  const snap = m.snapshot; const src = snap?.sources || {};
  const llm = m.llm;
  const chips = [];
  if (snap) {
    chips.push(`<span class="chip click" data-act="data-panel"><span class="dot ${snap.mode === "demo" ? "warn" : "ok"}"></span>${snap.mode === "demo" ? "演示数据" : "扶摇快照 " + esc(snap.snapshot_id)}</span>`);
    if (src.prices?.last_date) chips.push(`<span class="chip">行情截至 ${esc(src.prices.last_date)}</span>`);
    if (src.valuation?.as_of) chips.push(`<span class="chip">估值 ${esc(src.valuation.as_of)}</span>`);
    chips.push(`<span class="chip">财务 2026 中报</span>`);
  } else {
    chips.push(`<span class="chip click" data-act="data-panel"><span class="dot bad"></span>真实数据未就绪</span>`);
  }
  chips.push(`<span class="chip"><span class="dot ${llm.available ? "ok" : "warn"}"></span>${llm.available ? "AI 解析：" + esc(llm.model || llm.provider) : "AI 未配置 · 规则解析"}</span>`);
  el.innerHTML = chips.join("");
  $("#footVersion").textContent = ` 版本 ${m.version}。`;
  const b = $("#banner");
  if (snap?.mode === "demo") {
    b.innerHTML = `<div class="bar demo"><b>演示数据</b>当前用的是合成数据（股票名带“演示”），只用来体验流程，数字不代表任何真实公司。<button class="btn tiny" data-act="go-live">切回真实数据</button></div>`;
  } else if (!m.data_ready) {
    const bs = m.build; const prog = bs && bs.total ? `（${bs.step || ""} ${bs.done || 0}/${bs.total}）` : bs?.step ? `（${bs.step}）` : "";
    b.innerHTML = `<div class="bar down"><b>真实数据快照尚未就绪</b>${esc(m.load_error || "")}${bs?.status === "running" ? " 正在从扶摇构建" + esc(prog) : bs?.status === "failed" ? "，上次构建失败：" + esc(bs.error || "") : ""}。在此之前不会给出任何筛选结论。<button class="btn tiny" data-act="go-demo">先用演示数据体验</button></div>`;
  } else b.innerHTML = "";
}

/* ── 第 1 步：输入 ──────────────────────────────────────── */
function renderInput() {
  $("#stepInput").innerHTML = `
    <div class="step-h"><span class="step-n">1</span><h2>一句话说出你的选股想法</h2><span class="sub">模糊一点也没关系，下一步会请你确认每个词的口径</span></div>
    <div class="ask">
      <textarea id="q" maxlength="300" placeholder="例如：经营改善、估值合理、走势相对稳定">${esc(S.query)}</textarea>
      <button class="btn primary big" data-act="parse" id="parseBtn">解析</button>
    </div>
    <div class="examples"><span class="muted small">试试：</span>${EXAMPLES.map((e, i) => `<button class="ex ${i === EXAMPLES.length - 1 ? "warn" : ""}" data-act="example" data-q="${esc(e)}">${esc(e)}</button>`).join("")}</div>
    <div class="principle">
      <div><b>AI 只翻译</b>把你的话映射到口径字典里的条件，越界输出会被守门器拦下</div>
      <div><b>引擎出结果</b>筛选、计数、敏感度都由确定性代码完成，同样输入得到同样结果</div>
      <div><b>数字有出处</b>每个值都能追到扶摇接口、字段、报告期、时点和 request_id</div>
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
  } catch (e) { handleErr(e); } finally { btn.disabled = false; btn.textContent = "解析"; }
}

function handleErr(e) {
  if (e.status === 503) { toast("真实数据还没准备好，可先用演示数据体验"); loadMeta(); return; }
  toast(e.message || "出错了");
}

/* ── 第 2 步：确认理解 ──────────────────────────────────── */
function renderUnderstand(r) {
  const p = S.parsed; const el = $("#stepUnderstand"); el.classList.remove("hidden");
  const modeBadge = p.mode === "llm" ? `<span class="badge ai">AI 解析 · ${esc(p.model || "")}${p.latency_ms ? " · " + (p.latency_ms / 1000).toFixed(1) + "s" : ""}</span>`
    : `<span class="badge rule">规则解析</span><span class="muted small">${esc(p.fallback_reason || "")}</span>`;
  const segHtml = S.segments.map((s, i) => segCard(s, i)).join("");
  const refused = S.segments.filter((s) => s.kind === "refused");
  const blocked = p.blocked || [];
  el.innerHTML = `
    <div class="step-h"><span class="step-n">2</span><h2>确认我理解得对不对</h2><span class="sub">每个模糊词都有几种可执行的口径，默认选了推荐项，可以改</span></div>
    <div class="mode">${modeBadge}</div>
    ${(r?.assumptions || S.assumptions).filter((a) => a.startsWith("AI 复述")).map((a) => `<div class="paraphrase">${esc(a)}</div>`).join("")}
    ${refused.length ? `<div class="notice refused"><b>已拒绝</b>${refused.map((s) => `“${esc(s.text)}”（${esc(s.reason)}）`).join("；")}。${esc(r?.refuse_hint || "")}</div>` : ""}
    ${r?.only_refused ? `<div class="notice unsup">这句话里没有可以转成数据条件的部分。可以换成“近 60 日涨幅”“股息率”“估值低于行业中位数”这类可验证的说法。</div>` : ""}
    <div class="segs">${segHtml || `<div class="empty">没有识别出可执行的片段</div>`}</div>
    ${blocked.length ? `<details class="fold"><summary>守门器拦下了 AI 的 ${blocked.length} 条输出（点开看原因）</summary><ul class="assume">${blocked.map((b) => `<li><code>${esc(JSON.stringify(b.segment).slice(0, 160))}</code> → ${esc(b.reason)}</li>`).join("")}</ul></details>` : ""}
    ${p.rules_shadow ? `<details class="fold"><summary>对照：同一句话的规则解析结果（用于核对 AI）</summary><ul class="assume">${p.rules_shadow.map((s) => `<li>${esc(s.text)} → ${esc(segLabel(s))}</li>`).join("") || "<li>规则没有识别出片段</li>"}</ul></details>` : ""}
    <details class="fold" open><summary>默认假设</summary><ul class="assume">${S.assumptions.filter((a) => !a.startsWith("AI 复述")).map((a) => `<li>${esc(a)}</li>`).join("")}</ul></details>`;
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
  if (s.kind === "unsupported") return `<div class="seg"><div class="seg-head"><span class="quote">${esc(s.text)}</span><span class="arrow">→</span><span class="badge miss">无法转为数据条件</span>${by}</div><div class="small muted">${esc(s.reason)}</div></div>`;
  const head = `<div class="seg-head"><span class="quote">${esc(s.text)}</span><span class="arrow">→</span><b>${esc(segLabel(s))}</b>${by}<span style="flex:1"></span><label class="small muted"><input type="checkbox" data-act="skip" data-i="${i}" ${skip ? "checked" : ""}> 不用这一条</label></div>`;
  if (s.kind !== "concept") return `<div class="seg ${skip ? "skip" : ""}">${head}${s.reason ? `<div class="small muted">${esc(s.reason)}</div>` : ""}</div>`;
  const c = concept(s.concept_id); const chosen = S.choices[i] && S.choices[i] !== SKIP ? S.choices[i] : s.option_id;
  const opts = c.options.map((o) => `
    <label class="opt ${o.id === chosen ? "on" : ""}">
      <input type="radio" name="seg${i}" data-act="choose" data-i="${i}" value="${esc(o.id)}" ${o.id === chosen ? "checked" : ""} ${skip ? "disabled" : ""}>
      <div><div class="t">${esc(o.label)} ${o.recommended ? '<span class="badge brand">推荐</span>' : ""}</div><div class="d">${esc(o.summary)}</div>
      <div class="c">${o.conds.map((x) => esc(condText(x))).join("；")}</div>${o.note ? `<div class="d">${esc(o.note)}</div>` : ""}</div>
    </label>`).join("");
  const unavail = (c.unavailable || []).map((u) => `<div class="opt off"><input type="radio" disabled><div><div class="t">${esc(u.label)}</div><div class="d">做不了：${esc(u.reason)}</div></div></div>`).join("");
  return `<div class="seg ${skip ? "skip" : ""}">${head}<div class="small" style="margin-bottom:6px">${esc(c.question)}${s.reason && s.by === "llm" ? ` <span class="muted">（AI：${esc(s.reason)}）</span>` : ""}</div><div class="opts">${opts}${unavail}</div></div>`;
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
      : `<input type="number" step="any" data-act="val" data-i="${i}" value="${c.value}">${rel ? " 倍" : " " + esc(m.unit)}`;
    const issue = (S.check?.issues || []).find((x) => x.condition_id === c.id);
    return `<tr>
      <td><div class="grp">${esc(c.label || "条件")}</div>${c.source_text ? `<div class="orig">来自“${esc(c.source_text)}”</div>` : ""}</td>
      <td><select data-act="metric" data-i="${i}">${metricOptions(c.metric)}</select></td>
      <td><select data-act="op" data-i="${i}">${cat.ops.map((o) => `<option value="${o}" ${o === c.op ? "selected" : ""} ${rel && o === "between" ? "disabled" : ""}>${opText[o]}</option>`).join("")}</select></td>
      <td>${valInput}</td>
      <td><select data-act="basis" data-i="${i}">${Object.entries(cat.bases).map(([k, v]) => `<option value="${k}" ${k === c.basis ? "selected" : ""} ${k !== "absolute" && (!m.relative_ok || c.op === "between") ? "disabled" : ""}>${esc(v)}</option>`).join("")}</select></td>
      <td class="cov num">${cov[c.metric] !== undefined ? Math.round(cov[c.metric] * 100) + "%" : "—"}</td>
      <td><button class="btn ghost tiny" data-act="del" data-i="${i}" title="删除">✕</button></td>
    </tr>${issue ? `<tr><td colspan="7"><div class="notice error">${esc(issue.message)}</div></td></tr>` : ""}`;
  }).join("");
  const u = s.universe;
  el.innerHTML = `
    <div class="step-h"><span class="step-n">3</span><h2>检查和修改条件</h2><span class="sub">这就是引擎实际执行的全部内容，没有隐藏规则</span></div>
    <div class="uni">
      <label>股票池 <select data-act="u-base">${cat.universes.map((x) => `<option value="${x.id}" ${x.id === u.base ? "selected" : ""}>${esc(x.label)}</option>`).join("")}</select></label>
      <span>行业 ${u.industries.map((code) => `<span class="ind-chip">${esc(indName(code))}<button data-act="u-ind-del" data-code="${esc(code)}">✕</button></span>`).join(" ")}
        <select data-act="u-ind-add"><option value="">＋ 加行业</option>${inds.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("")}</select></span>
      <label><input type="checkbox" data-act="u-st" ${u.exclude_st ? "checked" : ""}> 剔除 ST</label>
      <label>上市满 <select data-act="u-days">${[0, 180, 365, 730].map((d) => `<option value="${d}" ${d === u.min_listed_days ? "selected" : ""}>${d} 天</option>`).join("")}</select></label>
      <label><input type="checkbox" data-act="u-bj" ${u.exclude_bj ? "checked" : ""}> 剔除北交所</label>
    </div>
    <table class="ctable"><thead><tr><th>来自</th><th>指标</th><th>比较</th><th>阈值</th><th>阈值基准</th><th title="全市场有数据的比例">覆盖率</th><th></th></tr></thead><tbody>${rows || `<tr><td colspan="7" class="empty">还没有条件</td></tr>`}</tbody></table>
    <div id="checkBox">${checkHtml()}</div>
    <div class="actions">
      <button class="btn" data-act="add">＋ 添加条件</button>
      <span class="muted small">运行编号由“条件 + 数据快照”决定，同样输入永远得到同样结果</span>
      <span class="spacer"></span>
      <button class="btn primary big" data-act="screen" id="screenBtn">运行筛选</button>
    </div>`;
}

function checkHtml() {
  const c = S.check; const parts = [];
  (S.notes || []).forEach((n) => parts.push(`<div class="notice ${n.severity}"><b>提示</b>${esc(n.message)}</div>`));
  if (!c) return parts.join("");
  c.issues.filter((i) => !i.condition_id).forEach((i) => parts.push(`<div class="notice error">${esc(i.message)}</div>`));
  const label = { conflict: "冲突", applicability: "口径不适用", tension: "张力", coverage: "缺数据较多" };
  c.conflicts.forEach((x) => parts.push(`<div class="notice ${x.severity}"><b>${label[x.severity] || "提示"}</b>${esc(x.message)}</div>`));
  return parts.join("");
}

let checkTimer = null;
function runCheck() {
  clearTimeout(checkTimer);
  checkTimer = setTimeout(async () => {
    try { S.check = await api("/api/check", { strategy: S.strategy }); } catch (e) { S.check = null; }
    const box = $("#checkBox"); if (box) box.innerHTML = checkHtml();
    const errs = (S.check?.issues || []).filter((i) => i.condition_id);
    if (errs.length) renderConditions();
  }, 250);
}

function markStale() {
  if (!S.result) return;
  const key = JSON.stringify(S.strategy);
  const box = $("#staleBox");
  if (box) box.innerHTML = key !== S.resultKey ? `<div class="notice stale"><span><b>结果已过期</b>条件改过了，下面的名单还是旧条件的结果。</span><button class="btn tiny primary" data-act="screen">重新筛选</button></div>` : "";
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
  } finally { if (btn) { btn.disabled = false; btn.textContent = "运行筛选"; } }
}

function renderResults() {
  const r = S.result; const el = $("#stepResults"); el.classList.remove("hidden");
  const src = r.data_sources || {};
  const maxF = Math.max(r.universe_count, 1);
  const funnel = [{ label: "股票池", count: r.universe_count }, ...r.funnel].map((f) => `
    <div class="funnel-row"><div class="funnel-bar"><i style="width:${(f.count / maxF) * 100}%"></i><span>${esc(f.label)}</span></div><div class="num">${fmtN(f.count, 0)}</div></div>`).join("");
  const sens = r.impact.map((imp, i) => sensHtml(imp, r.conditions[i], i)).join("");
  const relax = (r.relax || []).map((x) => `<li>“${esc(x.label)}”去掉会多出 ${x.removed_adds} 只${x.nearest_relax ? `；放到 ${x.sweep_kind === "multiplier" ? x.nearest_relax.x + " 倍中位数" : fmtN(x.nearest_relax.x)} 时可筛出 ${x.nearest_relax.count} 只` : ""}</li>`).join("");
  const label = { conflict: "冲突", applicability: "口径不适用", tension: "张力", coverage: "缺数据较多" };
  el.innerHTML = `
    <div class="step-h"><span class="step-n">4</span><h2>结果与解释</h2><span class="sub">入选、差一点、被排除、数据缺失分开放，缺失不算通过</span></div>
    <div id="staleBox"></div>
    <p class="summary">${esc(r.summary.text)}</p>
    <div class="meta-line">
      <span>运行编号 <code>${esc(r.run_id)}</code></span><span>数据快照 <code>${esc(r.snapshot_id)}</code>${r.data_mode === "demo" ? ' <span class="badge miss">演示数据</span>' : ""}</span>
      ${src.prices?.last_date ? `<span>行情截至 ${esc(src.prices.last_date)}</span>` : ""}${src.valuation?.as_of ? `<span>估值快照 ${esc(src.valuation.as_of)}</span>` : ""}
      <span>财务 2026 中报</span><span>耗时 ${r.elapsed_ms} ms</span>
    </div>
    ${(r.data_notes || []).map((n) => `<div class="notice coverage">${esc(n)}</div>`).join("")}
    ${r.conflicts.filter((x) => x.severity !== "coverage").map((x) => `<div class="notice ${x.severity}"><b>${label[x.severity]}</b>${esc(x.message)}</div>`).join("")}
    ${relax ? `<div class="notice tension"><b>结果偏少</b>最卡人的条件：<ul class="assume">${relax}</ul></div>` : ""}
    <div class="unisteps">${r.universe_steps.map((u, i) => `${i ? "<span class='muted'>→</span>" : ""}<span>${esc(u.step)} <span class="n num">${fmtN(u.count, 0)}</span></span>`).join("")}</div>
    <div class="grid2">
      <div class="panel"><h3>逐条漏斗<span class="sub">按条件顺序依次叠加后还剩多少只</span></h3>${funnel}</div>
      <div class="panel"><h3>条件变化的影响<span class="sub">点曲线上的点，直接把阈值改成那个值</span></h3><div class="sens">${sens}</div></div>
    </div>
    <div class="tabs">${Object.entries(CAT).map(([k, v]) => `<button class="tab ${S.tab === k ? "on" : ""}" data-act="tab" data-tab="${k}">${v}<span class="tab-n">${fmtN(r.counts[k], 0)}</span></button>`).join("")}</div>
    <div class="toolbar">
      <input type="search" id="lookup" placeholder="查某只股票为什么入选或没入选：代码或简称" value="">
      <button class="btn" data-act="lookup">查依据</button>
      <span class="spacer"></span>
      <button class="btn" data-act="compare" ${S.selected.size >= 2 ? "" : "disabled"}>对比所选（${S.selected.size}）</button>
      <button class="btn" data-act="save">保存策略</button>
      <button class="btn" data-act="save-monitor">转为监控</button>
      <button class="btn" data-act="csv">导出 CSV</button>
      <button class="btn" data-act="explain">AI 白话解读</button>
    </div>
    <div id="aiBox"></div>
    <p class="tab-help">${esc(CAT_HELP[S.tab])}</p>
    <div id="tableBox">${tableHtml()}</div>`;
}

function sensHtml(imp, c, i) {
  const sw = imp.sweep; const ex = imp.added_examples.map((x) => esc(x.name)).join("、");
  let svg = `<div class="muted small">该条件没有可扫描的阈值</div>`;
  if (sw && sw.points.length > 1) {
    const W = 300, H = 64, P = 6; const xs = sw.points.map((p) => p.x), ys = sw.points.map((p) => p.count);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y1 = Math.max(...ys, 1);
    const X = (x) => P + ((x - x0) / (x1 - x0 || 1)) * (W - 2 * P), Y = (y) => H - 14 - (y / y1) * (H - 22);
    const d = sw.points.map((p, k) => `${k ? "L" : "M"}${X(p.x).toFixed(1)},${Y(p.count).toFixed(1)}`).join("");
    const cx = X(Math.min(Math.max(sw.current, x0), x1));
    svg = `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" data-i="${i}">
      <path class="a" d="${d}L${X(x1)},${H - 14}L${X(x0)},${H - 14}Z"></path><path class="l" d="${d}"></path>
      <line class="cur" x1="${cx}" x2="${cx}" y1="2" y2="${H - 14}"></line>
      ${sw.points.map((p) => `<circle cx="${X(p.x)}" cy="${Y(p.count)}" r="7" fill-opacity="0" data-act="sweep" data-i="${i}" data-x="${p.x}" data-n="${p.count}"></circle>`).join("")}
      <text x="${P}" y="${H - 2}">${fmtN(x0)}</text><text x="${W - P}" y="${H - 2}" text-anchor="end">${fmtN(x1)}</text></svg>
      <div class="spark-tip" id="tip${i}">横轴：${esc(sw.x_label)}；纵轴：其他条件不变时入选数</div>`;
  }
  return `<div class="sens-item"><div class="sens-top"><b>${esc(c.text)}</b><span>${imp.removed_adds > 0 ? `去掉它 <b class="num">+${imp.removed_adds}</b> 只` : "去掉它不影响结果"}</span></div>
    ${imp.removed_adds > 0 && ex ? `<div class="small muted">会多出：${ex}${imp.removed_adds > imp.added_examples.length ? " 等" : ""}</div>` : ""}${svg}</div>`;
}

function currentRows() {
  const r = S.result; let rows = r.rows.filter((x) => x.k === S.tab);
  if (S.filter) { const f = S.filter.toLowerCase(); rows = rows.filter((x) => x.c.toLowerCase().includes(f) || String(x.n).toLowerCase().includes(f) || String(x.i || "").includes(f)); }
  if (S.sort) {
    const { col, dir } = S.sort;
    const key = col === "name" ? (x) => x.c : col === "ind" ? (x) => x.i || "" : col === "gap" ? (x) => x.g ?? -1 : (x) => x.v[col] ?? -Infinity;
    rows = [...rows].sort((a, b) => (key(a) > key(b) ? 1 : key(a) < key(b) ? -1 : 0) * dir);
  } else if (S.tab === "near_miss") rows = [...rows].sort((a, b) => (a.g ?? 9) - (b.g ?? 9));
  return rows;
}

function tableHtml() {
  const r = S.result; const rows = currentRows();
  if (!rows.length) return `<div class="empty">${S.tab === "passed" ? "没有股票满足全部条件。看看上面“条件变化的影响”，放宽最卡人的那一条。" : "这一组没有股票"}</div>`;
  const arrow = (c) => (S.sort?.col === c ? (S.sort.dir > 0 ? " ↑" : " ↓") : "");
  const head = `<tr><th class="l"></th><th class="l" data-act="sort" data-col="name">股票${arrow("name")}</th><th class="l" data-act="sort" data-col="ind">行业${arrow("ind")}</th>
    ${r.conditions.map((c, i) => `<th data-act="sort" data-col="${i}" title="${esc(c.text)}">${esc(c.metric_name)}<br><span class="muted">${esc(opText[c.op])} ${c.basis === "absolute" ? (c.op === "between" ? c.value.join("～") : c.value) : c.basis === "industry_median" ? "行业中位" : "市场中位"}</span>${arrow(String(i))}</th>`).join("")}
    ${S.tab === "near_miss" || S.tab === "excluded" ? `<th data-act="sort" data-col="gap">最大差距${arrow("gap")}</th>` : ""}</tr>`;
  const body = rows.slice(0, S.showN).map((x) => `<tr data-act="row" data-code="${esc(x.c)}">
    <td class="l" onclick="event.stopPropagation()"><input type="checkbox" data-act="pick" data-code="${esc(x.c)}" ${S.selected.has(x.c) ? "checked" : ""}></td>
    <td class="l">${esc(x.n)}<span class="code">${esc(x.c)}</span></td><td class="l">${esc(x.i || "—")}</td>
    ${x.v.map((v, i) => `<td class="num v-${x.s[i]}" title="${x.s[i] === "m" ? "数据缺失" : "阈值 " + fmtN(x.t[i])}">${x.s[i] === "m" ? "缺失" : fmtN(v)}${x.s[i] === "p" ? " ✓" : x.s[i] === "f" ? " ✗" : ""}</td>`).join("")}
    ${S.tab === "near_miss" || S.tab === "excluded" ? `<td class="num">${x.g == null ? "—" : Math.round(x.g * 100) + "%"}</td>` : ""}</tr>`).join("");
  return `<div class="rtable-wrap"><table class="rtable"><thead>${head}</thead><tbody>${body}</tbody></table></div>
    ${rows.length > S.showN ? `<div class="more"><button class="btn" data-act="more">再显示 ${Math.min(150, rows.length - S.showN)} 只（共 ${rows.length}）</button></div>` : ""}`;
}

/* ── 证据抽屉 ─────────────────────────────────────────── */
async function openEvidence(q) {
  openDrawer(`<div class="muted"><span class="spin"></span> 加载证据…</div>`);
  try {
    const e = await api("/api/evidence", { strategy: S.strategy, query: q });
    const cls = { passed: "pass", near_miss: "near", excluded: "fail", missing: "miss", out_of_universe: "rule" }[e.category];
    const items = e.items.map((it) => `<div class="ev-item">
        <div class="row1"><span class="cond">${esc(it.label)}｜${esc(it.condition)}</span><span class="badge ${{ pass: "pass", fail: "fail", missing: "miss" }[it.status]}">${esc(it.status_text)}</span></div>
        <div class="val ${it.status === "fail" ? "v-f" : ""}">${esc(it.value_text)}</div>
        <div class="thr">阈值：${esc(it.threshold_text)}${it.gap_text ? `　<b class="v-f">${esc(it.gap_text)}</b>` : ""}${it.missing_reason ? `　<span class="v-m">${esc(it.missing_reason)}</span>` : ""}</div>
        ${it.trace ? `<div class="trace">计算：${esc(it.trace)}</div>` : ""}
        <div class="src">来源：${esc(it.source.provider)}<br>字段：<code>${esc(it.source.fields)}</code>　口径期：${esc(it.source.period)}　单位：${esc(it.source.unit)}
        ${it.source.as_of ? `<br>数据时点：${esc(it.source.as_of)}` : ""}${it.source.request_id ? `<br>request_id：<code>${esc(it.source.request_id)}</code>` : ""}</div>
      </div>`).join("");
    const list = (a) => (a.length ? `<ul>${a.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<div class="muted">无</div>`);
    openDrawer(`
      <div class="ev-head"><div><h3>${esc(e.name)} <span class="code muted small">${esc(e.thscode)}</span></h3><div class="muted small">${esc(e.industry_l1 && e.industry_l1 !== e.industry ? e.industry_l1 + " / " : "")}${esc(e.industry || "")}${e.data_mode === "demo" ? " · 演示数据" : ""}</div></div>
      <div><span class="badge ${cls}">${esc(e.category_text)}</span> <button class="btn ghost tiny" data-act="close">关闭 ✕</button></div></div>
      <div class="verdict">${esc(e.verdict)}</div>
      ${items}
      ${e.items.length ? `<div class="three"><div class="f"><h4>事实（数据本身）</h4>${list(e.facts)}</div><div class="i"><h4>推断（按你选的口径得出）</h4>${list(e.inferences)}</div><div class="u"><h4>不确定与局限</h4>${list(e.uncertain)}</div></div>` : ""}
      <p class="muted small" style="margin-top:14px">快照 ${esc(e.snapshot_id)}。以上不构成投资建议。</p>`);
  } catch (err) { openDrawer(`<div class="notice error">${esc(err.message)}</div><button class="btn" data-act="close">关闭</button>`); }
}
function openDrawer(html) { $("#drawerInner").innerHTML = html; $("#drawer").classList.add("open"); $("#scrim").classList.add("on"); }
function closeDrawer() { $("#drawer").classList.remove("open"); $("#scrim").classList.remove("on"); $("#modal").classList.add("hidden"); }

/* ── 对比、导出、AI 解读、保存、监控 ──────────────────────── */
async function doCompare() {
  try {
    const r = await api("/api/compare", { strategy: S.strategy, thscodes: [...S.selected].slice(0, 5) });
    const head = `<tr><th class="l">指标</th>${r.stocks.map((s) => `<th>${esc(s.name)}<br><span class="muted small">${esc(CAT[s.category] || "不在股票池")}</span></th>`).join("")}</tr>`;
    const body = r.metrics.map((m) => `<tr><td class="l">${esc(m.name)}<span class="muted small">（${esc(m.unit)}）</span></td>${r.stocks.map((s) => `<td class="num">${fmtN(s.values[m.id])}<span class="pct">${r.percentile[m.id][s.thscode] == null ? "" : "股票池分位 " + r.percentile[m.id][s.thscode] + "%"}</span></td>`).join("")}</tr>`).join("");
    $("#modalInner").innerHTML = `<div class="ev-head"><h3>对比 ${r.stocks.length} 只股票</h3><button class="btn ghost" data-act="close">关闭 ✕</button></div><p class="muted small">${esc(r.note)}</p><div class="rtable-wrap"><table class="cmp"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
    $("#modal").classList.remove("hidden");
  } catch (e) { handleErr(e); }
}

function doCsv() {
  const r = S.result; const cols = ["代码", "名称", "行业", "分组", ...r.conditions.map((c) => c.text), "结果编号", "数据快照"];
  const lines = [cols, ...r.rows.map((x) => [x.c, x.n, x.i || "", CAT[x.k], ...x.v.map((v, i) => (x.s[i] === "m" ? "缺失" : v)), r.run_id, r.snapshot_id])];
  const csv = "﻿" + lines.map((l) => l.map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`).join(",")).join("\n");
  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" })); a.download = `明筛_${r.run_id}.csv`; a.click();
}

async function doExplain() {
  const box = $("#aiBox"); box.innerHTML = `<div class="ai-box"><span class="spin"></span> AI 正在写白话解读（写完要过数字守门器和合规检查）…</div>`;
  try {
    const r = await api("/api/explain", { strategy: S.strategy });
    if (r.ok) box.innerHTML = `<div class="ai-box"><div class="small muted">AI 解读 · ${esc(r.model || "")} · <span class="badge pass">已通过守门器</span> ${esc(r.checked.numbers)}；${esc(r.checked.compliance)}</div><div style="white-space:pre-wrap;margin-top:6px">${esc(r.text)}</div></div>`;
    else if (r.blocked) box.innerHTML = `<div class="ai-box blocked"><b>AI 解读被拦下</b>：${esc(r.reason)}。${r.unsupported_numbers?.length ? "找不到出处的数字：" + esc(r.unsupported_numbers.join("、")) + "。" : ""}${r.compliance_hits?.length ? "合规问题：" + esc(r.compliance_hits.join("、")) + "。" : ""}<details class="fold"><summary>被拦下的草稿</summary><div style="white-space:pre-wrap">${esc(r.draft)}</div></details><div class="small" style="margin-top:6px">改用模板摘要：${esc(r.fallback)}</div></div>`;
    else box.innerHTML = `<div class="ai-box blocked">AI 解读不可用（${esc(r.reason)}）。模板摘要：${esc(r.fallback)}</div>`;
  } catch (e) { box.innerHTML = ""; handleErr(e); }
}

async function doSave(monitor) {
  const name = prompt("给这个策略起个名字", S.query.slice(0, 30)); if (name === null) return;
  try {
    const s = await api("/api/strategies", { strategy: S.strategy, name });
    if (monitor) { await api("/api/monitors", { strategy_id: s.id }); toast("已保存并转为监控：数据快照更新后自动重跑，记录新进入和移出的股票"); }
    else toast("已保存");
    renderSaved();
  } catch (e) { handleErr(e); }
}

async function renderSaved() {
  const el = $("#stepSaved");
  let list = [], mons = [];
  try { [list, mons] = await Promise.all([api("/api/strategies"), api("/api/monitors")]); } catch { /* 数据未就绪时忽略 */ }
  const monBy = Object.fromEntries(mons.map((m) => [m.strategy_id, m]));
  el.innerHTML = `<div class="step-h"><span class="step-n">5</span><h2>我的策略与监控</h2><span class="sub">保存在本浏览器的匿名编号下，不需要登录，不收集个人信息</span></div>
    ${list.length ? `<div class="saved-list">${list.map((s) => {
      const m = monBy[s.id];
      return `<div class="saved-item"><div class="grow"><b>${esc(s.name)}</b> <span class="muted small">${esc(s.created_at.slice(0, 16).replace("T", " "))} · 入选 ${s.counts.passed} 只 · 快照 ${esc(s.snapshot_id)}${s.data_mode === "demo" ? " · 演示" : ""}</span>
        ${m ? `<div class="small">监控中 · ${esc(m.last_run?.note || "")}</div>` : ""}</div>
        <button class="btn tiny" data-act="load" data-id="${s.id}">载入</button>
        ${m ? `<button class="btn tiny" data-act="mon-run" data-id="${m.id}">立即重跑</button><button class="btn tiny" data-act="mon-hist" data-id="${m.id}">历史</button>` : `<button class="btn tiny" data-act="mon-new" data-id="${s.id}">转为监控</button>`}
        <button class="btn ghost tiny" data-act="sdel" data-id="${s.id}">删除</button><div class="runs" id="runs-${m ? m.id : ""}"></div></div>`;
    }).join("")}</div>` : `<div class="muted small">还没有保存的策略。跑出结果后点“保存策略”或“转为监控”。</div>`}`;
}

async function monHistory(id) {
  try {
    const runs = await api(`/api/monitors/${id}/runs`);
    $(`#runs-${id}`).innerHTML = `<ul>${runs.map((r) => `<li><b>${esc(r.run_at.slice(0, 16).replace("T", " "))}</b> ${esc(r.note)}${r.entered.length ? `　新进入：${r.entered.map((x) => esc(x.name)).join("、")}` : ""}${r.exited.length ? `　移出：${r.exited.map((x) => esc(x.name) + "（" + esc(x.why) + "）").join("；")}` : ""}</li>`).join("")}</ul>`;
  } catch (e) { handleErr(e); }
}

function dataPanel() {
  const m = S.meta; const snap = m.snapshot; const src = snap?.sources || {};
  const covRows = Object.entries(m.coverage || {}).map(([k, v]) => `<tr><td>${esc(metric(k)?.name || k)}</td><td class="num">${Math.round(v * 100)}%</td><td class="muted">${esc(metric(k)?.period || "")}</td></tr>`).join("");
  $("#modalInner").innerHTML = `<div class="ev-head"><h3>数据状态</h3><button class="btn ghost" data-act="close">关闭 ✕</button></div>
    ${snap ? `<div class="kv">
      <div>快照编号</div><div>${esc(snap.snapshot_id)}（${esc(snap.mode === "demo" ? "演示" : "真实")}）</div>
      <div>构建时间</div><div>${esc(snap.built_at)}</div>
      <div>股票数</div><div>${fmtN(m.universe_size, 0)}</div>
      <div>估值快照</div><div>${esc(src.valuation?.as_of || "—")} ${esc(src.valuation?.endpoint || "")}</div>
      <div>财务报告期</div><div>${esc((src.financials?.reports || []).join("、"))}（最新 ${esc(src.financials?.latest || "")}）</div>
      <div>行情</div><div>截至 ${esc(src.prices?.last_date || "—")}；交易日历最新交易日 ${esc(src.prices?.calendar_last_trading_day || "—")}</div>
      <div>行业分组</div><div>${esc(JSON.stringify(src.industry?.level_chosen ?? src.industry?.level ?? ""))} 级，${esc(src.industry?.groups ?? src.industry?.count ?? "")} 组</div>
      <div>取数统计</div><div>${esc(JSON.stringify(snap.fetch_stats || {}))}</div>
    </div>
    ${(snap.notes || []).map((n) => `<div class="notice coverage">${esc(n)}</div>`).join("")}
    <h4>各指标覆盖率（全市场有数据的比例）</h4><table class="covtable"><tbody>${covRows}</tbody></table>` : `<div class="notice error">${esc(m.load_error || "没有快照")}</div>`}
    ${m.build ? `<h4>最近一次构建</h4><pre class="small" style="white-space:pre-wrap">${esc(JSON.stringify({ status: m.build.status, step: m.build.step, done: m.build.done, total: m.build.total, error: m.build.error, stats: m.build.stats }, null, 1))}</pre>` : ""}`;
  $("#modal").classList.remove("hidden");
}

/* ── 事件 ─────────────────────────────────────────────── */
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
  else if (a === "sort") { const col = t.dataset.col; S.sort = { col: isNaN(Number(col)) ? col : Number(col), dir: S.sort?.col == col ? -S.sort.dir : 1 }; $("#tableBox").innerHTML = tableHtml(); }
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
  else if (a === "pick") { if (t.checked) S.selected.add(t.dataset.code); else S.selected.delete(t.dataset.code); const b = document.querySelector('[data-act="compare"]'); if (b) { b.disabled = S.selected.size < 2; b.textContent = `对比所选（${S.selected.size}）`; } }
});

document.addEventListener("mouseover", (ev) => {
  const t = ev.target.closest('[data-act="sweep"]'); if (!t) return;
  const tip = $("#tip" + t.dataset.i); if (tip) tip.textContent = `阈值 ${fmtN(Number(t.dataset.x))} → 入选 ${t.dataset.n} 只（点一下改成这个阈值）`;
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") closeDrawer();
  if (ev.key === "Enter" && ev.target.id === "q" && (ev.metaKey || ev.ctrlKey)) doParse();
  if (ev.key === "Enter" && ev.target.id === "lookup") { const q = ev.target.value.trim(); if (q) openEvidence(q); }
});
$("#scrim").addEventListener("click", closeDrawer);

async function loadMeta() {
  try {
    S.meta = await api("/api/meta");
    S.meta.industryByName = {}; S.meta.industryCodes = {};
  } catch (e) { toast("服务连接失败"); return; }
  renderTop(); renderInput(); renderSaved();
  if (S.strategy) { renderConditions(); }
  if (S.meta.data_ready) {
    // 行业名 ↔ 代码：从一次空策略检查里拿不到，这里用 catalog 外的 industries 列表 + 后端解析时回填
    try { const r = await api("/api/industries"); S.meta.industryByName = r.by_name; S.meta.industryCodes = r.by_code; } catch { /* 可选 */ }
  }
  if (!S.meta.data_ready) setTimeout(loadMeta, 15000);
}
loadMeta();
