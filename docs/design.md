# Design: dispatcher screen

Read this instead of the HTML. Reference implementation of the style: the simulator player (`src/metro_control/assets/sim_player.html`).

## Purpose and voice
Dispatchers of the SPb metro see **ahead of time** where crowds will build up (weather, events, trains) and what to do. Line 1 is live; the design hints at the whole network (line switcher, «Схема метро»).
Screen text is labels and numbers only. No explanatory sentences, no "how to read" hints. Put help in `title` tooltips.

## Tokens (`assets/ui.css`, `assets/ui.js` → `C`)
| Role | Value |
|---|---|
| Paper / card / rule | `#F4F5F7` / `#FFFFFF` / `#E3E6EA` |
| Ink / muted | `#1B2430` / `#6B7685` |
| Console (dark player bar) | `#0E2A47`, controls `#1C3D60` |
| Line 2 blue = base, "50–80 %" | `#0078C9` (quiet `#CFE3F3` below 50 %) |
| Line 4 orange = "80–100 %" | `#EA7125` |
| Line 1 red = overload > 100 %, line spine, line badge | `#D6083B` |
| Line 3 green = "policy helped" | `#009A49` |
| Line 5 purple = step buttons | `#702785` |
Font: Golos Text (local `static/GolosText.ttf`), tabular numbers. Russian thousands separator U+202F, true minus `−`.
Load is **demand / capacity** everywhere (`band()` 4 flat bands for maps, `smooth()` ramp for animated elements).

## Components
- **Console**: dark bar; round play (blue, playing = orange pause), stop red, steps purple; big clock; speed segmented ×1 ×2 ×4; mode `<select>`; scrubber with day overview (waiting curve + peak band) and decision diamonds.
- **Heatmap**: segments × time, north | south; hovered row lifts and grows (eased, shadow); future dimmed; playhead with time label; drag anywhere to seek.
- **Line strip**: horizontal line, segments above (north) / below (south) coloured by load, % only above 80 %; queues = orange circles; hover scales an element; recent decision target outlined (red pulse if critical).
- **Decision banner** (sim player `#banner`): dark card above the console showing the selected decision (`<time> <what> <target>` bold, full YandexGPT text clamped to 4 lines, `YandexGPT` chip, ✕ / Esc). Selecting = click or Enter on a decision (persistent `.sel`, `aria-selected`); hidden without selection or without LLM text. The old events banner is gone; station dots: `<title>` = name + reasons of the slot; anomaly = dashed orange ring (r 11) + legend «аномалия».
- **Demand select** (sim console, before «Режим»): Факт / Прогноз switched client-side; no forecast run → Прогноз disabled with the CLI command as title; forecast active → grey chip `прогноз q50: <source>`; source errors of the shown run in one red line `#srcerr` under the console (max 3 + «ещё N»). No captions or warnings around the player.
- **Header**: title, then a line selector (`<details>`: active roundel + chevron; list of all lines, inactive greyed, `aria-disabled`, «нет данных») and the line name in its own block with a ≥ 12 px gap.
- **Sidebar**: hidden at rest behind a 16 px handle (menu icon); hover / focus slides it over the content (`transform` 220 ms ease-out). «Источники» pinned to its bottom. Main content padding 1.5 rem.
- **KPI cards**: label, big number, chip vs baseline (green better / red worse).
- **Decision list**: chronological, follows the playhead (eased scroll), past / current / future states, red left bar + badge for critical, click = seek.
- **Slider (forecast)**: native range, continuous drag, snaps on release, time bubble; strip eases between slots.
- Interaction: everything time-related is draggable; keyboard ← → (Shift ×4), ↑ ↓ speed, Home/End, space.

## Do not
- Explanatory text on screen; captions that repeat the title; badges like "(mock)" inline in titles (use one small grey `mock` chip).
- Glow, gradient washes, coloured halos, drop shadows on static elements (shadow only on lifted/hovered items).
- Identical rounded cards for everything, all-caps labels, `·` separators, `→` on buttons (frontend-design tells).
- Hard-coded line name/colour: take them from `config/line.json` (`config/lines.json` for the switcher).

## UI acceptance checklist (every UI slice; `ui-checker` reports PASS/FAIL per line)
1. 0 JS errors / tracebacks (`scripts/ui_check.py`).
2. Fits 1440×900 without horizontal scroll; the key view (console + main chart) is above the fold.
3. Palette and font as above; no forbidden items.
4. Every new interaction works by mouse and keyboard; transitions are eased, no flicker.
5. Numbers are formatted (U+202F, `%`, `−`); MSK times.
6. Text on screen ≤ labels + numbers; help only in tooltips.
