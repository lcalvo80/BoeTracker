-- schema.sql (PostgreSQL) actualizado

CREATE TABLE IF NOT EXISTS items (
    id SERIAL PRIMARY KEY,
    identificador TEXT UNIQUE NOT NULL,
    clase_item TEXT,
    titulo TEXT,
    titulo_resumen TEXT,
    departamento_nombre TEXT,
    epigrafe TEXT,
    seccion_nombre TEXT,
    control TEXT,
    fecha_publicacion DATE,
    resumen JSONB,
    informe_impacto JSONB,
    likes INTEGER DEFAULT 0,
    dislikes INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Auto-actualización de updated_at
CREATE OR REPLACE FUNCTION trigger_set_timestamp()
RETURNS TRIGGER AS $$
BEGIN
   NEW.updated_at = NOW();
   RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS set_timestamp ON items;

CREATE TRIGGER set_timestamp
BEFORE UPDATE ON items
FOR EACH ROW
EXECUTE PROCEDURE trigger_set_timestamp();

-- Índices para rendimiento
CREATE INDEX IF NOT EXISTS idx_identificador ON items(identificador);
CREATE INDEX IF NOT EXISTS idx_fecha ON items(fecha_publicacion);
CREATE INDEX IF NOT EXISTS idx_departamento ON items(departamento_nombre);
CREATE INDEX IF NOT EXISTS idx_epigrafe ON items(epigrafe);
CREATE INDEX IF NOT EXISTS idx_seccion ON items(seccion_nombre);

-- Tabla de comentarios
CREATE TABLE IF NOT EXISTS comments (
    id SERIAL PRIMARY KEY,
    item_identificador TEXT REFERENCES items(identificador) ON DELETE CASCADE,
    user_name TEXT NOT NULL,
    comment TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
