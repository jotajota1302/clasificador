"""Configuración común.

- Decisiones (clasificador): modelo local de Ollama vía SDK de TypeSafe.
- Generación de texto: MiniMax en la nube (API compatible con OpenAI).

Puedes sobrescribir cualquier valor con variables de entorno, p. ej.:
    $env:TYPESAFE_DEFAULT_MODEL = "tev1:0.8b"
"""

import os
import re

import httpx

OLLAMA_URL = os.environ.setdefault("TYPESAFE_BASE_URL", "http://localhost:11434")
os.environ.setdefault("TYPESAFE_API_KEY", "ollama")  # Ollama ignora la clave, pero el SDK la exige
MODELO = os.environ.setdefault("TYPESAFE_DEFAULT_MODEL", "nimble")

MINIMAX_URL = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.io/v1")
MINIMAX_RAPIDO = "MiniMax-M3.1-Flash-Preview"
MINIMAX_GRANDE = "MiniMax-M3"


def minimax_chat(modelo: str, mensajes: list[dict]) -> str:
    """Llama a MiniMax y devuelve solo la respuesta final (sin el razonamiento)."""
    r = httpx.post(
        f"{MINIMAX_URL}/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['MINIMAX_API_KEY']}"},
        # reasoning_split separa el "pensamiento" en otro campo (reasoning_content)
        json={"model": modelo, "messages": mensajes, "reasoning_split": True},
        timeout=300,
    )
    r.raise_for_status()
    texto = r.json()["choices"][0]["message"]["content"]
    # Los modelos M2.x meten el razonamiento entre <think> dentro del contenido
    return re.sub(r"<think>.*?</think>", "", texto, flags=re.S).strip()
