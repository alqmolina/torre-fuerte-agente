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

_TOOLS = [
    {
        "name": "calcular_hipoteca",
        "description": (
            "Calcula la cuota mensual exacta de un crédito hipotecario en Colombia. "
            "Úsala cuando el lead pregunte por financiamiento, crédito, cuota mensual, "
            "o quiera saber cuánto pagaría por mes por un apartamento. "
            "Si no especifica tasa, usa 12.5% anual (tasa típica Colombia 2025). "
            "Si no especifica plazo, calcula para 20 Y 30 años como comparación. "
            "Si no especifica cuota inicial, usa 30% (mínimo requerido en Colombia para vivienda no VIS)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "precio_cop": {
                    "type": "number",
                    "description": "Precio del apartamento en pesos colombianos (COP)",
                },
                "entrada_pct": {
                    "type": "number",
                    "description": "Porcentaje de cuota inicial (ej: 30 para el 30%)",
                },
                "plazo_anos": {
                    "type": "integer",
                    "description": "Plazo del crédito en años (10, 15, 20 o 30)",
                },
                "tasa_anual_pct": {
                    "type": "number",
                    "description": "Tasa de interés anual en porcentaje (ej: 12.5)",
                },
            },
            "required": ["precio_cop", "entrada_pct", "plazo_anos", "tasa_anual_pct"],
        },
    }
]


def _ejecutar_herramienta(nombre: str, params: dict) -> str:
    if nombre == "calcular_hipoteca":
        from agent.tools import calcular_hipoteca
        return calcular_hipoteca(**params)
    return f"Herramienta '{nombre}' no encontrada."


def _content_a_dicts(content: list) -> list:
    """Convierte content blocks del SDK (objetos Pydantic) a dicts planos para la API."""
    result = []
    for block in content:
        t = getattr(block, "type", None)
        if t == "text":
            result.append({"type": "text", "text": block.text})
        elif t == "tool_use":
            result.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
    return result


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


async def generar_resumen_handoff(historial: list[dict], nombre: str, temperatura: str, idioma: str) -> str:
    """Genera un resumen de 3 puntos de la conversación para que el asesor entre contextualizado."""
    if not historial:
        return ""
    msgs = [
        f"{'Cliente' if m['role'] == 'user' else 'Bot'}: {m['content'][:300]}"
        for m in historial[-20:]
        if not m["content"].startswith("[Sistema:")
    ]
    if not msgs:
        return ""
    conversacion = "\n".join(msgs)
    lang = "inglés" if idioma == "en" else "español"
    prompt = (
        f"Eres un asistente de ventas inmobiliarias. Resume en exactamente 3 puntos concisos "
        f"(una línea cada uno, sin títulos) esta conversación de WhatsApp para el asesor humano:\n\n"
        f"Lead: {nombre} | Temperatura: {temperatura}\n\n{conversacion}\n\n"
        f"Responde en {lang}. Formato:\n"
        f"• [Qué busca: tipo/apto/habitaciones]\n"
        f"• [Intención: vivir o invertir, urgencia]\n"
        f"• [Estado: dónde quedó, qué falta o por qué pidió asesor]"
    )
    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.error(f"Error generando resumen handoff: {e}")
        return ""


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

    # Inyectar precios de apartamentos para que Claude pueda llamar la calculadora
    try:
        from agent.tools import obtener_disponibilidad
        aptos = obtener_disponibilidad()
        lineas_precio = "\n".join(
            f"  • {a['apto']}: ${a['precio']:,} COP ({a['hab']} hab · {a['area']} m²)"
            for a in aptos
        )
    except Exception:
        lineas_precio = ""

    if idioma == "en":
        system_prompt += (
            "\n\n## Apartment prices (use these for mortgage calculations)\n"
            + lineas_precio
            + "\n\n## Mortgage calculator — MANDATORY\n"
            "You MUST call the `calcular_hipoteca` tool whenever the lead asks about:\n"
            "- Monthly payments, installments, or how much they would pay per month\n"
            "- Financing, mortgage, or credit options\n"
            "- Whether they can afford an apartment\n"
            "NEVER estimate or approximate mortgage payments with text — always call the tool.\n"
            "If the lead hasn't specified a term, calculate for BOTH 20 and 30 years (call the tool twice).\n"
            "If no down payment specified, use 30% (Colombia minimum for non-VIS housing).\n"
            "If no rate specified, use 12.5% annual (typical Colombia 2025)."
        )
    else:
        system_prompt += (
            "\n\n## Precios de apartamentos (úsalos para los cálculos hipotecarios)\n"
            + lineas_precio
            + "\n\n## Calculadora hipotecaria — OBLIGATORIO\n"
            "DEBES llamar la herramienta `calcular_hipoteca` siempre que el lead pregunte:\n"
            "- Cuota mensual, cuánto pagaría al mes, valor de la cuota\n"
            "- Financiamiento, crédito hipotecario, opciones de pago\n"
            "- Si puede pagar un apartamento o qué necesita para comprarlo\n"
            "NUNCA estimes ni aproximes cuotas hipotecarias con texto — siempre usa la herramienta.\n"
            "Si el lead no especificó plazo, calcula para 20 Y 30 años (llama la herramienta dos veces).\n"
            "Si no especificó cuota inicial, usa 30% (mínimo en Colombia para vivienda no VIS).\n"
            "Si no especificó tasa, usa 12.5% anual (tasa típica Colombia 2025)."
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
            messages=mensajes,
            tools=_TOOLS,
        )

        # Manejar tool use — loop hasta que Claude termine (puede llamar la herramienta varias veces)
        for _ in range(5):  # máximo 5 iteraciones para evitar loops infinitos
            if response.stop_reason != "tool_use":
                break
            tool_blocks = [b for b in response.content if b.type == "tool_use"]
            if not tool_blocks:
                break
            mensajes.append({"role": "assistant", "content": _content_a_dicts(response.content)})
            resultados = []
            for tb in tool_blocks:
                resultado = _ejecutar_herramienta(tb.name, tb.input)
                logger.info(f"Tool use: {tb.name}({tb.input}) → {resultado[:80]}")
                resultados.append({"type": "tool_result", "tool_use_id": tb.id, "content": resultado})
            mensajes.append({"role": "user", "content": resultados})
            response = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1536,
                system=system_prompt,
                messages=mensajes,
                tools=_TOOLS,
            )

        respuesta = next((b.text for b in response.content if b.type == "text"), "")
        if not respuesta:
            respuesta = obtener_mensaje_fallback()
        logger.info(f"Respuesta generada ({response.usage.input_tokens} in / {response.usage.output_tokens} out)")
        return respuesta

    except Exception as e:
        logger.error(f"Error Claude API [{type(e).__name__}]: {e}")
        return obtener_mensaje_error()
