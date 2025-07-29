from app.services.database import db

class Seccion(db.Model):
    __tablename__ = "secciones"
    codigo = db.Column(db.Text, primary_key=True)
    nombre = db.Column(db.Text, nullable=False)
