# agent/providers/__init__.py — Factory de proveedores
# Generado por AgentKit

import os
from agent.providers.base import ProveedorWhatsApp


def obtener_proveedor() -> ProveedorWhatsApp:
    """Retorna el proveedor de WhatsApp configurado en WHATSAPP_PROVIDER."""
    proveedor = os.getenv("WHATSAPP_PROVIDER", "twilio").lower().strip()

    if proveedor == "twilio":
        from agent.providers.twilio import ProveedorTwilio
        return ProveedorTwilio()
    elif proveedor == "meta":
        from agent.providers.meta import ProveedorMeta
        return ProveedorMeta()
    else:
        raise ValueError(f"Proveedor no soportado: {proveedor}. Usa: twilio o meta")
