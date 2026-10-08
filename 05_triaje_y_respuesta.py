"""Paso 5: flujo completo de soporte.

1. Jev (modelo de decisión) clasifica el ticket: una llamada, ~0,5 s, fracciones de céntimo.
2. Si el modelo duda (confianza baja), el ticket va a un humano y no se gasta el LLM grande.
3. Si no, el LLM grande redacta un borrador de respuesta adaptado a la clasificación.

Uso:  uv run 05_triaje_y_respuesta.py "Me habéis cobrado dos veces, quiero el dinero ya"
"""

import sys
import time

from config import LLM_GRANDE, MODELO, llm_chat
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

UMBRAL_CONFIANZA = 0.5

ticket = sys.argv[1] if len(sys.argv) > 1 else (
    "Sois unos inútiles, me habéis cobrado dos veces la suscripción y nadie me contesta. Quiero mi dinero."
)

with TypeSafeClient(timeout=300) as client:
    t0 = time.perf_counter()
    r = client.system_one(
        state={"ticket": ticket},
        questions={
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
        },
    )
    ms = (time.perf_counter() - t0) * 1000

equipo = r.choices["equipo"]
reembolso = r.nouls["reembolso"].noul > 0.5
enfadado = r.nouls["enfadado"].noul > 0.5
urgencia = r.scores["urgencia"].score

print(f"Ticket: {ticket}\n")
print(f"[{MODELO} · {ms:.0f} ms]")
print(f"  equipo={equipo.choice} (confianza {equipo.confidence:.2f})  reembolso={reembolso}  "
      f"enfadado={enfadado}  urgencia={urgencia:.2f}/2\n")

if equipo.confidence < UMBRAL_CONFIANZA:
    print(f"-> Confianza < {UMBRAL_CONFIANZA}: se envía a revisión humana, no se llama al LLM grande.")
    sys.exit()

# La clasificación se convierte en instrucciones concretas para el LLM
pautas = [f"Respondes en nombre del equipo de {equipo.choice} de una empresa de software."]
if enfadado:
    pautas.append("El cliente está molesto: empieza reconociendo el problema y disculpándote, sin excusas.")
if reembolso:
    pautas.append("Pide reembolso: confirma que se revisará el cargo y que el reembolso se tramita en 3-5 días hábiles.")
if urgencia >= 1.4:
    pautas.append("Es urgente: indica que el caso se ha escalado con prioridad alta.")
pautas.append("Máximo 5 frases, en español, tono profesional y cercano. Firma como 'Equipo de soporte'.")

t0 = time.perf_counter()
borrador = llm_chat(
    LLM_GRANDE,
    [
        {"role": "system", "content": "\n".join(pautas)},
        {"role": "user", "content": ticket},
    ],
)
print(f"[{LLM_GRANDE} · {time.perf_counter() - t0:.1f} s] Borrador:\n")
print(borrador)
