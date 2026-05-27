# AgentKit — Agente de WhatsApp con IA

Plantilla lista para desplegar un agente de WhatsApp con IA para cualquier tipo de negocio.

## Stack
- **IA**: Claude claude-sonnet-4-6 (Anthropic)
- **Backend**: FastAPI + Python 3.11
- **Canales**: WhatsApp (Meta Cloud API) + Facebook Messenger
- **BD**: SQLite (local) / PostgreSQL (Railway)
- **Deploy**: Railway (automático desde GitHub)

## Funcionalidades incluidas
- Respuesta automática con IA personalizada para tu negocio
- Registro y calificación de leads (caliente/tibio/frío)
- Agendamiento de citas con Google Calendar
- Recordatorios automáticos (24h y 1h antes)
- Seguimiento automático a leads inactivos
- Transferencia a asesor humano (handoff)
- Envío de archivos e imágenes del negocio
- Panel de administración completo
- Broadcast segmentado
- Exportación de leads y citas a Excel
- Soporte bilingüe (español/inglés automático)

---

## Guía de configuración

### Paso 1 — Fork y clonar
```bash
git clone https://github.com/TU-USUARIO/TU-REPO.git
cd TU-REPO
pip install -r requirements.txt
```

### Paso 2 — Variables de entorno
Crea un archivo `.env` en la raíz:
```env
# IA
ANTHROPIC_API_KEY=sk-ant-...

# WhatsApp (elige uno)
WHATSAPP_PROVIDER=meta
META_ACCESS_TOKEN=...
META_PHONE_NUMBER_ID=...
META_VERIFY_TOKEN=mi-token-secreto

# Messenger (opcional)
META_PAGE_TOKEN=...
META_PAGE_ID=...
META_MESSENGER_VERIFY_TOKEN=mi-token-messenger

# Notificaciones
RESEND_API_KEY=...
EMAIL_LEADS=tu@email.com
ASESOR_WHATSAPP=573001234567

# Google Calendar (opcional)
GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account",...}
GOOGLE_CALENDAR_ID=primary

# Admin panel
ADMIN_USER=admin
ADMIN_PASSWORD=tu-password-seguro

# Servidor
BASE_URL=https://tu-app.up.railway.app
PORT=8080
ENVIRONMENT=production
```

### Paso 3 — Configurar el negocio
Edita **`config/business.yaml`**:
- Nombre, descripción y horario del negocio
- Mensajes automáticos de seguimiento y recordatorios
- Catálogo de productos/servicios (si aplica)

### Paso 4 — Crear el system prompt
Edita **`config/prompts.yaml`**:
- Reemplaza los placeholders `[...]` con la información de tu negocio
- Agrega precios, servicios, FAQ y cualquier información relevante
- Sé específico: cuanta más información, mejor atiende el agente

### Paso 5 — Agregar archivos del negocio (opcional)
Coloca en la carpeta `knowledge/`:
- Logo: `knowledge/logo.jpg`
- PDFs informativos: `knowledge/planos/catalogo.pdf`
- Imágenes: `knowledge/renders/fotos/imagen1.jpg`

Actualiza `config/business.yaml` → sección `logo`, `planos` y `renders`.

### Paso 6 — Probar localmente
```bash
# Prueba sin WhatsApp
python tests/test_local.py

# Arrancar servidor
uvicorn agent.main:app --reload --port 8080
```

### Paso 7 — Deploy en Railway
1. Sube el repo a GitHub
2. En [railway.app](https://railway.app): New Project → Deploy from GitHub
3. Agrega las variables de entorno del Paso 2
4. Railway asigna una URL pública → configura el webhook de WhatsApp

---

## Marcadores que usa el agente

El agente de IA usa marcadores especiales en sus respuestas para activar acciones:

| Marcador | Acción |
|---|---|
| `[LEAD:nombre\|email\|item\|hab\|temp\|intencion]` | Registra el lead |
| `[VISITA:nombre\|YYYY-MM-DD\|HH:MM\|notas]` | Agenda una cita |
| `[HANDOFF:razon]` | Transfiere a asesor humano |
| `[PLANO:codigo]` | Envía un archivo PDF |
| `[RENDER:codigo]` | Envía imágenes del catálogo |
| `[REAGENDAR_VISITA:id\|fecha\|hora]` | Reagenda una cita |
| `[CANCELAR_VISITA:id]` | Cancela una cita |

Incluye estos marcadores en el system prompt con ejemplos de cuándo usarlos.

---

## Estructura del proyecto

```
agentkit/
├── agent/
│   ├── main.py          ← FastAPI + webhook + tareas background
│   ├── brain.py         ← Conexión Claude API
│   ├── memory.py        ← Base de datos (leads, citas, historial)
│   ├── tools.py         ← Herramientas del agente
│   ├── admin.py         ← Panel de administración
│   ├── google_calendar.py
│   └── providers/       ← WhatsApp (Meta) + Messenger
├── config/
│   ├── business.yaml    ← ⭐ Configuración del negocio
│   └── prompts.yaml     ← ⭐ System prompt del agente
├── knowledge/           ← Archivos del negocio (logo, PDFs, imágenes)
├── tests/
│   └── test_local.py    ← Chat de prueba en terminal
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

---

## Agregar funciones nuevas

Para agregar una nueva capacidad al agente:

1. **Definir el marcador** en `config/prompts.yaml` (ej: `[PEDIDO:producto|cantidad]`)
2. **Crear la función** en `agent/tools.py`
3. **Procesar el marcador** en `agent/main.py` → función `_procesar_mensaje_canal`
4. **El agente lo usa automáticamente** según las instrucciones del system prompt
