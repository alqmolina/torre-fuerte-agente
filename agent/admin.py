# agent/admin.py — Panel de admin para atención humana en handoff

import asyncio
import json
import os
import hmac
import hashlib
import secrets
import time
import httpx
import logging
from typing import List
from fastapi import APIRouter, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

logger = logging.getLogger("agentkit")

from agent.memory import (
    obtener_historial,
    obtener_handoffs_activos,
    obtener_perfil_lead,
    guardar_mensaje,
    desactivar_handoff,
    limpiar_historial,
    obtener_metricas,
    obtener_leads_filtrados,
    crear_broadcast,
    actualizar_broadcast,
    registrar_broadcast_log,
    obtener_historial_broadcasts,
    guardar_nota,
    obtener_notas,
    actualizar_lead,
    guardar_visita,
    obtener_visitas_lead,
    obtener_visitas_proximas,
    cancelar_visita,
    obtener_idioma,
    Handoff,
    async_session,
)
from sqlalchemy import select as sa_select

router = APIRouter(prefix="/admin", tags=["admin"])
proveedor = None  # inyectado desde main.py en lifespan

_fallback_password = os.getenv("META_VERIFY_TOKEN", "torrefuerte2024")
ADMIN_USER = os.getenv("TF_ADMIN_USER") or os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("TF_ADMIN_PASSWORD") or os.getenv("ADMIN_PASSWORD", _fallback_password)
_SECRET = ADMIN_PASSWORD

_ICONOS = {"caliente": "🔥", "tibio": "🌡️", "frío": "❄️", "frio": "❄️"}

_DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
_DIAS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_MESES_EN = ["January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December"]


def _fmt_fecha(fecha: str, idioma: str = "es") -> str:
    try:
        from datetime import datetime as _dt
        d = _dt.strptime(fecha, "%Y-%m-%d")
        if idioma == "en":
            return f"{_DIAS_EN[d.weekday()]}, {_MESES_EN[d.month-1]} {d.day}, {d.year}"
        return f"{_DIAS_ES[d.weekday()]}, {d.day} de {_MESES_ES[d.month-1]} de {d.year}"
    except Exception:
        return fecha


def _esc(text: str) -> str:
    return (text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;"))


def _make_token() -> str:
    ts = str(int(time.time()))
    sig = hmac.new(_SECRET.encode(), ts.encode(), hashlib.sha256).hexdigest()
    return f"{ts}.{sig}"


def _valid_token(token: str) -> bool:
    try:
        ts, sig = token.split(".", 1)
        expected = hmac.new(_SECRET.encode(), ts.encode(), hashlib.sha256).hexdigest()
        if not secrets.compare_digest(sig, expected):
            return False
        return (int(time.time()) - int(ts)) < 86400  # válido 24 horas
    except Exception:
        return False


async def _subir_media_meta(file_bytes: bytes, mime_type: str, filename: str) -> str | None:
    """Sube un archivo a Meta API y retorna el media_id."""
    access_token = os.getenv("META_ACCESS_TOKEN")
    phone_number_id = os.getenv("META_PHONE_NUMBER_ID")
    if not access_token or not phone_number_id:
        return None
    url = f"https://graph.facebook.com/v21.0/{phone_number_id}/media"
    async with httpx.AsyncClient() as client:
        r = await client.post(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            data={"messaging_product": "whatsapp", "type": mime_type},
            files={"file": (filename, file_bytes, mime_type)},
            timeout=30,
        )
        if r.status_code == 200:
            return r.json().get("id")
        logger.error(f"Error subiendo media a Meta: {r.status_code} — {r.text}")
        return None


async def _enviar_media_id_meta(telefono: str, media_id: str, tipo: str, filename: str = "") -> bool:
    """Envía un mensaje de media usando media_id obtenido de Meta API."""
    access_token = os.getenv("META_ACCESS_TOKEN")
    phone_number_id = os.getenv("META_PHONE_NUMBER_ID")
    url = f"https://graph.facebook.com/v21.0/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    media_obj: dict = {"id": media_id}
    if tipo == "document" and filename:
        media_obj["filename"] = filename
    payload = {
        "messaging_product": "whatsapp",
        "to": telefono,
        "type": tipo,
        tipo: media_obj,
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(url, json=payload, headers=headers, timeout=30)
        if r.status_code != 200:
            logger.error(f"Error enviando media Meta: {r.status_code} — {r.text}")
        return r.status_code == 200


def _tipo_media(mime_type: str) -> str:
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("audio/"):
        return "audio"
    return "document"


def _autenticado(request: Request) -> bool:
    return _valid_token(request.cookies.get("tf_admin", ""))


# ── Login ────────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    if _autenticado(request):
        return RedirectResponse("/admin", status_code=302)
    error_html = '<p style="color:#e74c3c;font-size:14px;margin-top:8px">Usuario o contraseña incorrectos</p>' if error else ""
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Torre Fuerte — Acceso Asesor</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #f0f4f8; min-height: 100vh;
            display: flex; align-items: center; justify-content: center; }}
    .card {{ background: white; border-radius: 16px; padding: 36px 32px;
             box-shadow: 0 4px 20px rgba(0,0,0,0.1); width: 100%; max-width: 360px; }}
    .logo {{ text-align: center; margin-bottom: 28px; }}
    .logo h1 {{ color: #1a3c5e; font-size: 22px; font-weight: 700; }}
    .logo p {{ color: #888; font-size: 13px; margin-top: 4px; }}
    label {{ display: block; font-size: 13px; font-weight: 600; color: #555; margin-bottom: 6px; }}
    input {{ width: 100%; border: 1px solid #ddd; border-radius: 8px; padding: 11px 14px;
             font-size: 15px; outline: none; transition: border 0.2s; }}
    input:focus {{ border-color: #1a3c5e; }}
    .field {{ margin-bottom: 18px; }}
    button {{ width: 100%; background: #1a3c5e; color: white; border: none;
              border-radius: 8px; padding: 13px; font-size: 15px; font-weight: 600;
              cursor: pointer; margin-top: 4px; }}
    button:hover {{ background: #15304e; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">
      <h1>Torre Fuerte</h1>
      <p>Panel de Asesor</p>
    </div>
    <form method="post" action="/admin/login">
      <div class="field">
        <label>Usuario</label>
        <input type="text" name="usuario" autofocus autocomplete="username">
      </div>
      <div class="field">
        <label>Contraseña</label>
        <input type="password" name="password" autocomplete="current-password">
      </div>
      {error_html}
      <button type="submit">Entrar</button>
    </form>
  </div>
</body>
</html>"""


@router.post("/login")
async def login_submit(usuario: str = Form(...), password: str = Form(...)):
    u_ok = secrets.compare_digest(usuario.strip().encode(), ADMIN_USER.encode())
    p_ok = secrets.compare_digest(password.strip().encode(), ADMIN_PASSWORD.encode())
    if u_ok and p_ok:
        token = _make_token()
        resp = RedirectResponse("/admin", status_code=303)
        resp.set_cookie("tf_admin", token, httponly=True, max_age=86400, samesite="lax")
        return resp
    return RedirectResponse("/admin/login?error=1", status_code=303)


@router.get("/logout")
async def logout():
    resp = RedirectResponse("/admin/login", status_code=302)
    resp.delete_cookie("tf_admin")
    return resp


# ── Lista de handoffs ─────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def admin_index(request: Request):
    if not _autenticado(request):
        return RedirectResponse("/admin/login", status_code=302)

    handoffs = await obtener_handoffs_activos()

    if not handoffs:
        content = """
        <div style="text-align:center;padding:60px 20px;color:#888">
          <div style="font-size:52px;margin-bottom:14px">✅</div>
          <p style="font-size:16px;font-weight:500">Sin conversaciones en transferencia</p>
          <p style="font-size:13px;margin-top:6px;color:#bbb">El bot está atendiendo todas las consultas</p>
        </div>"""
        badge = ""
    else:
        badge = f'<span style="background:#e74c3c;color:white;border-radius:12px;padding:3px 10px;font-size:13px;font-weight:bold;margin-left:auto">{len(handoffs)}</span>'
        cards = ""
        for h in handoffs:
            temp = h["temperatura"].lower()
            icono = _ICONOS.get(temp, "")
            temp_class = temp if temp in ("caliente", "tibio") else "frio"
            nombre_esc = _esc(h["nombre"])
            ultimo_esc = _esc(h["ultimo_mensaje"])
            role_label = "Bot" if h["ultimo_mensaje_role"] == "assistant" else "Lead"
            cards += f"""
            <a href="/admin/chat/{h['telefono']}" style="text-decoration:none;color:inherit;display:block">
              <div class="card {temp_class}">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
                  <span style="font-weight:600;font-size:16px">{nombre_esc}</span>
                  <span class="badge badge-{temp_class}">{icono} {_esc(h['temperatura']).upper()}</span>
                </div>
                <div style="color:#666;font-size:13px;margin-bottom:6px">📱 {_esc(h['telefono'])}</div>
                <div style="color:#555;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
                  <span style="color:#999;font-size:12px">[{role_label}]</span> {ultimo_esc}
                </div>
                <div style="display:flex;justify-content:space-between;margin-top:8px;font-size:12px;color:#999">
                  <span>🏠 {_esc(h['apto']) or 'Apto no especificado'}</span>
                  <span>{_esc(h['ultimo_mensaje_tiempo'])}</span>
                </div>
              </div>
            </a>"""
        content = cards

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Torre Fuerte — Asesor</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f0f4f8; min-height: 100vh; }}
    .header {{ background: #1a3c5e; color: white; padding: 16px 20px; display: flex; align-items: center; gap: 12px; }}
    .container {{ max-width: 620px; margin: 0 auto; padding: 16px; }}
    .card {{ background: white; border-radius: 12px; padding: 16px; margin-bottom: 12px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); border-left: 4px solid #ccc; transition: box-shadow 0.15s; }}
    .card:hover {{ box-shadow: 0 3px 10px rgba(0,0,0,0.12); }}
    .card.caliente {{ border-left-color: #e74c3c; }}
    .card.tibio {{ border-left-color: #f39c12; }}
    .card.frio {{ border-left-color: #3498db; }}
    .badge {{ font-size: 12px; padding: 3px 9px; border-radius: 10px; font-weight: 600; }}
    .badge-caliente {{ background: #fde8e8; color: #c0392b; }}
    .badge-tibio {{ background: #fef3cd; color: #d68910; }}
    .badge-frio {{ background: #dbeafe; color: #1a56db; }}
  </style>
</head>
<body>
  <div class="header">
    <div style="flex:1">
      <div style="font-size:18px;font-weight:600">Torre Fuerte · Panel Asesor</div>
      <div style="font-size:13px;opacity:0.7;margin-top:2px">Conversaciones en transferencia</div>
    </div>
    {badge}
    <a href="/admin/visitas" style="color:rgba(255,255,255,0.8);font-size:12px;text-decoration:none;margin-right:12px">📅 Visitas</a>
    <a href="/admin/broadcast" style="color:rgba(255,255,255,0.8);font-size:12px;text-decoration:none;margin-right:12px">📢 Broadcast</a>
    <a href="/admin/dashboard" style="color:rgba(255,255,255,0.8);font-size:12px;text-decoration:none;margin-right:12px">📊 Métricas</a>
    <a href="/admin/logout" style="color:rgba(255,255,255,0.6);font-size:12px;text-decoration:none">Salir</a>
  </div>
  <div class="container">
    {content}
    <p style="text-align:center;font-size:12px;color:#bbb;margin-top:20px">Actualiza cada 15 segundos</p>
  </div>
  <script>setTimeout(() => location.reload(), 15000);</script>
</body>
</html>"""


# ── Chat con un lead ──────────────────────────────────────────────────────────

@router.get("/chat/{telefono}", response_class=HTMLResponse)
async def admin_chat(telefono: str, request: Request):
    if not _autenticado(request):
        return RedirectResponse("/admin/login", status_code=302)

    historial = await obtener_historial(telefono, limite=100)
    perfil = await obtener_perfil_lead(telefono)
    nombre_raw = perfil.get("nombre") if perfil else None
    apto_raw = perfil.get("apto", "") if perfil else ""
    intencion_raw = perfil.get("intencion", "") if perfil else ""

    if not nombre_raw:
        async with async_session() as _s:
            _r = await _s.execute(sa_select(Handoff).where(Handoff.telefono == telefono))
            _h = _r.scalar_one_or_none()
            if _h and _h.nombre:
                nombre_raw = _h.nombre

    # Obtener resumen del handoff
    resumen_raw = ""
    async with async_session() as _s2:
        _r2 = await _s2.execute(sa_select(Handoff).where(Handoff.telefono == telefono))
        _h2 = _r2.scalar_one_or_none()
        if _h2:
            resumen_raw = _h2.resumen or ""
            if not nombre_raw and _h2.nombre:
                nombre_raw = _h2.nombre

    notas = await obtener_notas(telefono)
    visitas = await obtener_visitas_lead(telefono)

    nombre = _esc(nombre_raw) if nombre_raw else "Desconocido"
    apto = _esc(apto_raw) if apto_raw else ""
    intencion = _esc(intencion_raw.capitalize()) if intencion_raw else ""
    resumen_html = _esc(resumen_raw).replace("\n", "<br>") if resumen_raw else ""

    if visitas:
        visitas_items_html = ""
        for v in visitas:
            color = "#27ae60" if v["estado"] == "confirmada" else "#aaa"
            tachado = "text-decoration:line-through;color:#aaa" if v["estado"] == "cancelada" else ""
            fecha_fmt = _fmt_fecha(v["fecha"])
            visitas_items_html += (
                f'<div style="padding:8px 0;border-bottom:1px solid #a9dfbf;font-size:13px">'
                f'<div style="font-weight:600;color:{color};{tachado}">📅 {_esc(fecha_fmt)} · ⏰ {_esc(v["hora"])}</div>'
                + (f'<div style="font-size:12px;color:#666;margin-top:2px">{_esc(v["notas"])}</div>' if v["notas"] else '')
                + f'<div style="font-size:11px;color:#aaa;margin-top:2px">{v["estado"].upper()}</div>'
                f'</div>'
            )
    else:
        visitas_items_html = '<p style="font-size:12px;color:#bbb;padding:6px 0">Sin visitas agendadas</p>'

    if notas:
        notas_items_html = "".join(
            '<div style="padding:7px 0;border-bottom:1px solid #ede0ff;font-size:13px;color:#333;line-height:1.4">'
            f'<span style="font-size:10px;color:#aaa;margin-right:8px">{_esc(n["creado_at"])}</span>'
            f'{_esc(n["texto"])}</div>'
            for n in notas
        )
    else:
        notas_items_html = '<p style="font-size:12px;color:#bbb;padding:6px 0">Sin notas aún</p>'

    partes_info = []
    if apto:
        partes_info.append(f"🏠 {apto}")
    if intencion:
        partes_info.append(f"💼 {intencion}")
    subtitulo_extra = " &nbsp;·&nbsp; ".join(partes_info) + (" &nbsp;·&nbsp; " if partes_info else "")

    mensajes_html = ""
    for msg in historial:
        contenido = msg["content"]
        if contenido.startswith("[Sistema:"):
            continue
        es_bot = msg["role"] == "assistant"
        align = "flex-end" if es_bot else "flex-start"
        bg = "#1a3c5e" if es_bot else "#ffffff"
        color = "#ffffff" if es_bot else "#222222"
        label = "Bot / Asesor" if es_bot else "Lead"
        contenido_esc = _esc(contenido).replace("\n", "<br>")
        mensajes_html += f"""
        <div style="display:flex;justify-content:{align};margin-bottom:10px;padding:0 4px">
          <div style="max-width:78%;background:{bg};color:{color};padding:10px 14px;
                      border-radius:16px;font-size:14px;line-height:1.5;
                      box-shadow:0 1px 3px rgba(0,0,0,0.1)">
            <div style="font-size:10px;opacity:0.55;margin-bottom:4px;font-weight:600">{label}</div>
            {contenido_esc}
          </div>
        </div>"""

    if not mensajes_html:
        mensajes_html = '<p style="text-align:center;color:#aaa;padding:30px">Sin mensajes aún</p>'

    tel_esc = _esc(telefono)

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Chat {tel_esc}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #e8edf2; height: 100dvh; display: flex; flex-direction: column; }}
    .header {{ background: #1a3c5e; color: white; padding: 12px 16px;
               display: flex; align-items: center; gap: 12px; flex-shrink: 0; }}
    .back {{ color: white; text-decoration: none; font-size: 22px; line-height: 1; }}
    .messages {{ flex: 1; overflow-y: auto; padding: 16px; }}
    .input-area {{ background: white; padding: 10px 14px; display: flex; gap: 10px;
                   align-items: flex-end; flex-shrink: 0; border-top: 1px solid #e0e0e0; }}
    textarea {{ flex: 1; border: 1px solid #ddd; border-radius: 20px; padding: 10px 16px;
                font-size: 15px; resize: none; outline: none; max-height: 120px;
                font-family: inherit; line-height: 1.4; }}
    textarea:focus {{ border-color: #1a3c5e; }}
    .send-btn {{ background: #1a3c5e; color: white; border: none; border-radius: 50%;
                 width: 44px; height: 44px; font-size: 18px; cursor: pointer;
                 flex-shrink: 0; display: flex; align-items: center; justify-content: center; }}
    .send-btn:disabled {{ opacity: 0.4; cursor: not-allowed; }}
    .close-btn {{ background: rgba(255,255,255,0.15); color: white;
                  border: 1px solid rgba(255,255,255,0.3); padding: 6px 14px;
                  border-radius: 20px; font-size: 12px; cursor: pointer; white-space: nowrap; }}
    .close-btn:hover {{ background: rgba(231,76,60,0.8); border-color: transparent; }}
    #sending {{ display:none; position:fixed; bottom:80px; left:50%; transform:translateX(-50%);
                background:#333; color:white; padding:6px 16px; border-radius:20px; font-size:13px; }}
    .attach-btn {{ background: none; border: none; font-size: 22px; cursor: pointer;
                   color: #888; padding: 0 4px; flex-shrink: 0; line-height: 1; }}
    .attach-btn:hover {{ color: #1a3c5e; }}
    #file-preview {{ display:none; background:#e8f0fe; border-top:1px solid #d0dff8;
                     padding:8px 16px; font-size:13px; color:#1a3c5e;
                     display:none; align-items:center; gap:8px; }}
    #file-preview span {{ flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
    #file-preview button {{ background:none; border:none; font-size:16px; cursor:pointer;
                             color:#888; padding:0; line-height:1; }}
  </style>
</head>
<body>
  <div class="header">
    <a href="/admin" class="back">←</a>
    <div style="flex:1;min-width:0">
      <div style="font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
        {nombre + ' &nbsp;·&nbsp; ' if nombre else ''}📱 {tel_esc}
      </div>
      <div style="font-size:12px;opacity:0.7">{subtitulo_extra}Respondiendo como Torre Fuerte</div>
    </div>
    <a href="/admin/visita/{tel_esc}"
       style="background:rgba(255,255,255,0.15);color:white;border:1px solid rgba(255,255,255,0.3);
              padding:6px 12px;border-radius:20px;font-size:12px;text-decoration:none;white-space:nowrap">
      📅 Agendar
    </a>
    <a href="/admin/lead/{tel_esc}/editar"
       style="background:rgba(255,255,255,0.15);color:white;border:1px solid rgba(255,255,255,0.3);
              padding:6px 12px;border-radius:20px;font-size:12px;text-decoration:none;white-space:nowrap">
      ✏️ Editar
    </a>
    <form method="post" action="/admin/close/{tel_esc}" style="margin:0">
      <button type="submit" class="close-btn"
              onclick="return confirm('¿Cerrar handoff y devolver al bot?')">
        Cerrar handoff
      </button>
    </form>
    <form method="post" action="/admin/delete/{tel_esc}" style="margin:0">
      <button type="submit" class="close-btn" style="background:rgba(231,76,60,0.2);border-color:rgba(231,76,60,0.5)"
              onclick="return confirm('¿Eliminar todo el historial de esta conversación? Esta acción no se puede deshacer.')">
        🗑️
      </button>
    </form>
  </div>
  {f'''<div style="background:#fff8e1;border-bottom:1px solid #f0d96a;padding:10px 16px;flex-shrink:0">
    <div style="font-size:11px;font-weight:700;color:#b7860b;margin-bottom:4px;letter-spacing:0.5px">📋 RESUMEN DE LA CONVERSACIÓN</div>
    <div style="font-size:13px;color:#555;line-height:1.5">{resumen_html}</div>
  </div>''' if resumen_html else ''}
  <details style="background:#e8f8f0;border-bottom:1px solid #a9dfbf;flex-shrink:0" open>
    <summary style="padding:10px 16px;cursor:pointer;font-size:11px;font-weight:700;color:#1e8449;letter-spacing:0.5px;user-select:none;list-style:none;display:flex;align-items:center;gap:6px">
      📅 VISITAS AGENDADAS <span style="background:#1e8449;color:white;border-radius:10px;padding:1px 7px;font-size:10px">{len(visitas)}</span>
    </summary>
    <div style="padding:0 16px 12px">
      {visitas_items_html}
      <a href="/admin/visita/{tel_esc}"
         style="display:inline-block;margin-top:10px;background:#1e8449;color:white;
                border-radius:6px;padding:7px 14px;font-size:13px;text-decoration:none;font-weight:600">
        + Agendar nueva visita
      </a>
    </div>
  </details>
  <details style="background:#f5f0ff;border-bottom:1px solid #ddd0f8;flex-shrink:0" {'open' if notas else ''}>
    <summary style="padding:10px 16px;cursor:pointer;font-size:11px;font-weight:700;color:#7c4dbd;letter-spacing:0.5px;user-select:none;list-style:none;display:flex;align-items:center;gap:6px">
      📝 NOTAS INTERNAS <span style="background:#7c4dbd;color:white;border-radius:10px;padding:1px 7px;font-size:10px">{len(notas)}</span>
    </summary>
    <div style="padding:0 16px 12px">
      {notas_items_html}
      <form method="post" action="/admin/nota/{tel_esc}" style="margin-top:10px;display:flex;gap:6px">
        <input type="text" name="texto" placeholder="Agregar nota interna..." required maxlength="500"
               style="flex:1;border:1px solid #c5b4e8;border-radius:6px;padding:7px 10px;font-size:13px;outline:none;font-family:inherit">
        <button type="submit" style="background:#7c4dbd;color:white;border:none;border-radius:6px;padding:7px 14px;font-size:13px;cursor:pointer;white-space:nowrap">Guardar</button>
      </form>
    </div>
  </details>
  <div class="messages" id="msgs">
    {mensajes_html}
  </div>
  <div id="sending">Enviando...</div>
  <div id="file-preview">
    <span id="file-name">📎 archivo</span>
    <button onclick="limpiarArchivo()" title="Quitar archivo">✕</button>
  </div>
  <div class="input-area">
    <input type="file" id="fileInput" accept="image/*,.pdf,.doc,.docx,.xls,.xlsx,.mp4,.mp3"
           style="display:none" onchange="archivoSeleccionado(this)">
    <button class="attach-btn" onclick="document.getElementById('fileInput').click()" title="Adjuntar archivo">📎</button>
    <textarea id="txt" placeholder="Escribe tu mensaje como asesor de Torre Fuerte..."
      rows="1"
      oninput="this.style.height='auto';this.style.height=Math.min(this.scrollHeight,120)+'px'"
      onkeydown="if(event.key==='Enter'&&!event.shiftKey){{event.preventDefault();enviar()}}"></textarea>
    <button class="send-btn" id="sendBtn" onclick="enviar()">➤</button>
  </div>
  <script>
    const msgs = document.getElementById('msgs');
    msgs.scrollTop = msgs.scrollHeight;
    const txt = document.getElementById('txt');

    function archivoSeleccionado(input) {{
      if (input.files && input.files[0]) {{
        const preview = document.getElementById('file-preview');
        document.getElementById('file-name').textContent = '📎 ' + input.files[0].name;
        preview.style.display = 'flex';
      }}
    }}

    function limpiarArchivo() {{
      document.getElementById('fileInput').value = '';
      document.getElementById('file-preview').style.display = 'none';
    }}

    async function enviar() {{
      const btn = document.getElementById('sendBtn');
      const msg = txt.value.trim();
      const fileInput = document.getElementById('fileInput');
      const archivo = fileInput.files[0];
      if (!msg && !archivo) return;

      btn.disabled = true;
      document.getElementById('sending').style.display = 'block';
      try {{
        if (archivo) {{
          const fd = new FormData();
          fd.append('archivo', archivo);
          await fetch('/admin/send-media/{tel_esc}', {{ method: 'POST', body: fd }});
          limpiarArchivo();
        }}
        if (msg) {{
          await fetch('/admin/send/{tel_esc}', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
            body: 'mensaje=' + encodeURIComponent(msg)
          }});
          txt.value = '';
          txt.style.height = 'auto';
        }}
        location.reload();
      }} finally {{
        btn.disabled = false;
        document.getElementById('sending').style.display = 'none';
      }}
    }}

    function _usuarioEditando() {{
      const tag = (document.activeElement?.tagName || '').toLowerCase();
      if (['input', 'textarea', 'select'].includes(tag)) return true;
      const archivo = document.getElementById('fileInput');
      return !!(archivo && archivo.files && archivo.files[0]);
    }}

    function intentarRefresh() {{
      if (!_usuarioEditando() && !txt.value.trim()) {{
        location.reload();
      }} else {{
        setTimeout(intentarRefresh, 4000);
      }}
    }}
    setTimeout(intentarRefresh, 15000);
  </script>
</body>
</html>"""


@router.post("/send/{telefono}")
async def admin_send(telefono: str, request: Request, mensaje: str = Form(...)):
    if not _autenticado(request):
        return RedirectResponse("/admin/login", status_code=302)
    if not mensaje.strip():
        return {"error": "Mensaje vacío"}
    if proveedor:
        await proveedor.enviar_mensaje(telefono, mensaje.strip())
    await guardar_mensaje(telefono, "assistant", mensaje.strip())
    return {"status": "ok"}


@router.post("/send-media/{telefono}")
async def admin_send_media(telefono: str, request: Request, archivo: UploadFile = File(...)):
    if not _autenticado(request):
        return {"error": "No autorizado"}
    file_bytes = await archivo.read()
    mime_type = archivo.content_type or "application/octet-stream"
    filename = archivo.filename or "archivo"
    tipo = _tipo_media(mime_type)

    media_id = await _subir_media_meta(file_bytes, mime_type, filename)
    if not media_id:
        return {"error": "No se pudo subir el archivo a Meta"}

    ok = await _enviar_media_id_meta(telefono, media_id, tipo, filename)
    if ok:
        await guardar_mensaje(telefono, "assistant", f"[Asesor envió {tipo}: {filename}]")
        return {"status": "ok"}
    return {"error": "No se pudo enviar el archivo"}


@router.get("/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    if not _autenticado(request):
        return RedirectResponse("/admin/login", status_code=302)

    m = await obtener_metricas()

    def barra(valor, maximo, color):
        pct = round(valor / maximo * 100) if maximo else 0
        return f'<div style="height:8px;background:#eee;border-radius:4px;margin-top:4px"><div style="width:{pct}%;height:100%;background:{color};border-radius:4px"></div></div>'

    def fila_bar(label, valor, total, color, emoji=""):
        pct = round(valor / total * 100) if total else 0
        return f"""
        <div style="margin-bottom:12px">
          <div style="display:flex;justify-content:space-between;font-size:14px">
            <span>{emoji} {_esc(label)}</span>
            <span style="font-weight:600">{valor} <span style="color:#999;font-weight:400">({pct}%)</span></span>
          </div>
          {barra(valor, total, color)}
        </div>"""

    # Temperatura
    total_leads = m["total_leads"]
    temp_html = ""
    temp_config = [("caliente", "#e74c3c", "🔥"), ("tibio", "#f39c12", "🌡️"), ("frío", "#3498db", "❄️"), ("frio", "#3498db", "❄️")]
    for temp, color, emoji in temp_config:
        v = m["por_temperatura"].get(temp, 0)
        if v:
            temp_html += fila_bar(temp.upper(), v, total_leads, color, emoji)
    if not temp_html:
        temp_html = '<p style="color:#aaa;font-size:13px">Sin datos aún</p>'

    # Intención
    int_html = ""
    int_config = [("vivir", "#27ae60", "🏠"), ("inversión", "#9b59b6", "💼"), ("inversion", "#9b59b6", "💼")]
    for intent, color, emoji in int_config:
        v = m["por_intencion"].get(intent, 0)
        if v:
            int_html += fila_bar(intent.capitalize(), v, total_leads, color, emoji)
    if not int_html:
        int_html = '<p style="color:#aaa;font-size:13px">Sin datos aún</p>'

    # Apartamentos top
    aptos_html = ""
    max_apto = m["aptos_top"][0][1] if m["aptos_top"] else 1
    for apto, cnt in m["aptos_top"]:
        aptos_html += fila_bar(apto, cnt, max_apto, "#1a3c5e", "🏢")
    if not aptos_html:
        aptos_html = '<p style="color:#aaa;font-size:13px">Sin datos aún</p>'

    # Habitaciones
    hab_html = ""
    max_hab = m["por_habitaciones"][0][1] if m["por_habitaciones"] else 1
    for hab, cnt in m["por_habitaciones"]:
        hab_html += fila_bar(f"{hab} hab.", cnt, max_hab, "#16a085", "🛏️")
    if not hab_html:
        hab_html = '<p style="color:#aaa;font-size:13px">Sin datos aún</p>'

    # Idioma
    total_pref = sum(m["por_idioma"].values()) or 1
    idioma_html = fila_bar("Español", m["por_idioma"].get("es", 0), total_pref, "#e67e22", "🇨🇴")
    idioma_html += fila_bar("English", m["por_idioma"].get("en", 0), total_pref, "#2980b9", "🇺🇸")

    seg1 = m["seguimientos_por_numero"].get(1, 0)
    seg2 = m["seguimientos_por_numero"].get(2, 0)

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Torre Fuerte — Dashboard</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f0f4f8; }}
    .header {{ background: #1a3c5e; color: white; padding: 16px 20px; display: flex; align-items: center; gap: 12px; }}
    .container {{ max-width: 900px; margin: 0 auto; padding: 20px 16px; }}
    .grid-4 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 20px; }}
    .grid-2 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 14px; margin-bottom: 14px; }}
    .kpi {{ background: white; border-radius: 12px; padding: 18px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); text-align: center; }}
    .kpi .val {{ font-size: 36px; font-weight: 700; color: #1a3c5e; line-height: 1.1; }}
    .kpi .lbl {{ font-size: 12px; color: #888; margin-top: 4px; text-transform: uppercase; letter-spacing: 0.5px; }}
    .kpi .sub {{ font-size: 11px; color: #bbb; margin-top: 2px; }}
    .card {{ background: white; border-radius: 12px; padding: 18px 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }}
    .card h3 {{ font-size: 13px; font-weight: 700; color: #1a3c5e; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 14px; }}
    .badge {{ display:inline-block; padding:3px 10px; border-radius:12px; font-size:12px; font-weight:600; }}
  </style>
</head>
<body>
  <div class="header">
    <a href="/admin" style="color:white;text-decoration:none;font-size:20px">←</a>
    <div style="flex:1">
      <div style="font-size:18px;font-weight:600">Torre Fuerte · Dashboard</div>
      <div style="font-size:12px;opacity:0.7">Métricas del agente en tiempo real</div>
    </div>
    <a href="/admin/logout" style="color:rgba(255,255,255,0.6);font-size:12px;text-decoration:none">Salir</a>
  </div>

  <div class="container">
    <div class="grid-4">
      <div class="kpi">
        <div class="val">{m['total_leads']}</div>
        <div class="lbl">Total Leads</div>
        <div class="sub">+{m['leads_hoy']} hoy · +{m['leads_semana']} esta semana</div>
      </div>
      <div class="kpi">
        <div class="val">{m['total_conversaciones']}</div>
        <div class="lbl">Conversaciones</div>
        <div class="sub">Números únicos atendidos</div>
      </div>
      <div class="kpi">
        <div class="val" style="color:{'#e74c3c' if m['handoffs_activos'] else '#27ae60'}">{m['handoffs_activos']}</div>
        <div class="lbl">Handoffs Activos</div>
        <div class="sub">{m['total_handoffs']} transferencias totales</div>
      </div>
      <div class="kpi">
        <div class="val">{m['tasa_conversion']}%</div>
        <div class="lbl">Tasa Calificación</div>
        <div class="sub">Leads / conversaciones</div>
      </div>
    </div>

    <div class="grid-4" style="margin-bottom:20px">
      <div class="kpi" style="background:#fff8e1;border:1px solid #f0d96a">
        <div class="val" style="color:#d68910">{m['leads_hoy']}</div>
        <div class="lbl">Leads hoy</div>
      </div>
      <div class="kpi" style="background:#eafaf1;border:1px solid #a9dfbf">
        <div class="val" style="color:#27ae60">{m['leads_semana']}</div>
        <div class="lbl">Últimos 7 días</div>
      </div>
      <div class="kpi" style="background:#eaf2fb;border:1px solid #aed6f1">
        <div class="val" style="color:#2980b9">{m['leads_mes']}</div>
        <div class="lbl">Últimos 30 días</div>
      </div>
      <div class="kpi" style="background:#f4f6f7;border:1px solid #d5dbdb">
        <div class="val" style="color:#555">{m['total_seguimientos']}</div>
        <div class="lbl">Seguimientos enviados</div>
        <div class="sub">#{seg1} primeros · #{seg2} segundos</div>
      </div>
    </div>

    <div class="grid-2">
      <div class="card">
        <h3>🌡️ Temperatura de leads</h3>
        {temp_html}
      </div>
      <div class="card">
        <h3>💼 Intención de compra</h3>
        {int_html}
      </div>
    </div>

    <div class="grid-2">
      <div class="card">
        <h3>🏢 Apartamentos más consultados</h3>
        {aptos_html}
      </div>
      <div class="card">
        <h3>🛏️ Habitaciones solicitadas</h3>
        {hab_html}
      </div>
    </div>

    <div class="grid-2">
      <div class="card">
        <h3>🌐 Idioma de los leads</h3>
        {idioma_html}
      </div>
      <div class="card" style="display:flex;align-items:center;justify-content:center;flex-direction:column;gap:8px">
        <a href="/leads/export" style="display:block;background:#1a3c5e;color:white;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px;width:100%;text-align:center">
          📥 Descargar Excel de Leads
        </a>
        <a href="/admin/broadcast" style="display:block;background:#1a5276;color:white;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px;width:100%;text-align:center">
          📢 Enviar Broadcast
        </a>
        <a href="/admin" style="display:block;background:#f0f4f8;color:#1a3c5e;padding:10px 24px;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px;border:1px solid #d0dce8;width:100%;text-align:center">
          💬 Ver conversaciones activas
        </a>
      </div>
    </div>

    <p style="text-align:center;font-size:12px;color:#bbb;margin-top:20px">Actualiza al recargar la página</p>
  </div>
</body>
</html>"""


# ── Broadcast ─────────────────────────────────────────────────────────────────

async def _ejecutar_broadcast(broadcast_id: int, leads: list[dict], mensaje: str):
    """Worker en background: envía el mensaje a cada lead con pausa entre envíos."""
    enviados = 0
    fallidos = 0
    for lead in leads:
        nombre = lead["nombre"] or ""
        texto = mensaje.replace("{{nombre}}", nombre).replace("{{name}}", nombre)
        try:
            ok = await proveedor.enviar_mensaje(lead["telefono"], texto) if proveedor else False
            await registrar_broadcast_log(broadcast_id, lead["telefono"], nombre, ok)
            if ok:
                enviados += 1
            else:
                fallidos += 1
        except Exception as exc:
            logger.error(f"Broadcast error {lead['telefono']}: {exc}")
            fallidos += 1
            await registrar_broadcast_log(broadcast_id, lead["telefono"], nombre, False)
        await asyncio.sleep(0.35)
    await actualizar_broadcast(broadcast_id, enviados, fallidos)
    logger.info(f"Broadcast {broadcast_id} completado: {enviados} ok / {fallidos} fallidos")


@router.get("/broadcast", response_class=HTMLResponse)
async def broadcast_form(request: Request):
    if not _autenticado(request):
        return RedirectResponse("/admin/login", status_code=302)

    return """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Torre Fuerte — Broadcast</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f0f4f8; }
    .header { background: #1a3c5e; color: white; padding: 16px 20px; display: flex; align-items: center; gap: 12px; }
    .container { max-width: 660px; margin: 0 auto; padding: 20px 16px; }
    .card { background: white; border-radius: 12px; padding: 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); margin-bottom: 16px; }
    .card h3 { font-size: 13px; font-weight: 700; color: #1a3c5e; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 16px; }
    label { font-size: 13px; font-weight: 600; color: #555; }
    select { border: 1px solid #ddd; border-radius: 8px; padding: 9px 12px; font-size: 14px; width: 100%; outline: none; }
    select:focus { border-color: #1a3c5e; }
    .check-group { display: flex; gap: 16px; flex-wrap: wrap; margin-top: 8px; }
    .check-group label { font-weight: 400; display: flex; align-items: center; gap: 6px; cursor: pointer; }
    textarea { width: 100%; border: 1px solid #ddd; border-radius: 8px; padding: 12px 14px;
               font-size: 15px; font-family: inherit; resize: vertical; outline: none; line-height: 1.5; }
    textarea:focus { border-color: #1a3c5e; }
    .btn-primary { width: 100%; background: #1a3c5e; color: white; border: none; border-radius: 8px;
                   padding: 14px; font-size: 16px; font-weight: 600; cursor: pointer; }
    .btn-primary:hover { background: #15304e; }
    .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
    .btn-sec { background: #f0f4f8; color: #1a3c5e; border: 1px solid #d0dce8; border-radius: 8px;
               padding: 9px 18px; font-size: 14px; font-weight: 600; cursor: pointer; }
    .btn-sec:hover { background: #e2eaf3; }
    .preview-box { margin-top: 12px; padding: 10px 14px; border-radius: 8px; font-size: 14px;
                   background: #eaf2fb; color: #1a56db; display: none; }
    .field-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 14px; }
  </style>
</head>
<body>
  <div class="header">
    <a href="/admin" style="color:white;text-decoration:none;font-size:20px">←</a>
    <div style="flex:1">
      <div style="font-size:18px;font-weight:600">Torre Fuerte · Broadcast</div>
      <div style="font-size:12px;opacity:0.7">Envío masivo segmentado a leads</div>
    </div>
    <a href="/admin/broadcast/historial" style="color:rgba(255,255,255,0.8);font-size:12px;text-decoration:none;margin-right:12px">📋 Historial</a>
    <a href="/admin/logout" style="color:rgba(255,255,255,0.6);font-size:12px;text-decoration:none">Salir</a>
  </div>

  <div class="container">
    <div class="card">
      <h3>🎯 Filtros de destinatarios</h3>
      <label>Temperatura</label>
      <div class="check-group">
        <label><input type="checkbox" name="temp" value="caliente" checked> 🔥 Caliente</label>
        <label><input type="checkbox" name="temp" value="tibio" checked> 🌡️ Tibio</label>
        <label><input type="checkbox" name="temp" value="frío" checked> ❄️ Frío</label>
      </div>
      <div class="field-row">
        <div>
          <label>Idioma</label>
          <select id="idioma" style="margin-top:6px">
            <option value="">Todos</option>
            <option value="es">🇨🇴 Español</option>
            <option value="en">🇺🇸 English</option>
          </select>
        </div>
        <div>
          <label>Intención</label>
          <select id="intencion" style="margin-top:6px">
            <option value="">Todos</option>
            <option value="vivir">🏠 Vivir</option>
            <option value="inversión">💼 Inversión</option>
          </select>
        </div>
      </div>
      <div style="margin-top:14px">
        <button class="btn-sec" onclick="calcularDestinatarios()">🔍 Calcular destinatarios</button>
        <div class="preview-box" id="preview"></div>
      </div>
    </div>

    <div class="card">
      <h3>✉️ Mensaje</h3>
      <p style="font-size:12px;color:#888;margin-bottom:10px">
        Usa <code style="background:#f4f6f7;padding:1px 5px;border-radius:4px">{{nombre}}</code> para personalizar con el nombre del lead.
      </p>
      <textarea id="mensaje" rows="6" placeholder="Hola {{nombre}}, te escribimos desde Torre Fuerte con una novedad especial..."></textarea>
      <div style="text-align:right;font-size:12px;color:#aaa;margin-top:4px" id="chars">0 caracteres</div>
    </div>

    <button class="btn-primary" id="send-btn" onclick="enviarBroadcast()">📤 Enviar broadcast</button>
    <p style="text-align:center;font-size:12px;color:#aaa;margin-top:10px">
      El envío se realiza en segundo plano. Monitorea el progreso en el historial.
    </p>
  </div>

  <script>
    document.getElementById('mensaje').addEventListener('input', function() {
      document.getElementById('chars').textContent = this.value.length + ' caracteres';
    });

    function _filtros() {
      const temps = Array.from(document.querySelectorAll('input[name="temp"]:checked')).map(c => c.value);
      return {
        temperaturas: temps,
        idioma: document.getElementById('idioma').value,
        intencion: document.getElementById('intencion').value,
      };
    }

    async function calcularDestinatarios() {
      const f = _filtros();
      if (!f.temperaturas.length) { alert('Selecciona al menos una temperatura'); return; }
      const body = new URLSearchParams({ idioma: f.idioma, intencion: f.intencion });
      f.temperaturas.forEach(t => body.append('temperaturas', t));
      const r = await fetch('/admin/broadcast/preview', { method: 'POST', body });
      const data = await r.json();
      const box = document.getElementById('preview');
      box.style.display = 'block';
      box.innerHTML = '<strong>' + data.count + ' leads</strong> recibirán este mensaje';
    }

    async function enviarBroadcast() {
      const f = _filtros();
      const mensaje = document.getElementById('mensaje').value.trim();
      if (!mensaje) { alert('Escribe un mensaje antes de enviar'); return; }
      if (!f.temperaturas.length) { alert('Selecciona al menos una temperatura'); return; }
      if (!confirm('¿Confirmar envío del broadcast? Esta acción enviará mensajes de WhatsApp reales.')) return;

      const btn = document.getElementById('send-btn');
      btn.disabled = true;
      btn.textContent = '⏳ Iniciando envío...';

      const body = new URLSearchParams({ idioma: f.idioma, intencion: f.intencion, mensaje });
      f.temperaturas.forEach(t => body.append('temperaturas', t));

      const r = await fetch('/admin/broadcast', { method: 'POST', body });
      const data = await r.json();
      if (data.ok) {
        window.location.href = '/admin/broadcast/historial';
      } else {
        btn.disabled = false;
        btn.textContent = '📤 Enviar broadcast';
        alert('Error: ' + (data.error || 'No se pudo iniciar el broadcast'));
      }
    }
  </script>
</body>
</html>"""


@router.post("/broadcast/preview")
async def broadcast_preview(
    request: Request,
    temperaturas: List[str] = Form(default=[]),
    idioma: str = Form(default=""),
    intencion: str = Form(default=""),
):
    if not _autenticado(request):
        return JSONResponse({"error": "No autorizado"}, status_code=401)
    leads = await obtener_leads_filtrados(temperaturas or None, idioma, intencion)
    return JSONResponse({"count": len(leads)})


@router.post("/broadcast")
async def broadcast_enviar(
    request: Request,
    temperaturas: List[str] = Form(default=[]),
    idioma: str = Form(default=""),
    intencion: str = Form(default=""),
    mensaje: str = Form(...),
):
    if not _autenticado(request):
        return JSONResponse({"error": "No autorizado"}, status_code=401)
    if not mensaje.strip():
        return JSONResponse({"error": "Mensaje vacío"})
    if not temperaturas:
        return JSONResponse({"error": "Selecciona al menos una temperatura"})

    leads = await obtener_leads_filtrados(temperaturas, idioma, intencion)
    if not leads:
        return JSONResponse({"error": "No hay leads que coincidan con los filtros seleccionados"})

    filtros_str = json.dumps({"temperaturas": temperaturas, "idioma": idioma, "intencion": intencion}, ensure_ascii=False)
    broadcast_id = await crear_broadcast(mensaje.strip(), filtros_str, len(leads))
    asyncio.create_task(_ejecutar_broadcast(broadcast_id, leads, mensaje.strip()))

    return JSONResponse({"ok": True, "id": broadcast_id, "total": len(leads)})


@router.get("/broadcast/historial", response_class=HTMLResponse)
async def broadcast_historial(request: Request):
    if not _autenticado(request):
        return RedirectResponse("/admin/login", status_code=302)

    campanas = await obtener_historial_broadcasts()

    filas = ""
    for c in campanas:
        estado_color = "#27ae60" if c["estado"] == "completado" else "#f39c12"
        estado_label = "✅ Completado" if c["estado"] == "completado" else "⏳ En proceso"
        pct = round(c["enviados"] / c["total"] * 100) if c["total"] else 0
        filas += f"""
        <div style="background:white;border-radius:10px;padding:16px;margin-bottom:12px;box-shadow:0 1px 4px rgba(0,0,0,0.08)">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:8px">
            <div style="font-size:14px;color:#333;line-height:1.4;flex:1">"{_esc(c['mensaje'])}{"..." if len(c["mensaje"]) >= 120 else ""}"</div>
            <span style="color:{estado_color};font-size:12px;font-weight:600;white-space:nowrap">{estado_label}</span>
          </div>
          <div style="display:flex;gap:16px;font-size:12px;color:#888;flex-wrap:wrap">
            <span>📅 {_esc(c['creado_at'])}</span>
            <span>👥 {c['total']} destinatarios</span>
            <span style="color:#27ae60">✔ {c['enviados']} enviados</span>
            {f'<span style="color:#e74c3c">✗ {c["fallidos"]} fallidos</span>' if c['fallidos'] else ''}
          </div>
          <div style="margin-top:8px;height:6px;background:#eee;border-radius:3px">
            <div style="width:{pct}%;height:100%;background:#1a3c5e;border-radius:3px"></div>
          </div>
        </div>"""

    if not filas:
        filas = '<div style="text-align:center;padding:60px;color:#aaa"><div style="font-size:40px;margin-bottom:12px">📭</div><p>Sin campañas aún</p></div>'

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Broadcast — Historial</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f0f4f8; }}
    .header {{ background: #1a3c5e; color: white; padding: 16px 20px; display: flex; align-items: center; gap: 12px; }}
    .container {{ max-width: 700px; margin: 0 auto; padding: 20px 16px; }}
  </style>
</head>
<body>
  <div class="header">
    <a href="/admin/broadcast" style="color:white;text-decoration:none;font-size:20px">←</a>
    <div style="flex:1">
      <div style="font-size:18px;font-weight:600">Broadcast · Historial</div>
      <div style="font-size:12px;opacity:0.7">Últimas 30 campañas</div>
    </div>
    <a href="/admin/broadcast" style="color:rgba(255,255,255,0.8);font-size:12px;text-decoration:none;margin-right:12px">+ Nueva campaña</a>
    <a href="/admin/logout" style="color:rgba(255,255,255,0.6);font-size:12px;text-decoration:none">Salir</a>
  </div>
  <div class="container">
    {filas}
  </div>
  <script>
    // Recargar si hay campañas en proceso
    const enProceso = document.querySelector('[style*="f39c12"]');
    if (enProceso) setTimeout(() => location.reload(), 5000);
  </script>
</body>
</html>"""


@router.get("/visitas", response_class=HTMLResponse)
async def admin_visitas(request: Request):
    if not _autenticado(request):
        return RedirectResponse("/admin/login", status_code=302)

    visitas = await obtener_visitas_proximas()

    filas = ""
    for v in visitas:
        fecha_fmt = _fmt_fecha(v["fecha"])
        pasada = v.get("pasada", False)
        borde = "#aaa" if pasada else "#27ae60"
        color_fecha = "#aaa" if pasada else "#27ae60"
        opacidad = "opacity:0.65;" if pasada else ""
        etiqueta = '<span style="background:#f0f4f8;color:#888;border-radius:10px;padding:1px 8px;font-size:11px;font-weight:600;margin-left:8px">PASADA</span>' if pasada else ""
        filas += (
            f'<div style="background:white;border-radius:10px;padding:14px 16px;margin-bottom:10px;'
            f'box-shadow:0 1px 4px rgba(0,0,0,0.08);border-left:4px solid {borde};{opacidad}">'
            f'<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">'
            f'<div>'
            f'<div style="font-weight:600;font-size:15px;color:#1a3c5e">{_esc(v["nombre"] or "Sin nombre")}{etiqueta}</div>'
            f'<div style="font-size:13px;color:{color_fecha};font-weight:600;margin-top:4px">📅 {_esc(fecha_fmt)} · ⏰ {_esc(v["hora"])}</div>'
            + (f'<div style="font-size:13px;color:#666;margin-top:4px">{_esc(v["notas"])}</div>' if v["notas"] else '')
            + f'<div style="font-size:12px;color:#999;margin-top:4px">📱 {_esc(v["telefono"])}</div>'
            f'</div>'
            f'<div style="display:flex;flex-direction:column;gap:6px;flex-shrink:0">'
            f'<a href="/admin/chat/{_esc(v["telefono"])}" style="background:#1a3c5e;color:white;border-radius:6px;'
            f'padding:6px 12px;font-size:12px;text-decoration:none;font-weight:600;white-space:nowrap">Ver chat</a>'
            f'<form method="post" action="/admin/visita/{v["id"]}/cancelar" style="margin:0">'
            f'<button type="submit" onclick="return confirm(\'¿Cancelar esta visita?\')" '
            f'style="background:#fde8e8;color:#c0392b;border:1px solid #f5b7b1;border-radius:6px;'
            f'padding:6px 12px;font-size:12px;cursor:pointer;width:100%;white-space:nowrap">Cancelar</button>'
            f'</form>'
            f'</div></div></div>'
        )

    if not filas:
        filas = '<div style="text-align:center;padding:60px;color:#aaa"><div style="font-size:40px;margin-bottom:12px">📅</div><p>Sin visitas confirmadas aún</p></div>'

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Visitas — Torre Fuerte</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f0f4f8; }}
    .header {{ background: #1a3c5e; color: white; padding: 16px 20px; display: flex; align-items: center; gap: 12px; }}
    .container {{ max-width: 700px; margin: 0 auto; padding: 20px 16px; }}
  </style>
</head>
<body>
  <div class="header">
    <a href="/admin" style="color:white;text-decoration:none;font-size:20px">←</a>
    <div style="flex:1">
      <div style="font-size:18px;font-weight:600">Torre Fuerte · Visitas</div>
      <div style="font-size:12px;opacity:0.7">Próximas visitas al proyecto</div>
    </div>
    <a href="/admin/logout" style="color:rgba(255,255,255,0.6);font-size:12px;text-decoration:none">Salir</a>
  </div>
  <div class="container">
    {filas}
  </div>
</body>
</html>"""


@router.get("/visita/{telefono}", response_class=HTMLResponse)
async def visita_form(telefono: str, request: Request):
    if not _autenticado(request):
        return RedirectResponse("/admin/login", status_code=302)

    perfil = await obtener_perfil_lead(telefono)
    nombre_v = _esc((perfil or {}).get("nombre") or "")
    tel_esc = _esc(telefono)

    from datetime import date as _date
    hoy = _date.today().isoformat()

    horas = ["09:00","09:30","10:00","10:30","11:00","11:30",
             "14:00","14:30","15:00","15:30","16:00","16:30","17:00"]
    opciones_hora = "".join(f'<option value="{h}">{h}</option>' for h in horas)

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Agendar Visita</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f0f4f8; }}
    .header {{ background: #1a3c5e; color: white; padding: 16px 20px; display: flex; align-items: center; gap: 12px; }}
    .container {{ max-width: 520px; margin: 0 auto; padding: 20px 16px; }}
    .card {{ background: white; border-radius: 12px; padding: 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }}
    label {{ display: block; font-size: 13px; font-weight: 600; color: #555; margin-bottom: 6px; }}
    input[type=text], input[type=date], select, textarea {{
      width: 100%; border: 1px solid #ddd; border-radius: 8px;
      padding: 10px 12px; font-size: 15px; outline: none; font-family: inherit; }}
    input:focus, select:focus, textarea:focus {{ border-color: #1a3c5e; }}
    .field {{ margin-bottom: 16px; }}
    .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
    .btn-save {{ width: 100%; background: #1e8449; color: white; border: none; border-radius: 8px;
                 padding: 14px; font-size: 16px; font-weight: 600; cursor: pointer; margin-top: 4px; }}
    .btn-save:hover {{ background: #196f3d; }}
    .check-row {{ display: flex; align-items: center; gap: 8px; font-size: 14px; color: #555; cursor: pointer; }}
  </style>
</head>
<body>
  <div class="header">
    <a href="/admin/chat/{tel_esc}" style="color:white;text-decoration:none;font-size:20px">←</a>
    <div>
      <div style="font-size:17px;font-weight:600">Agendar Visita</div>
      <div style="font-size:12px;opacity:0.7">📱 {tel_esc}</div>
    </div>
  </div>
  <div class="container">
    <form method="post" action="/admin/visita/{tel_esc}">
      <div class="card">
        <div class="field">
          <label>Nombre del lead</label>
          <input type="text" name="nombre" value="{nombre_v}" placeholder="Nombre" required>
        </div>
        <div class="grid-2">
          <div class="field">
            <label>Fecha de visita</label>
            <input type="date" name="fecha" min="{hoy}" required>
          </div>
          <div class="field">
            <label>Hora</label>
            <select name="hora">{opciones_hora}</select>
          </div>
        </div>
        <div class="field" style="margin-bottom:16px">
          <label>Notas internas (opcional)</label>
          <textarea name="notas" rows="2" placeholder="Ej: Interesado en penthouse, viene con pareja..."></textarea>
        </div>
        <label class="check-row" style="margin-bottom:20px">
          <input type="checkbox" name="enviar_wp" value="1" checked style="width:auto;accent-color:#1e8449">
          Enviar confirmación por WhatsApp al lead
        </label>
        <button type="submit" class="btn-save">📅 Confirmar visita</button>
      </div>
    </form>
  </div>
</body>
</html>"""


@router.post("/visita/{telefono}")
async def visita_save(
    telefono: str,
    request: Request,
    nombre: str = Form(...),
    fecha: str = Form(...),
    hora: str = Form(...),
    notas: str = Form(default=""),
    enviar_wp: str = Form(default=""),
):
    if not _autenticado(request):
        return RedirectResponse("/admin/login", status_code=302)

    await guardar_visita(telefono, nombre.strip(), fecha, hora, notas.strip())

    if enviar_wp == "1" and proveedor:
        idioma = await obtener_idioma(telefono) or "es"
        fecha_fmt = _fmt_fecha(fecha, idioma)
        notas_line = f"\n📝 {notas.strip()}" if notas.strip() else ""
        if idioma == "en":
            msg = (
                f"🏢 *Torre Fuerte — Visit Confirmed*\n\n"
                f"Hello {nombre.strip()}, your visit to Torre Fuerte is confirmed:\n\n"
                f"📅 *Date:* {fecha_fmt}\n"
                f"⏰ *Time:* {hora}"
                f"{notas_line}\n\n"
                f"We look forward to seeing you! To reschedule, just reply here."
            )
        else:
            msg = (
                f"🏢 *Torre Fuerte — Visita confirmada*\n\n"
                f"Hola {nombre.strip()}, confirmamos tu visita al proyecto:\n\n"
                f"📅 *Fecha:* {fecha_fmt}\n"
                f"⏰ *Hora:* {hora}"
                f"{notas_line}\n\n"
                f"¡Te esperamos! Si necesitas cambiar la fecha, escríbenos aquí."
            )
        await proveedor.enviar_mensaje(telefono, msg)

    return RedirectResponse(f"/admin/chat/{telefono}", status_code=303)


@router.post("/visita/{visita_id}/cancelar")
async def visita_cancelar(visita_id: int, request: Request):
    if not _autenticado(request):
        return RedirectResponse("/admin/login", status_code=302)
    await cancelar_visita(visita_id)
    referer = request.headers.get("referer", "/admin/visitas")
    return RedirectResponse(referer, status_code=303)


@router.get("/lead/{telefono}/editar", response_class=HTMLResponse)
async def lead_editar_form(telefono: str, request: Request):
    if not _autenticado(request):
        return RedirectResponse("/admin/login", status_code=302)

    perfil = await obtener_perfil_lead(telefono)
    if not perfil:
        return RedirectResponse(f"/admin/chat/{telefono}", status_code=302)

    tel_esc = _esc(telefono)
    nombre_v = _esc(perfil.get("nombre") or "")
    apto_v = _esc(perfil.get("apto") or "")
    hab_v = _esc(perfil.get("habitaciones") or "")
    temp_v = perfil.get("temperatura") or ""
    int_v = perfil.get("intencion") or ""

    def sel_temp(val):
        return 'selected' if temp_v == val else ''

    def sel_int(val):
        return 'selected' if int_v in (val, val.replace("ó", "o")) else ''

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Editar Lead</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f0f4f8; }}
    .header {{ background: #1a3c5e; color: white; padding: 16px 20px; display: flex; align-items: center; gap: 12px; }}
    .container {{ max-width: 520px; margin: 0 auto; padding: 20px 16px; }}
    .card {{ background: white; border-radius: 12px; padding: 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); margin-bottom: 14px; }}
    label {{ display: block; font-size: 13px; font-weight: 600; color: #555; margin-bottom: 6px; }}
    input[type=text], select {{ width: 100%; border: 1px solid #ddd; border-radius: 8px;
                                padding: 10px 12px; font-size: 15px; outline: none; font-family: inherit; }}
    input[type=text]:focus, select:focus {{ border-color: #1a3c5e; }}
    .field {{ margin-bottom: 16px; }}
    .temp-btns {{ display: flex; gap: 8px; }}
    .temp-btn {{ flex: 1; padding: 10px; border: 2px solid #ddd; border-radius: 8px; background: white;
                 font-size: 14px; font-weight: 600; cursor: pointer; text-align: center; transition: all 0.15s; }}
    .temp-btn.active-caliente {{ border-color: #e74c3c; background: #fde8e8; color: #c0392b; }}
    .temp-btn.active-tibio {{ border-color: #f39c12; background: #fef3cd; color: #d68910; }}
    .temp-btn.active-frio {{ border-color: #3498db; background: #dbeafe; color: #1a56db; }}
    .btn-save {{ width: 100%; background: #1a3c5e; color: white; border: none; border-radius: 8px;
                 padding: 14px; font-size: 16px; font-weight: 600; cursor: pointer; }}
    .btn-save:hover {{ background: #15304e; }}
  </style>
</head>
<body>
  <div class="header">
    <a href="/admin/chat/{tel_esc}" style="color:white;text-decoration:none;font-size:20px">←</a>
    <div>
      <div style="font-size:17px;font-weight:600">Editar Lead</div>
      <div style="font-size:12px;opacity:0.7">📱 {tel_esc}</div>
    </div>
  </div>

  <div class="container">
    <form method="post" action="/admin/lead/{tel_esc}/editar">
      <div class="card">
        <div class="field">
          <label>Nombre</label>
          <input type="text" name="nombre" value="{nombre_v}" placeholder="Nombre del lead">
        </div>
        <div class="field">
          <label>Temperatura</label>
          <div class="temp-btns" id="temp-btns">
            <button type="button" class="temp-btn {'active-caliente' if temp_v == 'caliente' else ''}"
                    onclick="setTemp('caliente', this)">🔥 Caliente</button>
            <button type="button" class="temp-btn {'active-tibio' if temp_v in ('tibio',) else ''}"
                    onclick="setTemp('tibio', this)">🌡️ Tibio</button>
            <button type="button" class="temp-btn {'active-frio' if temp_v in ('frío','frio') else ''}"
                    onclick="setTemp('frio', this)">❄️ Frío</button>
          </div>
          <input type="hidden" name="temperatura" id="temp-val" value="{temp_v}">
        </div>
        <div class="field">
          <label>Intención</label>
          <select name="intencion">
            <option value="" {'selected' if not int_v else ''}>Sin especificar</option>
            <option value="vivir" {sel_int('vivir')}>🏠 Vivir</option>
            <option value="inversión" {sel_int('inversión')}>💼 Inversión</option>
          </select>
        </div>
        <div class="field">
          <label>Apartamento de interés</label>
          <input type="text" name="apto" value="{apto_v}" placeholder="Ej: D-401, Penthouse 02...">
        </div>
        <div class="field" style="margin-bottom:0">
          <label>Habitaciones</label>
          <select name="habitaciones">
            <option value="" {'selected' if not hab_v else ''}>Sin especificar</option>
            <option value="1" {'selected' if hab_v=='1' else ''}>1 habitación</option>
            <option value="2" {'selected' if hab_v=='2' else ''}>2 habitaciones</option>
            <option value="3" {'selected' if hab_v=='3' else ''}>3 habitaciones</option>
            <option value="4" {'selected' if hab_v=='4' else ''}>4+ habitaciones</option>
          </select>
        </div>
      </div>
      <button type="submit" class="btn-save">Guardar cambios</button>
    </form>
  </div>

  <script>
    function setTemp(val, btn) {{
      document.getElementById('temp-val').value = val;
      document.querySelectorAll('.temp-btn').forEach(b => b.className = 'temp-btn');
      const cls = val === 'caliente' ? 'active-caliente' : val === 'tibio' ? 'active-tibio' : 'active-frio';
      btn.className = 'temp-btn ' + cls;
    }}
  </script>
</body>
</html>"""


@router.post("/lead/{telefono}/editar")
async def lead_editar_save(
    telefono: str,
    request: Request,
    nombre: str = Form(default=""),
    temperatura: str = Form(default=""),
    intencion: str = Form(default=""),
    apto: str = Form(default=""),
    habitaciones: str = Form(default=""),
):
    if not _autenticado(request):
        return RedirectResponse("/admin/login", status_code=302)
    await actualizar_lead(
        telefono,
        nombre=nombre.strip() or None,
        temperatura=temperatura.strip() or None,
        intencion=intencion.strip() or None,
        apto=apto.strip() if apto.strip() != "" else "",
        habitaciones=habitaciones.strip() if habitaciones.strip() != "" else "",
    )
    return RedirectResponse(f"/admin/chat/{telefono}", status_code=303)


@router.post("/nota/{telefono}")
async def admin_nota(telefono: str, request: Request, texto: str = Form(...)):
    if not _autenticado(request):
        return RedirectResponse("/admin/login", status_code=302)
    if texto.strip():
        await guardar_nota(telefono, texto.strip())
    return RedirectResponse(f"/admin/chat/{telefono}", status_code=303)


@router.post("/close/{telefono}")
async def admin_close(telefono: str, request: Request):
    if not _autenticado(request):
        return RedirectResponse("/admin/login", status_code=302)
    await desactivar_handoff(telefono)
    return RedirectResponse("/admin", status_code=303)


@router.post("/delete/{telefono}")
async def admin_delete(telefono: str, request: Request):
    if not _autenticado(request):
        return RedirectResponse("/admin/login", status_code=302)
    await limpiar_historial(telefono)
    await desactivar_handoff(telefono)
    return RedirectResponse("/admin", status_code=303)
