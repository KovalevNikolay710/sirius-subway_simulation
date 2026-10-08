"""Scripted UI check: start the app, run steps, collect JS errors and screenshots.

    uv run python scripts/ui_check.py --steps runs/briefs/U1.steps.json --out runs/ui/U1
    uv run python scripts/ui_check.py --shot forecast.png   # one screenshot of the start page

Steps file: a JSON list; `frame` = a selector inside the iframe to act in (players are iframes).
    {"nav": "Симулятор"}                          sidebar page link / tab with this text
    {"click": "#play", "frame": "#heat"}
    {"drag": "#heat", "frame": "#heat", "from": [0.3, 0.4], "to": [0.6, 0.4]}  (box fractions)
    {"hover": "#acts li.crit", "frame": "#acts"}
    {"key": "ArrowRight", "times": 4, "frame": "#heat"}   (clicks `focus`, default body, first)
    {"fill": "#rng", "value": "3", "frame": "#rng"}
    {"eval": "state.pos", "frame": "#heat", "name": "pos"}  JS value -> output "values"
    {"upload": "input[type=file]", "path": "data/raw/x.zip"}
    {"wait": 800} / any step may carry "wait" (ms after it, default 400)
    {"shot": "after-drag", "full": true}          PNG in --out
Prints {"errors": [...], "values": {...}, "shots": [...]}; exit 1 on JS/page errors.
Chromium runs with --no-sandbox (root, WSL); the app is started on a free port and stopped.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Frame, Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_app(port: int, env: dict[str, str], log: Path) -> subprocess.Popen:
    cmd = ["uv", "run", "streamlit", "run", "app.py", "--server.headless", "true"]
    cmd += ["--server.port", str(port)]
    p = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=log.open("w"), stderr=subprocess.STDOUT)
    for _ in range(180):
        try:
            urllib.request.urlopen(f"http://localhost:{port}/_stcore/health", timeout=1)
            return p
        except OSError:
            time.sleep(0.5)
    p.kill()
    raise SystemExit(f"app did not start, see {log}")


def _frame(page: Page, sel: str | None) -> Page | Frame:
    if not sel:
        return page
    for _ in range(40):
        for f in page.frames:
            if f != page.main_frame and f.query_selector(sel):
                return f
        page.wait_for_timeout(250)
    raise LookupError(f"no iframe contains {sel}")


def _nav(page: Page, text: str) -> None:
    for role in ("link", "tab", "button"):
        loc = page.get_by_role(role, name=text)
        if loc.count():
            loc.first.click()
            return
    page.get_by_text(text, exact=True).first.click()


def run(steps: list[dict], url: str, out: Path, width: int, height: int) -> dict:
    res: dict = {"errors": [], "values": {}, "shots": []}
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": width, "height": height})
        page.on("pageerror", lambda e: res["errors"].append(f"pageerror: {e}"))
        page.on("console", lambda m: res["errors"].append(m.text) if m.type == "error" else None)
        page.goto(url)
        page.wait_for_timeout(6000)
        for st in steps:
            f = _frame(page, st.get("frame"))
            if "nav" in st:
                _nav(page, st["nav"])
                page.wait_for_timeout(3000)
            elif "click" in st:
                f.click(st["click"])
            elif "hover" in st:
                f.hover(st["hover"])
            elif "fill" in st:
                f.fill(st["fill"], str(st["value"]))
                f.dispatch_event(st["fill"], "input")
            elif "key" in st:
                if st.get("frame"):
                    f.click(st.get("focus", "body"))
                for _ in range(int(st.get("times", 1))):
                    page.keyboard.press(st["key"])
            elif "drag" in st:
                box = f.locator(st["drag"]).first.bounding_box()
                fx, fy = st.get("from") or [0.3, 0.5]
                tx, ty = st.get("to") or [0.7, 0.5]
                page.mouse.move(box["x"] + box["width"] * fx, box["y"] + box["height"] * fy)
                page.mouse.down()
                page.mouse.move(
                    box["x"] + box["width"] * tx, box["y"] + box["height"] * ty, steps=10
                )
                page.mouse.up()
            elif "upload" in st:
                f.locator(st["upload"]).first.set_input_files(str(ROOT / st["path"]))
            elif "eval" in st:
                res["values"][st.get("name", st["eval"])] = f.evaluate(st["eval"])
            elif "shot" in st:
                p = out / f"{st['shot']}.png"
                page.screenshot(path=str(p), full_page=bool(st.get("full", True)))
                res["shots"].append(str(p.relative_to(ROOT)))
            page.wait_for_timeout(int(st.get("wait", 400)))
        browser.close()
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", help="JSON file with steps")
    ap.add_argument("--shot", help="just one screenshot of the start page (file name)")
    ap.add_argument("--out", default="runs/ui/check")
    ap.add_argument("--url", help="use a running app instead of starting one")
    ap.add_argument("--run-dir", help="METRO_RUN_DIR for the started app")
    ap.add_argument("--sim-dir", help="METRO_SIM_DIR for the started app")
    ap.add_argument("--size", default="1440x900")
    a = ap.parse_args()
    steps = json.loads(Path(a.steps).read_text(encoding="utf-8")) if a.steps else []
    if a.shot:
        steps.append({"shot": Path(a.shot).stem})
    w, h = (int(x) for x in a.size.split("x"))
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    app = None
    url = a.url
    if not url:
        env = dict(os.environ)
        if a.run_dir:
            env["METRO_RUN_DIR"] = a.run_dir
        if a.sim_dir:
            env["METRO_SIM_DIR"] = a.sim_dir
        port = _free_port()
        app = _start_app(port, env, out / "streamlit.log")
        url = f"http://localhost:{port}"
    try:
        res = run(steps, url, out, w, h)
    finally:
        if app is not None:
            app.terminate()
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
