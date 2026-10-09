"""Pages bridge rendering contract; real WebUI auth is tested separately in the container."""

import json
from pathlib import Path

from playwright.async_api import async_playwright

from notido.models import Settings

ROOT = Path(__file__).resolve().parent.parent


async def test_pages_five_views_no_html_execution(tmp_path):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 800})
        settings = {
            "timezone": "Asia/Shanghai",
            "provider_id": "example-provider",
            "default_project": "p1",
            "allowed_projects": ["p1"],
            "project_aliases": {},
            "identity": {
                "school": "东北大学",
                "college": None,
                "major": None,
                "entry_year": None,
                "current_year": None,
                "class_name": None,
                "roles": [],
                "confirmed_at": None,
            },
            "account_ref": "test-account",
            "credential_generation": 1,
            "account_region": "cn",
            "authorization_revision": 1,
            "min_free_bytes": 0,
            "max_jobs": 100,
            "max_inbox": 1000,
            "silence_seconds": 30,
            "window_seconds": 120,
            "materials": Settings().materials.model_dump(),
            "time_budgets": Settings().time_budgets.model_dump(),
            "retention": Settings().retention.model_dump(),
        }
        data = {
            "status": {
                "astrbot_bridge": {"version": "4.28.2"},
                "provider": {"configured": True},
                "task_cli": {"account_confirmed": True, "version": "0.1.14"},
                "attachment_cli": {"readiness": True, "reason": "已验证"},
                "db": "ready",
                "worker": "running",
                "jobs": [],
                "checked_at": 1791363308,
                "maintenance": None,
            },
            "settings": {"settings": settings, "revision": 1, "bindings": []},
            "projects": {"projects": [{"id": "p1", "name": "学习"}]},
            "notices": {
                "items": [
                    {
                        "id": "group-test",
                        "state": "awaiting_clarification",
                        "first_at": 1791363308,
                        "revision": 1,
                    }
                ],
                "next_cursor": None,
            },
            "notices/group-test": {
                "group": {"id": "group-test", "state": "awaiting_clarification", "revision": 1},
                "session": {"question": None},
                "segments": [
                    {
                        "source_id": "source",
                        "location": "page:1",
                        "state": "read",
                        "text": "<img src=x onerror=window.owned=true>",
                    }
                ],
                "versions": [],
            },
            "assets": {
                "items": [
                    {
                        "id": "asset-test",
                        "name": "<script>window.owned=true</script>.txt",
                        "state": "ready",
                        "created_at": 1791363308,
                        "group_id": "group-test",
                        "hash": "a" * 64,
                        "error": None,
                    }
                ],
                "next_cursor": None,
            },
            "operations": {"items": [], "next_cursor": None},
            "receipts": {"items": [], "next_cursor": None},
        }
        await page.add_init_script(
            "window.__fixture="
            + json.dumps(data, ensure_ascii=False)
            + ";window.AstrBotPluginPage={ready:async()=>({}),apiGet:async(e)=>window.__fixture[e],apiPost:async()=>({}),upload:async()=>({}),download:async()=>({})};"
        )

        async def static(route):
            import mimetypes
            from urllib.parse import urlparse

            name = urlparse(route.request.url).path.lstrip("/") or "index.html"
            if name not in {"index.html", "app.js", "style.css"}:
                await route.abort()
                return
            await route.fulfill(
                path=ROOT / "pages/dashboard" / name,
                content_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
            )

        await page.route("http://localhost:16999/**", static)
        await page.goto("http://localhost:16999/index.html")
        await page.get_by_role("heading", name="处理状态").wait_for()
        await page.get_by_role("button", name="通知 / 待处理", exact=True).click()
        await page.get_by_role("button", name="查看依据与问题").click()
        await page.get_by_text("<img src=x onerror=window.owned=true>", exact=True).wait_for()
        await page.get_by_role("heading", name="在 AstrBot 会话继续", exact=True).wait_for()
        assert await page.get_by_role("button", name="续办并获取新问题").count() == 0
        assert await page.get_by_role("button", name="提交回答").count() == 0
        assert await page.get_by_label("选择原件", exact=True).count() == 0
        assert not await page.evaluate("Boolean(window.owned)")
        await page.get_by_role("button", name="关闭详情").click()
        await page.get_by_role("button", name="原件", exact=True).click()
        await page.get_by_text("<script>window.owned=true</script>.txt", exact=True).wait_for()
        assert not await page.evaluate("Boolean(window.owned)")
        await page.get_by_role("button", name="操作 / 恢复", exact=True).click()
        await page.get_by_role("heading", name="回执补发").wait_for()
        await page.get_by_role("button", name="设置", exact=True).click()
        await page.get_by_label("时区", exact=True).wait_for()
        assert await page.get_by_label("学校", exact=True).count() == 0
        assert await page.get_by_label("AstrBot Provider ID", exact=True).count() == 0
        await page.get_by_role("button", name="滴答与清单", exact=True).click()
        assert await page.get_by_label("默认清单", exact=True).input_value() == "p1"
        assert not await page.get_by_label("时区", exact=True).is_visible()
        await page.get_by_role("button", name="材料与运行", exact=True).click()
        assert await page.get_by_label("静默收集窗口（秒）", exact=True).count() == 0
        assert await page.get_by_label("单次模型调用秒数", exact=True).count() == 0
        await page.get_by_role("heading", name="材料读取预算", exact=True).wait_for()
        assert not await page.get_by_label("默认清单", exact=True).is_visible()
        await page.get_by_role("button", name="常规", exact=True).click()
        await page.screenshot(path=str(tmp_path / "settings-desktop.png"), full_page=True)
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.screenshot(path=str(tmp_path / "settings-mobile.png"), full_page=True)
        assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        await browser.close()


async def test_restore_review_pagination_and_explicit_acknowledgement():
    from notido.models import Settings

    data = {
        "status": {
            "astrbot_bridge": {"version": "4.28.2"},
            "provider": {"configured": False},
            "task_cli": {"account_confirmed": True, "version": "0.1.14"},
            "attachment_cli": {"readiness": True},
            "maintenance": "RESTORE_REMOTE_REVIEW_REQUIRED",
            "db": "ready",
            "worker": "running",
            "jobs": [],
            "checked_at": 1791363308,
        },
        "settings": {"settings": Settings().model_dump(), "revision": 7},
        "recovery/status": {
            "required": True,
            "review": {
                "id": "review",
                "state": "ready",
                "checked_at": 1791363308,
                "payload": {
                    "history": [
                        {
                            "operation_id": f"history-{i}",
                            "state": "succeeded",
                            "verification": "matches",
                            "current": {"title": "<script>window.owned=true</script>"},
                        }
                        for i in range(25)
                    ],
                    "scope": {
                        "tasks": [
                            {"title": f"远端任务 {i}", "id": f"remote-{i}"} for i in range(25)
                        ]
                    },
                },
            },
        },
        "operations": {
            "items": [
                {
                    "id": "op-first",
                    "kind": "create",
                    "state": "validated",
                    "revision": 1,
                    "created_at": 1791363308,
                    "result": None,
                    "paused": True,
                }
            ],
            "next_cursor": "next",
        },
        "operations/next": {
            "items": [
                {
                    "id": "op-second",
                    "kind": "create",
                    "state": "validated",
                    "revision": 1,
                    "created_at": 1791363308,
                    "result": None,
                    "paused": True,
                }
            ],
            "next_cursor": None,
        },
        "receipts": {"items": []},
    }
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        await page.add_init_script(
            "window.__fixture="
            + json.dumps(data, ensure_ascii=False)
            + ";window.__posts=[];window.AstrBotPluginPage={ready:async()=>({}),apiGet:async(e,p)=>window.__fixture[e+(p?.cursor?'/next':'')],apiPost:async(e,p)=>{window.__posts.push({endpoint:e,body:p});return {};}};"
        )

        async def static(route):
            from urllib.parse import urlparse

            name = urlparse(route.request.url).path.lstrip("/") or "index.html"
            if name not in {"index.html", "app.js", "style.css"}:
                await route.abort()
            else:
                await route.fulfill(path=ROOT / "pages/dashboard" / name)

        await page.route("http://localhost:16999/**", static)
        await page.goto("http://localhost:16999/index.html")
        await page.get_by_role("button", name="操作 / 恢复", exact=True).click()
        await page.get_by_role("heading", name="备份恢复复核", exact=True).wait_for()
        assert await page.get_by_role("heading", name="history-0", exact=True).count() == 1
        assert await page.get_by_role("heading", name="history-20", exact=True).count() == 0
        await page.get_by_role("button", name="下一批", exact=True).first.click()
        await page.get_by_role("heading", name="history-20", exact=True).wait_for()
        assert await page.get_by_role("heading", name="history-0", exact=True).count() == 0
        # The main operation cursor must append to its own list, not to the review panel.
        await page.get_by_role("button", name="载入更多", exact=True).click()
        assert await page.locator("#content > .list > .card").count() == 2
        assert not await page.evaluate("Boolean(window.owned)")
        assert not await page.get_by_label(
            "我已人工核对备份后的新增/修改及未匹配任务（含旧账号记录）"
        ).is_checked()
        await page.get_by_label("我已人工核对备份后的新增/修改及未匹配任务（含旧账号记录）").check()
        await page.get_by_label("保留旧操作暂停，未知结果不重做，未开始计划另行核验").check()
        await page.get_by_role("button", name="确认复核并解除恢复维护").click()
        posts = await page.evaluate("window.__posts")
        assert posts[-1]["endpoint"] == "recovery/confirm"
        assert posts[-1]["body"]["reviewed_remote_history"] is True
        assert posts[-1]["body"]["keep_old_operations_paused"] is True
        assert posts[-1]["body"]["expected_revision"] == 7 and posts[-1]["body"]["request_id"]
        assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        await browser.close()
