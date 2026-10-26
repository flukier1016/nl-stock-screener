"""给 README 截产品图：无界面 Chrome 打开线上站点，跑一句示例，按步骤截图（1280×800，2 倍清晰度）。

    .venv/bin/python scripts/readme_shots.py [--site URL]
输出：docs/images/<当前版本>/*.jpg。解析方式由线上实际状态决定；合规拒绝示例在规则层处理。
同时检查页面有没有违反内容安全策略（CSP）的报错，有就打印出来。
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import Page, async_playwright

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "mingshai/VERSION").read_text().strip()
OUT = ROOT / "docs/images" / f"v{VERSION}"
QUERY = "经营改善、估值合理、走势相对稳定"


async def scroll_to(page: Page, selector: str) -> None:
    await page.evaluate("""(sel) => { const el = document.querySelector(sel); const top = document.querySelector('header');
        const off = top ? top.getBoundingClientRect().height + 12 : 12;
        window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - off); }""", selector)
    await page.wait_for_timeout(500)


async def shot(page: Page, name: str) -> None:
    await page.screenshot(path=str(OUT / f"{name}.jpg"), type="jpeg", quality=82)
    print("  ", name)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="https://mingshai-production.up.railway.app")
    site = ap.parse_args().site.rstrip("/")
    OUT.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 800}, device_scale_factor=2,
                                        color_scheme="light", locale="zh-CN")
        page = await ctx.new_page()
        page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: problems.append(str(e)))
        await page.goto(f"{site}/?q={quote(QUERY)}&run=1", wait_until="networkidle")
        await page.wait_for_selector("#stepResults:not(.hidden) .kpis", timeout=60000)
        await page.wait_for_timeout(1500)
        fonts = await page.evaluate("() => document.fonts.check('16px \"IBM Plex Mono\"')")

        await scroll_to(page, "#stepResults")
        await shot(page, "results")
        await scroll_to(page, "#stepUnderstand")
        await shot(page, "understand")
        await scroll_to(page, "#stepConditions")
        await shot(page, "conditions")

        await page.locator("#stepResults tr[data-act=row]").first.click()
        await page.wait_for_selector("#drawer.open .ev-item", timeout=20000)
        await page.wait_for_timeout(800)
        await shot(page, "evidence")
        await page.locator("[data-act=close]").first.click()
        await page.wait_for_timeout(500)

        await page.locator("[data-act=theme][data-theme=dark]").click()
        await page.wait_for_timeout(400)
        await scroll_to(page, "#stepResults")
        await shot(page, "results-dark")

        await page.set_viewport_size({"width": 390, "height": 844})
        await page.wait_for_timeout(600)
        await scroll_to(page, "#stepResults")
        await page.screenshot(path=str(OUT / "mobile-dark.jpg"), type="jpeg", quality=82)
        print("   mobile-dark")
        await page.set_viewport_size({"width": 1280, "height": 800})
        await page.locator("[data-act=theme][data-theme=light]").click()

        await page.evaluate("window.scrollTo(0, 0)")
        await page.fill("#q", "明天会涨停的股票")
        await page.locator("[data-act=parse]").first.click()
        await page.wait_for_function("() => { const b = document.querySelector('#parseBtn'); return b && !b.disabled; }",
                                     timeout=30000)
        await page.wait_for_timeout(800)
        await scroll_to(page, "#stepUnderstand")
        await shot(page, "refusal")
        await browser.close()
    print("IBM Plex 字体已加载：", fonts)
    csp = [x for x in problems if "Content Security Policy" in x or "Refused to" in x]
    print("CSP 报错：", csp or "无")
    print("其他控制台错误：", [x for x in problems if x not in csp] or "无")


if __name__ == "__main__":
    asyncio.run(main())
