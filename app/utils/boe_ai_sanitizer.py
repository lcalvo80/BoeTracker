# app/utils/boe_ai_sanitizer.py
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, List


# Patrones típicos de cabecera/pie del BOE (muy repetidos por página)
_RE_DROP_LINE_PATTERNS: List[re.Pattern] = [
    # Cabecera BOE
    re.compile(r"^\s*BOLET[IÍ]N\s+OFICIAL\s+DEL\s+ESTADO\s*$", re.IGNORECASE),

    # "Núm. 23 Lunes 26 de enero de 2026 Sec. II.B. Pág. 12451 cve: BOE-A-2026-1836"
    # (a veces "Pág. 3320cve:" sin espacio)
    re.compile(
        r"^\s*N[uú]m\.\s*\d+\s+.*\s+Sec\.\s*.*\s+P[aá]g\.\s*\d+.*(cve:\s*BOE-.*)?\s*$",
        re.IGNORECASE,
    ),

    # Línea "cve: BOE-A-2026-1836" suelta
    re.compile(r"^\s*cve:\s*BOE-[A-Z]-\d{4}-\d+\s*$", re.IGNORECASE),

    # Verificable en / Verificable en la dirección
    re.compile(r"^\s*Verificable\s+en(\s+la\s+direcci[oó]n)?\s+https?://.*$", re.IGNORECASE),

    # URLs genéricas BOE (a menudo vienen solas en líneas)
    re.compile(r"^\s*https?://(www\.)?boe\.es\b.*$", re.IGNORECASE),

    # Extranet ARDE
    re.compile(r"^\s*https?://extranet\.boe\.es/arde\b.*$", re.IGNORECASE),

    # Depósito legal / ISSN (a veces vienen combinados en una línea)
    re.compile(r"^\s*D\.\s*L\.\s*:\s*.*$", re.IGNORECASE),
    re.compile(r"^\s*ISSN\s*:\s*\d{4}-\d{4}\s*$", re.IGNORECASE),
    re.compile(r"^\s*BOLET[IÍ]N\s+OFICIAL\s+DEL\s+ESTADO.*ISSN\s*:\s*\d{4}-\d{4}.*$", re.IGNORECASE),

    # IDs del anuncio (p.ej. "ID: A260002195-1")
    re.compile(r"^\s*ID:\s*[A-Z]\d{9}-\d+\s*$", re.IGNORECASE),

    # Líneas de maquetación
    re.compile(r"^\s*-{3,}\s*$"),
]

# Marcadores para filtro por frecuencia (conservador)
_FREQUENCY_MARKERS = (
    "boletín oficial del estado",
    "verificable en",
    "cve:",
    "issn",
    "d. l.:",
    "https://www.boe.es",
    "http://www.boe.es",
    "extranet.boe.es/arde",
    "d. l.: m-",
)

_RE_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_RE_ONLY_PAGE_NUMBER = re.compile(r"^\s*\d{3,6}\s*$")  # números sueltos típicos de página


def _normalize_lines(text: str) -> List[str]:
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    out: List[str] = []
    for raw in t.split("\n"):
        s = _RE_MULTI_SPACE.sub(" ", raw.strip())
        if not s:
            continue
        out.append(s)
    return out


def _matches_any_pattern(line: str, patterns: Iterable[re.Pattern]) -> bool:
    for p in patterns:
        if p.match(line):
            return True
    return False


def sanitize_for_ai(raw_text: str) -> str:
    """
    Elimina texto altamente repetitivo y de bajo valor semántico que es transversal
    en el BOE (cabeceras/pies, verificable/cve/issn/dl, URLs genéricas, IDs, etc.)
    para reducir caracteres antes de llamar a OpenAI.

    Conservador: NO intenta “entender” el contenido, solo quita boilerplate.
    """
    lines = _normalize_lines(raw_text)
    if not lines:
        return ""

    # 1) Drop por patrón
    kept: List[str] = []
    for ln in lines:
        if _matches_any_pattern(ln, _RE_DROP_LINE_PATTERNS):
            continue
        if _RE_ONLY_PAGE_NUMBER.match(ln):
            continue
        kept.append(ln)

    if not kept:
        return ""

    # 2) Deduplicado consecutivo (frecuente en extracción PDF)
    dedup: List[str] = []
    prev = None
    for ln in kept:
        if ln == prev:
            continue
        dedup.append(ln)
        prev = ln

    if not dedup:
        return ""

    # 3) Filtro por frecuencia SOLO si contiene marcadores muy característicos
    counts = Counter(dedup)
    final: List[str] = []
    for ln in dedup:
        low = ln.lower()
        if counts[ln] >= 2 and any(m in low for m in _FREQUENCY_MARKERS):
            continue
        final.append(ln)

    return "\n".join(final).strip()
