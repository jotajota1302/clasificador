"""Paso 1: llamada HTTP cruda a /v1/systemone, sin SDK.

Sirve para ver exactamente qué se envía y qué devuelve el modelo.
Uso:  uv run 01_api_cruda.py
"""

import json
import time

import httpx

from config import MODELO, OLLAMA_URL

peticion = {
    "model": MODELO,
    # "state": el contexto sobre el que se decide. Puede ser texto o un objeto JSON.
    "state": {
        "ticket": "Me habéis cobrado dos veces la suscripción de septiembre. Quiero que me devolváis el cargo duplicado."
    },
    # "questions": preguntas con nombre. Todas se responden en UNA sola llamada.
    "questions": {
        # choice -> elige UNA etiqueta de un conjunto cerrado
        "equipo": {
            "type": "choice",
            "instructions": "¿Qué equipo debe gestionar este ticket?",
            "criteria": {
                "facturacion": "Pagos, cobros y devoluciones",
                "tecnico": "Errores, caídas e integraciones",
                "otro": "Nada de lo anterior",
            },
        },
        # noul -> sí/no, devuelve la probabilidad de "sí" (0..1)
        "pide_reembolso": {
            "type": "noul",
            "instructions": "¿El cliente pide explícitamente un reembolso?",
        },
        # score -> escala ordinal; devuelve el valor esperado sobre los índices 0..n-1
        "urgencia": {
            "type": "score",
            "instructions": "¿Cómo de urgente es este ticket?",
            "criteria": ["Rutina", "Pronto", "Urgente"],
        },
    },
}

print(">>> PETICIÓN")
print(json.dumps(peticion, indent=2, ensure_ascii=False))

t0 = time.perf_counter()
r = httpx.post(f"{OLLAMA_URL}/v1/systemone", json=peticion, timeout=300)
ms = (time.perf_counter() - t0) * 1000

print(f"\n>>> RESPUESTA  (HTTP {r.status_code}, {ms:.0f} ms)")
try:
    print(json.dumps(r.json(), indent=2, ensure_ascii=False))
except ValueError:
    print(r.text)
