"""按旁白节奏操作线上站点并逐帧录制（本机 Chrome 无界面模式 + CDP 截屏流）。

每句旁白开始时：写字幕、记下时间戳、执行这句对应的操作；下一句要等到“本句配音放完”且“操作完成”
两者都满足才开始，所以画面和声音不会错位，接口偶尔慢一点也只是画面多停一会儿。
字幕、鼠标、点击涟漪、片头片尾都是叠在页面上的层，和画面一起录下来，不依赖后期字幕滤镜。

    .venv/bin/python scripts/demo_video/record.py            # 需先跑 tts_qwen.py
输出：build/demo/frames/*.jpg、frames.json（每帧时间戳）、timeline.json（每句开始时间）
"""
from __future__ import annotations

import asyncio
import base64
import json
import shutil
import time
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import Page, async_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "build/demo"
SITE = json.loads((ROOT / "scripts/demo_video/script.json").read_text(encoding="utf-8"))["site"]
QUERY = "业绩越来越好，价格不算贵，股价别太折腾"
REFUSE = "明天会涨停的股票"
GAP = 0.35            # 句间停顿
VIEW = {"width": 1920, "height": 1080}  # 无界面 Chrome 的截屏流按 CSS 像素出帧，所以直接用 1920×1080
ZOOM = 1.2                               # 页面内容放大，叠加层（字幕、鼠标、片头片尾）不放大

OVERLAY_CSS = """
#dv-cursor{position:fixed;left:-40px;top:-40px;width:30px;height:30px;z-index:2147483647;pointer-events:none;
  filter:drop-shadow(0 2px 4px rgba(14,21,48,.35));}
.dv-ripple{position:fixed;width:20px;height:20px;border-radius:50%;border:3px solid #C8102E;pointer-events:none;z-index:2147483646;
  transform:translate(-50%,-50%);animation:dvr .7s ease-out forwards}
@keyframes dvr{from{transform:translate(-50%,-50%) scale(.4);opacity:1}to{transform:translate(-50%,-50%) scale(3.4);opacity:0}}
#dv-subwrap{position:fixed;left:0;right:0;bottom:40px;display:flex;flex-direction:column;align-items:center;gap:8px;z-index:2147483640;pointer-events:none}
#dv-chap{background:#3B4A8A;color:#fff;font:600 18px/1 "PingFang SC",sans-serif;padding:8px 16px;border-radius:999px;opacity:0;transition:opacity .3s}
#dv-sub{max-width:1560px;background:rgba(14,21,48,.88);color:#fff;font:600 30px/1.5 "PingFang SC","Hiragino Sans GB",sans-serif;
  letter-spacing:.6px;padding:12px 26px;border-radius:14px;text-align:center;box-shadow:0 10px 30px rgba(0,0,0,.28);opacity:0;transition:opacity .25s}
.dv-card{position:fixed;inset:0;z-index:2147483645;display:flex;align-items:center;justify-content:center;
  background:radial-gradient(1200px 600px at 20% 10%,#2b3a86 0%,#1E2761 45%,#0E1530 100%);color:#fff;transition:opacity .7s;font-family:"PingFang SC",sans-serif}
.dv-card .in{text-align:center;max-width:1480px}
body > :not(#dv-cursor):not(#dv-subwrap):not(.dv-card):not(.dv-ripple):not(script):not(style){zoom:%ZOOM%}
.dv-card img{width:144px;height:144px;border-radius:32px;box-shadow:0 18px 40px rgba(0,0,0,.35)}
.dv-card h1{font-size:78px;letter-spacing:6px;margin:26px 0 8px;font-weight:700}
.dv-card h2{font-size:34px;font-weight:500;color:#DCE1F1;margin:0 0 30px;letter-spacing:2px}
.dv-card .meta{font-size:23px;color:#B9C2E4;line-height:1.9}
.dv-card .pill{display:inline-block;margin:8px 8px 0;padding:8px 18px;border:1px solid rgba(255,255,255,.28);border-radius:999px;font-size:20px;color:#fff;background:rgba(255,255,255,.06)}
.dv-card .url{font-family:"IBM Plex Mono",Menlo,monospace;font-size:27px;color:#F4C36A;margin-top:6px}
"""

OVERLAY_JS = """
(() => {
  if (window.__dv) return; window.__dv = true;
  const $ = (id) => document.getElementById(id);
  window.dvSub = (t, chap) => { const s = $('dv-sub'), h = $('dv-chap'); if (!s) return;
    s.textContent = t; s.style.opacity = t ? 1 : 0; h.textContent = chap || ''; h.style.opacity = chap ? 1 : 0; };
  window.dvRipple = (x, y) => { const r = document.createElement('div'); r.className = 'dv-ripple'; r.style.left = x + 'px'; r.style.top = y + 'px';
    document.body.appendChild(r); setTimeout(() => r.remove(), 800); };
  window.dvCard = (id, html) => { let d = $(id); if (!d) { d = document.createElement('div'); d.id = id; d.className = 'dv-card'; document.body.appendChild(d); }
    d.innerHTML = html; d.style.opacity = 1; };
  window.dvHide = (id) => { const d = $(id); if (d) { d.style.opacity = 0; setTimeout(() => d.remove(), 800); } };
  const ready = () => {
    const st = document.createElement('style'); st.textContent = %CSS%; document.head.appendChild(st);
    const c = document.createElement('div'); c.id = 'dv-cursor';
    c.innerHTML = '<svg viewBox="0 0 24 24" width="30" height="30"><path d="M4 2.5 19 13l-6.6 1.4L9 21z" fill="#fff" stroke="#0E1530" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    document.body.appendChild(c);
    const w = document.createElement('div'); w.id = 'dv-subwrap'; w.innerHTML = '<div id="dv-chap"></div><div id="dv-sub"></div>';
    document.body.appendChild(w);
    document.addEventListener('mousemove', e => { c.style.left = (e.clientX - 3) + 'px'; c.style.top = (e.clientY - 2) + 'px'; }, true);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready); else ready();
})();
""".replace("%CSS%", json.dumps(OVERLAY_CSS.replace("%ZOOM%", str(ZOOM))))

TITLE = f"""<div class="in"><img src="/static/icon.svg"><h1>所以然</h1><h2>一句话选股 · 不只给名单，也讲清为什么</h2>
<div class="meta">自然语言智能选股与策略解释器</div>
<div style="margin-top:22px"><span class="pill">线上真实数据：同花顺金融数据 API（扶摇）全市场 5,580 只</span>
<span class="pill">意图解析：自有 Claude API（Claude Opus 5.5）</span></div></div>"""
END = f"""<div class="in"><img src="/static/icon.svg"><h1>所以然</h1><h2>AI 只翻译 · 程序出结果 · 每个数字都有出处</h2>
<div class="meta">在线体验</div><div class="url">{SITE.replace('https://', '').rstrip('/')}</div>
<div class="meta" style="margin-top:14px">源码与文档</div><div class="url">github.com/flukier1016/nl-stock-screener</div>
<div style="margin-top:24px"><span class="pill">数据：同花顺金融数据 API（扶摇）</span><span class="pill">意图解析：自有 Claude API</span>
<span class="pill">不预测涨跌 · 不构成投资建议</span></div></div>"""


class Recorder:
    def __init__(self, page: Page, cdp, fdir: Path):
        self.page, self.cdp, self.fdir = page, cdp, fdir
        self.frames: list[tuple[str, float]] = []
        self.n = 0

    async def on_frame(self, params) -> None:
        self.n += 1
        name = f"f{self.n:06d}.jpg"
        (self.fdir / name).write_bytes(base64.b64decode(params["data"]))
        self.frames.append((name, params["metadata"]["timestamp"]))
        try:
            await self.cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
        except Exception:  # noqa: BLE001 —— 结束时可能已断开
            pass


# ── 鼠标与页面操作 ───────────────────────────────────────────────────
async def center(page: Page, selector: str, nth: int = 0) -> tuple[float, float]:
    box = await page.locator(selector).nth(nth).bounding_box()
    if not box:
        raise RuntimeError(f"找不到元素 {selector}")
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


async def move(page: Page, x: float, y: float, steps: int = 30) -> None:
    await page.mouse.move(x, y, steps=steps)


async def click(page: Page, selector: str, nth: int = 0, steps: int = 30) -> None:
    await scroll_into_view(page, selector, nth)
    x, y = await center(page, selector, nth)
    await move(page, x, y, steps)
    await page.wait_for_timeout(180)
    await page.evaluate("([x, y]) => window.dvRipple(x, y)", [x, y])
    await page.mouse.click(x, y)


async def scroll_to(page: Page, selector: str, offset: int = 76, nth: int = 0) -> None:
    await page.evaluate("""([sel, off, nth]) => { const el = document.querySelectorAll(sel)[nth]; if (!el) return;
        window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - off, behavior: 'smooth' }); }""",
                        [selector, offset, nth])
    await page.wait_for_timeout(950)


async def scroll_into_view(page: Page, selector: str, nth: int = 0) -> None:
    inview = await page.evaluate("""([sel, nth]) => { const el = document.querySelectorAll(sel)[nth]; if (!el) return true;
        const r = el.getBoundingClientRect(); return r.top > 70 && r.bottom < window.innerHeight - 150; }""", [selector, nth])
    if not inview:
        await page.evaluate("""([sel, nth]) => { const el = document.querySelectorAll(sel)[nth];
            el.scrollIntoView({ behavior: 'smooth', block: 'center' }); }""", [selector, nth])
        await page.wait_for_timeout(900)


async def type_into(page: Page, selector: str, text: str, delay: int = 95) -> None:
    await click(page, selector)
    await page.keyboard.press("Meta+A")
    await page.keyboard.press("Backspace")
    await page.keyboard.type(text, delay=delay)


async def wait_parsed(page: Page, timeout: float = 40000) -> None:
    await page.wait_for_function("() => { const b = document.querySelector('#parseBtn'); return b && !b.disabled; }",
                                 timeout=timeout)
    await page.wait_for_timeout(300)


# ── 每句旁白对应的操作 ───────────────────────────────────────────────
async def act(page: Page, name: str) -> None:
    if name in ("none", "title_card"):
        return
    if name == "hide_title":
        await page.evaluate("window.dvHide('dv-title')")
        await page.wait_for_timeout(900)
        await move(page, 960, 470, 40)
    elif name == "type_query":
        await type_into(page, "#q", QUERY, delay=110)
    elif name == "click_parse":
        await click(page, "#parseBtn")
        await wait_parsed(page)
        await page.wait_for_timeout(500)
        await scroll_to(page, "#stepUnderstand")
    elif name == "show_clarify":
        await move(page, *(await center(page, ".paraphrase")), steps=35)
        await page.wait_for_timeout(1400)
        await move(page, *(await center(page, ".seg .opt.on", 0)), steps=35)
    elif name == "hover_options":
        for sel, nth in ((".seg .opt", 1), (".seg .opt", 2), (".seg .opt.off", 0)):
            await move(page, *(await center(page, sel, nth)), steps=26)
            await page.wait_for_timeout(700)
        await scroll_to(page, ".seg", nth=2)
    elif name == "show_conditions":
        await scroll_to(page, "#stepConditions")
        await move(page, *(await center(page, ".ctable tbody tr", 0)), steps=30)
    elif name == "edit_threshold":
        idx = await page.evaluate("() => S.strategy.conditions.findIndex(c => c.metric === 'mdd_120d')")
        sel = f'input[data-act="val"][data-i="{idx}"]'
        await scroll_into_view(page, sel)
        x, y = await center(page, sel)
        await move(page, x, y, 30)
        await page.evaluate("([x, y]) => window.dvRipple(x, y)", [x, y])
        await page.mouse.click(x, y, click_count=3)
        await page.keyboard.type("25", delay=180)
        await page.keyboard.press("Tab")
        await page.wait_for_timeout(900)
        if await page.locator("#checkBox .notice").count():
            await move(page, *(await center(page, "#checkBox")), steps=25)
    elif name == "run_screen":
        await click(page, "#screenBtn")
        await page.wait_for_selector("#stepResults:not(.hidden) .kpis", timeout=40000)
        await page.wait_for_timeout(1200)
        await scroll_to(page, "#stepResults")
    elif name == "show_kpis":
        for nth in (1, 2, 4):
            await move(page, *(await center(page, ".kpi", nth)), steps=28)
            await page.wait_for_timeout(650)
    elif name == "show_funnel":
        await scroll_to(page, ".grid2", offset=90)
        for nth in (0, 3, 6):
            await move(page, *(await center(page, ".funnel-row", nth)), steps=24)
            await page.wait_for_timeout(450)
        await move(page, *(await center(page, ".sens-item", 0)), steps=30)
    elif name == "open_evidence":
        await scroll_to(page, ".tabs", offset=90)
        await click(page, 'tr[data-act="row"]', 0)
        await page.wait_for_selector("#drawer.open .ev-item", timeout=20000)
    elif name == "scroll_evidence":
        await move(page, 1580, 520, 30)
        await page.evaluate("""() => new Promise(res => { const d = document.querySelector('#drawer'); const end = d.scrollHeight - d.clientHeight;
            const t0 = performance.now(), dur = 5200; const step = (t) => { const k = Math.min(1, (t - t0) / dur);
            d.scrollTop = end * (k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2); if (k < 1) requestAnimationFrame(step); else res(); };
            requestAnimationFrame(step); })""")
    elif name == "lookup_maotai":
        await click(page, '#drawer [data-act="close"]')
        await page.wait_for_timeout(500)
        await type_into(page, "#lookup", "贵州茅台", delay=140)
        await click(page, '[data-act="lookup"]')
        await page.wait_for_selector("#drawer.open .verdict", timeout=20000)
        await page.wait_for_timeout(400)
    elif name == "hover_verdict":
        await move(page, *(await center(page, "#drawer .verdict")), steps=30)
        await page.wait_for_timeout(1200)
        if await page.locator("#drawer .ev-item.fail").count():
            await move(page, *(await center(page, "#drawer .ev-item.fail", 0)), steps=30)
    elif name == "click_sweep":
        await click(page, '#drawer [data-act="close"]')
        await page.wait_for_timeout(400)
        await scroll_to(page, ".grid2", offset=90)
        # 选“120 日最大回撤”那条曲线上、阈值比当前大一些的点
        target = await page.evaluate("""() => { const items = [...document.querySelectorAll('.sens-item')];
            const k = items.findIndex(it => it.textContent.includes('最大回撤')); if (k < 0) return null;
            const pts = [...items[k].querySelectorAll('circle.hit')].map(c => ({x: +c.dataset.x, n: +c.dataset.n, r: c.getBoundingClientRect()}));
            items[k].scrollIntoView({block: 'center'});
            const cur = S.strategy.conditions.find(c => c.metric === 'mdd_120d').value;
            const p = pts.filter(p => p.x > cur + 2).sort((a, b) => a.x - b.x)[0] || pts[pts.length - 1];
            const r = p.r; return [r.left + r.width / 2, r.top + r.height / 2]; }""")
        if target:
            await page.wait_for_timeout(500)
            target = await page.evaluate("""() => { const items = [...document.querySelectorAll('.sens-item')];
                const k = items.findIndex(it => it.textContent.includes('最大回撤'));
                const cur = S.strategy.conditions.find(c => c.metric === 'mdd_120d').value;
                const pts = [...items[k].querySelectorAll('circle.hit')].map(c => ({x: +c.dataset.x, r: c.getBoundingClientRect()}));
                const p = pts.filter(p => p.x > cur + 2).sort((a, b) => a.x - b.x)[0] || pts[pts.length - 1];
                return [p.r.left + p.r.width / 2, p.r.top + p.r.height / 2]; }""")
            await move(page, target[0], target[1], 34)
            await page.wait_for_timeout(700)
            await page.evaluate("([x, y]) => window.dvRipple(x, y)", target)
            await page.mouse.click(*target)
            await page.wait_for_timeout(2600)
    elif name == "ai_explain":
        await scroll_to(page, ".toolbar", offset=110)
        await click(page, '[data-act="explain"]')
        await page.wait_for_function("() => { const b = document.querySelector('#aiBox'); return b && !b.querySelector('.spin') && b.textContent.trim().length > 0; }",
                                     timeout=60000)
        await page.wait_for_timeout(400)
        await move(page, *(await center(page, "#aiBox")), steps=30)
    elif name == "save_monitor":
        await click(page, '[data-act="save-monitor"]')
        await page.wait_for_timeout(1600)
        await scroll_to(page, "#stepSaved", offset=200)
        await move(page, *(await center(page, "#stepSaved .saved-item", 0)), steps=30)
    elif name == "refuse_demo":
        await page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
        await page.wait_for_timeout(900)
        await type_into(page, "#q", REFUSE, delay=120)
        await click(page, "#parseBtn")
        await wait_parsed(page)
        await page.wait_for_timeout(400)
        await scroll_to(page, "#stepUnderstand")
    elif name == "show_refusal":
        await move(page, *(await center(page, "#stepUnderstand .notice.refused")), steps=30)
    elif name == "end_card":
        await page.evaluate("([id, html]) => window.dvCard(id, html)", ["dv-end", END])
    else:
        raise ValueError(f"未知操作 {name}")


async def main() -> None:
    lines = json.loads((OUT / "tts/lines.json").read_text(encoding="utf-8"))
    fdir = OUT / "frames"
    shutil.rmtree(fdir, ignore_errors=True)
    fdir.mkdir(parents=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True,
                                          args=[f"--window-size={VIEW['width']},{VIEW['height']}"])
        ctx = await browser.new_context(viewport=VIEW, device_scale_factor=1, color_scheme="light", locale="zh-CN")
        await ctx.add_init_script(OVERLAY_JS)
        page = await ctx.new_page()
        page.on("dialog", lambda d: asyncio.ensure_future(d.accept("业绩改善 · 估值合理 · 走势稳定")))
        await page.goto(SITE + "?demo=1", wait_until="networkidle")
        await page.evaluate("() => { try { localStorage.setItem('ms_theme', 'light'); } catch (e) {} }")
        await page.evaluate("([id, html]) => window.dvCard(id, html)", ["dv-title", TITLE])
        await page.wait_for_timeout(600)
        cdp = await ctx.new_cdp_session(page)
        rec = Recorder(page, cdp, fdir)
        cdp.on("Page.screencastFrame", lambda prm: asyncio.ensure_future(rec.on_frame(prm)))
        await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": 1920, "maxHeight": 1080,
                                                "everyNthFrame": 1})
        await page.wait_for_timeout(800)
        timeline = []
        t_start = time.time()
        for ln in lines:
            t = time.time()
            await page.evaluate("([s, c]) => window.dvSub(s, c)", [ln["display"], ln["chapter"]])
            timeline.append({"i": ln["i"], "start": t, "dur": ln["dur"], "file": ln["file"], "display": ln["display"]})
            task = asyncio.create_task(act(page, ln["action"]))
            await asyncio.sleep(ln["dur"] + GAP)
            await task
            print(f"  {ln['i']:02d} {ln['action']:<16} {time.time() - t:5.1f}s（配音 {ln['dur']:.1f}s）", flush=True)
        await page.evaluate("window.dvSub('', '')")
        await page.wait_for_timeout(2600)
        await cdp.send("Page.stopScreencast")
        await page.wait_for_timeout(300)
        t_end = time.time()
        await ctx.close()
        await browser.close()
    (OUT / "frames.json").write_text(json.dumps({"frames": rec.frames, "t_start": t_start, "t_end": t_end},
                                                ensure_ascii=False), encoding="utf-8")
    (OUT / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"录制完成：{len(rec.frames)} 帧，{t_end - t_start:.1f}s", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
