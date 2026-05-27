# agent/providers/meta.py — Adaptador para Meta WhatsApp Cloud API
# Generado por AgentKit

import os
import logging
import mimetypes
import httpx
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("agentkit")


class ProveedorMeta(ProveedorWhatsApp):
    """Proveedor de WhatsApp usando la API oficial de Meta (Cloud API)."""

    def __init__(self):
        self.access_token = os.getenv("META_ACCESS_TOKEN")
        self.phone_number_id = os.getenv("META_PHONE_NUMBER_ID")
        self.verify_token = os.getenv("META_VERIFY_TOKEN", "agentkit-verify")
        self.api_version = "v21.0"

    async def validar_webhook(self, request: Request):
        """Meta requiere verificación GET con hub.verify_token."""
        params = request.query_params
        mode = params.get("hub.mode")
        token = params.get("hub.verify_token")
        challenge = params.get("hub.challenge")
        if mode == "subscribe" and token == self.verify_token:
            return int(challenge)
        return None

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Parsea el payload anidado de Meta Cloud API."""
        body = await request.json()
        mensajes = []
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    if msg.get("type") == "text":
                        mensajes.append(MensajeEntrante(
                            telefono=msg.get("from", ""),
                            texto=msg.get("text", {}).get("body", ""),
                            mensaje_id=msg.get("id", ""),
                            es_propio=False,
                        ))
        return mensajes

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """Envía mensaje de texto via Meta WhatsApp Cloud API."""
        if not self.access_token or not self.phone_number_id:
            logger.warning("META_ACCESS_TOKEN o META_PHONE_NUMBER_ID no configurados")
            return False
        url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": telefono,
            "type": "text",
            "text": {"body": mensaje},
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, headers=headers)
            if r.status_code != 200:
                logger.error(f"Error Meta API: {r.status_code} — {r.text}")
            return r.status_code == 200

    async def _subir_archivo_local(self, ruta_local: str) -> str | None:
        """Lee un archivo del filesystem local y lo sube a Meta para obtener un media_id."""
        try:
            mime_type, _ = mimetypes.guess_type(ruta_local)
            if not mime_type:
                mime_type = "application/octet-stream"
            with open(ruta_local, "rb") as f:
                contenido = f.read()
            nombre = os.path.basename(ruta_local)
            upload_url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/media"
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(
                    upload_url,
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    data={"messaging_product": "whatsapp", "type": mime_type},
                    files={"file": (nombre, contenido, mime_type)},
                )
            if r.status_code == 200:
                media_id = r.json().get("id")
                logger.info(f"Archivo subido a Meta ({nombre}), media_id: {media_id}")
                return media_id
            logger.error(f"Error subiendo archivo a Meta: {r.status_code} — {r.text}")
            return None
        except Exception as e:
            logger.error(f"Excepción subiendo archivo local a Meta: {e}")
            return None

    async def _subir_video(self, url_video: str) -> str | None:
        """Descarga un MP4 desde URL y lo sube a Meta para obtener un media_id."""
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                dl = await client.get(url_video)
                if dl.status_code != 200:
                    logger.error(f"Error descargando video: {dl.status_code}")
                    return None

                nombre = url_video.split("/")[-1]
                upload_url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/media"
                r = await client.post(
                    upload_url,
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    data={"messaging_product": "whatsapp", "type": "video/mp4"},
                    files={"file": (nombre, dl.content, "video/mp4")},
                )
                if r.status_code == 200:
                    media_id = r.json().get("id")
                    logger.info(f"Video subido a Meta, media_id: {media_id}")
                    return media_id
                logger.error(f"Error subiendo video a Meta: {r.status_code} — {r.text}")
                return None
        except Exception as e:
            logger.error(f"Excepción subiendo video: {e}")
            return None

    async def enviar_imagen_local(self, telefono: str, ruta_archivo: str, caption: str = "") -> bool:
        """Lee imagen del filesystem, la sube a Meta y la envía por media_id (más confiable que link)."""
        if not self.access_token or not self.phone_number_id:
            logger.warning("META_ACCESS_TOKEN o META_PHONE_NUMBER_ID no configurados")
            return False
        media_id = await self._subir_archivo_local(ruta_archivo)
        if not media_id:
            logger.error(f"No se pudo subir imagen local: {ruta_archivo}")
            return False
        api_url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": telefono,
            "type": "image",
            "image": {"id": media_id, "caption": caption},
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    api_url,
                    json=payload,
                    headers={"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"},
                )
                if r.status_code != 200:
                    logger.error(f"Error enviando imagen por media_id: {r.status_code} — {r.text}")
                return r.status_code == 200
        except Exception as e:
            logger.error(f"Excepción enviando imagen local: {e}")
            return False

    async def enviar_media(self, telefono: str, url_media: str, caption: str = "") -> bool:
        """Envía un documento, imagen o video via Meta WhatsApp Cloud API."""
        if not self.access_token or not self.phone_number_id:
            logger.warning("META_ACCESS_TOKEN o META_PHONE_NUMBER_ID no configurados")
            return False

        api_url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        ext = url_media.split(".")[-1].lower()

        if ext == "mp4":
            media_id = await self._subir_video(url_media)
            if not media_id:
                logger.error(f"No se pudo obtener media_id para el video: {url_media}")
                return False
            payload = {
                "messaging_product": "whatsapp",
                "to": telefono,
                "type": "video",
                "video": {"id": media_id, "caption": caption},
            }
        elif ext == "pdf":
            payload = {
                "messaging_product": "whatsapp",
                "to": telefono,
                "type": "document",
                "document": {"link": url_media, "caption": caption},
            }
        else:
            payload = {
                "messaging_product": "whatsapp",
                "to": telefono,
                "type": "image",
                "image": {"link": url_media, "caption": caption},
            }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(api_url, json=payload, headers=headers)
                if r.status_code != 200:
                    logger.error(f"Error Meta media: {r.status_code} — {r.text}")
                return r.status_code == 200
        except Exception as e:
            logger.error(f"Excepción enviando media a Meta: {e}")
            return False
