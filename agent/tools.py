# agent/tools.py — Herramientas del agente Torre Fuerte
# Generado por AgentKit

import os
import re
import yaml
import logging
from datetime import datetime

logger = logging.getLogger("agentkit")

PLANOS_DIR = "knowledge/planos"
RENDERS_DIR = "knowledge/renders"

MAPA_PLANOS = {
    "301": "301.pdf",
    "302": "302.pdf",
    "303": "303.pdf",
    "401": "401.pdf",
    "402": "402.pdf",
    "501": "501.pdf",
    "502": "502.pdf",
    "601": "601.pdf",
    "602": "602.pdf",
    "603": "603.pdf",
    "701": "701.pdf",
    "702": "702.pdf",
    "801": "801.pdf",
    "802": "802.pdf",
    "901": "901.pdf",
    "902": "902.pdf",
    "903": "903.pdf",
    "1011": "1011.pdf",
    "1012": "1012.pdf",
    "ph1111": "PH1111.pdf",
    "ph1112": "PH1112.pdf",
    "1111": "PH1111.pdf",
    "1112": "PH1112.pdf",
    "todo": "torre-fuerte-Aptos-todo.pdf",
    "todos": "torre-fuerte-Aptos-todo.pdf",
}

# Carpeta de renders por clave normalizada
MAPA_RENDERS = {
    "401": "render-401",
    "penthouse": "penthouse-1111",
    "penthouse1111": "penthouse-1111",
    "ph1111": "penthouse-1111",
    "1111": "penthouse-1111",
}

# Extensiones de archivo que se envían como media
EXTENSIONES_MEDIA = {".jpg", ".jpeg", ".png", ".gif", ".mp4", ".pdf"}


def obtener_plano(codigo_apto: str) -> str | None:
    """Retorna la ruta al PDF de plano dado un código de apartamento."""
    codigo = codigo_apto.lower().replace("apto", "").replace("-", "").replace(" ", "").strip()
    archivo = MAPA_PLANOS.get(codigo)
    if not archivo:
        return None
    ruta = os.path.join(PLANOS_DIR, archivo)
    return ruta if os.path.exists(ruta) else None


def obtener_renders(clave: str) -> list[str]:
    """
    Retorna la lista de rutas de archivos de renders dado una clave.
    Incluye imágenes y videos. Retorna lista vacía si no existe.
    """
    clave_norm = clave.lower().replace("apto", "").replace("-", "").replace(" ", "").strip()
    carpeta = MAPA_RENDERS.get(clave_norm)
    if not carpeta:
        return []

    ruta_carpeta = os.path.join(RENDERS_DIR, carpeta)
    if not os.path.isdir(ruta_carpeta):
        return []

    archivos = sorted([
        os.path.join(ruta_carpeta, f)
        for f in os.listdir(ruta_carpeta)
        if not f.startswith(".") and os.path.splitext(f)[1].lower() in EXTENSIONES_MEDIA
    ])
    return archivos


def extraer_marcadores_plano(texto: str) -> tuple[str, list[str]]:
    """Extrae marcadores [PLANO:XXX] del texto de Claude."""
    patron = re.compile(r'\[PLANO:([^\]]+)\]', re.IGNORECASE)
    codigos = [m.group(1).strip() for m in patron.finditer(texto)]
    texto_limpio = patron.sub("", texto).strip()
    return texto_limpio, codigos


def extraer_marcadores_render(texto: str) -> tuple[str, list[str]]:
    """Extrae marcadores [RENDER:XXX] del texto de Claude."""
    patron = re.compile(r'\[RENDER:([^\]]+)\]', re.IGNORECASE)
    claves = [m.group(1).strip() for m in patron.finditer(texto)]
    texto_limpio = patron.sub("", texto).strip()
    return texto_limpio, claves


def cargar_info_negocio() -> dict:
    """Carga la información del negocio desde business.yaml."""
    try:
        with open("config/business.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        logger.error("config/business.yaml no encontrado")
        return {}


def obtener_disponibilidad() -> list[dict]:
    """Retorna la lista de apartamentos disponibles."""
    return [
        {"apto": "A-301", "nivel": 3, "area": 100, "hab": 2, "precio": 1087000000},
        {"apto": "B-302", "nivel": 3, "area": 134.87, "hab": 3, "precio": 1448161000},
        {"apto": "D-401", "nivel": 4, "area": 119, "hab": 3, "precio": 1288200000},
        {"apto": "E-402", "nivel": 4, "area": 187.61, "hab": 4, "precio": 1994883000},
        {"apto": "F-501", "nivel": 5, "area": 114.24, "hab": 3, "precio": 1242672000},
        {"apto": "G-502", "nivel": 5, "area": 159.58, "hab": 4, "precio": 1744674000},
        {"apto": "A'-601", "nivel": 6, "area": 98.87, "hab": 2, "precio": 1087861000},
        {"apto": "B'-602", "nivel": 6, "area": 139.53, "hab": 3, "precio": 1541350000},
        {"apto": "D-701", "nivel": 7, "area": 119, "hab": 3, "precio": 1298700000},
        {"apto": "E-702", "nivel": 7, "area": 187.96, "hab": 4, "precio": 2043988000},
        {"apto": "F-801", "nivel": 8, "area": 114.24, "hab": 3, "precio": 1253172000},
        {"apto": "G-802", "nivel": 8, "area": 159.58, "hab": 4, "precio": 1755174000},
        {"apto": "B-902", "nivel": 9, "area": 134.72, "hab": 3, "precio": 1467616000},
        {"apto": "D-1011", "nivel": 10, "area": 119, "hab": 3, "precio": 1309200000},
        {"apto": "E-1012", "nivel": 10, "area": 187.61, "hab": 4, "precio": 2050883000},
        {"apto": "PH-1111", "nivel": "11-12", "area": 247.06, "hab": 4, "precio": 3407898000},
        {"apto": "PH-1112", "nivel": "11-12", "area": 301.24, "hab": 4, "precio": 4128492000},
    ]


def registrar_lead(telefono: str, nombre: str, interes: str, presupuesto: str = "") -> dict:
    """Registra un lead interesado en el proyecto."""
    lead = {
        "telefono": telefono,
        "nombre": nombre,
        "interes": interes,
        "presupuesto": presupuesto,
        "fecha": datetime.now().isoformat(),
    }
    logger.info(f"Lead registrado: {lead}")
    return {"registrado": True}


def registrar_visita(telefono: str, nombre: str, fecha: str, hora: str) -> dict:
    """Registra una solicitud de visita al proyecto."""
    visita = {
        "telefono": telefono,
        "nombre": nombre,
        "fecha_visita": fecha,
        "hora_visita": hora,
        "registrado": datetime.now().isoformat(),
    }
    logger.info(f"Visita agendada: {visita}")
    return {
        "confirmado": True,
        "mensaje": f"Visita agendada para el {fecha} a las {hora}."
    }
