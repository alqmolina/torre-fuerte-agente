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

load_dotenv()

from agent.brain import generar_respuesta
from agent.memory import inicializar_db, guardar_mensaje, obtener_historial, guardar_lead, lead_existe, obtener_perfil_lead
from agent.tools import (
    extraer_marcadores_plano,
    extraer_marcadores_render,
    extraer_marcador_lead,
    obtener_plano,
    obtener_urls_renders,
    enviar_email_lead,
)

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
log_level = logging.DEBUG if ENVIRONMENT == "development" else logging.INFO
logging.basicConfig(level=log_level)
logger = logging.getLogger("agentkit")

PORT = int(os.getenv("PORT", 8000))
BASE_URL = os.getenv("BASE_URL", f"http://localhost:{PORT}")

# El proveedor se inicializa en lifespan, no al importar el módulo
proveedor = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global proveedor

    # Diagnóstico: mostrar variables de entorno disponibles (solo nombres, no valores secretos)
    env_keys = list(os.environ.keys())
    logger.info(f"Variables de entorno disponibles: {env_keys}")
    logger.info(f"WHATSAPP_PROVIDER = '{os.getenv('WHATSAPP_PROVIDER', 'NO DEFINIDO')}'")

    await inicializar_db()

    from agent.providers import obtener_proveedor
    proveedor = obtener_proveedor()

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
    return {
        "status": "ok",
        "service": "torre-fuerte-agente",
        "proveedor": proveedor.__class__.__name__ if proveedor else "no iniciado",
    }


@app.get("/debug")
async def debug():
    """Diagnóstico: muestra variables de entorno relevantes (sin valores secretos)."""
    return {
        "WHATSAPP_PROVIDER": os.getenv("WHATSAPP_PROVIDER", "NO CONFIGURADO"),
        "ENVIRONMENT": os.getenv("ENVIRONMENT", "NO CONFIGURADO"),
        "BASE_URL": os.getenv("BASE_URL", "NO CONFIGURADO"),
        "ANTHROPIC_API_KEY": "configurado" if os.getenv("ANTHROPIC_API_KEY") else "NO CONFIGURADO",
        "META_PHONE_NUMBER_ID": os.getenv("META_PHONE_NUMBER_ID", "NO CONFIGURADO"),
        "META_VERIFY_TOKEN": os.getenv("META_VERIFY_TOKEN", "NO CONFIGURADO"),
        "META_ACCESS_TOKEN": "configurado" if os.getenv("META_ACCESS_TOKEN") else "NO CONFIGURADO",
        "RESEND_API_KEY": "configurado" if os.getenv("RESEND_API_KEY") else "NO CONFIGURADO",
        "EMAIL_LEADS": os.getenv("EMAIL_LEADS", "NO CONFIGURADO"),
        "PORT": os.getenv("PORT", "NO CONFIGURADO"),
    }


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
            perfil = await obtener_perfil_lead(msg.telefono)
            respuesta_raw = await generar_respuesta(msg.texto, historial, perfil)

            texto_sin_planos, codigos_plano = extraer_marcadores_plano(respuesta_raw)
            texto_sin_renders, claves_render = extraer_marcadores_render(texto_sin_planos)
            texto_limpio, lead_data = extraer_marcador_lead(texto_sin_renders)

            await guardar_mensaje(msg.telefono, "user", msg.texto)
            await guardar_mensaje(msg.telefono, "assistant", texto_limpio)

            if texto_limpio:
                await proveedor.enviar_mensaje(msg.telefono, texto_limpio)

            for codigo in codigos_plano:
                ruta = obtener_plano(codigo)
                if ruta:
                    url = f"{BASE_URL}/planos/{os.path.basename(ruta)}"
                    await proveedor.enviar_media(msg.telefono, url, f"Plano apartamento {codigo.upper()}")
                    logger.info(f"Plano enviado: {url}")
                else:
                    await proveedor.enviar_mensaje(
                        msg.telefono,
                        f"Lo siento, no encontré el plano del apartamento {codigo}."
                    )

            for clave in claves_render:
                urls = obtener_urls_renders(clave, BASE_URL)
                if urls:
                    for url in urls:
                        await proveedor.enviar_media(msg.telefono, url)
                        logger.info(f"Render enviado: {url}")
                        await asyncio.sleep(0.5)
                else:
                    await proveedor.enviar_mensaje(msg.telefono, "Lo siento, no encontré renders para esa opción.")

            if lead_data and not await lead_existe(msg.telefono):
                await guardar_lead(
                    msg.telefono,
                    lead_data["nombre"],
                    lead_data.get("email", ""),
                    lead_data.get("apto", ""),
                    lead_data.get("habitaciones", ""),
                    lead_data.get("temperatura", ""),
                    lead_data.get("intencion", ""),
                )
                enviar_email_lead(
                    msg.telefono,
                    lead_data["nombre"],
                    lead_data.get("email", ""),
                    lead_data.get("apto", ""),
                    lead_data.get("habitaciones", ""),
                    lead_data.get("temperatura", ""),
                    lead_data.get("intencion", ""),
                )
                logger.info(f"Lead registrado: {lead_data['nombre']} ({msg.telefono})")

            logger.info(f"Respuesta enviada a {msg.telefono}")

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error en webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))
