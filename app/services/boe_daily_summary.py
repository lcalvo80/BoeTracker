# app/services/boe_daily_summary.py
from __future__ import annotations

"""Construcción de inputs para el Resumen Diario (por secciones) a partir del XML del BOE.

Diseño (MVP, robusto y barato en tokens):
- El XML del BOE puede ser enorme (sobre todo en secciones de oposiciones/anuncios).
- En lugar de pasar todo el XML o todo el texto, generamos señales compactas:
  * total de entradas por sección
  * distribución de entradas por departamento (top-N)
  * muestra determinista de títulos/identificadores (primero/último)

Luego, OpenAI genera 2–4 frases por sección apoyándose SOLO en estas señales.
"""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple
from xml.etree import ElementTree as ET

from app.services.lookup import normalize_code


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


def _safe_text(x: Optional[str]) -> str:
    return (x or "").strip()


def _iter_items_in_section(seccion: ET.Element) -> Iterable[Tuple[ET.Element, str, str]]:
    """Itera items en una <seccion> devolviendo (item_el, dept_name, epigrafe_name)."""
    for dept in seccion.findall("departamento"):
        dept_name = _safe_text(dept.get("nombre"))
        for ep in dept.findall("epigrafe"):
            ep_name = _safe_text(ep.get("nombre"))
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
        key = it.identificador.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _make_dept_counts(items: List[SectionItem], *, max_depts: int = 12) -> List[Tuple[str, int]]:
    counts: Dict[str, int] = {}
    for it in items:
        dept = (it.departamento or "").strip() or "(sin departamento)"
        counts[dept] = counts.get(dept, 0) + 1

    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    return ordered[: max(1, int(max_depts))]


def _make_sample(items: List[SectionItem], *, head: int = 14, tail: int = 6) -> List[SectionItem]:
    if not items:
        return []
    head = max(1, int(head))
    tail = max(0, int(tail))
    if len(items) <= head + tail:
        return items
    return items[:head] + items[-tail:]


def build_section_inputs(root: ET.Element) -> List[SectionInput]:
    """Convierte el XML (root) en una lista de SectionInput."""
    out: List[SectionInput] = []

    for seccion in root.findall(".//seccion"):
        code_raw = _safe_text(seccion.get("codigo"))
        name = _safe_text(seccion.get("nombre"))
        code = normalize_code(code_raw)
        if not code or not name:
            continue

        items: List[SectionItem] = []
        for item_el, dept_name, ep_name in _iter_items_in_section(seccion):
            ident = _safe_text(item_el.findtext("identificador"))
            titulo = _safe_text(item_el.findtext("titulo"))
            if not ident or not titulo:
                continue
            items.append(
                SectionItem(
                    identificador=ident,
                    titulo=titulo,
                    departamento=dept_name,
                    epigrafe=ep_name,
                )
            )

        items = _dedupe_by_ident(items)
        total = len(items)

        dept_counts = _make_dept_counts(items)
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
