# agent/memory.py — Memoria de conversaciones

import os
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Text, DateTime, select, Integer, Boolean
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./agentkit.db")

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
