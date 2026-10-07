# Decision log

| # | Date | Decision | Why |
|---|------|----------|-----|
| D1 | 2026-10-07 | Own repo `metro-control`; exchange with team via versioned contracts and files in `runs/` | Team uses 4 repos |
| D2 | 2026-10-07 | Real 15-min entries are the demand base; scenarios overlay real days | Organizers provided data |
| D3 | 2026-10-07 | OD by gravity model, attraction = entries in mirrored period, transfers as coefficients | No exits/OD/transfers in data |
| D4 | 2026-10-07 | Train-level event simulator, fixed run times from 99-min cycle, hard capacity 1458, fluid passengers | 1–2 day budget, honest wait/queue metrics |
| D5 | 2026-10-07 | r = segment demand / (departures × 1458), control horizon +30 min, on r>1 ×2, off r<0.8 ×3 | ТЗ rules; person 4 may override in config |
| D6 | 2026-10-07 | Mock forecast = median station×slot×day type; mock policy = ТЗ rules; both labelled `mock` | Demo must run without persons 2 and 4 |
