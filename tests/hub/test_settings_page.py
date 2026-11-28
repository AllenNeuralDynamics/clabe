"""The hub's Settings tab, and the loading screen every page shows while it downloads."""

import os
import threading
import time

import pytest
import requests
import uvicorn

from clabe.hub import Fleet, HubSettings, create_hub_app
from clabe.server import free_port
from clabe.server._app import STATIC_DIR

pytestmark = pytest.mark.skipif(not (STATIC_DIR / "index.html").is_file(), reason="the web UI is not built")


@pytest.fixture
def hub():
    port = free_port("127.0.0.1", 18560)
    app = create_hub_app(Fleet(HubSettings()), token=None, static_dir=STATIC_DIR, poll=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)


def test_the_page_shows_a_loading_screen_until_it_starts(hub):
    page = requests.get(f"{hub}/", timeout=10).text
    assert 'class="boot"' in page and "boot-bar" in page and "Reload" in page


def test_the_settings_tab_sets_the_theme_and_the_time_zone_and_keeps_them(hub):
    playwright_api = pytest.importorskip("playwright.sync_api")
    with playwright_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel=os.environ.get("CLABE_TEST_BROWSER_CHANNEL") or None)
        except playwright_api.Error as exc:
            pytest.skip(f"no browser for Playwright: {exc}")
        page = browser.new_page(color_scheme="light")
        page.set_default_timeout(60_000)
        page.goto(f"{hub}/")
        page.get_by_role("button", name="Settings").click()  # the gear opens the Settings tab
        page.get_by_role("tab", name="Settings").wait_for()
        page.wait_for_url(f"{hub}/?tab=settings")

        page.get_by_role("radio", name="Dark").click()
        page.wait_for_function("() => document.documentElement.dataset.theme === 'dark'")
        page.get_by_role("radio", name="UTC").click()
        page.get_by_text("Times are kept in UTC").wait_for()
        assert page.evaluate("() => [localStorage.getItem('clabe.theme'), localStorage.getItem('clabe.time')]") == [
            "dark",
            "utc",
        ]

        page.reload()  # kept in the browser, and the tab in the address
        page.get_by_role("radio", name="Dark").wait_for()
        assert page.get_by_role("radio", name="Dark").get_attribute("aria-checked") == "true"
        assert page.get_by_role("radio", name="UTC").get_attribute("aria-checked") == "true"
        assert page.evaluate("() => document.documentElement.dataset.theme") == "dark"

        page.get_by_role("radio", name="System").click()  # follows the computer: light here
        page.wait_for_function("() => document.documentElement.dataset.theme === 'light'")
        browser.close()
