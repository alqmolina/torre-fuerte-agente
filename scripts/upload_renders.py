#!/usr/bin/env python3
"""
Sube los renders a GitHub Releases y genera la RENDERS_BASE_URL para Railway.
Uso: python3 scripts/upload_renders.py
"""

import os
import sys
import json
import mimetypes
import httpx

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")  # Exportar antes de ejecutar: export GITHUB_TOKEN=ghp_...
REPO_OWNER = "alqmolina"
REPO_NAME = "torre-fuerte-agente"
RELEASE_TAG = "renders-v1"
RELEASE_NAME = "Renders Torre Fuerte"

RENDERS_DIR = "knowledge/renders"

# Mapeo carpeta → prefijo para nombre de archivo en GitHub Release
CARPETAS = {
    "render-401": "apt401",
    "penthouse-1111": "ph1111",
}

EXTENSIONES_VALIDAS = {".jpg", ".jpeg", ".png", ".gif", ".mp4"}


def crear_release(client: httpx.Client) -> str:
    """Crea el release en GitHub. Retorna el upload_url."""
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

    # Verificar si ya existe
    r = client.get(url, headers=headers)
    for release in r.json():
        if release.get("tag_name") == RELEASE_TAG:
            print(f"  Release '{RELEASE_TAG}' ya existe, usando el existente.")
            return release["upload_url"].replace("{?name,label}", "")

    # Crear nuevo release
    payload = {
        "tag_name": RELEASE_TAG,
        "name": RELEASE_NAME,
        "body": "Renders y multimedia Torre Fuerte Apartamentos",
        "draft": False,
        "prerelease": False,
    }
    r = client.post(url, headers=headers, json=payload)
    r.raise_for_status()
    data = r.json()
    print(f"  Release '{RELEASE_TAG}' creado.")
    return data["upload_url"].replace("{?name,label}", "")


def archivo_ya_existe(client: httpx.Client, release_id: int, nombre: str) -> bool:
    """Verifica si un archivo ya fue subido al release."""
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/{release_id}/assets"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    r = client.get(url, headers=headers)
    assets = r.json()
    return any(a["name"] == nombre for a in assets)


def obtener_release_id(client: httpx.Client) -> int:
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/tags/{RELEASE_TAG}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    r = client.get(url, headers=headers)
    return r.json()["id"]


def subir_archivo(client: httpx.Client, upload_url: str, ruta_local: str, nombre_remoto: str):
    """Sube un archivo al release de GitHub."""
    mime, _ = mimetypes.guess_type(ruta_local)
    if not mime:
        mime = "application/octet-stream"

    url = f"{upload_url}?name={nombre_remoto}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Content-Type": mime,
    }
    with open(ruta_local, "rb") as f:
        data = f.read()

    r = client.post(url, headers=headers, content=data, timeout=300)
    if r.status_code in (201, 422):  # 422 = ya existe
        return True
    r.raise_for_status()
    return True


def main():
    print("\nSubiendo renders a GitHub Releases...")
    print(f"Repo: {REPO_OWNER}/{REPO_NAME} — Tag: {RELEASE_TAG}\n")

    urls_generadas = {}

    with httpx.Client() as client:
        upload_url = crear_release(client)
        release_id = obtener_release_id(client)

        for carpeta, prefijo in CARPETAS.items():
            ruta_carpeta = os.path.join(RENDERS_DIR, carpeta)
            if not os.path.isdir(ruta_carpeta):
                print(f"  Carpeta no encontrada: {ruta_carpeta}")
                continue

            archivos = sorted([
                f for f in os.listdir(ruta_carpeta)
                if not f.startswith(".") and os.path.splitext(f)[1].lower() in EXTENSIONES_VALIDAS
            ])

            urls_carpeta = []
            print(f"  Subiendo {len(archivos)} archivos de '{carpeta}'...")

            for archivo in archivos:
                # Nombre limpio sin espacios para la URL
                nombre_limpio = f"{prefijo}_{archivo.replace(' ', '_')}"
                ruta_local = os.path.join(ruta_carpeta, archivo)
                size_mb = os.path.getsize(ruta_local) / (1024 * 1024)

                if archivo_ya_existe(client, release_id, nombre_limpio):
                    print(f"    ✓ {nombre_limpio} (ya existe)")
                else:
                    print(f"    Subiendo {nombre_limpio} ({size_mb:.1f} MB)...", end="", flush=True)
                    subir_archivo(client, upload_url, ruta_local, nombre_limpio)
                    print(" listo")

                url = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/releases/download/{RELEASE_TAG}/{nombre_limpio}"
                urls_carpeta.append(url)

            urls_generadas[prefijo] = urls_carpeta

    print("\n✓ Todos los renders subidos correctamente.\n")
    print("=" * 60)
    print("Copia estas URLs en agent/tools.py (MAPA_RENDERS_URLS):")
    print("=" * 60)
    print(json.dumps(urls_generadas, indent=2, ensure_ascii=False))
    print()
    print("RENDERS_BASE_URL ya no es necesaria — las URLs están en tools.py")


if __name__ == "__main__":
    main()
