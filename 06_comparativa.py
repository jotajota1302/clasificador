"""Paso 6: comparativa en la misma tarea con 40 tickets etiquetados a mano.

Tres formas de clasificar, todas por OpenRouter:
  - jev:<modelo>   modelo de DECISIÓN de verdad (Jev, /v1/systemone): una llamada, probabilidades
  - dec:<modelo>   IMITACIÓN con un LLM de chat: 1 token por pregunta + logprobs (decision.py)
  - json:<modelo>  LLM de chat al que se le pide un JSON con las tres respuestas y se parsea

Mide: acierto por pregunta, latencia media y p95, respuestas mal formadas,
tokens y coste. Además, para los modos decisión, la curva confianza -> cobertura/acierto
(automatizar solo lo que el modelo tiene claro), que un LLM que escribe texto no da.

Uso:  uv run 06_comparativa.py
      uv run 06_comparativa.py jev:typesafe/jev-1.13 json:minimax/minimax-m3
"""

import json
import re
import sys
import time
from pathlib import Path
from statistics import mean, quantiles

from rich.console import Console
from rich.table import Table

import httpx

from config import MODELO, OPENROUTER_URL, cabeceras, openrouter
from decision import LLM_IMITACION, Choice, DecisionClient, Noul, Score

EQUIPOS = {
    "facturacion": "Pagos, cobros, facturas y devoluciones",
    "tecnico": "Errores, caídas, seguridad, acceso e integraciones",
    "comercial": "Precios, descuentos, presupuestos, ampliaciones y altas nuevas",
    "otro": "Nada de lo anterior",
}
URGENCIAS = ["Rutina: puede esperar", "Pronto: hay que atenderlo hoy o mañana", "Urgente: impacto grave ahora mismo"]

PREGUNTAS = {
    "equipo": Choice(instructions="¿Qué equipo debe gestionar este ticket?", criteria=EQUIPOS),
    "reembolso": Noul(instructions="¿El cliente pide explícitamente que le devuelvan dinero?"),
    "urgencia": Score(instructions="¿Cómo de urgente es este ticket?", criteria=URGENCIAS),
}

PROMPT_LLM = f"""Clasifica el ticket de soporte. Responde SOLO con un JSON con esta forma exacta:
{{"equipo": "<una de: {', '.join(EQUIPOS)}>", "reembolso": <true|false>, "urgencia": <0|1|2>}}

Equipos: {json.dumps(EQUIPOS, ensure_ascii=False)}
reembolso: true solo si el cliente pide explícitamente que le devuelvan dinero.
urgencia: {json.dumps(dict(enumerate(URGENCIAS)), ensure_ascii=False)}"""

def parsear(texto: str) -> dict | None:
    m = re.search(r"\{.*\}", re.sub(r"<think>.*?</think>", "", texto, flags=re.S), re.S)
    try:
        d = json.loads(m.group(0)) if m else None
        if d["equipo"] in EQUIPOS and int(d["urgencia"]) in (0, 1, 2):
            return {"equipo": d["equipo"], "reembolso": bool(d["reembolso"]), "urgencia": int(d["urgencia"])}
    except (TypeError, KeyError, ValueError, json.JSONDecodeError):
        pass
    return None


# --- Contendientes: cada uno devuelve (prediccion | None, tokens_entrada, tokens_salida, extra) ---

# Las mismas preguntas en el formato JSON de /v1/systemone (petición directa para leer usage.cost)
PREGUNTAS_JEV = {
    "equipo": {"type": "choice", "instructions": PREGUNTAS["equipo"].instructions, "criteria": EQUIPOS},
    "reembolso": {"type": "noul", "instructions": PREGUNTAS["reembolso"].instructions},
    "urgencia": {"type": "score", "instructions": PREGUNTAS["urgencia"].instructions, "criteria": URGENCIAS},
}


def modo_jev(http: httpx.Client, modelo: str):
    def f(ticket):
        r = http.post(f"{OPENROUTER_URL}/v1/systemone", json={
            "model": modelo, "state": {"ticket": ticket}, "questions": PREGUNTAS_JEV})
        r.raise_for_status()
        d = r.json()
        a, u = d["answers"], d.get("usage", {})
        urg = a["urgencia"]["probabilities"]
        pred = {
            "equipo": a["equipo"]["choice"],
            "reembolso": a["reembolso"]["noul"] > 0.5,
            "urgencia": int(max(urg, key=urg.get)),
        }
        return (pred, u.get("input_tokens", 0), u.get("output_tokens", 0),
                {"confianza": a["equipo"]["confidence"], "coste": u.get("cost", 0) or 0})
    return f


def modo_decision(client: DecisionClient, modelo: str):
    def f(ticket):
        r = client.system_one(state={"ticket": ticket}, questions=PREGUNTAS, model=modelo)
        eq, urg = r.choices["equipo"], r.scores["urgencia"]
        pred = {
            "equipo": eq.choice,
            "reembolso": r.nouls["reembolso"].noul > 0.5,
            "urgencia": max(urg.probabilities, key=urg.probabilities.get),
        }
        return pred, r.usage.input_tokens, r.usage.output_tokens, {"confianza": eq.confidence, "coste": r.usage.cost}
    return f


def modo_json(modelo: str):
    def f(ticket):
        r = openrouter({"model": modelo, "temperature": 0,
                        "messages": [{"role": "system", "content": PROMPT_LLM}, {"role": "user", "content": ticket}]})
        u = r.get("usage", {})
        return (parsear(r["choices"][0]["message"]["content"] or ""),
                u.get("prompt_tokens", 0), u.get("completion_tokens", 0), {"coste": u.get("cost", 0) or 0})
    return f


def evaluar(nombre, fn, datos, console):
    fn(datos[0]["ticket"])  # calentamiento: carga el modelo / abre conexión
    filas = []
    for i, d in enumerate(datos, 1):
        t0 = time.perf_counter()
        try:
            pred, tin, tout, extra = fn(d["ticket"])
        except Exception as e:  # noqa: BLE001
            pred, tin, tout, extra = None, 0, 0, {"error": str(e)}
        ms = (time.perf_counter() - t0) * 1000
        filas.append({**d, "pred": pred, "ms": ms, "tin": tin, "tout": tout, **extra})
        console.print(f"  [{nombre}] {i}/{len(datos)} {ms:6.0f} ms", end="\r")
    console.print(" " * 60, end="\r")
    return filas


def resumen(filas):
    ok = [f for f in filas if f["pred"]]
    acierto = lambda k: sum(f["pred"][k] == f[k] for f in ok) / len(filas)  # noqa: E731
    lat = [f["ms"] for f in filas]
    return {
        "equipo": acierto("equipo"), "reembolso": acierto("reembolso"), "urgencia": acierto("urgencia"),
        "todo": sum(all(f["pred"][k] == f[k] for k in ("equipo", "reembolso", "urgencia")) for f in ok) / len(filas),
        "ms_media": mean(lat), "ms_p95": quantiles(lat, n=20)[-1],
        "mal_formadas": len(filas) - len(ok),
        "tok_in": mean(f["tin"] for f in filas), "tok_out": mean(f["tout"] for f in filas),
        "coste": sum(f.get("coste", 0) for f in filas),
    }


def main():
    console = Console()
    datos = json.loads(Path("dataset_etiquetado.json").read_text(encoding="utf-8"))
    elegidos = sys.argv[1:] or [f"jev:{MODELO}", f"dec:{LLM_IMITACION}", f"json:{LLM_IMITACION}"]

    resultados = {}
    with DecisionClient() as client, httpx.Client(headers=cabeceras(), timeout=120) as http:
        for nombre in elegidos:
            modo, modelo = nombre.split(":", 1)
            fn = {"jev": lambda: modo_jev(http, modelo), "dec": lambda: modo_decision(client, modelo),
                  "json": lambda: modo_json(modelo)}[modo]()
            console.print(f"Evaluando [bold]{nombre}[/bold]...")
            resultados[nombre] = evaluar(nombre, fn, datos, console)

    t = Table(title=f"Comparativa · {len(datos)} tickets etiquetados", show_lines=True)
    for col in ["Modelo", "Equipo", "Reembolso", "Urgencia", "Las 3 bien", "ms media", "ms p95",
                "JSON roto", "Tokens in/out", "Coste $"]:
        t.add_column(col, justify="left" if col == "Modelo" else "right")
    for nombre, filas in resultados.items():
        s = resumen(filas)
        t.add_row(nombre, f"{s['equipo']:.0%}", f"{s['reembolso']:.0%}", f"{s['urgencia']:.0%}",
                  f"{s['todo']:.0%}", f"{s['ms_media']:.0f}", f"{s['ms_p95']:.0f}",
                  str(s["mal_formadas"]), f"{s['tok_in']:.0f}/{s['tok_out']:.0f}", f"{s['coste']:.4f}")
    console.print(t)

    # Lo que solo da un modelo de decisión: probabilidades calibrables -> automatizar solo lo seguro
    for nombre, filas in resultados.items():
        if nombre.startswith("json:"):
            continue
        c = Table(title=f"{nombre}: si solo automatizo cuando confianza(equipo) >= umbral")
        for col in ["Umbral", "Se automatiza", "Acierto en lo automatizado", "A revisión humana"]:
            c.add_column(col, justify="right")
        for u in [0.0, 0.5, 0.7, 0.8, 0.9]:
            auto = [f for f in filas if f["pred"] and f.get("confianza", 0) >= u]
            ac = sum(f["pred"]["equipo"] == f["equipo"] for f in auto) / len(auto) if auto else 0
            c.add_row(f"{u:.1f}", f"{len(auto) / len(filas):.0%}", f"{ac:.0%}", str(len(filas) - len(auto)))
        console.print(c)

    # Errores de cada modelo en la pregunta "equipo", para ver dónde falla
    for nombre, filas in resultados.items():
        malos = [f for f in filas if not f["pred"] or f["pred"]["equipo"] != f["equipo"]]
        if malos:
            console.print(f"\n[bold]{nombre}[/bold] falla 'equipo' en:")
            for f in malos:
                p = f["pred"]["equipo"] if f["pred"] else "JSON roto"
                console.print(f"  esperado={f['equipo']:<11} obtenido={p:<11} {f['ticket'][:70]}")

    Path("resultados").mkdir(exist_ok=True)
    Path("resultados/comparativa_openrouter.json").write_text(
        json.dumps(resultados, ensure_ascii=False, indent=1), encoding="utf-8")
    console.print("\nDetalle guardado en resultados/comparativa_openrouter.json")


if __name__ == "__main__":
    main()
