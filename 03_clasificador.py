"""Paso 3: clasificador de tickets en lote, con latencia por decisión.

Uso:  uv run 03_clasificador.py [tickets.json]
      $env:TYPESAFE_DEFAULT_MODEL="~typesafe/jev-latest"; uv run 03_clasificador.py   # otra versión de Jev
"""

import json
import sys
import time

from rich.console import Console
from rich.table import Table

from config import MODELO
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

PREGUNTAS = {
    "equipo": Choice(
        instructions="¿Qué equipo debe gestionar este ticket?",
        criteria={
            "facturacion": "Pagos, cobros, facturas y devoluciones",
            "tecnico": "Errores, caídas, acceso e integraciones",
            "comercial": "Precios, descuentos y altas nuevas",
            "otro": "Nada de lo anterior",
        },
    ),
    "reembolso": Noul(instructions="¿El cliente pide explícitamente un reembolso?"),
    "enfadado": Noul(instructions="¿El cliente está enfadado o frustrado?"),
    "urgencia": Score(
        instructions="¿Cómo de urgente es este ticket?",
        criteria=["Rutina", "Pronto", "Urgente"],
    ),
}

ruta = sys.argv[1] if len(sys.argv) > 1 else "tickets.json"
with open(ruta, encoding="utf-8") as f:
    tickets = json.load(f)

tabla = Table(title=f"Triaje con {MODELO}", show_lines=True)
tabla.add_column("Ticket", max_width=50)
tabla.add_column("Equipo")
tabla.add_column("Reembolso", justify="right")
tabla.add_column("Enfadado", justify="right")
tabla.add_column("Urgencia 0-2", justify="right")
tabla.add_column("ms", justify="right")

latencias = []
with TypeSafeClient(timeout=300) as client:
    # La primera llamada abre la conexión; la hacemos aparte para no falsear la media.
    client.system_one(state={"ticket": "hola"}, questions={"x": PREGUNTAS["enfadado"]})

    for ticket in tickets:
        t0 = time.perf_counter()
        r = client.system_one(state={"ticket": ticket}, questions=PREGUNTAS)
        ms = (time.perf_counter() - t0) * 1000
        latencias.append(ms)

        eq = r.choices["equipo"]
        urg = r.scores["urgencia"].score
        color = "red" if urg >= 1.4 else "yellow" if urg >= 0.7 else "green"
        tabla.add_row(
            ticket,
            f"{eq.choice} ({eq.confidence:.2f})",
            f"{r.nouls['reembolso'].noul:.2f}",
            f"{r.nouls['enfadado'].noul:.2f}",
            f"[{color}]{urg:.2f}[/{color}]",
            f"{ms:.0f}",
        )

console = Console()
console.print(tabla)
console.print(
    f"{len(tickets)} tickets x {len(PREGUNTAS)} preguntas · "
    f"media {sum(latencias) / len(latencias):.0f} ms por ticket"
)
