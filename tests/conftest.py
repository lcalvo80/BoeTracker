# tests/conftest.py
import os
import importlib
import pytest

"""
Intenta cargar la Flask app de dos formas:
1) mediante create_app(config) en app.__init__
2) mediante una variable global 'app' en app.__init__
Ajusta si tu layout difiere.
"""

def _load_flask_app():
    app_module = importlib.import_module("app")
    # 1) patrón factory: create_app(config)
    if hasattr(app_module, "create_app"):
        return app_module.create_app({
            "TESTING": True,
            "DEBUG": True,
            "DEBUG_FILTERS_ENABLED": True,  # habilita /_debug/echo y X-Debug-Filters
            "LOG_FILTERS": False,           # evita ruido en test
        })
    # 2) patrón app global: app = Flask(__name__)
    if hasattr(app_module, "app"):
        app = getattr(app_module, "app")
        app.config.update(
            TESTING=True,
            DEBUG=True,
            DEBUG_FILTERS_ENABLED=True,
            LOG_FILTERS=False,
        )
        return app
    raise RuntimeError("No se pudo instanciar la Flask app. Asegúrate de tener app.create_app o app.app")


@pytest.fixture(scope="session")
def app():
    app = _load_flask_app()
    # Asegura prefix correcto para items; ajusta si registras con otro blueprint/prefix
    # Por ejemplo si registras: app.register_blueprint(items.bp, url_prefix="/api/items")
    # los tests usan /api/items en las rutas.
    return app


@pytest.fixture()
def client(app):
    return app.test_client()
