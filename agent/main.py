# agent/main.py — Servidor FastAPI + Webhook de WhatsApp
# Generado por AgentKit

import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from agent.brain import generar_respuesta
from agent.memory import inicializar_db, guardar_mensaje, obtener_historial
from agent.providers import obtener_proveedor
from agent.tools import (
    extraer_marcadores_plano,
    extraer_marcadores_render,
    obtener_plano,
    obtener_renders,
)

load_dotenv()

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
log_level = logging.DEBUG if ENVIRONMENT == "development" else logging.INFO
logging.basicConfig(level=log_level)
logger = logging.getLogger("agentkit")

proveedor = obtener_proveedor()
PORT = int(os.getenv("PORT", 8000))
BASE_URL = os.getenv("BASE_URL", f"http://localhost:{PORT}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa la base de datos al arrancar el servidor."""
    await inicializar_db()
    logger.info("Base de datos inicializada")
    logger.info(f"Servidor corriendo en puerto {PORT}")
    logger.info(f"Proveedor: {proveedor.__class__.__name__}")
    logger.info(f"BASE_URL: {BASE_URL}")
    yield


app = FastAPI(
    title="Torre Fuerte — Agente WhatsApp",
    version="1.0.0",
    lifespan=lifespan
)

if os.path.exists("knowledge/planos"):
    app.mount("/planos", StaticFiles(directory="knowledge/planos"), name="planos")

if os.path.exists("knowledge/renders"):
    app.mount("/renders", StaticFiles(directory="knowledge/renders"), name="renders")


@app.get("/")
async def health_check():
    return {"status": "ok", "service": "torre-fuerte-agente"}


@app.get("/webhook")
async def webhook_verificacion(request: Request):
    resultado = await proveedor.validar_webhook(request)
    if resultado is not None:
        return PlainTextResponse(str(resultado))
    return {"status": "ok"}


@app.post("/webhook")
async def webhook_handler(request: Request):
    """
    Recibe mensajes de WhatsApp via Twilio.
    Procesa respuesta de Claude y envía texto + planos + renders según corresponda.
    """
    try:
        mensajes = await proveedor.parsear_webhook(request)

        for msg in mensajes:
            if msg.es_propio or not msg.texto:
                continue

            logger.info(f"Mensaje de {msg.telefono}: {msg.texto}")

            historial = await obtener_historial(msg.telefono)
            respuesta_raw = await generar_respuesta(msg.texto, historial)

            # Extraer marcadores de planos y renders
            texto_sin_planos, codigos_plano = extraer_marcadores_plano(respuesta_raw)
            texto_limpio, claves_render = extraer_marcadores_render(texto_sin_planos)

            await guardar_mensaje(msg.telefono, "user", msg.texto)
            await guardar_mensaje(msg.telefono, "assistant", texto_limpio)

            # Enviar respuesta de texto
            if texto_limpio:
                await proveedor.enviar_mensaje(msg.telefono, texto_limpio)

            # Enviar planos
            for codigo in codigos_plano:
                ruta = obtener_plano(codigo)
                if ruta:
                    url = f"{BASE_URL}/planos/{os.path.basename(ruta)}"
                    await proveedor.enviar_media(msg.telefono, url, f"Plano apartamento {codigo.upper()}")
                    logger.info(f"Plano enviado: {url}")
                else:
                    await proveedor.enviar_mensaje(
                        msg.telefono,
                        f"Lo siento, no encontré el plano del apartamento {codigo}. Le recomendamos contactar a nuestro equipo de ventas."
                    )

            # Enviar renders (imagen por imagen con pausa para no saturar Twilio)
            for clave in claves_render:
                archivos = obtener_renders(clave)
                if archivos:
                    for ruta_archivo in archivos:
                        # Construir URL relativa a /renders
                        ruta_relativa = os.path.relpath(ruta_archivo, "knowledge/renders")
                        url = f"{BASE_URL}/renders/{ruta_relativa}"
                        await proveedor.enviar_media(msg.telefono, url)
                        logger.info(f"Render enviado: {url}")
                        await asyncio.sleep(0.5)  # pausa entre envíos para no saturar la API
                else:
                    await proveedor.enviar_mensaje(
                        msg.telefono,
                        "Lo siento, no encontré renders para esa opción."
                    )

            logger.info(f"Respuesta enviada a {msg.telefono}")

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error en webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))
