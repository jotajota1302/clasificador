"""Configuración común. Todo pasa por OpenRouter con una sola API key.

- Decisiones (clasificador): Jev, el modelo de decisión de TypeSafe, servido por
  OpenRouter en /api/v1/systemone. Se usa con el SDK oficial typesafe-sdk
  cambiando solo la URL base (https://openrouter.ai/docs/guides/community/jev).
- Generación de texto: LLMs normales por /api/v1/chat/completions (p. ej. MiniMax).

La clave se lee de OPENROUTER_API_KEY o del fichero .env (no se sube a git).
Puedes sobrescribir cualquier valor con variables de entorno, p. ej.:
    $env:TYPESAFE_DEFAULT_MODEL = "~typesafe/jev-latest"
"""

import os
import re
from pathlib import Path

import httpx
import truststore

# Usa el almacén de certificados de Windows (necesario detrás de proxies corporativos)
truststore.inject_into_ssl()

_env = Path(__file__).with_name(".env")
if _env.exists():
    for linea in _env.read_text(encoding="utf-8").splitlines():
        if "=" in linea and not linea.lstrip().startswith("#"):
            k, v = linea.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

OPENROUTER_URL = os.environ.setdefault("TYPESAFE_BASE_URL", "https://openrouter.ai/api")  # el SDK añade /v1/systemone
os.environ.setdefault("TYPESAFE_API_KEY", os.environ.get("OPENROUTER_API_KEY", ""))  # misma clave de OpenRouter
MODELO = os.environ.setdefault("TYPESAFE_DEFAULT_MODEL", "typesafe/jev-1.13")

LLM_RAPIDO = os.environ.get("LLM_RAPIDO", "openai/gpt-4o-mini")
LLM_GRANDE = os.environ.get("LLM_GRANDE", "minimax/minimax-m3")


def cabeceras() -> dict:
    return {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}", "X-Title": "clasificador-demo"}


def openrouter(payload: dict, timeout: float = 300) -> dict:
    """POST a /v1/chat/completions de OpenRouter; devuelve el JSON de respuesta."""
    r = httpx.post(f"{OPENROUTER_URL}/v1/chat/completions", headers=cabeceras(), json=payload, timeout=timeout)
    r.raise_for_status()
    d = r.json()
    if "error" in d:
        raise RuntimeError(d["error"])
    return d


def llm_chat(modelo: str, mensajes: list[dict], **extra) -> str:
    """Llama a un LLM de OpenRouter y devuelve solo la respuesta final (sin el razonamiento)."""
    d = openrouter({"model": modelo, "messages": mensajes, **extra})
    texto = d["choices"][0]["message"]["content"] or ""
    return re.sub(r"<think>.*?</think>", "", texto, flags=re.S).strip()
