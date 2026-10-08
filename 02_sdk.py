"""Paso 2: lo mismo, pero con el SDK oficial de TypeSafe (respuestas tipadas).

Uso:  uv run 02_sdk.py
"""

import config  # noqa: F401  (configura las variables de entorno del SDK)
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

ticket = "La API devuelve error 500 desde esta mañana y tenemos la tienda caída. ¡Ayuda!"

preguntas = {
    "equipo": Choice(
        instructions="¿Qué equipo debe gestionar este ticket?",
        criteria={
            "facturacion": "Pagos, cobros y devoluciones",
            "tecnico": "Errores, caídas e integraciones",
            "otro": "Nada de lo anterior",
        },
    ),
    "pide_reembolso": Noul(instructions="¿El cliente pide explícitamente un reembolso?"),
    "urgencia": Score(
        instructions="¿Cómo de urgente es este ticket?",
        criteria=["Rutina", "Pronto", "Urgente"],
    ),
}

with TypeSafeClient(timeout=300) as client:
    res = client.system_one(state={"ticket": ticket}, questions=preguntas)

equipo = res.choices["equipo"]
reembolso = res.nouls["pide_reembolso"]
urgencia = res.scores["urgencia"]

print(f"Ticket: {ticket}\n")
print(f"equipo         -> {equipo.choice}  (confianza {equipo.confidence:.2f})")
print(f"                  probabilidades: {equipo.probabilities}")
print(f"pide_reembolso -> P(sí) = {reembolso.noul:.3f}")
print(f"urgencia       -> {urgencia.score:.2f} en escala 0..{len(urgencia.legend) - 1}")
print(f"                  {urgencia.legend}")
print(f"                  probabilidades: {urgencia.probabilities}")
print(f"\nTokens: {res.usage}")
