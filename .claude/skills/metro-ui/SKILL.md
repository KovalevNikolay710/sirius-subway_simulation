---
name: metro-ui
description: Style and acceptance rules for the metro-control dispatcher screen (Streamlit app.py + in-browser players in src/metro_control/assets). Load for any slice that touches the screen, app.py, assets/*.html, ui.css/ui.js or figures.py.
---
1. Read `docs/design.md` (tokens, components, "Do not", acceptance checklist). Do not read the player HTML in full: grep the part you change.
2. Shared look lives in `src/metro_control/assets/ui.css` and `ui.js` (palette `C`, `band`, `smooth`, `fmt`, `el`, …); `sim_view._page()` inlines them into each iframe. New players start from those, never copy them.
3. Logic stays in `src/metro_control/` (pure, tested); `app.py` only draws. Payloads for players are built in `sim_view.py`.
4. Verify with `uv run python scripts/ui_check.py --steps runs/briefs/<ID>.steps.json --out runs/ui/<ID>` (starts the app itself; steps: nav, click, drag, hover, key, fill, eval, upload, shot). Write the steps file next to the brief.
5. Report PASS/FAIL per checklist line of `docs/design.md`; attach at most 2 screenshot paths.
