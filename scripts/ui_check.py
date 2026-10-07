"""Screenshot a running Streamlit app with playwright; exit 1 on traceback/exception."""

from __future__ import annotations

import argparse
import sys

from playwright.sync_api import sync_playwright


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--click-text", default=None)
    args = ap.parse_args()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(args.url)
        page.wait_for_selector('[data-testid="stApp"]', timeout=30000)
        page.wait_for_timeout(1000)
        page.wait_for_function(
            "() => !document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=60000
        )
        if args.click_text:
            page.get_by_text(args.click_text).first.click()
            page.wait_for_timeout(1500)
            page.wait_for_function(
                "() => !document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=60000
            )
        page.screenshot(path=args.out, full_page=True)
        body = page.inner_text("body")
        bad = "Traceback" in body or page.locator('[data-testid="stException"]').count() > 0
        browser.close()
    for line in body.splitlines()[:60]:
        print(line)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
