# agent/providers/twilio.py — Adaptador para Twilio WhatsApp
# Generado por AgentKit

import os
import logging
import base64
import httpx
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("agentkit")


class ProveedorTwilio(ProveedorWhatsApp):
    """Proveedor de WhatsApp usando Twilio."""

    def __init__(self):
        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        self.auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        self.phone_number = os.getenv("TWILIO_PHONE_NUMBER")

    def _auth_header(self) -> dict:
        auth = base64.b64encode(f"{self.account_sid}:{self.auth_token}".encode()).decode()
        return {"Authorization": f"Basic {auth}"}

    def _messages_url(self) -> str:
        return f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Parsea el payload form-encoded de Twilio."""
        form = await request.form()
        texto = form.get("Body", "")
        telefono = form.get("From", "").replace("whatsapp:", "")
        mensaje_id = form.get("MessageSid", "")
        if not texto:
            return []
        return [MensajeEntrante(
            telefono=telefono,
            texto=texto,
            mensaje_id=mensaje_id,
            es_propio=False,
        )]

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """Envía mensaje de texto via Twilio."""
        if not all([self.account_sid, self.auth_token, self.phone_number]):
            logger.warning("Variables de Twilio no configuradas")
            return False
        data = {
            "From": f"whatsapp:{self.phone_number}",
            "To": f"whatsapp:{telefono}",
            "Body": mensaje,
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(self._messages_url(), data=data, headers=self._auth_header())
            if r.status_code != 201:
                logger.error(f"Error Twilio texto: {r.status_code} — {r.text}")
            return r.status_code == 201

    async def enviar_media(self, telefono: str, url_media: str, caption: str = "") -> bool:
        """Envía un archivo (PDF, imagen) via URL pública usando Twilio."""
        if not all([self.account_sid, self.auth_token, self.phone_number]):
            logger.warning("Variables de Twilio no configuradas")
            return False
        data = {
            "From": f"whatsapp:{self.phone_number}",
            "To": f"whatsapp:{telefono}",
            "MediaUrl": url_media,
        }
        if caption:
            data["Body"] = caption
        async with httpx.AsyncClient() as client:
            r = await client.post(self._messages_url(), data=data, headers=self._auth_header())
            if r.status_code != 201:
                logger.error(f"Error Twilio media: {r.status_code} — {r.text}")
            return r.status_code == 201
