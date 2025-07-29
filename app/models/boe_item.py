from app.services.database import db
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime

class BOEItem(db.Model):
    __tablename__ = "boe_items"
    id = db.Column(db.Integer, primary_key=True)
    identificador = db.Column(db.Text, unique=True, nullable=False)
    clase_item = db.Column(db.Text)
    titulo = db.Column(db.Text)
    titulo_resumen = db.Column(db.Text)
    resumen = db.Column(db.Text)  # base64 gzip
    informe_impacto = db.Column(JSONB)
    identificador_boletin = db.Column(db.Text)
    seccion_codigo = db.Column(db.Text, db.ForeignKey("secciones.codigo"))
    departamento_codigo = db.Column(db.Text, db.ForeignKey("departamentos.codigo"))
    epigrafe = db.Column(db.Text)
    control = db.Column(db.Text)
    fecha_publicacion = db.Column(db.Date)
    likes = db.Column(db.Integer, default=0)
    dislikes = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    departamento = db.relationship("Departamento", backref="items")
    seccion = db.relationship("Seccion", backref="items")
