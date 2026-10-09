"""Read-only browser acceptance of the real native Pages shell and grouped UI.

The private local test login is read in-process, never printed or captured.
This probe does not save settings, upload originals, retry or delete tasks.
"""

import argparse
import asyncio
import json
import os

from playwright.async_api import TimeoutError as BrowserTimeout
from playwright.async_api import async_playwright

from tools.container_smoke import BASE, PORT, ROOT, wait_ready
from tools.native_corpus_smoke import runtime_version


async def main(batch="v7"):
    if PORT != 16190:
        raise RuntimeError("use the sole current V7 acceptance instance")
    wait_ready()
    version = runtime_version()
    credentials = json.loads((ROOT / "runtime-data/acceptance-webui.json").read_text())
    evidence = ROOT / f"runtime-data/native-pages-{batch}"
    if (ROOT / f"runtime-data/native-pages-{batch}-results.json").exists():
        raise RuntimeError("retain existing browser evidence; select another batch")
    evidence.mkdir(exist_ok=True)
    checks = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={"width": 1365, "height": 950})
            await page.goto(BASE + "/#/auth/login")
            await page.locator('input[name="username"]').fill(credentials["username"])
            await page.locator('input[name="password"]').fill(credentials["password"])
            await page.locator("form").get_by_role("button", name="登录", exact=True).click()
            await page.wait_for_url(lambda url: "login" not in url, timeout=30000)
            await page.goto(BASE + "/#/plugin-page/astrbot_plugin_notido/dashboard")
            frame = page.frame_locator("iframe")
            await frame.get_by_role("heading", name="处理状态", exact=True).wait_for(timeout=30000)
            # A fresh browser can receive the framework's dismissible notice
            # over the whole Pages iframe. Escape closes it without agreeing to
            # terms or saving any preference; don't force-click through it.
            try:
                await page.locator(".v-overlay__scrim:visible").wait_for(timeout=5000)
                await page.keyboard.press("Escape")
                await page.locator(".v-overlay__scrim:visible").wait_for(state="hidden")
            except BrowserTimeout:
                if await page.locator(".v-overlay__scrim:visible").count():
                    raise RuntimeError(
                        "framework modal requires inspection; don't bypass it"
                    ) from None
            checks.append({"check": "authenticated_real_framework_pages_bridge", "pass": True})
            await frame.get_by_role("button", name="设置", exact=True).click()
            navigation = frame.get_by_role("complementary", name="设置分组")
            await navigation.wait_for()
            await navigation.get_by_role("button", name="数据与维护", exact=True).wait_for()
            titles = ["常规", "滴答与清单", "会话授权", "材料与运行", "数据与维护"]
            if await navigation.get_by_role("button").all_text_contents() != titles:
                raise RuntimeError("expected exactly five setting groups")
            for index, title in enumerate(titles):
                await navigation.get_by_role("button", name=title, exact=True).click()
                await frame.get_by_role("heading", name=title, exact=True).wait_for()
                if await frame.locator(".settings-group:visible").count() != 1:
                    raise RuntimeError("setting groups are stacked instead of selected")
                await page.screenshot(path=evidence / f"settings-{index + 1}.png")
                checks.append({"check": "group_" + title, "pass": True})
                if batch == "read-budget" and title == "材料与运行":
                    field = frame.locator('input[name="time_budgets.group_read_seconds"]')
                    if await field.input_value() != "180":
                        raise RuntimeError("cumulative decoder budget is missing or has the wrong default")
                    checks.append({"check": "group_read_budget_control", "pass": True})
            await navigation.get_by_role("button", name="常规", exact=True).click()
            if await frame.locator(
                'input[name="identity.school"],input[name="provider_id"]'
            ).count():
                raise RuntimeError("manual identity or Provider controls are still present")
            checks.append({"check": "no_manual_identity_or_provider_controls", "pass": True})
            await frame.get_by_role("button", name="通知 / 待处理", exact=True).click()
            await frame.get_by_role("button", name="查看依据与问题").first.wait_for()
            await page.screenshot(path=evidence / "notices.png")
            checks.append({"check": "real_notice_history_visible", "pass": True})
            await frame.get_by_role("button", name="操作 / 恢复", exact=True).click()
            await frame.get_by_role("heading", name="操作 / 恢复", exact=True).wait_for()
            await page.screenshot(path=evidence / "operations.png")
            checks.append({"check": "real_operation_history_visible", "pass": True})
        finally:
            await browser.close()
    result = {**version, "port": PORT, "checks": checks, "mutations": 0}
    if runtime_version() != version:
        raise RuntimeError("installed version changed during browser checks")
    target = ROOT / f"runtime-data/native-pages-{batch}-results.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    print(json.dumps({"checks": len(checks), "pass": True, "mutations": 0}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", choices=("v7", "v7-r2", "read-budget"), default="v7")
    asyncio.run(main(parser.parse_args().batch))
