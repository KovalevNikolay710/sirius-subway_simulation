"""Page "Данные": source statuses and uploads."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import streamlit as st

from metro_control.ingest import ingest
from metro_control.screen import source_statuses
from metro_control.timeline import load_timeline

ctx = st.session_state["ctx"]
sim_dir, ICONS, MOCK_ICON = ctx.sim_dir, ctx.icons, ctx.mock_icon

UPLOAD_KINDS = {
    "entries": ("Входы по станциям (Excel)", ["xlsx"]),
    "forecast": ("Прогноз (json, csv, parquet)", ["json", "csv", "parquet"]),
    "recommendation": ("Рекомендации (json, jsonl)", ["json", "jsonl"]),
    "explanation": ("Объяснения (txt)", ["txt"]),
    "sim": ("Запись симуляции (json, jsonl)", ["json", "jsonl"]),
}


def sim_as_of() -> datetime | None:
    """17:30 MSK of the simulated day, so the forecast tab shows the same day as the simulator."""
    tl = load_timeline(sim_dir)[0] if sim_dir is not None else None
    if tl is None:
        return None
    return datetime.fromisoformat(f"{tl.date}T17:30:00+03:00")


def data_tab() -> None:
    overridden = st.session_state.get("run_dir") or st.session_state.get("sim_dir")
    if overridden and st.button("Вернуться к демо-данным", icon=":material/undo:"):
        st.session_state.pop("run_dir", None)
        st.session_state.pop("sim_dir", None)
        st.session_state.pop("ingest_notes", None)
        st.rerun()

    with st.container(border=True):
        st.subheader("ZIP-архив")
        archive = st.file_uploader(
            "Архив", type=["zip"], key="up_zip", label_visibility="collapsed"
        )
    singles: dict[str, list] = {}
    with st.container(border=True):
        st.subheader("Файлы по отдельности")
        cols = st.columns(2)
        for n, (kind, (label, types)) in enumerate(UPLOAD_KINDS.items()):
            singles[kind] = cols[n % 2].file_uploader(
                label, type=types, accept_multiple_files=True, key=f"up_{kind}"
            )
    if st.button("Загрузить и показать", type="primary", icon=":material/upload:"):
        files, kinds = [], {}
        if archive is not None:
            files.append((archive.name, archive.getvalue()))
        for kind, items in singles.items():
            for f in items or []:
                name = f.name
                if kind == "explanation" and not name.lower().startswith("explanation"):
                    name = f"explanations/{name}"  # per-recommendation text, named by its id
                files.append((name, f.getvalue()))
                kinds[name] = kind
        if not files:
            st.warning("Выберите архив или файлы.")
        else:
            try:
                with st.spinner("Разбираю файлы…"):
                    rep = ingest(
                        files,
                        Path("runs"),
                        kinds=kinds,
                        default_entries=Path("data/processed/station_entries.parquet"),
                        as_of=sim_as_of(),
                    )
            except ValueError as e:
                st.error(str(e))
            else:
                if rep.run_dir is not None and (rep.run_dir / "load.json").is_file():
                    st.session_state["run_dir"] = str(rep.run_dir)
                if rep.sim_dir is not None:
                    st.session_state["sim_dir"] = str(rep.sim_dir)
                st.session_state["ingest_notes"] = [(n.level, n.file, n.text) for n in rep.notes]
                st.rerun()
    notes = st.session_state.get("ingest_notes")
    if notes:
        st.markdown("**Результат загрузки**")
        for level, file, text in notes:
            where = f"`{file.split('/')[-1]}`: " if file else ""
            st.markdown(f"{ICONS.get(level, '')} {where}{text}")


with st.container(border=True):
    st.subheader("Источники данных")
    if ctx.bundle is not None:
        for st_ in source_statuses(ctx.run_dir, ctx.bundle):
            mock = st_.level == "warning" and st_.text.endswith(": mock")
            st.markdown(f"{MOCK_ICON if mock else ICONS[st_.level]} {st_.text}")
    st.caption(f"Данные: {ctx.run_dir}")
    st.caption(f"Симуляция: {sim_dir if sim_dir is not None else 'нет записи'}")
data_tab()
