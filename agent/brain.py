# agent/brain.py — Cerebro del agente: conexión con Claude API
# Generado por AgentKit

import os
import yaml
import logging
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def cargar_config_prompts() -> dict:
    """Lee toda la configuración desde config/prompts.yaml."""
    try:
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error("config/prompts.yaml no encontrado")
        return {}


def cargar_system_prompt() -> str:
    """Lee el system prompt desde config/prompts.yaml."""
    config = cargar_config_prompts()
    return config.get("system_prompt", "Eres un asistente útil. Responde en español.")


def obtener_mensaje_error() -> str:
    config = cargar_config_prompts()
    return config.get("error_message", "Lo siento, estoy teniendo problemas técnicos. Por favor intenta de nuevo en unos minutos.")


def obtener_mensaje_fallback() -> str:
    config = cargar_config_prompts()
    return config.get("fallback_message", "Disculpa, no entendí tu mensaje. ¿Podrías reformularlo?")


async def generar_respuesta(mensaje: str, historial: list[dict], perfil: dict | None = None, idioma: str | None = None) -> str:
    """
    Genera una respuesta usando Claude API.

    Args:
        mensaje: El mensaje nuevo del usuario
        historial: Lista de mensajes anteriores [{"role": "user/assistant", "content": "..."}]
        perfil: Datos del lead si ya ha contactado antes (nombre, intereses, etc.)

    Returns:
        La respuesta generada por Claude
    """
    if not mensaje or len(mensaje.strip()) < 2:
        return obtener_mensaje_fallback()

    system_prompt = cargar_system_prompt()

    if idioma == "en":
        system_prompt += (
            "\n\n## Language\n"
            "This customer communicates in ENGLISH. You MUST respond entirely in English.\n"
            "Translate all project information, prices, descriptions, and responses to English.\n"
            "Do not mix Spanish and English under any circumstances."
        )
        system_prompt += (
            "\n\n## Human handoff\n"
            "Add `[HANDOFF:requested_by_user]` at the END of your response (after all text) when:\n"
            "- The user explicitly asks to speak with an advisor, sales agent, or human.\n"
            "Add `[HANDOFF:conversation_completed]` at the END of your response when:\n"
            "- The lead is fully qualified (you have their name, interest, and intent) AND\n"
            "  they have expressed concrete interest in visiting, getting a formal quote, or buying.\n"
            "CRITICAL: Whenever you emit [HANDOFF], you MUST also emit [LEAD:...] in the SAME response "
            "with whatever info you have (leave fields empty if unknown). "
            "Example: [LEAD:John Smith||D-401|3|warm|investment][HANDOFF:requested_by_user]\n"
            "Only emit [HANDOFF] once per conversation. Never emit it in the middle of a message."
        )
    else:
        system_prompt += (
            "\n\n## Idioma\n"
            "Este cliente se comunica en ESPAÑOL. Responde siempre en español."
        )
        system_prompt += (
            "\n\n## Transferencia a asesor humano\n"
            "Agrega `[HANDOFF:solicitado_por_usuario]` AL FINAL de tu respuesta (después de todo el texto) cuando:\n"
            "- El usuario pida explícitamente hablar con un asesor, vendedor o persona humana.\n"
            "Agrega `[HANDOFF:conversacion_completada]` AL FINAL de tu respuesta cuando:\n"
            "- El lead esté completamente calificado (tienes su nombre, interés y intención) Y\n"
            "  haya expresado interés concreto en visitar, cotizar formalmente o comprar.\n"
            "CRÍTICO: Siempre que emitas [HANDOFF], DEBES emitir también [LEAD:...] en la MISMA respuesta "
            "con toda la info que tengas (deja campos vacíos si no los conoces). "
            "Ejemplo: [LEAD:Juan Pérez||D-401|3|caliente|vivir][HANDOFF:solicitado_por_usuario]\n"
            "Solo emite [HANDOFF] una vez por conversación. Nunca en medio del texto."
        )

    if perfil and perfil.get("nombre"):
        if idioma == "en":
            datos = (
                f"\n\n## Current customer (returning lead)\n"
                f"This customer has contacted before. Greet them by name and follow up on their previous interests.\n"
                f"- Name: {perfil['nombre']}\n"
                f"- Apartment of interest: {perfil['apto'] or 'Not specified'}\n"
                f"- Bedrooms: {perfil['habitaciones'] or 'Not specified'}\n"
                f"- Intent: {perfil['intencion'] or 'Not specified'}\n"
                f"- First contact: {perfil['fecha']}\n"
            )
        else:
            datos = (
                f"\n\n## Cliente actual (lead conocido)\n"
                f"Este cliente ya ha contactado antes. Salúdale por su nombre y retoma desde sus intereses previos.\n"
                f"- Nombre: {perfil['nombre']}\n"
                f"- Apto de interés: {perfil['apto'] or 'No especificado'}\n"
                f"- Habitaciones: {perfil['habitaciones'] or 'No especificado'}\n"
                f"- Intención: {perfil['intencion'] or 'No especificada'}\n"
                f"- Primera consulta: {perfil['fecha']}\n"
            )
        system_prompt += datos

    mensajes = []
    for msg in historial:
        mensajes.append({
            "role": msg["role"],
            "content": msg["content"]
        })

    mensajes.append({
        "role": "user",
        "content": mensaje
    })

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_prompt,
            messages=mensajes
        )

        respuesta = response.content[0].text
        logger.info(f"Respuesta generada ({response.usage.input_tokens} in / {response.usage.output_tokens} out)")
        return respuesta

    except Exception as e:
        logger.error(f"Error Claude API: {e}")
        return obtener_mensaje_error()
