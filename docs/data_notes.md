# Organizer data — structure notes

Read this instead of opening the Excel files. Raw files: `data/raw/Данные Сириус/` (git-ignored).

## 1. 15-min station entries — `1.Пассажиропоток+ ГД/Входные пассажиропотоки Линия 1 {фев,май,июль,сен} 2026 по 15-мин.xlsx`
- One sheet per day, name `ТВхП DDMMYYYY X 15-мин.`, plus an empty `Лист1` to skip. Feb 28 days, May 31, Jul 31, Sep 30.
- The letter X is ambiguous (П = Понедельник/Пятница, В = Вторник/Воскресенье, С = Среда/Суббота): derive the weekday from the date.
  Cell A1 holds the full title, e.g. `Таблица входных потоков. 30092026 Среда`.
- Shape 28×99. Row 1 (0-based): column 0 empty, column 1 empty, columns 2..97 = 96 slot starts `03:00 … 02:45` as `datetime.time`
  (slots after midnight belong to the next calendar date). Row 2: line totals per slot, last cell = day total.
  Rows 3..26: 24 vestibules — column 0 name, column 1 day total, columns 2..97 entries per slot. Row 27: grand total.
- Vestibule names (rows 3..26, line order south → north): Пр.Ветеранов-1, Пр.Ветеранов-2, Ленинский пр.-1, Ленинский пр.-2, Автово,
  Кировский завод, Нарвская, Балтийская, Технологический институт-1 (no -2), Пушкинская, Владимирская, Пл. Восстания-1, Пл. Восстания-2,
  Чернышевская, Пл.Ленина-1, Пл.Ленина-2, Выборгская, Лесная, Пл. Мужества, Политехническая, Академическая, Гражданский пр.,
  Девяткино-2, Девяткино-1. Sum the `-1/-2` vestibules → 19 stations.

## 2. Hourly entries — `1.Пассажиропоток+ ГД/Пассажиропоток 2026 Линия 1.xlsx`
- One sheet. Rows 0–5 are a summary; row 6 header `Дата | Станция | Количество пассажиров`; data from row 7; the last row is a total line.
- `Дата` format `DD.MM.YYYY HH`. Range 2026-01-01 00 … 2026-09-30. ~150k rows, long format.
- 23 points, **Технологический институт is missing**. Names differ from file 1: `пр. Ветеранов-1`, `пл.Восстания-1`, `пл.Ленина-1`,
  `пл.Мужества`, `Гражданский`, `Девяткино II`, `Девяткино I`.

## 3. Line and rolling stock parameters
- Capacity: 8-car train, **1458 passengers** at 5 p/m² (organizers: "this is the maximum"); Baltiets type 1478; crush 2608–2640 not used.
- Full round trip (there and back) **99 min**. Reserve: 4 trains, 2 at Avtovo depot (TCh-1), 2 at Severnoye depot (TCh-4, by Devyatkino), 15–20 min to enter the line.
- Headway min–max by departure period (weekday / Sat / Sun):
  05:45–08:00 1:45–8:00 / 2:30–8:00 / 2:30–8:00; 08–09 1:45–3:30 / 2:30–5:00 / 2:30–5:00; 09–17 1:45–5:30 / 2:45–6:00 / 2:45–6:00;
  17–19 2:00–4:00 / 2:45–6:00 / 2:45–6:00; 19–00 2:00–9:00 / 2:45–10:30 / 2:45–10:30.
- Planned pairs per hour, hours 05..24 (photo of the rolling schedule):
  weekday from 01.09: 7,20,30,32,26,22,20,18,18,20,24,28,30,30,28,20,15,14,11,7 (trains on line peak 53);
  weekend from 01.09: 7,16,19,21,20,20,20,19,19,19,20,20,20,19,18,16,15,13,11,7.
- Turnback points (track map PDF): Pl. Vosstaniya, Pl. Lenina, Avtovo; depots Avtovo/Dachnoye (south), Severnoye (north).

## 4. Other
- `2.ОСНОВНЫЕ ПОКАЗАТЕЛИ выполнения ГД/MO1*.xls` — HTML in KOI8-R (form MO-1). Line 1, Q3 2026: 74 299 trains planned, 2 041 527 train-km,
  55 395 train-hours (46 276 moving), operating speed 36.86 km/h, max pairs 32, mean 20.49. Used by person 4 for economics.
- `3.Характеристики состава и Линии/Пример остановки…docx` — incident 31.08.2026 (Chernyshevskaya): turnbacks at Vosstaniya/Lenina,
  vestibule closures and entry limits at Devyatkino, Grazhdansky, Muzhestva. Example of the "capacity limit" action.
- `Диаграмма пассажиропотока (пример)` — 10-min entry charts as images only; no extra numbers.
