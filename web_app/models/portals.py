"""SQLAlchemy models for portal mapping and session tracking."""

from sqlalchemy import Column, Integer, String, JSON, DateTime, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
import json

Base = declarative_base()


class Portal(Base):
    """Portales mapeados (EPS, empresas, etc)."""
    __tablename__ = "portales"
    
    id = Column(Integer, primary_key=True)
    nombre = Column(String(255), unique=True, nullable=False)  # ej: "EPS Sura"
    url = Column(String(512), nullable=False)
    estructura_json = Column(JSON, nullable=True)  # Formularios, botones, navegación
    login_url = Column(String(512), nullable=True)
    fecha_mapeo = Column(DateTime, default=datetime.utcnow)
    activo = Column(Boolean, default=True)
    
    def __repr__(self):
        return f"<Portal {self.nombre}>"


class Formulario(Base):
    """Formularios detectados en portales."""
    __tablename__ = "formularios"
    
    id = Column(Integer, primary_key=True)
    portal_id = Column(Integer, ForeignKey("portales.id"), nullable=False)
    nombre = Column(String(255))  # ej: "radicación de incapacidad"
    campos_json = Column(JSON)  # [{name, type, required, options}, ...]
    accion_url = Column(String(512), nullable=True)  # URL del form action
    fecha_descubierto = Column(DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f"<Formulario {self.nombre}>"


class Endpoint(Base):
    """APIs descubiertas en portales (interceptadas)."""
    __tablename__ = "endpoints"
    
    id = Column(Integer, primary_key=True)
    portal_id = Column(Integer, ForeignKey("portales.id"), nullable=False)
    metodo = Column(String(10), nullable=False)  # GET, POST, PUT, DELETE
    ruta = Column(String(512), nullable=False)  # /api/radicar, /api/submit
    parametros_json = Column(JSON, nullable=True)  # Parámetros esperados
    respuesta_json = Column(JSON, nullable=True)  # Estructura de respuesta
    fecha_descubierto = Column(DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f"<Endpoint {self.metodo} {self.ruta}>"


class SesionExitosa(Base):
    """Sesiones exitosas guardadas para reutilizar."""
    __tablename__ = "sesiones_exitosas"
    
    id = Column(Integer, primary_key=True)
    portal_id = Column(Integer, ForeignKey("portales.id"), nullable=False)
    pasos_exactos = Column(JSON)  # Secuencia de pasos que funcionó
    tiempo_total_segundos = Column(Integer)
    datos_entrada = Column(JSON)  # Qué datos se usaron
    resultado = Column(String(2048))  # Resultado final
    fecha = Column(DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f"<SesionExitosa {self.portal_id}>"
