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
    },
    {
        "name": "verificar_disponibilidad_visita",
        "description": (
            "Verifica si una fecha y hora están disponibles para agendar una visita al proyecto. "
            "DEBES llamar esta herramienta SIEMPRE que el lead proponga una fecha (con o sin hora) "
            "para visitar o llamar. Si el slot ya está ocupado, la herramienta te indica los "
            "horarios tomados para que ofrezcas alternativas. "
            "Nunca confirmes una cita sin llamar primero esta herramienta."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {
                    "type": "string",
                    "description": "Fecha propuesta en formato YYYY-MM-DD",
                },
                "hora": {
                    "type": "string",
                    "description": "Hora propuesta en formato HH:MM (24h). Opcional si solo propone fecha.",
                },
            },
            "required": ["fecha"],
        },
    },
]


def _hora_a_minutos(hora: str) -> int:
    try:
        h, m = hora.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return -1


async def _ejecutar_herramienta(nombre: str, params: dict) -> str:
    if nombre == "calcular_hipoteca":
        from agent.tools import calcular_hipoteca
        return calcular_hipoteca(**params)
    if nombre == "verificar_disponibilidad_visita":
        from agent.memory import obtener_visitas_proximas
        fecha = params.get("fecha", "").strip()
        hora = params.get("hora", "").strip()
        if not fecha:
            return "Fecha no especificada. Pide al lead que indique una fecha válida."
        visitas = await obtener_visitas_proximas()
        ese_dia = [v for v in visitas if v["fecha"] == fecha]
        if not ese_dia:
            if hora:
                return f"Disponible: el {fecha} a las {hora} no tiene visitas agendadas. Puedes confirmar la cita."
            return f"El {fecha} está completamente libre. No hay visitas agendadas ese día."
        horas_ocupadas = sorted(v["hora"] for v in ese_dia)
        if hora:
            prop_min = _hora_a_minutos(hora)
            conflicto = any(
                abs(prop_min - _hora_a_minutos(h)) < 60
                for h in horas_ocupadas
                if _hora_a_minutos(h) >= 0
            )
            if conflicto:
                return (
                    f"No disponible: el {fecha} a las {hora} hay una visita cercana ya agendada. "
                    f"Horarios ocupados ese día: {', '.join(horas_ocupadas)}. "
                    f"Informa al lead que ese horario no está disponible y pídele otra fecha u hora."
                )
            return f"Disponible: el {fecha} a las {hora} está libre. Puedes confirmar la cita."
        return (
            f"El {fecha} tiene estos horarios ya ocupados: {', '.join(horas_ocupadas)}. "
            f"Pide al lead que elija un horario diferente (separados por al menos 1 hora)."
        )
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


async def generar_respuesta(mensaje: str, historial: list[dict], perfil: dict | None = None, idioma: str | None = None, telefono: str | None = None) -> str:
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
        system_prompt += (
            "\n\n## Visit scheduling\n"
            "STEP 1 — Availability check (MANDATORY):\n"
            "  Whenever the lead proposes a date (with or without a time), you MUST call "
            "`verificar_disponibilidad_visita` BEFORE responding about that date.\n"
            "  - If the result says the slot is NOT available: tell the lead that date/time is taken "
            "and ask them to choose another one. Do NOT emit [VISITA].\n"
            "  - If the result says the slot IS available: proceed to confirm and emit [VISITA].\n\n"
            "STEP 2 — Confirm and tag:\n"
            "  When the lead has confirmed an available date AND time, add at the END of your response:\n"
            "  `[VISITA:lead_name|YYYY-MM-DD|HH:MM|optional_notes]`\n"
            "  Example: `[VISITA:John Smith|2026-05-28|10:00|Interested in D-401 3-bedroom]`\n\n"
            "Rules:\n"
            "- Convert dates to YYYY-MM-DD (e.g. 'next Tuesday May 27' → 2026-05-27).\n"
            "- Convert times to 24h HH:MM (e.g. '3pm' → 15:00).\n"
            "- Use the lead's name if known, otherwise 'Lead'.\n"
            "- Never emit [VISITA] more than once per confirmed appointment."
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
        system_prompt += (
            "\n\n## Agendamiento de visitas\n"
            "PASO 1 — Verificar disponibilidad (OBLIGATORIO):\n"
            "  Cada vez que el lead proponga una fecha (con o sin hora), DEBES llamar "
            "`verificar_disponibilidad_visita` ANTES de responder sobre esa fecha.\n"
            "  - Si el resultado dice que el horario NO está disponible: informa al lead que esa "
            "fecha/hora ya está ocupada y pídele que elija otra. NO emitas [VISITA].\n"
            "  - Si el resultado dice que el horario SÍ está disponible: procede a confirmar y emite [VISITA].\n\n"
            "PASO 2 — Confirmar y etiquetar:\n"
            "  Cuando el lead haya confirmado una fecha Y hora disponibles, agrega AL FINAL de tu respuesta:\n"
            "  `[VISITA:nombre_lead|YYYY-MM-DD|HH:MM|notas_opcionales]`\n"
            "  Ejemplo: `[VISITA:Juan Pérez|2026-05-28|10:00|Interesado en D-401 de 3 habitaciones]`\n\n"
            "Reglas:\n"
            "- Convierte fechas a YYYY-MM-DD (ej: 'el martes 27' → 2026-05-27).\n"
            "- Convierte la hora a HH:MM en 24h (ej: '3pm' → 15:00, '10am' → 10:00).\n"
            "- Usa el nombre del lead si lo conoces, si no usa 'Lead'.\n"
            "- No emitas [VISITA] más de una vez por cita confirmada."
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

    # Inyectar visitas activas del lead para que Claude pueda gestionarlas
    if telefono:
        try:
            from agent.memory import obtener_visitas_lead
            todas = await obtener_visitas_lead(telefono)
            activas = [v for v in todas if v.get("estado") not in ("cancelada", "completada")]
            if activas:
                if idioma == "en":
                    lineas = "\n".join(
                        f"  • ID:{v['id']} | {v['fecha']} at {v['hora']}"
                        + (f" | {v['notas']}" if v.get("notas") else "")
                        for v in activas
                    )
                    system_prompt += (
                        "\n\n## Lead's scheduled visits\n"
                        f"{lineas}\n\n"
                        "If the lead asks to CANCEL a visit:\n"
                        "1. Show them the visit details and ask for confirmation.\n"
                        "2. Once they confirm, add at the END of your response: `[CANCELAR_VISITA:ID]`.\n"
                        "3. Confirm in your message that the visit has been cancelled.\n"
                        "Do NOT emit [CANCELAR_VISITA] without explicit confirmation.\n\n"
                        "If the lead asks to RESCHEDULE a visit:\n"
                        "1. Show the current visit and ask for the new date and time.\n"
                        "2. Call `verificar_disponibilidad_visita` for the proposed new slot.\n"
                        "3. If available, confirm and add at the END: `[REAGENDAR_VISITA:ID|YYYY-MM-DD|HH:MM]`.\n"
                        "4. If not available, inform the lead and ask for another date/time.\n"
                        "Do NOT emit [REAGENDAR_VISITA] without availability check and lead confirmation."
                    )
                else:
                    lineas = "\n".join(
                        f"  • ID:{v['id']} | {v['fecha']} a las {v['hora']}"
                        + (f" | {v['notas']}" if v.get("notas") else "")
                        for v in activas
                    )
                    system_prompt += (
                        "\n\n## Visitas agendadas del lead\n"
                        f"{lineas}\n\n"
                        "Si el lead pide CANCELAR su visita:\n"
                        "1. Muéstrale los datos y pídele confirmación explícita.\n"
                        "2. Cuando confirme, agrega AL FINAL: `[CANCELAR_VISITA:ID]`.\n"
                        "3. Confirma en tu mensaje que la visita fue cancelada.\n"
                        "NO emitas [CANCELAR_VISITA] sin confirmación del lead.\n\n"
                        "Si el lead pide REAGENDAR su visita:\n"
                        "1. Muestra la visita actual y pide la nueva fecha y hora.\n"
                        "2. Llama `verificar_disponibilidad_visita` para el nuevo horario propuesto.\n"
                        "3. Si está disponible, confirma y agrega AL FINAL: `[REAGENDAR_VISITA:ID|YYYY-MM-DD|HH:MM]`.\n"
                        "4. Si no está disponible, informa al lead y pide otro horario.\n"
                        "NO emitas [REAGENDAR_VISITA] sin verificar disponibilidad y confirmar con el lead."
                    )
        except Exception as e:
            logger.warning(f"No se pudieron cargar visitas del lead: {e}")

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
                resultado = await _ejecutar_herramienta(tb.name, tb.input)
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
