# tests/test_local.py — Simulador de chat en terminal
# Generado por AgentKit

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.brain import generar_respuesta
from agent.memory import inicializar_db, guardar_mensaje, obtener_historial, limpiar_historial
from agent.tools import extraer_marcadores_plano, extraer_marcadores_render, obtener_plano, obtener_renders

TELEFONO_TEST = "test-local-001"


async def main():
    """Loop principal del chat de prueba."""
    await inicializar_db()

    print()
    print("=" * 55)
    print("   Torre Fuerte — Test Local")
    print("=" * 55)
    print()
    print("  Escribe mensajes como si fueras un cliente.")
    print("  Comandos especiales:")
    print("    'limpiar'  — borra el historial")
    print("    'salir'    — termina el test")
    print()
    print("-" * 55)
    print()

    while True:
        try:
            mensaje = input("Tu: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nTest finalizado.")
            break

        if not mensaje:
            continue

        if mensaje.lower() == "salir":
            print("\nTest finalizado.")
            break

        if mensaje.lower() == "limpiar":
            await limpiar_historial(TELEFONO_TEST)
            print("[Historial borrado]\n")
            continue

        historial = await obtener_historial(TELEFONO_TEST)

        print("\nTorre Fuerte: ", end="", flush=True)
        respuesta_raw = await generar_respuesta(mensaje, historial)

        # Extraer marcadores
        texto_sin_planos, codigos_plano = extraer_marcadores_plano(respuesta_raw)
        texto_limpio, claves_render = extraer_marcadores_render(texto_sin_planos)

        print(texto_limpio)

        # Mostrar planos
        for codigo in codigos_plano:
            ruta = obtener_plano(codigo)
            if ruta:
                print(f"\n  [PLANO] → {os.path.abspath(ruta)}")
            else:
                print(f"\n  [PLANO] Apartamento {codigo} no encontrado")

        # Mostrar renders
        for clave in claves_render:
            archivos = obtener_renders(clave)
            if archivos:
                print(f"\n  [RENDERS — {len(archivos)} archivos]")
                for f in archivos:
                    print(f"    → {os.path.abspath(f)}")
            else:
                print(f"\n  [RENDERS] No encontrado para: {clave}")

        print()

        await guardar_mensaje(TELEFONO_TEST, "user", mensaje)
        await guardar_mensaje(TELEFONO_TEST, "assistant", texto_limpio)


if __name__ == "__main__":
    asyncio.run(main())
