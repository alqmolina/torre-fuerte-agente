# agent/memory.py — Memoria de conversaciones

import os
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Text, DateTime, select, Integer, Boolean, func, distinct, or_
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = (
    os.getenv("TF_DATABASE_URL") or
    os.getenv("POSTGRES_URL") or
    os.getenv("DATABASE_URL") or
    "sqlite+aiosqlite:///./agentkit.db"
)

if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

_COL = timedelta(hours=-5)


def _col(dt: datetime) -> str:
    return (dt + _COL).strftime("%Y-%m-%d %H:%M")


def _col_hora(dt: datetime) -> str:
    return (dt + _COL).strftime("%H:%M")


class Base(DeclarativeBase):
    pass


class Mensaje(Base):
    __tablename__ = "mensajes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(50), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MensajeProcesado(Base):
    """IDs de mensajes ya procesados — evita respuestas duplicadas."""
    __tablename__ = "mensajes_procesados"

    mensaje_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    procesado_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(50), index=True, unique=True)
    nombre: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(200), default="")
    apto: Mapped[str] = mapped_column(String(100), default="")
    habitaciones: Mapped[str] = mapped_column(String(50), default="")
    temperatura: Mapped[str] = mapped_column(String(20), default="")
    intencion: Mapped[str] = mapped_column(String(50), default="")
    fecha: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SeguimientoLead(Base):
    """Registro de mensajes de seguimiento enviados a cada lead."""
    __tablename__ = "seguimientos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(50), index=True)
    numero: Mapped[int] = mapped_column(Integer)  # 1 o 2
    enviado_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Broadcast(Base):
    """Campaña de broadcast masivo."""
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mensaje: Mapped[str] = mapped_column(Text)
    filtros: Mapped[str] = mapped_column(Text, default="")
    total: Mapped[int] = mapped_column(Integer, default=0)
    enviados: Mapped[int] = mapped_column(Integer, default=0)
    fallidos: Mapped[int] = mapped_column(Integer, default=0)
    estado: Mapped[str] = mapped_column(String(20), default="en_proceso")
    creado_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BroadcastLog(Base):
    """Log individual de cada envío de broadcast."""
    __tablename__ = "broadcast_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    broadcast_id: Mapped[int] = mapped_column(Integer, index=True)
    telefono: Mapped[str] = mapped_column(String(50))
    nombre: Mapped[str] = mapped_column(String(200), default="")
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    enviado_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Visita(Base):
    """Visita agendada al proyecto."""
    __tablename__ = "visitas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(50), index=True)
    nombre: Mapped[str] = mapped_column(String(200), default="")
    fecha: Mapped[str] = mapped_column(String(20))   # "2024-01-15"
    hora: Mapped[str] = mapped_column(String(10))    # "10:00"
    notas: Mapped[str] = mapped_column(Text, default="")
    estado: Mapped[str] = mapped_column(String(20), default="confirmada")
    creado_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RecordatorioEnviado(Base):
    """Registro de recordatorios de visita ya enviados."""
    __tablename__ = "recordatorios_enviados"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    visita_id: Mapped[int] = mapped_column(Integer, index=True)
    tipo: Mapped[str] = mapped_column(String(10))  # "24h" o "1h"
    enviado_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NotaLead(Base):
    """Notas internas del asesor sobre un lead."""
    __tablename__ = "notas_lead"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(50), index=True)
    texto: Mapped[str] = mapped_column(Text)
    asesor: Mapped[str] = mapped_column(String(100), default="")
    creado_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Preferencia(Base):
    __tablename__ = "preferencias"

    telefono: Mapped[str] = mapped_column(String(50), primary_key=True)
    idioma: Mapped[str] = mapped_column(String(10), default="es")


class Handoff(Base):
    __tablename__ = "handoffs"

    telefono: Mapped[str] = mapped_column(String(50), primary_key=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    razon: Mapped[str] = mapped_column(String(200), default="")
    nombre: Mapped[str | None] = mapped_column(String(200), nullable=True, default=None)
    resumen: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ultimo_aviso: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)


async def inicializar_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ── Mensajes ──────────────────────────────────────────────────────────────────

async def guardar_mensaje(telefono: str, role: str, content: str):
    async with async_session() as session:
        session.add(Mensaje(telefono=telefono, role=role, content=content, timestamp=datetime.utcnow()))
        await session.commit()


async def obtener_historial(telefono: str, limite: int = 50) -> list[dict]:
    async with async_session() as session:
        query = (
            select(Mensaje)
            .where(Mensaje.telefono == telefono)
            .order_by(Mensaje.timestamp.desc())
            .limit(limite)
        )
        result = await session.execute(query)
        mensajes = result.scalars().all()
        mensajes.reverse()
        return [{"role": m.role, "content": m.content} for m in mensajes]


async def limpiar_historial(telefono: str):
    async with async_session() as session:
        result = await session.execute(select(Mensaje).where(Mensaje.telefono == telefono))
        for msg in result.scalars().all():
            await session.delete(msg)
        await session.commit()


# ── Deduplicación ─────────────────────────────────────────────────────────────

async def mensaje_ya_procesado(mensaje_id: str) -> bool:
    """True si este mensaje ya fue procesado antes (evita duplicados de Meta)."""
    if not mensaje_id:
        return False
    async with async_session() as session:
        result = await session.execute(
            select(MensajeProcesado).where(MensajeProcesado.mensaje_id == mensaje_id)
        )
        return result.scalar_one_or_none() is not None


async def marcar_mensaje_procesado(mensaje_id: str):
    """Registra el mensaje_id como procesado."""
    if not mensaje_id:
        return
    async with async_session() as session:
        try:
            session.add(MensajeProcesado(mensaje_id=mensaje_id, procesado_at=datetime.utcnow()))
            await session.commit()
        except Exception:
            await session.rollback()


# ── Leads ─────────────────────────────────────────────────────────────────────

async def obtener_perfil_lead(telefono: str) -> dict | None:
    async with async_session() as session:
        result = await session.execute(select(Lead).where(Lead.telefono == telefono))
        lead = result.scalar_one_or_none()
        if not lead:
            return None
        return {
            "nombre": lead.nombre,
            "email": lead.email,
            "apto": lead.apto,
            "habitaciones": lead.habitaciones,
            "temperatura": lead.temperatura,
            "intencion": lead.intencion,
            "fecha": lead.fecha.strftime("%Y-%m-%d"),
        }


async def lead_existe(telefono: str) -> bool:
    async with async_session() as session:
        result = await session.execute(select(Lead).where(Lead.telefono == telefono))
        return result.scalar_one_or_none() is not None


async def guardar_lead(telefono: str, nombre: str, email: str = "", apto: str = "", habitaciones: str = "", temperatura: str = "", intencion: str = ""):
    async with async_session() as session:
        session.add(Lead(
            telefono=telefono, nombre=nombre, email=email,
            apto=apto, habitaciones=habitaciones,
            temperatura=temperatura, intencion=intencion,
            fecha=datetime.utcnow()
        ))
        await session.commit()


async def actualizar_lead(
    telefono: str,
    nombre: str | None = None,
    temperatura: str | None = None,
    intencion: str | None = None,
    apto: str | None = None,
    habitaciones: str | None = None,
) -> None:
    async with async_session() as session:
        result = await session.execute(select(Lead).where(Lead.telefono == telefono))
        lead = result.scalar_one_or_none()
        if not lead:
            return
        if nombre is not None:
            lead.nombre = nombre
        if temperatura is not None:
            lead.temperatura = temperatura
        if intencion is not None:
            lead.intencion = intencion
        if apto is not None:
            lead.apto = apto
        if habitaciones is not None:
            lead.habitaciones = habitaciones
        await session.commit()


async def obtener_todos_los_leads() -> list[dict]:
    async with async_session() as session:
        result = await session.execute(select(Lead).order_by(Lead.fecha.desc()))
        return [
            {
                "fecha": _col(lead.fecha),
                "nombre": lead.nombre,
                "telefono": lead.telefono,
                "email": lead.email,
                "apto": lead.apto,
                "habitaciones": lead.habitaciones,
                "temperatura": lead.temperatura,
                "intencion": lead.intencion,
            }
            for lead in result.scalars().all()
        ]


# ── Seguimiento de leads ───────────────────────────────────────────────────────

# Horas desde el último mensaje para enviar cada seguimiento
_SEGUIMIENTO_HORAS = {
    "caliente": [24, 96],    # #1 a las 24h, #2 a las 96h
    "tibio":    [48, 120],   # #1 a las 48h, #2 a las 120h (5 días)
    "frio":     [72, 168],   # #1 a las 72h (3 días), #2 a las 168h (7 días)
}


async def obtener_leads_para_seguimiento() -> list[dict]:
    """Leads que necesitan mensaje de seguimiento automático."""
    now = datetime.utcnow()
    async with async_session() as session:
        result = await session.execute(select(Lead))
        leads = result.scalars().all()

        pendientes = []
        for lead in leads:
            temp = lead.temperatura.lower().replace("í", "i") if lead.temperatura else ""
            umbrales = _SEGUIMIENTO_HORAS.get(temp)
            if not umbrales:
                continue

            # Saltar si hay handoff activo
            h_res = await session.execute(
                select(Handoff).where(Handoff.telefono == lead.telefono, Handoff.activo == True)
            )
            if h_res.scalar_one_or_none():
                continue

            # Último mensaje
            last_res = await session.execute(
                select(Mensaje.timestamp)
                .where(Mensaje.telefono == lead.telefono)
                .order_by(Mensaje.timestamp.desc())
                .limit(1)
            )
            last_ts = last_res.scalar_one_or_none()
            if not last_ts:
                continue

            horas_sin_respuesta = (now - last_ts).total_seconds() / 3600

            # Seguimientos ya enviados
            seg_res = await session.execute(
                select(SeguimientoLead).where(SeguimientoLead.telefono == lead.telefono)
            )
            enviados = {s.numero for s in seg_res.scalars().all()}

            # Idioma del lead
            pref_res = await session.execute(
                select(Preferencia).where(Preferencia.telefono == lead.telefono)
            )
            pref = pref_res.scalar_one_or_none()
            idioma = pref.idioma if pref else "es"

            for i, umbral in enumerate(umbrales, start=1):
                if i in enviados:
                    continue
                if i == 2 and 1 not in enviados:
                    continue
                if horas_sin_respuesta >= umbral:
                    pendientes.append({
                        "telefono": lead.telefono,
                        "nombre": lead.nombre,
                        "temperatura": lead.temperatura,
                        "numero": i,
                        "apto": lead.apto,
                        "habitaciones": lead.habitaciones,
                        "intencion": lead.intencion,
                        "idioma": idioma,
                    })
                    break

        return pendientes


async def registrar_seguimiento(telefono: str, numero: int):
    """Marca que se envió el seguimiento #numero a este lead."""
    async with async_session() as session:
        session.add(SeguimientoLead(telefono=telefono, numero=numero, enviado_at=datetime.utcnow()))
        await session.commit()


# ── Idioma ────────────────────────────────────────────────────────────────────

async def guardar_idioma(telefono: str, idioma: str):
    async with async_session() as session:
        result = await session.execute(select(Preferencia).where(Preferencia.telefono == telefono))
        pref = result.scalar_one_or_none()
        if pref:
            pref.idioma = idioma
        else:
            session.add(Preferencia(telefono=telefono, idioma=idioma))
        await session.commit()


async def obtener_idioma(telefono: str) -> str | None:
    async with async_session() as session:
        result = await session.execute(select(Preferencia).where(Preferencia.telefono == telefono))
        pref = result.scalar_one_or_none()
        return pref.idioma if pref else None


# ── Handoff ───────────────────────────────────────────────────────────────────

def _norm_tel(telefono: str) -> str:
    return telefono.lstrip("+")


async def activar_handoff(telefono: str, razon: str = "", nombre: str = "", resumen: str = "") -> None:
    telefono = _norm_tel(telefono)
    async with async_session() as session:
        result = await session.execute(select(Handoff).where(Handoff.telefono == telefono))
        h = result.scalar_one_or_none()
        if h:
            h.activo = True
            h.razon = razon
            if nombre:
                h.nombre = nombre
            if resumen:
                h.resumen = resumen
            h.timestamp = datetime.utcnow()
        else:
            session.add(Handoff(
                telefono=telefono, activo=True, razon=razon,
                nombre=nombre or None, resumen=resumen or None,
                timestamp=datetime.utcnow()
            ))
        await session.commit()


async def desactivar_handoff(telefono: str) -> None:
    telefono = _norm_tel(telefono)
    async with async_session() as session:
        result = await session.execute(select(Handoff).where(Handoff.telefono == telefono))
        h = result.scalar_one_or_none()
        if h:
            h.activo = False
            await session.commit()


async def esta_en_handoff(telefono: str) -> bool:
    telefono = _norm_tel(telefono)
    async with async_session() as session:
        result = await session.execute(
            select(Handoff).where(Handoff.telefono == telefono, Handoff.activo == True)
        )
        return result.scalar_one_or_none() is not None


async def debe_enviar_aviso_handoff(telefono: str, intervalo_horas: int = 2) -> bool:
    telefono = _norm_tel(telefono)
    async with async_session() as session:
        result = await session.execute(select(Handoff).where(Handoff.telefono == telefono))
        h = result.scalar_one_or_none()
        if not h:
            return False
        if h.ultimo_aviso is None:
            return True
        return (datetime.utcnow() - h.ultimo_aviso).total_seconds() > intervalo_horas * 3600


async def registrar_aviso_handoff(telefono: str) -> None:
    telefono = _norm_tel(telefono)
    async with async_session() as session:
        result = await session.execute(select(Handoff).where(Handoff.telefono == telefono))
        h = result.scalar_one_or_none()
        if h:
            h.ultimo_aviso = datetime.utcnow()
            await session.commit()


async def obtener_handoffs_activos() -> list[dict]:
    async with async_session() as session:
        result = await session.execute(
            select(Handoff).where(Handoff.activo == True).order_by(Handoff.timestamp.desc())
        )
        handoffs = result.scalars().all()

        resultado = []
        for h in handoffs:
            lead_result = await session.execute(select(Lead).where(Lead.telefono == h.telefono))
            lead = lead_result.scalar_one_or_none()

            last_msg_result = await session.execute(
                select(Mensaje)
                .where(Mensaje.telefono == h.telefono)
                .order_by(Mensaje.timestamp.desc())
                .limit(1)
            )
            last_msg = last_msg_result.scalar_one_or_none()

            resultado.append({
                "telefono": h.telefono,
                "razon": h.razon,
                "timestamp": _col(h.timestamp),
                "nombre": (lead.nombre if lead and lead.nombre else None) or h.nombre or "Desconocido",
                "temperatura": lead.temperatura if lead else "",
                "apto": lead.apto if lead else "",
                "intencion": lead.intencion if lead else "",
                "resumen": h.resumen or "",
                "ultimo_mensaje": last_msg.content[:80] if last_msg else "",
                "ultimo_mensaje_role": last_msg.role if last_msg else "",
                "ultimo_mensaje_tiempo": _col_hora(last_msg.timestamp) if last_msg else "",
            })

        return resultado


async def obtener_leads_pendientes_handoff(minutos: int = 20) -> list[dict]:
    """Leads tibio/caliente sin handoff activo cuyo último mensaje fue hace más de `minutos` minutos."""
    desde = datetime.utcnow() - timedelta(minutes=minutos)
    async with async_session() as session:
        result = await session.execute(
            select(Lead).where(Lead.temperatura.in_(["tibio", "caliente"]))
        )
        leads = result.scalars().all()

        pendientes = []
        for lead in leads:
            h_result = await session.execute(
                select(Handoff).where(Handoff.telefono == lead.telefono, Handoff.activo == True)
            )
            if h_result.scalar_one_or_none():
                continue

            last_result = await session.execute(
                select(Mensaje.timestamp)
                .where(Mensaje.telefono == lead.telefono)
                .order_by(Mensaje.timestamp.desc())
                .limit(1)
            )
            last_ts = last_result.scalar_one_or_none()
            if last_ts and last_ts < desde:
                pendientes.append({
                    "telefono": lead.telefono,
                    "nombre": lead.nombre,
                    "email": lead.email,
                    "apto": lead.apto,
                    "habitaciones": lead.habitaciones,
                    "temperatura": lead.temperatura,
                    "intencion": lead.intencion,
                })

        return pendientes


# ── Métricas ──────────────────────────────────────────────────────────────────

async def obtener_metricas() -> dict:
    """Retorna todas las métricas del agente para el dashboard."""
    now = datetime.utcnow()
    hoy = now.replace(hour=0, minute=0, second=0, microsecond=0)
    semana = hoy - timedelta(days=7)
    mes = hoy - timedelta(days=30)

    async with async_session() as session:
        total_leads = (await session.scalar(select(func.count(Lead.id)))) or 0
        total_conversaciones = (await session.scalar(
            select(func.count(distinct(Mensaje.telefono)))
        )) or 0

        leads_hoy = (await session.scalar(
            select(func.count(Lead.id)).where(Lead.fecha >= hoy)
        )) or 0
        leads_semana = (await session.scalar(
            select(func.count(Lead.id)).where(Lead.fecha >= semana)
        )) or 0
        leads_mes = (await session.scalar(
            select(func.count(Lead.id)).where(Lead.fecha >= mes)
        )) or 0

        temp_rows = await session.execute(
            select(Lead.temperatura, func.count(Lead.id)).group_by(Lead.temperatura)
        )
        por_temperatura = {(r[0] or "sin datos"): r[1] for r in temp_rows}

        int_rows = await session.execute(
            select(Lead.intencion, func.count(Lead.id)).group_by(Lead.intencion)
        )
        por_intencion = {(r[0] or "sin datos"): r[1] for r in int_rows}

        apto_rows = await session.execute(
            select(Lead.apto, func.count(Lead.id))
            .where(Lead.apto != "")
            .group_by(Lead.apto)
            .order_by(func.count(Lead.id).desc())
            .limit(6)
        )
        aptos_top = [(r[0], r[1]) for r in apto_rows]

        hab_rows = await session.execute(
            select(Lead.habitaciones, func.count(Lead.id))
            .where(Lead.habitaciones != "")
            .group_by(Lead.habitaciones)
            .order_by(func.count(Lead.id).desc())
        )
        por_habitaciones = [(r[0], r[1]) for r in hab_rows]

        total_handoffs = (await session.scalar(select(func.count(Handoff.telefono)))) or 0
        handoffs_activos = (await session.scalar(
            select(func.count(Handoff.telefono)).where(Handoff.activo == True)
        )) or 0

        total_seguimientos = (await session.scalar(select(func.count(SeguimientoLead.id)))) or 0
        seg_rows = await session.execute(
            select(SeguimientoLead.numero, func.count(SeguimientoLead.id))
            .group_by(SeguimientoLead.numero)
        )
        seguimientos_por_numero = {r[0]: r[1] for r in seg_rows}

        idioma_rows = await session.execute(
            select(Preferencia.idioma, func.count(Preferencia.telefono))
            .group_by(Preferencia.idioma)
        )
        por_idioma = {(r[0] or "es"): r[1] for r in idioma_rows}

        return {
            "total_leads": total_leads,
            "total_conversaciones": total_conversaciones,
            "leads_hoy": leads_hoy,
            "leads_semana": leads_semana,
            "leads_mes": leads_mes,
            "por_temperatura": por_temperatura,
            "por_intencion": por_intencion,
            "aptos_top": aptos_top,
            "por_habitaciones": por_habitaciones,
            "total_handoffs": total_handoffs,
            "handoffs_activos": handoffs_activos,
            "total_seguimientos": total_seguimientos,
            "seguimientos_por_numero": seguimientos_por_numero,
            "por_idioma": por_idioma,
            "tasa_conversion": round(total_leads / total_conversaciones * 100, 1) if total_conversaciones else 0,
        }


# ── Visitas ───────────────────────────────────────────────────────────────────

async def guardar_visita(telefono: str, nombre: str, fecha: str, hora: str, notas: str = "") -> int:
    import logging
    logger = logging.getLogger("agentkit")
    async with async_session() as session:
        v = Visita(telefono=telefono, nombre=nombre, fecha=fecha, hora=hora,
                   notas=notas, estado="confirmada", creado_at=datetime.utcnow())
        session.add(v)
        await session.commit()
        await session.refresh(v)
        logger.info(f"guardar_visita: id={v.id} tel={telefono} fecha={fecha} estado={v.estado!r}")
        return v.id


async def obtener_visitas_lead(telefono: str) -> list[dict]:
    async with async_session() as session:
        result = await session.execute(
            select(Visita).where(Visita.telefono == telefono).order_by(Visita.fecha.asc(), Visita.hora.asc())
        )
        return [
            {"id": v.id, "nombre": v.nombre, "fecha": v.fecha, "hora": v.hora,
             "notas": v.notas, "estado": v.estado, "creado_at": _col(v.creado_at)}
            for v in result.scalars().all()
        ]


async def obtener_visitas_proximas() -> list[dict]:
    """Todas las visitas no-canceladas ordenadas por fecha (próximas primero, pasadas al final)."""
    import logging
    logger = logging.getLogger("agentkit")
    hoy = (datetime.utcnow() + _COL).strftime("%Y-%m-%d")
    async with async_session() as session:
        result = await session.execute(
            select(Visita)
            .where(or_(
                Visita.estado == "confirmada",
                Visita.estado == "completada",
                Visita.estado.is_(None),
            ))
            .order_by(Visita.fecha.asc(), Visita.hora.asc())
        )
        visitas = result.scalars().all()
        logger.info(f"obtener_visitas_proximas: {len(visitas)} visitas encontradas (estado=confirmada o NULL)")
        for v in visitas:
            logger.info(f"  visita id={v.id} tel={v.telefono} fecha={v.fecha} estado={v.estado!r}")
        proximas = [v for v in visitas if v.fecha and v.fecha >= hoy]
        pasadas  = [v for v in visitas if v.fecha and v.fecha < hoy]
        ordenadas = proximas + list(reversed(pasadas))
        return [
            {"id": v.id, "telefono": v.telefono, "nombre": v.nombre,
             "fecha": v.fecha, "hora": v.hora, "notas": v.notas,
             "estado": v.estado or "confirmada",
             "pasada": bool(v.fecha and v.fecha < hoy)}
            for v in ordenadas
        ]


async def cancelar_visita(visita_id: int) -> None:
    async with async_session() as session:
        result = await session.execute(select(Visita).where(Visita.id == visita_id))
        v = result.scalar_one_or_none()
        if v:
            v.estado = "cancelada"
            await session.commit()


async def completar_visita(visita_id: int) -> None:
    """Marca una visita como realizada."""
    async with async_session() as session:
        result = await session.execute(select(Visita).where(Visita.id == visita_id))
        v = result.scalar_one_or_none()
        if v:
            v.estado = "completada"
            await session.commit()


async def obtener_visitas_para_recordatorio() -> list[dict]:
    """Retorna visitas confirmadas que necesitan recordatorio 24h o 1h antes (aún no enviado)."""
    from datetime import timedelta
    ahora = datetime.utcnow() + _COL
    en_23h   = ahora + timedelta(hours=23)
    en_25h   = ahora + timedelta(hours=25)
    en_45min = ahora + timedelta(minutes=45)
    en_75min = ahora + timedelta(minutes=75)

    resultados = []
    async with async_session() as session:
        result = await session.execute(
            select(Visita).where(or_(Visita.estado == "confirmada", Visita.estado.is_(None)))
        )
        for v in result.scalars().all():
            try:
                dt_visita = datetime.strptime(f"{v.fecha} {v.hora}", "%Y-%m-%d %H:%M")
            except Exception:
                continue
            for tipo, lo, hi in [("24h", en_23h, en_25h), ("1h", en_45min, en_75min)]:
                if lo <= dt_visita <= hi:
                    ya = await session.scalar(
                        select(func.count(RecordatorioEnviado.id)).where(
                            RecordatorioEnviado.visita_id == v.id,
                            RecordatorioEnviado.tipo == tipo,
                        )
                    )
                    if not ya:
                        resultados.append({
                            "id": v.id, "telefono": v.telefono,
                            "nombre": v.nombre or "Lead",
                            "fecha": v.fecha, "hora": v.hora, "tipo": tipo,
                        })
    return resultados


async def marcar_recordatorio(visita_id: int, tipo: str) -> None:
    """Registra que el recordatorio de tipo '24h' o '1h' ya fue enviado."""
    async with async_session() as session:
        session.add(RecordatorioEnviado(
            visita_id=visita_id, tipo=tipo, enviado_at=datetime.utcnow()
        ))
        await session.commit()


# ── Notas del asesor ──────────────────────────────────────────────────────────

async def guardar_nota(telefono: str, texto: str, asesor: str = "") -> None:
    async with async_session() as session:
        session.add(NotaLead(telefono=telefono, texto=texto.strip(), asesor=asesor, creado_at=datetime.utcnow()))
        await session.commit()


async def obtener_notas(telefono: str) -> list[dict]:
    async with async_session() as session:
        result = await session.execute(
            select(NotaLead).where(NotaLead.telefono == telefono).order_by(NotaLead.creado_at.asc())
        )
        return [
            {
                "id": n.id,
                "texto": n.texto,
                "asesor": n.asesor,
                "creado_at": _col(n.creado_at),
            }
            for n in result.scalars().all()
        ]


# ── Broadcast ─────────────────────────────────────────────────────────────────

async def obtener_leads_filtrados(
    temperaturas: list[str] | None = None,
    idioma: str = "",
    intencion: str = "",
) -> list[dict]:
    """Leads que coinciden con los filtros para un broadcast."""
    async with async_session() as session:
        query = select(Lead)
        if temperaturas:
            all_temps: set[str] = set()
            for t in temperaturas:
                all_temps.add(t)
                if t == "frío":
                    all_temps.add("frio")
                elif t == "frio":
                    all_temps.add("frío")
            query = query.where(Lead.temperatura.in_(list(all_temps)))
        if intencion:
            if intencion in ("inversión", "inversion"):
                query = query.where(Lead.intencion.in_(["inversión", "inversion"]))
            else:
                query = query.where(Lead.intencion == intencion)

        result = await session.execute(query)
        leads = result.scalars().all()

        output = []
        for lead in leads:
            pref_res = await session.execute(
                select(Preferencia).where(Preferencia.telefono == lead.telefono)
            )
            pref = pref_res.scalar_one_or_none()
            lead_idioma = pref.idioma if pref else "es"
            if idioma and lead_idioma != idioma:
                continue
            output.append({
                "telefono": lead.telefono,
                "nombre": lead.nombre or "",
                "idioma": lead_idioma,
            })
        return output


async def crear_broadcast(mensaje: str, filtros: str, total: int) -> int:
    async with async_session() as session:
        b = Broadcast(mensaje=mensaje, filtros=filtros, total=total,
                      estado="en_proceso", creado_at=datetime.utcnow())
        session.add(b)
        await session.commit()
        await session.refresh(b)
        return b.id


async def actualizar_broadcast(broadcast_id: int, enviados: int, fallidos: int):
    async with async_session() as session:
        result = await session.execute(select(Broadcast).where(Broadcast.id == broadcast_id))
        b = result.scalar_one_or_none()
        if b:
            b.enviados = enviados
            b.fallidos = fallidos
            b.estado = "completado"
            await session.commit()


async def registrar_broadcast_log(broadcast_id: int, telefono: str, nombre: str, ok: bool):
    async with async_session() as session:
        session.add(BroadcastLog(
            broadcast_id=broadcast_id, telefono=telefono, nombre=nombre,
            ok=ok, enviado_at=datetime.utcnow()
        ))
        await session.commit()


async def obtener_historial_broadcasts() -> list[dict]:
    async with async_session() as session:
        result = await session.execute(
            select(Broadcast).order_by(Broadcast.creado_at.desc()).limit(30)
        )
        return [
            {
                "id": b.id,
                "mensaje": b.mensaje[:120],
                "filtros": b.filtros,
                "total": b.total,
                "enviados": b.enviados,
                "fallidos": b.fallidos,
                "estado": b.estado,
                "creado_at": _col(b.creado_at),
            }
            for b in result.scalars().all()
        ]
