# agent/admin.py — Panel de admin para atención humana en handoff

import os
import secrets
from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from agent.memory import (
    obtener_historial,
    obtener_handoffs_activos,
    guardar_mensaje,
    desactivar_handoff,
)

router = APIRouter(prefix="/admin", tags=["admin"])
security = HTTPBasic()
proveedor = None  # inyectado desde main.py en lifespan

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "torrefuerte2024")

_ICONOS = {"caliente": "🔥", "tibio": "🌡️", "frío": "❄️", "frio": "❄️"}


def _esc(text: str) -> str:
    return (text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;"))


def _auth(credentials: HTTPBasicCredentials = Depends(security)):
    user_ok = secrets.compare_digest(credentials.username.encode(), ADMIN_USER.encode())
    pass_ok = secrets.compare_digest(credentials.password.encode(), ADMIN_PASSWORD.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=401,
            detail="Credenciales incorrectas",
            headers={"WWW-Authenticate": "Basic"},
        )


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def admin_index(_: None = Depends(_auth)):
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
    .footer {{ text-align: center; font-size: 12px; color: #bbb; margin-top: 20px; }}
  </style>
</head>
<body>
  <div class="header">
    <div style="flex:1">
      <div style="font-size:18px;font-weight:600">Torre Fuerte · Panel Asesor</div>
      <div style="font-size:13px;opacity:0.7;margin-top:2px">Conversaciones en transferencia</div>
    </div>
    {badge}
  </div>
  <div class="container">
    {content}
    <p class="footer">Actualiza automáticamente cada 15 segundos</p>
  </div>
  <script>setTimeout(() => location.reload(), 15000);</script>
</body>
</html>"""


@router.get("/chat/{telefono}", response_class=HTMLResponse)
async def admin_chat(telefono: str, _: None = Depends(_auth)):
    historial = await obtener_historial(telefono, limite=100)

    mensajes_html = ""
    for msg in historial:
        contenido = msg["content"]
        if contenido.startswith("[Sistema:"):
            continue
        es_bot = msg["role"] == "assistant"
        align = "flex-end" if es_bot else "flex-start"
        bg = "#1a3c5e" if es_bot else "#ffffff"
        color = "#ffffff" if es_bot else "#222222"
        label = "Bot" if es_bot else "Lead"
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
    .close-btn {{ background: rgba(255,255,255,0.15); color: white; border: 1px solid rgba(255,255,255,0.3);
                  padding: 6px 14px; border-radius: 20px; font-size: 12px; cursor: pointer;
                  white-space: nowrap; }}
    .close-btn:hover {{ background: rgba(231,76,60,0.8); border-color: transparent; }}
    #sending {{ display:none; position:fixed; bottom:80px; left:50%; transform:translateX(-50%);
                background:#333; color:white; padding:6px 16px; border-radius:20px; font-size:13px; }}
  </style>
</head>
<body>
  <div class="header">
    <a href="/admin" class="back">←</a>
    <div style="flex:1;min-width:0">
      <div style="font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">📱 {tel_esc}</div>
      <div style="font-size:12px;opacity:0.7">Respondiendo como Torre Fuerte</div>
    </div>
    <form method="post" action="/admin/close/{tel_esc}" style="margin:0">
      <button type="submit" class="close-btn"
              onclick="return confirm('¿Cerrar handoff y devolver al bot?')">
        Cerrar handoff
      </button>
    </form>
  </div>
  <div class="messages" id="msgs">
    {mensajes_html}
  </div>
  <div id="sending">Enviando...</div>
  <div class="input-area">
    <textarea id="txt" placeholder="Escribe tu mensaje como asesor de Torre Fuerte..."
      rows="1"
      oninput="this.style.height='auto';this.style.height=Math.min(this.scrollHeight,120)+'px'"
      onkeydown="if(event.key==='Enter'&&!event.shiftKey){{event.preventDefault();enviar()}}"></textarea>
    <button class="send-btn" id="sendBtn" onclick="enviar()">➤</button>
  </div>
  <script>
    const msgs = document.getElementById('msgs');
    msgs.scrollTop = msgs.scrollHeight;

    async function enviar() {{
      const txt = document.getElementById('txt');
      const btn = document.getElementById('sendBtn');
      const msg = txt.value.trim();
      if (!msg) return;

      btn.disabled = true;
      document.getElementById('sending').style.display = 'block';

      try {{
        const res = await fetch('/admin/send/{tel_esc}', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
          body: 'mensaje=' + encodeURIComponent(msg)
        }});
        if (res.ok) {{
          txt.value = '';
          txt.style.height = 'auto';
          location.reload();
        }}
      }} finally {{
        btn.disabled = false;
        document.getElementById('sending').style.display = 'none';
      }}
    }}

    // Auto-refresh cada 8 segundos para ver mensajes nuevos del lead
    setTimeout(() => location.reload(), 8000);
  </script>
</body>
</html>"""


@router.post("/send/{telefono}")
async def admin_send(telefono: str, mensaje: str = Form(...), _: None = Depends(_auth)):
    if not mensaje.strip():
        raise HTTPException(status_code=400, detail="Mensaje vacío")
    if proveedor:
        await proveedor.enviar_mensaje(telefono, mensaje.strip())
    await guardar_mensaje(telefono, "assistant", mensaje.strip())
    return {"status": "ok"}


@router.post("/close/{telefono}")
async def admin_close(telefono: str, _: None = Depends(_auth)):
    await desactivar_handoff(telefono)
    return RedirectResponse(url="/admin", status_code=303)
