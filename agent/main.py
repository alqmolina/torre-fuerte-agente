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
    guardar_visita, cancelar_visita, reagendar_visita,
    obtener_visitas_para_recordatorio, marcar_recordatorio,
    guardar_evento_id, obtener_evento_id, borrar_evento_id,
)
import agent.admin as admin_module
from agent.google_calendar import crear_evento_visita, actualizar_evento_visita, eliminar_evento_visita
from agent.tools import (
    extraer_marcadores_plano,
    extraer_marcadores_render,
    extraer_marcador_lead,
    extraer_marcador_handoff,
    extraer_marcador_visita,
    extraer_marcador_cancelar_visita,
    extraer_marcador_reagendar_visita,
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

# Los proveedores se inicializan en lifespan, no al importar el módulo
proveedor = None
proveedor_messenger = None


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
    """Compone el mensaje de seguimiento leyendo plantillas de config/business.yaml."""
    from agent.tools import cargar_info_negocio
    cfg = cargar_info_negocio()
    negocio = cfg.get("negocio", {}).get("nombre_corto") or cfg.get("negocio", {}).get("nombre", "")
    n = nombre if nombre and nombre != "Desconocido" else ""
    temp = temperatura.lower().replace("í", "i")
    lang = "en" if idioma == "en" else "es"
    fallback = f"¡Hola{' ' + n if n else ''}! 👋 Solo quería hacer seguimiento a tu consulta. ¡Seguimos disponibles para ayudarte! 🏠"

    plantillas = cfg.get("mensajes", {}).get("seguimiento", {}).get(lang, {}).get(temp)
    if not plantillas:
        plantillas = cfg.get("mensajes", {}).get("seguimiento", {}).get(lang, {}).get("frio", [])
    if not plantillas or numero < 1 or numero > len(plantillas):
        return fallback

    return plantillas[numero - 1].format(nombre=n or "!", negocio=negocio)


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

                from agent.tools import cargar_info_negocio
                _cfg_h = cargar_info_negocio()
                _negocio_h = _cfg_h.get("negocio", {}).get("nombre_corto") or _cfg_h.get("negocio", {}).get("nombre", "")
                _msgs_h = _cfg_h.get("mensajes", {}).get("handoff_automatico", {})
                _tpl_h = _msgs_h.get("en" if idioma == "en" else "es", "")
                msg_lead = _tpl_h.format(nombre=nombre, negocio=_negocio_h) if _tpl_h else (
                    f"Hi {nombre}! 👋 An advisor will reach out to you shortly. 🏠✨"
                    if idioma == "en" else
                    f"¡Hola {nombre}! 👋 Un asesor se pondrá en contacto contigo muy pronto. 🏠✨"
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


def _fmt_fecha_notif(fecha: str, idioma: str) -> str:
    """Formatea YYYY-MM-DD a texto legible en es/en."""
    try:
        from datetime import datetime as _dt
        d = _dt.strptime(fecha, "%Y-%m-%d")
        if idioma == "en":
            return f"{_MESES_EN[d.month-1]} {d.day}, {d.year}"
        return f"{d.day} de {_MESES_ES[d.month-1]} de {d.year}"
    except Exception:
        return fecha


async def _notificar_visita_asesor(telefono: str, nombre: str, fecha: str, hora: str, notas: str,
                                    tipo: str = "nueva", visita_id: int | None = None) -> None:
    """Envía WhatsApp al asesor cuando el bot agenda, reagenda o cancela una visita."""
    if not ASESOR_WHATSAPP or not proveedor:
        return
    if tipo == "reagenda":
        titulo = "🔄 VISITA REAGENDADA (via WhatsApp)"
    elif tipo == "cancela":
        titulo = "❌ VISITA CANCELADA (via WhatsApp)"
    else:
        titulo = "📅 NUEVA VISITA AGENDADA (via WhatsApp)"

    if tipo == "cancela":
        msg = (
            f"{titulo}\n\n"
            f"👤 Lead: {nombre or 'Sin nombre'}\n"
            f"📱 Tel: {telefono}"
            + (f"\n🔢 Visita ID: {visita_id}" if visita_id else "")
            + f"\n\nVer chat: {BASE_URL}/admin/chat/{telefono}"
        )
    else:
        fecha_fmt = _fmt_fecha_notif(fecha, "es")
        msg = (
            f"{titulo}\n\n"
            f"👤 Lead: {nombre or 'Sin nombre'}\n"
            f"📱 Tel: {telefono}\n"
            f"📅 Fecha: {fecha_fmt}\n"
            f"⏰ Hora: {hora}"
            + (f"\n📝 Notas: {notas}" if notas else "")
            + f"\n\nVer chat: {BASE_URL}/admin/chat/{telefono}"
        )
    try:
        await proveedor.enviar_mensaje(ASESOR_WHATSAPP.lstrip("+"), msg)
    except Exception as e:
        logger.warning(f"No se pudo notificar visita al asesor: {e}")


async def _tarea_recordatorios_visitas() -> None:
    """Tarea background: envía recordatorios de visita 24h y 1h antes (bot y manuales)."""
    await asyncio.sleep(120)  # esperar arranque completo
    while True:
        try:
            pendientes = await obtener_visitas_para_recordatorio()
            if pendientes:
                logger.info(f"Recordatorios pendientes: {len(pendientes)}")
            for item in pendientes:
                telefono = item["telefono"]
                nombre   = item["nombre"]
                hora     = item["hora"]
                tipo     = item["tipo"]

                # Normalizar teléfono: quitar + y espacios
                tel_envio = telefono.strip().lstrip("+").replace(" ", "")

                try:
                    idioma = await obtener_idioma(tel_envio) or "es"
                except Exception:
                    idioma = "es"

                try:
                    from datetime import datetime as _dt
                    from agent.tools import cargar_info_negocio
                    d = _dt.strptime(item["fecha"], "%Y-%m-%d")
                    _cfg_r = cargar_info_negocio()
                    _negocio_r = _cfg_r.get("negocio", {}).get("nombre_corto") or _cfg_r.get("negocio", {}).get("nombre", "")
                    _msgs_r = _cfg_r.get("mensajes", {}).get("recordatorio_visita", {})
                    lang_r = "en" if idioma == "en" else "es"
                    tipo_key = "dia_antes" if tipo == "24h" else "una_hora"
                    _tpl_r = _msgs_r.get(lang_r, {}).get(tipo_key, "")
                    if idioma == "en":
                        fecha_fmt = f"{_MESES_EN[d.month-1]} {d.day}, {d.year}"
                    else:
                        fecha_fmt = f"{d.day} de {_MESES_ES[d.month-1]} de {d.year}"
                    if _tpl_r:
                        msg = _tpl_r.format(nombre=nombre, negocio=_negocio_r, fecha=fecha_fmt, hora=hora)
                    elif idioma == "en":
                        msg = (f"Hi {nombre}! Reminder: tomorrow you have a visit.\n\n📅 {fecha_fmt}\n⏰ {hora}"
                               if tipo == "24h" else f"Hi {nombre}! Your visit is in about 1 hour ({hora}). See you soon!")
                    else:
                        msg = (f"¡Hola {nombre}! Mañana tienes una visita:\n\n📅 {fecha_fmt}\n⏰ {hora}\n\n¡Te esperamos!"
                               if tipo == "24h" else f"¡Hola {nombre}! En 1 hora tienes tu visita ({hora}). ¡Nos vemos!")
                except Exception as e:
                    logger.error(f"Error formateando recordatorio para visita {item['id']}: {e}")
                    continue

                if proveedor:
                    ok = await proveedor.enviar_mensaje(tel_envio, msg)
                    if ok:
                        await marcar_recordatorio(item["id"], tipo)
                        try:
                            await guardar_mensaje(tel_envio, "assistant", msg)
                        except Exception as e:
                            logger.warning(f"No se pudo guardar mensaje de recordatorio en historial: {e}")
                        logger.info(f"Recordatorio {tipo} enviado a {nombre} ({tel_envio})")
                    else:
                        logger.error(f"Fallo envío recordatorio {tipo} a {tel_envio}")

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

    global proveedor_messenger
    if os.getenv("META_PAGE_TOKEN"):
        from agent.providers.messenger import ProveedorMessenger
        proveedor_messenger = ProveedorMessenger()
        logger.info("Proveedor Messenger/Instagram inicializado")

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


@app.post("/debug/logo/{telefono}")
async def debug_enviar_logo(telefono: str):
    """Fuerza el envío del logo al número indicado — sirve para probar el mecanismo."""
    from agent.tools import cargar_info_negocio as _cfg_logo
    cfg = _cfg_logo()
    logo_cfg = cfg.get("logo", {})
    logo_archivo = (logo_cfg.get("archivo") if isinstance(logo_cfg, dict) else logo_cfg) or ""
    if not logo_archivo:
        return {"ok": False, "error": "Logo no configurado en business.yaml"}
    ruta = f"knowledge/{logo_archivo}"
    existe = os.path.exists(ruta)
    if not existe:
        return {"ok": False, "error": f"Archivo no encontrado: {ruta}"}
    ok = await proveedor.enviar_imagen_local(telefono, ruta)
    historial = await obtener_historial(telefono)
    return {
        "ok": ok,
        "ruta": ruta,
        "existe": existe,
        "historial_len": len(historial),
        "telefono": telefono,
    }


@app.delete("/debug/historial/{telefono}")
async def debug_reset_historial(telefono: str):
    """Borra el historial de un número para forzar historial==0 en el próximo mensaje."""
    from agent.memory import limpiar_historial
    await limpiar_historial(telefono)
    return {"ok": True, "telefono": telefono, "historial": "borrado"}


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


async def _procesar_mensaje_canal(msg, prov) -> None:
    """Pipeline completo de procesamiento de un mensaje entrante (cualquier canal)."""
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
            await prov.enviar_mensaje(msg.telefono, aviso)
            await registrar_aviso_handoff(msg.telefono)
        return

    historial = await obtener_historial(msg.telefono)
    idioma = await obtener_idioma(msg.telefono)

    if len(historial) == 0:
        if idioma is None:
            idioma = _detectar_idioma(msg.texto)
            await guardar_idioma(msg.telefono, idioma)
        from agent.tools import cargar_info_negocio as _cargarcfg
        _logo_cfg = _cargarcfg().get("logo", {})
        _logo_archivo = (_logo_cfg.get("archivo") if isinstance(_logo_cfg, dict) else _logo_cfg) or ""
        if _logo_archivo:
            ruta_logo = f"knowledge/{_logo_archivo}"
            logger.info(f"Enviando logo al inicio: {ruta_logo}")
            try:
                ok = await prov.enviar_imagen_local(msg.telefono, ruta_logo)
                if ok:
                    logger.info("Logo enviado correctamente")
                else:
                    logger.warning(f"enviar_imagen_local retornó False para logo: {ruta_logo}")
            except Exception as e:
                logger.error(f"Excepción enviando logo: {e}")
        else:
            logger.info("Logo deshabilitado en business.yaml")
    elif idioma is None:
        idioma = _detectar_idioma(msg.texto)
        await guardar_idioma(msg.telefono, idioma)

    perfil = await obtener_perfil_lead(msg.telefono)
    respuesta_raw = await generar_respuesta(msg.texto, historial, perfil, idioma, telefono=msg.telefono)

    texto_sin_planos, codigos_plano = extraer_marcadores_plano(respuesta_raw)
    texto_sin_renders, claves_render = extraer_marcadores_render(texto_sin_planos)
    texto_sin_lead, lead_data = extraer_marcador_lead(texto_sin_renders)
    texto_sin_visita, visita_data = extraer_marcador_visita(texto_sin_lead)
    texto_sin_cancelar, cancelar_visita_id = extraer_marcador_cancelar_visita(texto_sin_visita)
    texto_sin_reagendar, reagendar_data = extraer_marcador_reagendar_visita(texto_sin_cancelar)
    texto_limpio, razon_handoff = extraer_marcador_handoff(texto_sin_reagendar)

    texto_a_guardar = texto_limpio
    if codigos_plano:
        texto_a_guardar += f"\n[Sistema: plano(s) enviado(s): {', '.join(codigos_plano)}]"
    if claves_render:
        texto_a_guardar += f"\n[Sistema: renders enviados: {', '.join(claves_render)}]"

    await guardar_mensaje(msg.telefono, "user", msg.texto)
    await guardar_mensaje(msg.telefono, "assistant", texto_a_guardar)

    if texto_limpio:
        await prov.enviar_mensaje(msg.telefono, texto_limpio)

    for codigo in codigos_plano:
        ruta = obtener_plano(codigo)
        if ruta:
            url = f"{BASE_URL}/planos/{os.path.basename(ruta)}"
            await prov.enviar_media(msg.telefono, url, f"Plano apartamento {codigo.upper()}")
            logger.info(f"Plano enviado: {url}")
        else:
            await prov.enviar_mensaje(
                msg.telefono,
                f"Lo siento, no encontré el plano del apartamento {codigo}."
            )

    for clave in claves_render:
        urls = obtener_urls_renders(clave, BASE_URL)
        if urls:
            for url in urls:
                await prov.enviar_media(msg.telefono, url)
                logger.info(f"Render enviado: {url}")
                await asyncio.sleep(0.5)
        else:
            await prov.enviar_mensaje(msg.telefono, "Lo siento, no encontré renders para esa opción.")

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

    if visita_data:
        nombre_v = visita_data["nombre"]
        if perfil and perfil.get("nombre"):
            nombre_v = perfil["nombre"]
        visita_id = await guardar_visita(
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
        await _notificar_visita_asesor(
            msg.telefono, nombre_v,
            visita_data["fecha"], visita_data["hora"], visita_data["notas"],
        )
        try:
            event_id = crear_evento_visita(visita_id, nombre_v, msg.telefono,
                visita_data["fecha"], visita_data["hora"], visita_data["notas"])
            if event_id:
                await guardar_evento_id(visita_id, event_id)
        except Exception as e:
            logger.error(f"Google Calendar: error creando evento visita {visita_id}: {e}")

    if cancelar_visita_id:
        await cancelar_visita(cancelar_visita_id)
        logger.info(f"Visita {cancelar_visita_id} cancelada ({msg.telefono})")
        await _notificar_visita_asesor(
            msg.telefono,
            (perfil or {}).get("nombre", "") or "Lead",
            "", "", "",
            tipo="cancela",
            visita_id=cancelar_visita_id,
        )
        try:
            event_id = await obtener_evento_id(cancelar_visita_id)
            if event_id:
                eliminar_evento_visita(event_id)
                await borrar_evento_id(cancelar_visita_id)
        except Exception as e:
            logger.error(f"Google Calendar: error cancelando visita {cancelar_visita_id}: {e}")

    if reagendar_data:
        await reagendar_visita(reagendar_data["id"], reagendar_data["fecha"], reagendar_data["hora"])
        logger.info(f"Visita {reagendar_data['id']} reagendada a {reagendar_data['fecha']} {reagendar_data['hora']} ({msg.telefono})")
        nombre_r = (perfil or {}).get("nombre", "") or "Lead"
        await _notificar_visita_asesor(
            msg.telefono, nombre_r,
            reagendar_data["fecha"], reagendar_data["hora"], "",
            tipo="reagenda",
        )
        try:
            event_id = await obtener_evento_id(reagendar_data["id"])
            if event_id:
                actualizar_evento_visita(event_id, nombre_r, msg.telefono,
                    reagendar_data["fecha"], reagendar_data["hora"])
        except Exception as e:
            logger.error(f"Google Calendar: error reagendando visita {reagendar_data['id']}: {e}")

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


@app.post("/webhook")
async def webhook_handler(request: Request):
    """Recibe mensajes de WhatsApp y los procesa con el pipeline del agente."""
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

            try:
                await _procesar_mensaje_canal(msg, proveedor)
            except Exception as e:
                logger.error(f"Error procesando mensaje de {msg.telefono}: {e}")

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error en webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/webhook/messenger")
async def webhook_messenger_verificacion(request: Request):
    """Verificación GET del webhook de Messenger/Instagram."""
    if proveedor_messenger is None:
        return {"status": "messenger no configurado"}
    resultado = await proveedor_messenger.validar_webhook(request)
    if resultado is not None:
        return PlainTextResponse(str(resultado))
    return {"status": "ok"}


@app.post("/webhook/messenger")
async def webhook_messenger_handler(request: Request):
    """Recibe mensajes de Facebook Messenger e Instagram DMs."""
    if proveedor_messenger is None:
        return {"status": "ok"}
    try:
        mensajes = await proveedor_messenger.parsear_webhook(request)
        for msg in mensajes:
            if msg.es_propio or not msg.texto:
                continue
            if await mensaje_ya_procesado(msg.mensaje_id):
                logger.info(f"Mensaje Messenger duplicado ignorado: {msg.mensaje_id}")
                continue
            await marcar_mensaje_procesado(msg.mensaje_id)
            try:
                await _procesar_mensaje_canal(msg, proveedor_messenger)
            except Exception as e:
                logger.error(f"Error procesando mensaje Messenger de {msg.telefono}: {e}")
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error en webhook/messenger: {e}")
        return {"status": "ok"}
