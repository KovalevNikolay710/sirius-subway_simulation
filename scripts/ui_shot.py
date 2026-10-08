import sys

from playwright.sync_api import sync_playwright

url, out = sys.argv[1], sys.argv[2]
tab = sys.argv[3] if len(sys.argv) > 3 else None
with sync_playwright() as p:
    b = p.chromium.launch(args=["--no-sandbox"])
    pg = b.new_page(
        viewport={"width": 1440, "height": int(sys.argv[4]) if len(sys.argv) > 4 else 900}
    )
    pg.goto(url)
    pg.wait_for_timeout(6000)
    if tab:
        pg.get_by_role("tab", name=tab).click()
        pg.wait_for_timeout(6000)
    pg.screenshot(path=out, full_page=True)
    b.close()
print("saved", out)
