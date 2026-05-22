# agent/memory.py — Memoria de conversaciones con SQLite
# Generado por AgentKit

import os
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Text, DateTime, select, Integer, Boolean, UniqueConstraint
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./agentkit.db")

if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Mensaje(Base):
    """Modelo de mensaje en la base de datos."""
    __tablename__ = "mensajes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(50), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Lead(Base):
    """Lead registrado durante una conversación."""
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


class Preferencia(Base):
    """Preferencias por número de teléfono (idioma, etc.)."""
    __tablename__ = "preferencias"

    telefono: Mapped[str] = mapped_column(String(50), primary_key=True)
    idioma: Mapped[str] = mapped_column(String(10), default="es")


class Handoff(Base):
    """Transferencias activas a asesor humano."""
    __tablename__ = "handoffs"

    telefono: Mapped[str] = mapped_column(String(50), primary_key=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    razon: Mapped[str] = mapped_column(String(200), default="")
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ultimo_aviso: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)


async def inicializar_db():
    """Crea las tablas si no existen."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def guardar_mensaje(telefono: str, role: str, content: str):
    """Guarda un mensaje en el historial de conversación."""
    async with async_session() as session:
        mensaje = Mensaje(
            telefono=telefono,
            role=role,
            content=content,
            timestamp=datetime.utcnow()
        )
        session.add(mensaje)
        await session.commit()


async def obtener_perfil_lead(telefono: str) -> dict | None:
    """Retorna el perfil guardado de un lead conocido, o None si es nuevo."""
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


async def obtener_historial(telefono: str, limite: int = 50) -> list[dict]:
    """
    Recupera los últimos N mensajes de una conversación.

    Args:
        telefono: Número de teléfono del cliente
        limite: Máximo de mensajes a recuperar (default: 20)

    Returns:
        Lista de diccionarios con role y content
    """
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

        return [
            {"role": msg.role, "content": msg.content}
            for msg in mensajes
        ]


async def lead_existe(telefono: str) -> bool:
    """Verifica si ya existe un lead registrado para este teléfono."""
    async with async_session() as session:
        result = await session.execute(select(Lead).where(Lead.telefono == telefono))
        return result.scalar_one_or_none() is not None


async def guardar_lead(telefono: str, nombre: str, email: str = "", apto: str = "", habitaciones: str = "", temperatura: str = "", intencion: str = ""):
    """Guarda un lead en la base de datos (solo una vez por teléfono)."""
    async with async_session() as session:
        lead = Lead(
            telefono=telefono,
            nombre=nombre,
            email=email,
            apto=apto,
            habitaciones=habitaciones,
            temperatura=temperatura,
            intencion=intencion,
            fecha=datetime.utcnow()
        )
        session.add(lead)
        await session.commit()


async def obtener_todos_los_leads() -> list[dict]:
    """Retorna todos los leads registrados ordenados por fecha descendente."""
    async with async_session() as session:
        result = await session.execute(select(Lead).order_by(Lead.fecha.desc()))
        leads = result.scalars().all()
        return [
            {
                "fecha": lead.fecha.strftime("%Y-%m-%d %H:%M"),
                "nombre": lead.nombre,
                "telefono": lead.telefono,
                "email": lead.email,
                "apto": lead.apto,
                "habitaciones": lead.habitaciones,
                "temperatura": lead.temperatura,
                "intencion": lead.intencion,
            }
            for lead in leads
        ]


async def guardar_idioma(telefono: str, idioma: str):
    """Guarda o actualiza el idioma preferido de un contacto."""
    async with async_session() as session:
        result = await session.execute(select(Preferencia).where(Preferencia.telefono == telefono))
        pref = result.scalar_one_or_none()
        if pref:
            pref.idioma = idioma
        else:
            session.add(Preferencia(telefono=telefono, idioma=idioma))
        await session.commit()


async def obtener_idioma(telefono: str) -> str | None:
    """Retorna el idioma guardado para un contacto, o None si es nuevo."""
    async with async_session() as session:
        result = await session.execute(select(Preferencia).where(Preferencia.telefono == telefono))
        pref = result.scalar_one_or_none()
        return pref.idioma if pref else None


async def activar_handoff(telefono: str, razon: str = "") -> None:
    """Activa o actualiza una transferencia a asesor humano."""
    async with async_session() as session:
        result = await session.execute(select(Handoff).where(Handoff.telefono == telefono))
        h = result.scalar_one_or_none()
        if h:
            h.activo = True
            h.razon = razon
            h.timestamp = datetime.utcnow()
        else:
            session.add(Handoff(telefono=telefono, activo=True, razon=razon, timestamp=datetime.utcnow()))
        await session.commit()


async def desactivar_handoff(telefono: str) -> None:
    """Desactiva la transferencia a asesor (el asesor terminó de atender)."""
    async with async_session() as session:
        result = await session.execute(select(Handoff).where(Handoff.telefono == telefono))
        h = result.scalar_one_or_none()
        if h:
            h.activo = False
            await session.commit()


async def esta_en_handoff(telefono: str) -> bool:
    """Verifica si hay una transferencia activa para este teléfono."""
    async with async_session() as session:
        result = await session.execute(
            select(Handoff).where(Handoff.telefono == telefono, Handoff.activo == True)
        )
        return result.scalar_one_or_none() is not None


async def debe_enviar_aviso_handoff(telefono: str, intervalo_horas: int = 2) -> bool:
    """True si no se ha enviado aviso de handoff en las últimas `intervalo_horas` horas."""
    async with async_session() as session:
        result = await session.execute(select(Handoff).where(Handoff.telefono == telefono))
        h = result.scalar_one_or_none()
        if not h:
            return False
        if h.ultimo_aviso is None:
            return True
        return (datetime.utcnow() - h.ultimo_aviso).total_seconds() > intervalo_horas * 3600


async def registrar_aviso_handoff(telefono: str) -> None:
    """Actualiza la marca de tiempo del último aviso enviado al lead en handoff."""
    async with async_session() as session:
        result = await session.execute(select(Handoff).where(Handoff.telefono == telefono))
        h = result.scalar_one_or_none()
        if h:
            h.ultimo_aviso = datetime.utcnow()
            await session.commit()


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


async def limpiar_historial(telefono: str):
    """Borra todo el historial de una conversación."""
    async with async_session() as session:
        query = select(Mensaje).where(Mensaje.telefono == telefono)
        result = await session.execute(query)
        mensajes = result.scalars().all()
        for msg in mensajes:
            await session.delete(msg)
        await session.commit()
