"""Paso 4: enrutado de modelos (model routing).

El modelo de decisión local (Ollama) decide si la pregunta es sencilla o compleja
y, según eso, se la mandamos al modelo rápido o al grande de MiniMax.

Uso:  uv run 04_router.py "¿Cuál es la capital de Francia?"
      uv run 04_router.py "Diseña un esquema de base de datos para una clínica veterinaria"
"""

import sys
import time

from config import MINIMAX_GRANDE, MINIMAX_RAPIDO, minimax_chat
from typesafe_sdk import Choice, TypeSafeClient

pregunta = sys.argv[1] if len(sys.argv) > 1 else "¿Cuántos días tiene un año bisiesto?"

with TypeSafeClient(timeout=300) as client:
    t0 = time.perf_counter()
    r = client.system_one(
        state={"mensaje_usuario": pregunta},
        questions={
            "dificultad": Choice(
                instructions="¿Qué tipo de modelo necesita esta petición para responderse bien?",
                criteria={
                    "simple": "Dato puntual, charla o respuesta corta; basta un modelo pequeño",
                    "compleja": "Razonamiento en varios pasos, código, diseño o análisis largo",
                },
            )
        },
    )
    ms_decision = (time.perf_counter() - t0) * 1000

dec = r.choices["dificultad"]
modelo = MINIMAX_GRANDE if dec.choice == "compleja" else MINIMAX_RAPIDO
print(f"Decisión (Ollama): {dec.choice} {dec.probabilities} en {ms_decision:.0f} ms -> {modelo}\n")

t0 = time.perf_counter()
respuesta = minimax_chat(modelo, [{"role": "user", "content": pregunta}])
print(respuesta)
print(f"\n[MiniMax: {time.perf_counter() - t0:.1f} s]")
