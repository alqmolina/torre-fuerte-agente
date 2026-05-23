# agent/admin.py — Panel de admin para atención humana en handoff

import os
import hmac
import hashlib
import secrets
import time
import httpx
import logging
from fastapi import APIRouter, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse

logger = logging.getLogger("agentkit")

from agent.memory import (
    obtener_historial,
    obtener_handoffs_activos,
    obtener_perfil_lead,
    guardar_mensaje,
    desactivar_handoff,
    limpiar_historial,
    obtener_metricas,
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

    nombre = _esc(nombre_raw) if nombre_raw else "Desconocido"
    apto = _esc(apto_raw) if apto_raw else ""
    intencion = _esc(intencion_raw.capitalize()) if intencion_raw else ""
    resumen_html = _esc(resumen_raw).replace("\n", "<br>") if resumen_raw else ""

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

    function intentarRefresh() {{
      const archivo = document.getElementById('fileInput').files[0];
      if (document.activeElement !== txt && !txt.value.trim() && !archivo) {{
        location.reload();
      }} else {{
        setTimeout(intentarRefresh, 4000);
      }}
    }}
    setTimeout(intentarRefresh, 10000);
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
        <a href="/leads/export" style="display:block;background:#1a3c5e;color:white;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px">
          📥 Descargar Excel de Leads
        </a>
        <a href="/admin" style="display:block;background:#f0f4f8;color:#1a3c5e;padding:10px 24px;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px;border:1px solid #d0dce8">
          💬 Ver conversaciones activas
        </a>
      </div>
    </div>

    <p style="text-align:center;font-size:12px;color:#bbb;margin-top:20px">Actualiza al recargar la página</p>
  </div>
</body>
</html>"""


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
