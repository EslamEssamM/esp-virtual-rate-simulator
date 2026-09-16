"""Full-page screenshots of the Streamlit pages with Playwright + installed Edge.

usage: shoot.py out_dir name=url [name=url ...]   (url relative to http://localhost:8501)
optional env: SHOT_ACTIONS = python snippet run with `page` before the shot
"""
import os, sys, time
from playwright.sync_api import sync_playwright

out = sys.argv[1]
os.makedirs(out, exist_ok=True)
targets = [a.split("=", 1) for a in sys.argv[2:]]
actions = os.environ.get("SHOT_ACTIONS", "")

with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True)
    ctx = browser.new_context(viewport={"width": 1500, "height": 1000}, color_scheme="light", device_scale_factor=1)
    page = ctx.new_page()
    for name, url in targets:
        page.goto("http://localhost:8501/" + url.lstrip("/"), wait_until="networkidle", timeout=120000)
        page.wait_for_timeout(3000)
        # wait until streamlit finishes running (status widget disappears)
        for _ in range(60):
            running = page.locator('[data-testid="stStatusWidget"]').count()
            if running == 0:
                break
            page.wait_for_timeout(1000)
        if actions:
            exec(actions, {"page": page, "time": time})
        page.wait_for_timeout(2500)
        # Streamlit scrolls inside its main section: grow the viewport to the content height
        h = page.evaluate("(document.querySelector('[data-testid=stMain]') || document.body).scrollHeight")
        page.set_viewport_size({"width": 1500, "height": min(int(h) + 40, 12000)})
        page.wait_for_timeout(2500)
        path = os.path.join(out, f"{name}.png")
        page.screenshot(path=path, full_page=False)
        print(name, path, h)
        page.set_viewport_size({"width": 1500, "height": 1000})
    browser.close()
