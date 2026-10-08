"""Paso 6: comparativa en la misma tarea con 40 tickets etiquetados a mano.

Contendientes:
  - nimble            modelo de DECISIÓN local (Ollama /v1/systemone)
  - qwen3:4b          LLM genérico local (Ollama /api/chat + JSON schema)
  - MiniMax Flash/M3  LLM en la nube (se le pide JSON en el prompt)

Mide: acierto por pregunta, latencia media y p95, respuestas mal formadas
y tokens. Además, para nimble, la curva confianza -> cobertura/acierto
(automatizar solo lo que el modelo tiene claro), que un LLM que escribe texto no da.

Uso:  uv run 06_comparativa.py            # todos
      uv run 06_comparativa.py nimble qwen3:4b
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from statistics import mean, quantiles

import httpx
from rich.console import Console
from rich.table import Table

from config import MINIMAX_GRANDE, MINIMAX_RAPIDO, MINIMAX_URL, MODELO, OLLAMA_URL
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

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

ESQUEMA = {
    "type": "object",
    "properties": {
        "equipo": {"type": "string", "enum": list(EQUIPOS)},
        "reembolso": {"type": "boolean"},
        "urgencia": {"type": "integer", "enum": [0, 1, 2]},
    },
    "required": ["equipo", "reembolso", "urgencia"],
}


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

def con_nimble(client: TypeSafeClient):
    def f(ticket):
        r = client.system_one(state={"ticket": ticket}, questions=PREGUNTAS, model=MODELO)
        eq, urg = r.choices["equipo"], r.scores["urgencia"]
        pred = {
            "equipo": eq.choice,
            "reembolso": r.nouls["reembolso"].noul > 0.5,
            "urgencia": max(urg.probabilities, key=urg.probabilities.get),
        }
        return pred, r.usage.input_tokens, r.usage.output_tokens, {"confianza": eq.confidence}
    return f


def con_ollama_llm(modelo: str):
    def f(ticket):
        r = httpx.post(f"{OLLAMA_URL}/api/chat", timeout=300, json={
            "model": modelo, "stream": False, "think": False, "format": ESQUEMA,
            "options": {"temperature": 0},
            "messages": [{"role": "system", "content": PROMPT_LLM}, {"role": "user", "content": ticket}],
        }).json()
        return parsear(r["message"]["content"]), r.get("prompt_eval_count", 0), r.get("eval_count", 0), {}
    return f


def con_minimax(modelo: str):
    def f(ticket):
        r = httpx.post(
            f"{MINIMAX_URL}/chat/completions", timeout=300,
            headers={"Authorization": f"Bearer {os.environ['MINIMAX_API_KEY']}"},
            json={"model": modelo, "reasoning_split": True, "temperature": 0.01,
                  "messages": [{"role": "system", "content": PROMPT_LLM}, {"role": "user", "content": ticket}]},
        ).json()
        u = r.get("usage", {})
        return (parsear(r["choices"][0]["message"]["content"]),
                u.get("prompt_tokens", 0), u.get("completion_tokens", 0), {})
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
    }


def main():
    console = Console()
    datos = json.loads(Path("dataset_etiquetado.json").read_text(encoding="utf-8"))
    elegidos = sys.argv[1:] or [MODELO, "qwen3:4b", MINIMAX_RAPIDO, MINIMAX_GRANDE]

    resultados = {}
    with TypeSafeClient(timeout=300) as client:
        for nombre in elegidos:
            if nombre == MODELO:
                fn = con_nimble(client)
            elif nombre.startswith("MiniMax"):
                fn = con_minimax(nombre)
            else:
                fn = con_ollama_llm(nombre)
            console.print(f"Evaluando [bold]{nombre}[/bold]...")
            resultados[nombre] = evaluar(nombre, fn, datos, console)

    t = Table(title=f"Comparativa · {len(datos)} tickets etiquetados", show_lines=True)
    for col in ["Modelo", "Equipo", "Reembolso", "Urgencia", "Las 3 bien", "ms media", "ms p95",
                "JSON roto", "Tokens in/out"]:
        t.add_column(col, justify="left" if col == "Modelo" else "right")
    for nombre, filas in resultados.items():
        s = resumen(filas)
        t.add_row(nombre, f"{s['equipo']:.0%}", f"{s['reembolso']:.0%}", f"{s['urgencia']:.0%}",
                  f"{s['todo']:.0%}", f"{s['ms_media']:.0f}", f"{s['ms_p95']:.0f}",
                  str(s["mal_formadas"]), f"{s['tok_in']:.0f}/{s['tok_out']:.0f}")
    console.print(t)

    # Lo que solo da un modelo de decisión: probabilidades calibrables -> automatizar solo lo seguro
    if MODELO in resultados:
        filas = resultados[MODELO]
        c = Table(title=f"{MODELO}: si solo automatizo cuando confianza(equipo) >= umbral")
        for col in ["Umbral", "Se automatiza", "Acierto en lo automatizado", "A revisión humana"]:
            c.add_column(col, justify="right")
        for u in [0.0, 0.5, 0.7, 0.8, 0.9]:
            auto = [f for f in filas if f["pred"] and f["confianza"] >= u]
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
    Path("resultados/comparativa.json").write_text(
        json.dumps(resultados, ensure_ascii=False, indent=1), encoding="utf-8")
    console.print("\nDetalle guardado en resultados/comparativa.json")


if __name__ == "__main__":
    main()
