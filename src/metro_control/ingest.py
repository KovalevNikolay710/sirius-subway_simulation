"""Uploaded files or one ZIP archive -> a run dir the dispatcher screen can show (no Streamlit).

Recognised by file name (see `classify`); everything else is listed as unused, never an error.
A run dir is built with `adapters.build_team_bundle`; several recommendations go to
`recommendations.jsonl`; compare-run files go to their own `compare-upload-*` dir.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import polars as pl

from metro_control import mock
from metro_control.adapters import AdapterError, build_team_bundle, read_recommendation
from metro_control.entries import load_holidays, load_workbooks
from metro_control.line import load_line
from metro_control.timeline import load_timeline

ENTRIES_RE = re.compile(r"^Входные пассажиропотоки Линия 1 .+ по 15-мин\.xlsx$")
SIM_FILES = (
    "timeline.json",
    "metrics.json",
    "actions.jsonl",
    "manifest.json",
    "state_baseline.json",
    "state_policy.json",
)
KINDS = ("entries", "forecast", "recommendation", "explanation", "sim")
MAX_UNPACKED = 300 * 2**20  # zip-bomb guard


@dataclass(frozen=True)
class Note:
    file: str
    level: str  # ok | info | warning | error
    text: str


@dataclass
class IngestReport:
    run_dir: Path | None
    sim_dir: Path | None = None
    notes: list[Note] = field(default_factory=list)

    def add(self, file: str, level: str, text: str) -> None:
        self.notes.append(Note(file, level, text))


def classify(name: str) -> str:
    """Kind of an uploaded file by its name: one of KINDS, "zip" or "other"."""
    p = PurePosixPath(name.replace("\\", "/"))
    base, low, suf = p.name, p.name.lower(), p.suffix.lower()
    if suf == ".zip":
        return "zip"
    if ENTRIES_RE.match(base):
        return "entries"
    if base in SIM_FILES:
        return "sim"
    if low.startswith("forecast") and suf in (".json", ".csv", ".parquet"):
        return "forecast"
    if low.startswith("recommendation") and suf in (".json", ".jsonl"):
        return "recommendation"
    if suf == ".txt" and (low.startswith("explanation") or p.parent.name == "explanations"):
        return "explanation"
    return "other"


def expand(files: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    """Unpack ZIP archives (one level; folders inside are kept in the name, not on disk)."""
    out: list[tuple[str, bytes]] = []
    for name, data in files:
        if classify(name) != "zip":
            out.append((name, data))
            continue
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as e:
            raise ValueError(f"{name}: не ZIP-архив ({e})") from e
        if sum(i.file_size for i in z.infolist()) > MAX_UNPACKED:
            raise ValueError(f"{name}: архив больше {MAX_UNPACKED // 2**20} МБ после распаковки")
        for info in z.infolist():
            if info.is_dir() or "__MACOSX" in info.filename:
                continue
            out.append((info.filename, z.read(info)))
    return out


def _safe(name: str) -> str:
    """Basename only: an uploaded name never chooses a path on disk."""
    base = PurePosixPath(name.replace("\\", "/")).name
    return re.sub(r"[^\w.\-() а-яА-ЯёЁ]", "_", base) or "file"


def ingest(
    files: list[tuple[str, bytes]],
    out_root: Path | str,
    *,
    kinds: dict[str, str] | None = None,
    default_entries: Path | str | None = None,
    now: datetime | None = None,
) -> IngestReport:
    """Build `out_root/upload-<ts>` (and `compare-upload-<ts>` for a sim run) from `files`.

    `kinds` forces a kind for a file name (per-kind upload boxes); otherwise `classify` decides.
    History for the load model: uploaded entry workbooks, else `default_entries` (parquet),
    else synthetic mock entries.
    """
    ts = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    root = Path(out_root)
    run_dir = root / f"upload-{ts}"
    stage = run_dir / "_in"
    stage.mkdir(parents=True, exist_ok=True)
    rep = IngestReport(run_dir)
    kinds = kinds or {}

    by_kind: dict[str, list[tuple[str, Path]]] = {k: [] for k in KINDS}
    for name, data in expand(files):
        kind = kinds.get(name) or classify(name)
        if kind not in by_kind:
            rep.add(name, "info", "не используется приложением")
            continue
        sub = (
            stage
            / kind
            / ("explanations" if PurePosixPath(name).parent.name == "explanations" else "")
        )
        sub.mkdir(parents=True, exist_ok=True)
        path = sub / _safe(name)
        path.write_bytes(data)
        by_kind[kind].append((name, path))

    if not any(by_kind.values()):
        rep.add("", "error", "не найдено ни одного подходящего файла")
        return rep

    if by_kind["sim"]:
        _sim(by_kind["sim"], root / f"compare-upload-{ts}", rep)
    if (
        by_kind["forecast"]
        or by_kind["recommendation"]
        or by_kind["explanation"]
        or by_kind["entries"]
    ):
        _bundle(by_kind, _entries(by_kind["entries"], default_entries, rep), run_dir, rep)
    else:
        rep.run_dir = None  # only a sim run was uploaded: keep the current packages
        shutil.rmtree(run_dir, ignore_errors=True)
    return rep


def _entries(
    found: list[tuple[str, Path]], default: Path | str | None, rep: IngestReport
) -> pl.DataFrame:
    if found:
        try:
            df = load_workbooks([p for _, p in found], load_line(), load_holidays())
        except (ValueError, KeyError, OSError) as e:
            for n, _ in found:
                rep.add(n, "error", f"не прочитан: {e}")
        else:
            for n, _ in found:
                rep.add(n, "ok", "входы по станциям прочитаны")
            df.write_parquet(rep.run_dir / "_in" / "station_entries.parquet")
            return df
    if default is not None and Path(default).is_file():
        rep.add(str(default), "info", "история входов взята из ранее загруженных данных")
        return pl.read_parquet(default)
    rep.add("", "warning", "нет входов по станциям: используется синтетическая история (mock)")
    return mock.synthetic_entries(datetime(2026, 9, 1).date(), 28)


def _bundle(by_kind: dict, entries: pl.DataFrame, run_dir: Path, rep: IngestReport) -> None:
    fc = by_kind["forecast"][0][1] if by_kind["forecast"] else None
    for n, _ in by_kind["forecast"][1:]:
        rep.add(n, "warning", "лишний прогноз: используется первый файл")
    recs = _read_recs(by_kind["recommendation"], rep)
    exp = next((p for n, p in by_kind["explanation"] if p.parent.name != "explanations"), None)
    first = None
    if recs:
        first = run_dir / "_in" / "recommendation_first.json"
        first.write_text(
            json.dumps(recs[0].model_dump(mode="json"), ensure_ascii=False), encoding="utf-8"
        )
    real_entries = (run_dir / "_in" / "station_entries.parquet").is_file()
    statuses = build_team_bundle(
        run_dir, entries, forecast=fc, recommendation=first, explanation=exp
    )
    if real_entries:  # facts came from the organizers' workbooks, not from the mock generator
        ent = json.loads((run_dir / "entries.json").read_text(encoding="utf-8"))
        ent["data_mode"] = "real"
        ent["manifest"]["source"] = "organizers: 15-min entries workbooks"
        (run_dir / "entries.json").write_text(
            json.dumps(ent, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
    names = {"forecast": "прогноз", "recommendation": "рекомендация", "explanation": "объяснение"}
    for key, st in statuses.items():
        level = {"ok": "ok", "missing": "info", "error": "error"}.get(st.status, "info")
        text = st.reason or (
            "загружено" if st.status == "ok" else "не загружено: используется mock"
        )
        rep.add(names[key], level, text)
    if recs:
        as_of = json.loads((run_dir / "forecast.json").read_text(encoding="utf-8"))["payload"][
            "as_of"
        ]
        cut = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        keep = [r for r in recs if r.payload.as_of <= cut]
        if len(keep) < len(recs):
            rep.add(
                "рекомендации",
                "warning",
                f"{len(recs) - len(keep)} шт. позже момента прогноза, пропущены",
            )
        (run_dir / "recommendations.jsonl").write_text(
            "".join(json.dumps(r.model_dump(mode="json"), ensure_ascii=False) + "\n" for r in keep),
            encoding="utf-8",
        )
    for n, p in by_kind["explanation"]:
        if p.parent.name == "explanations":
            d = run_dir / "explanations"
            d.mkdir(exist_ok=True)
            base = PurePosixPath(n.replace("\\", "/")).name  # keep the id as given (may hold ":")
            if base in ("", ".", ".."):
                continue
            (d / base).write_bytes(p.read_bytes())
            rep.add(n, "ok", "объяснение к рекомендации " + p.stem)


def _read_recs(found: list[tuple[str, Path]], rep: IngestReport) -> list:
    out = []
    for name, path in found:
        lines = (
            path.read_text(encoding="utf-8").splitlines()
            if path.suffix.lower() == ".jsonl"
            else [None]
        )
        for i, line in enumerate(lines, 1):
            src = path
            if line is not None:
                if not line.strip():
                    continue
                src = path.with_name(f"{path.stem}_{i}.json")
                src.write_text(line, encoding="utf-8")
            try:
                out.append(read_recommendation(src))
            except (AdapterError, ValueError, OSError) as e:
                where = name if line is None else f"{name}, строка {i}"
                rep.add(where, "error", str(e))
    ids, uniq = set(), []
    for r in out:
        if r.payload.recommendation_id not in ids:
            ids.add(r.payload.recommendation_id)
            uniq.append(r)
    if uniq:
        rep.add("рекомендации", "ok", f"принято: {len(uniq)}")
    return uniq


def _sim(found: list[tuple[str, Path]], sim_dir: Path, rep: IngestReport) -> None:
    sim_dir.mkdir(parents=True, exist_ok=True)
    for _, p in found:
        (sim_dir / p.name).write_bytes(p.read_bytes())
    tl, reason = load_timeline(sim_dir)
    if tl is None:
        rep.add("симуляция", "error", f"запись не читается: {reason}")
        return
    rep.sim_dir = sim_dir
    rep.add("симуляция", "ok", f"прогон «{tl.scenario}» за {tl.date}, {tl.n_frames} кадров")
