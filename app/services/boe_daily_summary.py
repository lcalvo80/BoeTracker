# app/services/boe_daily_summary.py
from __future__ import annotations

"""Construcción de inputs para el Resumen Diario (por secciones) a partir del XML del BOE.

Mejoras v2 (lectura + calidad IA sin más tokens):
- Normaliza whitespace (evita saltos/duplicados en títulos).
- Extrae texto con itertext() (robusto si <titulo> contiene subnodos).
- Muestra determinista más representativa:
  * head + tail
  * mid (posiciones repartidas)
  * 1 por top-departamentos (si hay muchos)
"""

import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple
from xml.etree import ElementTree as ET

from app.services.lookup import normalize_code


# Defaults de muestreo (ajustables por env)
_SAMPLE_HEAD = int(os.getenv("DAILY_SUMMARY_SAMPLE_HEAD", "14"))
_SAMPLE_TAIL = int(os.getenv("DAILY_SUMMARY_SAMPLE_TAIL", "6"))
_SAMPLE_MID = int(os.getenv("DAILY_SUMMARY_SAMPLE_MID", "4"))
_SAMPLE_DEPT = int(os.getenv("DAILY_SUMMARY_SAMPLE_PER_TOP_DEPT", "4"))  # nº de depts top a cubrir (1 item c/u)
_SAMPLE_MAX = int(os.getenv("DAILY_SUMMARY_SAMPLE_MAX", "28"))
_DEPT_MAX = int(os.getenv("DAILY_SUMMARY_DEPT_MAX", "12"))


@dataclass(frozen=True)
class SectionItem:
    identificador: str
    titulo: str
    departamento: str = ""
    epigrafe: str = ""


@dataclass(frozen=True)
class SectionInput:
    seccion_codigo: str
    seccion_nombre: str
    total_entradas: int
    dept_counts: List[Tuple[str, int]]
    sample_items: List[SectionItem]


_WS_RE = re.compile(r"\s+")


def _collapse_ws(s: Optional[str]) -> str:
    """Trim + colapsa whitespace en un único espacio."""
    return _WS_RE.sub(" ", (s or "").strip())


def _safe_attr(el: ET.Element, key: str) -> str:
    return _collapse_ws(el.get(key) if el is not None else "")


def _findtext_full(parent: ET.Element, tag: str) -> str:
    """Texto robusto (incluye subnodos) para <tag>...</tag>."""
    el = parent.find(tag)
    if el is None:
        return ""
    txt = "".join(el.itertext())
    return _collapse_ws(txt)


def _iter_items_in_section(seccion: ET.Element) -> Iterable[Tuple[ET.Element, str, str]]:
    """Itera items en una <seccion> devolviendo (item_el, dept_name, epigrafe_name)."""
    for dept in seccion.findall("departamento"):
        dept_name = _safe_attr(dept, "nombre")
        for ep in dept.findall("epigrafe"):
            ep_name = _safe_attr(ep, "nombre")
            for item in ep.findall("item"):
                yield item, dept_name, ep_name
        for item in dept.findall("item"):
            yield item, dept_name, ""

    # items huérfanos directamente bajo sección
    for item in seccion.findall("item"):
        yield item, "", ""


def _dedupe_by_ident(items: List[SectionItem]) -> List[SectionItem]:
    seen = set()
    out: List[SectionItem] = []
    for it in items:
        key = (it.identificador or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _make_dept_counts(items: List[SectionItem], *, max_depts: int = _DEPT_MAX) -> List[Tuple[str, int]]:
    counts: Dict[str, int] = {}
    for it in items:
        dept = _collapse_ws(it.departamento) or "(sin departamento)"
        counts[dept] = counts.get(dept, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    return ordered[: max(1, int(max_depts))]


def _pick_mid_indexes(n: int, k: int) -> List[int]:
    """Devuelve k índices en el tramo medio, repartidos (determinista)."""
    if n <= 0 or k <= 0:
        return []
    # evitamos primeras/últimas 10% para no solapar con head/tail
    start = max(0, int(n * 0.10))
    end = min(n - 1, int(n * 0.90))
    if end <= start:
        return []
    span = end - start + 1
    if span <= k:
        return list(range(start, end + 1))
    step = span / float(k + 1)
    idxs = []
    for i in range(1, k + 1):
        idx = int(round(start + i * step))
        idx = max(start, min(end, idx))
        idxs.append(idx)
    # dedupe manteniendo orden
    out = []
    seen = set()
    for x in idxs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _make_sample(
    items: List[SectionItem],
    *,
    head: int = _SAMPLE_HEAD,
    tail: int = _SAMPLE_TAIL,
    mid: int = _SAMPLE_MID,
    top_depts: int = _SAMPLE_DEPT,
    max_items: int = _SAMPLE_MAX,
) -> List[SectionItem]:
    """Muestra determinista: head+tail+mid+1 por top departamento (si aplica)."""
    if not items:
        return []

    head = max(1, int(head))
    tail = max(0, int(tail))
    mid = max(0, int(mid))
    top_depts = max(0, int(top_depts))
    max_items = max(6, int(max_items))

    n = len(items)
    if n <= max_items:
        return items

    chosen: List[SectionItem] = []
    seen_id: set[str] = set()

    def add(it: SectionItem):
        ident = (it.identificador or "").strip()
        if not ident or ident in seen_id:
            return
        seen_id.add(ident)
        chosen.append(it)

    # 1) Head
    for it in items[:head]:
        add(it)

    # 2) Tail
    if tail > 0:
        for it in items[-tail:]:
            add(it)

    # 3) Mid (repartido)
    for idx in _pick_mid_indexes(n, mid):
        add(items[idx])

    # 4) 1 por top departamento (primera ocurrencia)
    if top_depts > 0:
        dept_counts = _make_dept_counts(items, max_depts=max(1, top_depts))
        top_dept_names = [d for d, _ in dept_counts if d]
        if top_dept_names:
            for dept_name in top_dept_names:
                for it in items:
                    if _collapse_ws(it.departamento) == _collapse_ws(dept_name):
                        add(it)
                        break

    # Si nos pasamos, recortamos manteniendo orden determinista (sin partir títulos aquí)
    if len(chosen) > max_items:
        chosen = chosen[:max_items]

    # Si nos quedamos cortos (muy raro), rellenamos con items desde el principio
    if len(chosen) < min(10, max_items):
        for it in items:
            add(it)
            if len(chosen) >= min(10, max_items):
                break

    return chosen


def build_section_inputs(root: ET.Element) -> List[SectionInput]:
    """Convierte el XML (root) en una lista de SectionInput."""
    out: List[SectionInput] = []

    for seccion in root.findall(".//seccion"):
        code_raw = _safe_attr(seccion, "codigo")
        name = _safe_attr(seccion, "nombre")
        code = normalize_code(code_raw)
        if not code or not name:
            continue

        items: List[SectionItem] = []
        for item_el, dept_name, ep_name in _iter_items_in_section(seccion):
            ident = _findtext_full(item_el, "identificador")
            titulo = _findtext_full(item_el, "titulo")
            if not ident or not titulo:
                continue
            items.append(
                SectionItem(
                    identificador=ident,
                    titulo=titulo,
                    departamento=_collapse_ws(dept_name),
                    epigrafe=_collapse_ws(ep_name),
                )
            )

        items = _dedupe_by_ident(items)
        total = len(items)

        dept_counts = _make_dept_counts(items, max_depts=_DEPT_MAX)
        sample_items = _make_sample(items)

        out.append(
            SectionInput(
                seccion_codigo=code,
                seccion_nombre=name,
                total_entradas=total,
                dept_counts=dept_counts,
                sample_items=sample_items,
            )
        )

    # Orden determinista por código (texto)
    out.sort(key=lambda s: (s.seccion_codigo.lower(), s.seccion_nombre.lower()))
    return out
