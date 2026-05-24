# agent/main.py — Servidor FastAPI + Webhook de WhatsApp
# Generado por AgentKit

import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

load_dotenv()

from agent.brain import generar_respuesta, generar_resumen_handoff
from agent.memory import (
    inicializar_db, guardar_mensaje, obtener_historial,
    guardar_lead, lead_existe, obtener_perfil_lead,
    guardar_idioma, obtener_idioma, obtener_todos_los_leads,
    activar_handoff, desactivar_handoff, esta_en_handoff,
    debe_enviar_aviso_handoff, registrar_aviso_handoff,
    obtener_leads_pendientes_handoff,
    mensaje_ya_procesado, marcar_mensaje_procesado,
    obtener_leads_para_seguimiento, registrar_seguimiento,
    guardar_visita,
    obtener_visitas_para_recordatorio, marcar_recordatorio,
)
import agent.admin as admin_module
from agent.tools import (
    extraer_marcadores_plano,
    extraer_marcadores_render,
    extraer_marcador_lead,
    extraer_marcador_handoff,
    extraer_marcador_visita,
    obtener_plano,
    obtener_urls_renders,
    enviar_email_lead,
    enviar_email_handoff,
    exportar_leads_excel,
)

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
log_level = logging.DEBUG if ENVIRONMENT == "development" else logging.INFO
logging.basicConfig(level=log_level)
logger = logging.getLogger("agentkit")

PORT = int(os.getenv("PORT", 8000))
BASE_URL = os.getenv("BASE_URL", f"http://localhost:{PORT}")
ASESOR_WHATSAPP = os.getenv("ASESOR_WHATSAPP", "")

_EN_WORDS = {
    "the", "is", "are", "i", "you", "what", "how", "want", "need", "have",
    "can", "hello", "hi", "thanks", "please", "apartment", "price", "bedroom",
    "looking", "interested", "information", "good", "morning", "evening",
    "night", "day", "help", "penthouse", "floor", "available", "send", "show",
}
_ES_WORDS = {
    "el", "la", "los", "las", "es", "son", "yo", "que", "qué", "cómo", "como",
    "quiero", "necesito", "hola", "gracias", "por", "favor", "apartamento",
    "precio", "habitación", "habitaciones", "busco", "buenas", "buenos",
    "días", "tardes", "noches", "información", "me", "con", "del", "para",
}


def _detectar_idioma(texto: str) -> str:
    words = set(texto.lower().split())
    en = len(words & _EN_WORDS)
    es = len(words & _ES_WORDS)
    return "en" if en > es else "es"

# El proveedor se inicializa en lifespan, no al importar el módulo
proveedor = None


async def _notificar_handoff(telefono: str, nombre: str, temperatura: str, razon: str, idioma: str, apto: str = "", habitaciones: str = "", email_lead: str = "", intencion: str = "", resumen: str = "") -> None:
    """Envía notificación de handoff al asesor por email y WhatsApp (si está configurado)."""
    enviar_email_handoff(telefono, nombre, temperatura, razon, apto, habitaciones, email_lead, intencion, resumen)

    if ASESOR_WHATSAPP and proveedor:
        from agent.tools import ICONOS_TEMPERATURA
        icono = ICONOS_TEMPERATURA.get(temperatura.lower(), "🔔")
        msg_asesor = (
            f"🔔 *TRANSFERENCIA A ASESOR*\n\n"
            f"📱 Tel: {telefono}\n"
            f"👤 Nombre: {nombre or 'No indicado'}\n"
            f"🏠 Apto: {apto or 'No especificado'}\n"
            f"💼 Intención: {intencion or 'No especificada'}\n"
            f"🌡️ Temperatura: {icono} {temperatura.upper()}\n"
            f"📋 Razón: {razon}\n\n"
            f"Responde desde Meta Business Suite:\nbusiness.facebook.com"
        )
        try:
            await proveedor.enviar_mensaje(ASESOR_WHATSAPP, msg_asesor)
        except Exception as e:
            logger.warning(f"No se pudo enviar WhatsApp al asesor: {e}")


def _mensaje_seguimiento(nombre: str, temperatura: str, numero: int, idioma: str) -> str:
    """Compone el mensaje de seguimiento según temperatura, número y idioma."""
    temp = temperatura.lower().replace("í", "i")
    n = nombre if nombre and nombre != "Desconocido" else ""
    s_es = f"¡Hola{' ' + n if n else ''}!"
    s_en = f"Hi{' ' + n if n else ''}!"

    msgs: dict = {
        "es": {
            "caliente": {
                1: f"{s_es} 👋 Solo quería hacer seguimiento a tu consulta sobre Torre Fuerte Apartamentos. ¿Pudiste revisar la información que te compartí? Estoy aquí para resolver cualquier duda 🏠✨",
                2: f"{s_es} 😊 Los apartamentos en Torre Fuerte tienen disponibilidad limitada. ¿Te gustaría agendar una visita al proyecto o hablar con uno de nuestros asesores?",
            },
            "tibio": {
                1: f"{s_es} 👋 Quería recordarte que en Torre Fuerte Apartamentos seguimos disponibles para ayudarte. ¿Tienes alguna pregunta pendiente sobre el proyecto? 🏠",
                2: f"{s_es} 😊 Este es nuestro último mensaje de seguimiento. Si decides retomar tu búsqueda de apartamento en Laureles, estaremos encantados de ayudarte. ¡Que tengas un excelente día!",
            },
            "frio": {
                1: f"{s_es} 👋 Vimos que estuviste conociendo Torre Fuerte Apartamentos en Laureles, Medellín. Si tienes alguna pregunta o quieres más información, con gusto te ayudamos 🏠",
                2: f"{s_es} 😊 Este es nuestro último mensaje. Si en algún momento retomas tu búsqueda de apartamento en Medellín, Torre Fuerte tiene opciones únicas en el corazón de Laureles. ¡Mucho éxito!",
            },
        },
        "en": {
            "caliente": {
                1: f"{s_en} 👋 Just following up on your inquiry about Torre Fuerte Apartments. Did you get a chance to review the information I shared? I'm here to answer any questions 🏠✨",
                2: f"{s_en} 😊 Our apartments have limited availability. Would you like to schedule a visit to the project or speak with one of our advisors?",
            },
            "tibio": {
                1: f"{s_en} 👋 Just a reminder that we're here to help at Torre Fuerte Apartments. Do you have any pending questions about the project or available units? 🏠",
                2: f"{s_en} 😊 This is our last follow-up message. If you ever decide to resume your apartment search in Laureles, we'd love to hear from you. Have a great day!",
            },
            "frio": {
                1: f"{s_en} 👋 We noticed you were exploring Torre Fuerte Apartments in Laureles, Medellín. If you have any questions or would like more information, we're happy to help 🏠",
                2: f"{s_en} 😊 This is our last message. If you ever resume your apartment search in Medellín, Torre Fuerte has unique options in the heart of Laureles. Best of luck!",
            },
        },
    }
    lang_msgs = msgs.get("en" if idioma == "en" else "es", msgs["es"])
    temp_msgs = lang_msgs.get(temp, lang_msgs.get("frio", {}))
    return temp_msgs.get(numero, f"{s_es} 👋 Solo quería hacer seguimiento a tu consulta sobre Torre Fuerte. ¡Seguimos disponibles para ayudarte! 🏠")


async def _tarea_seguimiento_leads() -> None:
    """Tarea background: envía mensajes de seguimiento a leads inactivos según su temperatura."""
    await asyncio.sleep(150)  # esperar arranque completo
    while True:
        try:
            leads = await obtener_leads_para_seguimiento()
            for item in leads:
                telefono = item["telefono"]
                msg = _mensaje_seguimiento(
                    item["nombre"], item["temperatura"], item["numero"], item.get("idioma", "es")
                )
                if proveedor:
                    await proveedor.enviar_mensaje(telefono, msg)
                await guardar_mensaje(telefono, "assistant", msg)
                await registrar_seguimiento(telefono, item["numero"])
                logger.info(f"Seguimiento #{item['numero']} enviado a {item['nombre']} ({telefono})")
        except Exception as e:
            logger.error(f"Error en tarea seguimiento: {e}")
        await asyncio.sleep(1800)  # revisar cada 30 minutos


async def _tarea_handoff_inactivos() -> None:
    """Tarea background: revisa cada 2 minutos si hay leads tibio/caliente inactivos 20+ min."""
    await asyncio.sleep(90)  # esperar que el servidor arranque completamente
    while True:
        try:
            pendientes = await obtener_leads_pendientes_handoff(minutos=20)
            for lead in pendientes:
                telefono = lead["telefono"]
                nombre = lead.get("nombre", "")
                temperatura = lead.get("temperatura", "")
                idioma = await obtener_idioma(telefono) or "es"

                historial_in = await obtener_historial(telefono, limite=30)
                resumen_in = await generar_resumen_handoff(historial_in, nombre, temperatura, idioma)
                await activar_handoff(telefono, "inactividad 20 minutos", nombre, resumen_in)

                if idioma == "en":
                    msg_lead = (
                        f"Hi {nombre}! 👋 A Torre Fuerte advisor will reach out to you shortly "
                        f"to continue with your inquiry. 🏠✨"
                    )
                else:
                    msg_lead = (
                        f"¡Hola {nombre}! 👋 Un asesor de Torre Fuerte se pondrá en contacto "
                        f"contigo muy pronto para continuar con tu consulta. 🏠✨"
                    )

                if proveedor:
                    await proveedor.enviar_mensaje(telefono, msg_lead)
                    await registrar_aviso_handoff(telefono)

                await _notificar_handoff(
                    telefono, nombre, temperatura, "inactividad 20 minutos", idioma,
                    lead.get("apto", ""), lead.get("habitaciones", ""), lead.get("email", ""),
                    lead.get("intencion", ""), resumen_in,
                )
                logger.info(f"Handoff automático por inactividad: {nombre} ({telefono})")

        except Exception as e:
            logger.error(f"Error en tarea handoff inactivos: {e}")

        await asyncio.sleep(120)  # revisar cada 2 minutos


_MESES_ES = ["enero","febrero","marzo","abril","mayo","junio",
             "julio","agosto","septiembre","octubre","noviembre","diciembre"]
_MESES_EN = ["January","February","March","April","May","June",
             "July","August","September","October","November","December"]


async def _tarea_recordatorios_visitas() -> None:
    """Tarea background: envía recordatorios de visita 24h y 1h antes."""
    await asyncio.sleep(120)  # esperar arranque completo
    while True:
        try:
            pendientes = await obtener_visitas_para_recordatorio()
            for item in pendientes:
                telefono = item["telefono"]
                nombre   = item["nombre"]
                hora     = item["hora"]
                tipo     = item["tipo"]
                idioma   = await obtener_idioma(telefono) or "es"
                tel_envio = telefono.lstrip("+")

                try:
                    from datetime import datetime as _dt
                    d = _dt.strptime(item["fecha"], "%Y-%m-%d")
                    if idioma == "en":
                        fecha_fmt = f"{_MESES_EN[d.month-1]} {d.day}, {d.year}"
                        if tipo == "24h":
                            msg = (
                                f"Hi {nombre}! 👋 Reminder: tomorrow you have a visit to "
                                f"Torre Fuerte Apartments:\n\n"
                                f"📅 {fecha_fmt}\n⏰ {hora}\n\n"
                                f"We look forward to seeing you! To reschedule, just reply here."
                            )
                        else:
                            msg = (
                                f"Hi {nombre}! ⏰ Your visit to Torre Fuerte Apartments "
                                f"is in about 1 hour ({hora}). See you soon! 🏠✨"
                            )
                    else:
                        fecha_fmt = f"{d.day} de {_MESES_ES[d.month-1]} de {d.year}"
                        if tipo == "24h":
                            msg = (
                                f"Hola {nombre}! 👋 Te recordamos que mañana tienes una visita "
                                f"al proyecto Torre Fuerte:\n\n"
                                f"📅 {fecha_fmt}\n⏰ {hora}\n\n"
                                f"¡Te esperamos! Si necesitas reprogramar, escríbenos aquí."
                            )
                        else:
                            msg = (
                                f"Hola {nombre}! ⏰ En aproximadamente 1 hora tienes tu visita "
                                f"a Torre Fuerte ({hora}). ¡Nos vemos pronto! 🏠✨"
                            )
                except Exception as e:
                    logger.error(f"Error formateando recordatorio para visita {item['id']}: {e}")
                    continue

                if proveedor:
                    ok = await proveedor.enviar_mensaje(tel_envio, msg)
                    if ok:
                        await marcar_recordatorio(item["id"], tipo)
                        await guardar_mensaje(telefono, "assistant", msg)
                        logger.info(f"Recordatorio {tipo} enviado a {nombre} ({telefono})")
                    else:
                        logger.error(f"Fallo envío recordatorio {tipo} a {telefono}")

        except Exception as e:
            logger.error(f"Error en tarea recordatorios: {e}")

        await asyncio.sleep(900)  # revisar cada 15 minutos


@asynccontextmanager
async def lifespan(app: FastAPI):
    global proveedor

    env_keys = list(os.environ.keys())
    logger.info(f"Variables de entorno disponibles: {env_keys}")
    logger.info(f"WHATSAPP_PROVIDER = '{os.getenv('WHATSAPP_PROVIDER', 'NO DEFINIDO')}'")

    await inicializar_db()

    from agent.providers import obtener_proveedor
    proveedor = obtener_proveedor()

    admin_module.proveedor = proveedor

    asyncio.create_task(_tarea_handoff_inactivos())
    asyncio.create_task(_tarea_seguimiento_leads())
    asyncio.create_task(_tarea_recordatorios_visitas())

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

app.include_router(admin_module.router)

if os.path.exists("knowledge/planos"):
    app.mount("/planos", StaticFiles(directory="knowledge/planos"), name="planos")

if os.path.exists("knowledge/renders"):
    app.mount("/renders", StaticFiles(directory="knowledge/renders"), name="renders")

if os.path.exists("knowledge"):
    app.mount("/assets", StaticFiles(directory="knowledge"), name="assets")


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
        "ADMIN_USER": os.getenv("TF_ADMIN_USER") or os.getenv("ADMIN_USER", "NO CONFIGURADO"),
        "DATABASE_URL": "postgresql (configurado)" if any(
            (os.getenv(k) or "").startswith("postgresql")
            for k in ("TF_DATABASE_URL", "POSTGRES_URL", "DATABASE_URL")
        ) else "sqlite (local — agrega TF_DATABASE_URL en Railway)",
    }


@app.delete("/handoff/{telefono}")
async def reset_handoff(telefono: str):
    """Desactiva el handoff de un lead — el asesor ya terminó de atenderlo."""
    await desactivar_handoff(telefono)
    logger.info(f"Handoff desactivado para {telefono}")
    return {"status": "ok", "telefono": telefono, "handoff": "desactivado"}


@app.get("/leads/export")
async def exportar_leads():
    """Descarga el Excel con todos los leads. Regenera desde la BD antes de servir."""
    leads = await obtener_todos_los_leads()
    ruta = exportar_leads_excel(leads)
    return FileResponse(
        ruta,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="leads-torre-fuerte.xlsx",
    )


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

            # Deduplicación: ignorar si ya procesamos este mensaje_id
            if await mensaje_ya_procesado(msg.mensaje_id):
                logger.info(f"Mensaje duplicado ignorado: {msg.mensaje_id}")
                continue
            await marcar_mensaje_procesado(msg.mensaje_id)

            logger.info(f"Mensaje de {msg.telefono}: {msg.texto}")

            # Si hay handoff activo, el bot cede el turno al asesor humano
            if await esta_en_handoff(msg.telefono):
                await guardar_mensaje(msg.telefono, "user", msg.texto)
                if await debe_enviar_aviso_handoff(msg.telefono):
                    idioma_h = await obtener_idioma(msg.telefono) or "es"
                    perfil_h = await obtener_perfil_lead(msg.telefono)
                    nombre_h = perfil_h.get("nombre", "") if perfil_h else ""
                    if idioma_h == "en":
                        aviso = (
                            f"Hi{' ' + nombre_h if nombre_h else ''}! 😊 "
                            f"You already have an advisor assigned who will contact you shortly."
                        )
                    else:
                        aviso = (
                            f"¡Hola{' ' + nombre_h if nombre_h else ''}! 😊 "
                            f"Ya tienes un asesor asignado que te contactará muy pronto."
                        )
                    await proveedor.enviar_mensaje(msg.telefono, aviso)
                    await registrar_aviso_handoff(msg.telefono)
                continue

            historial = await obtener_historial(msg.telefono)
            idioma = await obtener_idioma(msg.telefono)

            if len(historial) == 0:
                if idioma is None:
                    idioma = _detectar_idioma(msg.texto)
                    await guardar_idioma(msg.telefono, idioma)
                url_logo = f"{BASE_URL}/assets/TF-LOGO.jpg"
                await proveedor.enviar_media(msg.telefono, url_logo)
                logger.info("Logo enviado al inicio de conversación")
            elif idioma is None:
                idioma = _detectar_idioma(msg.texto)
                await guardar_idioma(msg.telefono, idioma)

            perfil = await obtener_perfil_lead(msg.telefono)
            respuesta_raw = await generar_respuesta(msg.texto, historial, perfil, idioma)

            texto_sin_planos, codigos_plano = extraer_marcadores_plano(respuesta_raw)
            texto_sin_renders, claves_render = extraer_marcadores_render(texto_sin_planos)
            texto_sin_lead, lead_data = extraer_marcador_lead(texto_sin_renders)
            texto_sin_visita, visita_data = extraer_marcador_visita(texto_sin_lead)
            texto_limpio, razon_handoff = extraer_marcador_handoff(texto_sin_visita)

            # Anotar en el historial qué media se envió para que Claude no lo repita
            texto_a_guardar = texto_limpio
            if codigos_plano:
                texto_a_guardar += f"\n[Sistema: plano(s) enviado(s): {', '.join(codigos_plano)}]"
            if claves_render:
                texto_a_guardar += f"\n[Sistema: renders enviados: {', '.join(claves_render)}]"

            await guardar_mensaje(msg.telefono, "user", msg.texto)
            await guardar_mensaje(msg.telefono, "assistant", texto_a_guardar)

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
                todos_los_leads = await obtener_todos_los_leads()
                exportar_leads_excel(todos_los_leads)

            # Visita: Claude emitió [VISITA] → guardar en BD
            if visita_data:
                nombre_v = visita_data["nombre"]
                # Preferir el nombre del perfil si ya está registrado
                if perfil and perfil.get("nombre"):
                    nombre_v = perfil["nombre"]
                await guardar_visita(
                    msg.telefono,
                    nombre_v,
                    visita_data["fecha"],
                    visita_data["hora"],
                    visita_data["notas"],
                )
                logger.info(
                    f"Visita auto-agendada: {nombre_v} ({msg.telefono}) "
                    f"{visita_data['fecha']} {visita_data['hora']}"
                )

            # Handoff: Claude emitió [HANDOFF] → transferir a asesor
            if razon_handoff and not await esta_en_handoff(msg.telefono):
                perfil_h = await obtener_perfil_lead(msg.telefono)
                nombre_h = (perfil_h or lead_data or {}).get("nombre", "")
                temperatura_h = (perfil_h or lead_data or {}).get("temperatura", "tibio")
                apto_h = (perfil_h or lead_data or {}).get("apto", "")
                hab_h = (perfil_h or lead_data or {}).get("habitaciones", "")
                email_h = (perfil_h or lead_data or {}).get("email", "")
                intencion_h = (perfil_h or lead_data or {}).get("intencion", "")

                historial_h = await obtener_historial(msg.telefono, limite=30)
                resumen_h = await generar_resumen_handoff(historial_h, nombre_h, temperatura_h, idioma)

                await activar_handoff(msg.telefono, razon_handoff, nombre_h, resumen_h)
                await registrar_aviso_handoff(msg.telefono)
                await _notificar_handoff(
                    msg.telefono, nombre_h, temperatura_h, razon_handoff,
                    idioma, apto_h, hab_h, email_h, intencion_h, resumen_h,
                )
                logger.info(f"Handoff activado para {msg.telefono} — razón: {razon_handoff}")

            logger.info(f"Respuesta enviada a {msg.telefono}")

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error en webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))
