# tests/test_items_filters.py
import json
import importlib
import types

"""
Estos tests NO tocan la BD.
Monkeypatcheamos get_filtered_items para capturar el payload
que le llega desde la ruta y devolvemos una respuesta dummy.
"""

def _install_fake_controller(monkeypatch, response=None, capture=None):
    """
    Reemplaza get_filtered_items en app.routes.items por un stub que:
    - guarda el payload en capture["payload"]
    - devuelve 'response' o un default
    """
    items_route = importlib.import_module("app.routes.items")

    def fake_get_filtered_items(payload):
        if capture is not None:
            capture["payload"] = payload
        return response or {
            "items": [],
            "page": payload.get("page", 1),
            "limit": payload.get("limit", 12),
            "total": 0,
            "pages": 0,
            "sort_by": payload.get("sort_by", "created_at"),
            "sort_dir": payload.get("sort_dir", "desc"),
        }

    monkeypatch.setattr(items_route, "get_filtered_items", fake_get_filtered_items)


def test_echo_parser(client, app):
    # Requiere DEBUG_FILTERS_ENABLED=True (lo activamos en conftest)
    res = client.get(
        "/api/items/_debug/echo"
        "?departamentos=Hacienda,Justicia"
        "&secciones=I&secciones=II"
        "&tags=a&tags=b"
        "&page=2&limit=50&sort_by=fecha&sort_dir=asc"
        "&has_resumen=true"
        "&fecha_desde=2024-01-01"
        "&fecha_hasta=31-12-2024"
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data.get("_debug") is True
    pf = data["parsed_filters"]

    assert pf["departamentos"] == ["Hacienda", "Justicia"]
    assert pf["secciones"] == ["I", "II"]
    assert pf["tags"] == ["a", "b"]
    assert pf["page"] == 2
    assert pf["limit"] == 50
    assert pf["sort_by"] == "fecha"
    assert pf["sort_dir"] == "asc"
    assert pf["has_resumen"] is True
    assert pf["fecha_desde"] == "2024-01-01"
    assert pf["fecha_hasta"] == "2024-12-31"


def test_items_xdebug_header_calls_controller_with_sane_payload(client, app, monkeypatch):
    capture = {}
    _install_fake_controller(monkeypatch, capture=capture)

    # sort_by y sort_dir inválidos deben sanearse a defaults
    res = client.get(
        "/api/items?ids=BOE-A-1,BOE-A-2&sort_by=unknown&sort_dir=UP",
        headers={"X-Debug-Filters": "1"},
    )
    assert res.status_code == 200
    assert "payload" in capture
    payload = capture["payload"]

    assert payload["ids"] == ["BOE-A-1", "BOE-A-2"]
    assert payload["sort_by"] == "created_at"  # saneado
    assert payload["sort_dir"] == "desc"       # saneado
    assert payload["page"] == 1
    assert payload["limit"] == 12


def test_items_supports_multiple_encodings_for_arrays(client, app, monkeypatch):
    capture = {}
    _install_fake_controller(monkeypatch, capture=capture)

    # mezcla: repetidas, [] y coma-separado
    res = client.get(
        "/api/items?"
        "departamentos[]=Hacienda&departamentos[]=Justicia"
        "&secciones=I,II"
        "&tags=x&tags=y"
        "&departamentos=Economia"  # repetida sin []
    )
    assert res.status_code == 200
    payload = capture["payload"]

    # El parser fusiona y deduplica manteniendo orden de llegada
    assert payload["departamentos"] == ["Hacienda", "Justicia", "Economia"]
    assert payload["secciones"] == ["I", "II"]
    assert payload["tags"] == ["x", "y"]


def test_items_normalizes_flags_and_dates(client, app, monkeypatch):
    capture = {}
    _install_fake_controller(monkeypatch, capture=capture)

    res = client.get(
        "/api/items?"
        "has_resumen=yes&has_impacto=0&has_comments=on&destacado=false"
        "&fecha_desde=01-01-2024&fecha_hasta=2024/12/31"
    )
    assert res.status_code == 200
    payload = capture["payload"]

    assert payload["has_resumen"] is True
    assert payload["has_impacto"] is False
    assert payload["has_comments"] is True
    assert payload["destacado"] is False

    # fechas normalizadas a YYYY-MM-DD
    assert payload["fecha_desde"] == "2024-01-01"
    assert payload["fecha_hasta"] == "2024-12-31"


def test_items_pagination_bounds(client, app, monkeypatch):
    capture = {}
    _install_fake_controller(monkeypatch, capture=capture)

    # limit por debajo y por encima de los límites se sanean
    res = client.get("/api/items?page=-5&limit=1000")
    assert res.status_code == 200
    payload = capture["payload"]
    assert payload["page"] == 1
    assert payload["limit"] == 100  # tope superior definido en la ruta


def test_items_response_shape_when_controller_raises(client, app, monkeypatch):
    # Simula una excepción en el controller para verificar respuesta estable
    def raising_controller(_payload):
        raise RuntimeError("boom")

    items_route = importlib.import_module("app.routes.items")
    monkeypatch.setattr(items_route, "get_filtered_items", raising_controller)

    res = client.get("/api/items?page=3&limit=24&sort_by=titulo&sort_dir=asc")
    assert res.status_code == 200
    data = res.get_json()

    assert data["items"] == []
    assert data["page"] == 3
    assert data["limit"] == 24
    assert data["total"] == 0
    assert data["pages"] == 0
    assert data["sort_by"] == "titulo"      # mapeado válido
    assert data["sort_dir"] == "asc"
