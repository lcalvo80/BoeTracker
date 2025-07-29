from app.services.database import db

class Departamento(db.Model):
    __tablename__ = "departamentos"
    codigo = db.Column(db.Text, primary_key=True)
    nombre = db.Column(db.Text, nullable=False)
