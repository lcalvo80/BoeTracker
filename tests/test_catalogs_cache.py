# tests/test_catalogs_cache.py
import importlib
import types

def _mock_catalog(monkeypatch, func_name, data):
    items_route = importlib.import_module("app.routes.items")
    monkeypatch.setattr(items_route, func_name, lambda: data)

def test_departamentos_cache_header(client, monkeypatch):
    _mock_catalog(monkeypatch, "list_departamentos", ["Hacienda", "Justicia"])
    res = client.get("/api/items/departamentos")
    assert res.status_code == 200
    assert res.headers.get("Cache-Control", "").startswith("public")
    assert "max-age=3600" in res.headers.get("Cache-Control", "")
    assert res.get_json() == ["Hacienda", "Justicia"]

def test_secciones_cache_header(client, monkeypatch):
    _mock_catalog(monkeypatch, "list_secciones", ["I", "II"])
    res = client.get("/api/items/secciones")
    assert res.status_code == 200
    assert "max-age=3600" in res.headers.get("Cache-Control", "")
    assert res.get_json() == ["I", "II"]

def test_epigrafes_cache_header(client, monkeypatch):
    _mock_catalog(monkeypatch, "list_epigrafes", ["A", "B"])
    res = client.get("/api/items/epigrafes")
    assert res.status_code == 200
    assert "max-age=3600" in res.headers.get("Cache-Control", "")
    assert res.get_json() == ["A", "B"]
