# agent/google_calendar.py — Integración con Google Calendar
# Sincroniza visitas agendadas con Google Calendar via Service Account

import os
import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("agentkit")

_COL_TZ = "America/Bogota"
_SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _get_service():
    """Crea el cliente de Google Calendar. Retorna None si no hay credenciales."""
    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sa_json:
        return None
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        info = json.loads(sa_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
        return build("calendar", "v3", credentials=creds)
    except Exception as e:
        logger.error(f"Google Calendar: error iniciando servicio: {e}")
        return None


def _calendar_id() -> str:
    return os.getenv("GOOGLE_CALENDAR_ID", "primary")


def _hora_fin(hora: str) -> str:
    """Suma 1 hora."""
    try:
        h, m = map(int, hora.split(":"))
        fin = datetime(2000, 1, 1, h, m) + timedelta(hours=1)
        return fin.strftime("%H:%M")
    except Exception:
        return hora


def crear_evento_visita(visita_id: int, nombre: str, telefono: str,
                        fecha: str, hora: str, notas: str = "") -> str | None:
    """Crea un evento en Google Calendar. Retorna el event_id o None si falla."""
    service = _get_service()
    if not service:
        return None
    try:
        tel = telefono.lstrip("+")
        desc = f"Lead: {nombre}\nTeléfono: +{tel}"
        if notas:
            desc += f"\nNotas: {notas}"
        event = {
            "summary": f"Visita Torre Fuerte — {nombre}",
            "description": desc,
            "start": {"dateTime": f"{fecha}T{hora}:00", "timeZone": _COL_TZ},
            "end":   {"dateTime": f"{fecha}T{_hora_fin(hora)}:00", "timeZone": _COL_TZ},
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "email", "minutes": 60},
                    {"method": "popup", "minutes": 30},
                ],
            },
        }
        result = service.events().insert(calendarId=_calendar_id(), body=event).execute()
        event_id = result.get("id")
        logger.info(f"Google Calendar: evento creado {event_id} (visita {visita_id})")
        return event_id
    except Exception as e:
        logger.error(f"Google Calendar: error creando evento para visita {visita_id}: {e}")
        return None


def actualizar_evento_visita(event_id: str, nombre: str, telefono: str,
                              fecha: str, hora: str, notas: str = "") -> bool:
    """Actualiza fecha, hora y datos de un evento existente."""
    service = _get_service()
    if not service:
        return False
    try:
        tel = telefono.lstrip("+")
        desc = f"Lead: {nombre}\nTeléfono: +{tel}"
        if notas:
            desc += f"\nNotas: {notas}"
        event = {
            "summary": f"Visita Torre Fuerte — {nombre}",
            "description": desc,
            "start": {"dateTime": f"{fecha}T{hora}:00", "timeZone": _COL_TZ},
            "end":   {"dateTime": f"{fecha}T{_hora_fin(hora)}:00", "timeZone": _COL_TZ},
        }
        service.events().update(calendarId=_calendar_id(), eventId=event_id, body=event).execute()
        logger.info(f"Google Calendar: evento {event_id} actualizado")
        return True
    except Exception as e:
        logger.error(f"Google Calendar: error actualizando evento {event_id}: {e}")
        return False


def eliminar_evento_visita(event_id: str) -> bool:
    """Elimina un evento del calendario."""
    service = _get_service()
    if not service:
        return False
    try:
        service.events().delete(calendarId=_calendar_id(), eventId=event_id).execute()
        logger.info(f"Google Calendar: evento {event_id} eliminado")
        return True
    except Exception as e:
        logger.error(f"Google Calendar: error eliminando evento {event_id}: {e}")
        return False
