# agent/providers/messenger.py — Adaptador para Facebook Messenger e Instagram DMs

import os
import logging
import httpx
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("agentkit")

_API_VERSION = "v21.0"


class ProveedorMessenger(ProveedorWhatsApp):
    """
    Proveedor para Facebook Messenger e Instagram DMs.
    Ambos canales usan la misma API de Meta (Graph API) y el mismo Page Access Token.
    Los mensajes de Messenger se identifican con prefijo 'fb_' y los de Instagram con 'ig_'.
    """

    def __init__(self):
        self.page_token = os.getenv("META_PAGE_TOKEN")
        self.page_id = os.getenv("META_PAGE_ID", "me")
        self.verify_token = os.getenv("META_MESSENGER_VERIFY_TOKEN",
                                      os.getenv("META_VERIFY_TOKEN", "agentkit-verify"))
        self._page_access_token: str | None = None  # cache del Page Access Token

    async def _obtener_page_access_token(self) -> str | None:
        """
        El System User token no sirve directamente para Messenger Send API.
        Se necesita un Page Access Token, que se obtiene llamando a /{page_id}?fields=access_token.
        El resultado se cachea para no llamar la API en cada mensaje.
        """
        if self._page_access_token:
            return self._page_access_token

        if not self.page_token:
            return None

        # Si el page_id es "me", no podemos intercambiar el token — usar directo
        if not self.page_id or self.page_id == "me":
            logger.warning("Messenger: META_PAGE_ID no configurado, usando token directo")
            return self.page_token

        url = f"https://graph.facebook.com/{_API_VERSION}/{self.page_id}"
        params = {"fields": "access_token", "access_token": self.page_token}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(url, params=params)
                if r.status_code == 200:
                    data = r.json()
                    token = data.get("access_token")
                    if token:
                        self._page_access_token = token
                        logger.info("Messenger: Page Access Token obtenido correctamente")
                        return token
                logger.error(f"Messenger: error obteniendo Page Access Token: {r.status_code} {r.text}")
        except Exception as e:
            logger.error(f"Messenger: excepción obteniendo Page Access Token: {e}")

        # Fallback: usar el token original
        return self.page_token

    async def validar_webhook(self, request: Request) -> int | None:
        """Meta requiere verificación GET con hub.verify_token."""
        params = request.query_params
        if (params.get("hub.mode") == "subscribe"
                and params.get("hub.verify_token") == self.verify_token):
            return int(params.get("hub.challenge", "0"))
        return None

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """
        Parsea eventos de Messenger (object=page) e Instagram (object=instagram).
        Ignora: mensajes propios (is_echo), eventos de lectura/typing, y mensajes sin texto.
        """
        try:
            body = await request.json()
        except Exception:
            return []

        objeto = body.get("object", "")
        logger.info(f"Messenger webhook recibido: object={objeto!r} keys={list(body.keys())}")
        # Solo procesar eventos de Messenger o Instagram
        if objeto not in ("page", "instagram"):
            logger.warning(f"Messenger webhook ignorado: object={objeto!r} no reconocido")
            return []

        mensajes = []
        for entry in body.get("entry", []):
            for evento in entry.get("messaging", []):
                # Ignorar eventos que no son mensajes (read, delivery, typing)
                if "message" not in evento:
                    continue

                mensaje = evento["message"]

                # Ignorar mensajes echo (enviados por la página)
                if mensaje.get("is_echo"):
                    continue

                texto = mensaje.get("text", "").strip()
                if not texto:
                    continue

                sender_id = evento.get("sender", {}).get("id", "")
                if not sender_id:
                    continue

                logger.info(f"Messenger mensaje entrante: objeto={objeto} sender={sender_id} texto={texto!r}")
                # Prefijo según canal para separar leads de WhatsApp
                if objeto == "instagram":
                    identificador = f"ig_{sender_id}"
                else:
                    identificador = f"fb_{sender_id}"

                mensajes.append(MensajeEntrante(
                    telefono=identificador,
                    texto=texto,
                    mensaje_id=mensaje.get("mid", f"{objeto}_{sender_id}_{mensaje.get('seq', '')}"),
                    es_propio=False,
                ))

        return mensajes

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """
        Envía un mensaje al lead via Messenger o Instagram.
        El identificador tiene prefijo 'fb_' o 'ig_' que se elimina para obtener el sender_id real.
        Intercambia el System User token por un Page Access Token antes de enviar.
        """
        if not self.page_token:
            logger.warning("META_PAGE_TOKEN no configurado — no se puede enviar mensaje")
            return False

        if telefono.startswith("fb_"):
            recipient_id = telefono[3:]
        elif telefono.startswith("ig_"):
            recipient_id = telefono[3:]
        else:
            recipient_id = telefono

        # Obtener Page Access Token (necesario para Messenger Send API)
        page_token = await self._obtener_page_access_token()
        if not page_token:
            logger.error("Messenger: no se pudo obtener Page Access Token")
            return False

        url = f"https://graph.facebook.com/{_API_VERSION}/{self.page_id}/messages"
        payload = {
            "recipient": {"id": recipient_id},
            "message": {"text": mensaje},
            "messaging_type": "RESPONSE",
        }
        headers = {"Content-Type": "application/json"}
        params = {"access_token": page_token}

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(url, json=payload, headers=headers, params=params)
                if r.status_code != 200:
                    logger.error(f"Error Messenger API {r.status_code}: {r.text}")
                    # Si falla con el page token cacheado, limpiar cache para reintentar la próxima vez
                    self._page_access_token = None
                return r.status_code == 200
        except Exception as e:
            logger.error(f"Excepción enviando mensaje Messenger a {telefono}: {e}")
            return False
