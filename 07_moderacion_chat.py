"""Paso 7: moderación de un chat en directo (la tarea donde brilla un modelo de decisión).

Un chat de streaming con mucho movimiento recibe varios mensajes por segundo y
cada mensaje debe decidirse ANTES de mostrarse (presupuesto: 1 s). Simulamos
que llegan a un ritmo fijo, un moderador los procesa en orden y medimos:

  - retraso real de cada mensaje = espera en la cola + tiempo de decisión
  - % de mensajes decididos dentro del presupuesto
  - acierto frente a las etiquetas manuales

Jev además da probabilidades, así que usamos tres zonas:
  P >= 0.9       -> ocultar automáticamente
  0.5 <= P < 0.9 -> ocultar y mandar a un moderador humano
  P < 0.5        -> publicar

Uso:  uv run 07_moderacion_chat.py                 # Jev vs LLM rápido que escribe JSON
      uv run 07_moderacion_chat.py --ritmo 3       # 3 mensajes/segundo
"""

import argparse
import json
import re
import time
from pathlib import Path
from statistics import quantiles

from rich.console import Console
from rich.table import Table

from config import LLM_RAPIDO, MODELO, openrouter
from typesafe_sdk import Noul, TypeSafeClient

PRESUPUESTO_MS = 1000

PREGUNTAS = {
    "ofensivo": Noul(instructions="¿El mensaje insulta, amenaza, acosa o discrimina a alguien? "
                                  "Las bromas entre amigos y la jerga de videojuegos no cuentan."),
    "spam": Noul(instructions="¿El mensaje es publicidad no solicitada, estafa o autopromoción con enlaces?"),
}

PROMPT = """Eres moderador de un chat de streaming. Responde SOLO con JSON:
{"ofensivo": true|false, "spam": true|false}
ofensivo: insulta, amenaza, acosa o discrimina (las bromas entre amigos y la jerga de videojuegos no cuentan).
spam: publicidad no solicitada, estafa o autopromoción con enlaces."""


def moderar_jev(client):
    def f(m):
        r = client.system_one(state={"usuario": m["usuario"], "mensaje": m["texto"]}, questions=PREGUNTAS)
        return {k: r.nouls[k].noul for k in PREGUNTAS}  # probabilidades
    return f


def moderar_llm(m):
    try:
        r = openrouter({"model": LLM_RAPIDO, "temperature": 0, "messages": [
            {"role": "system", "content": PROMPT}, {"role": "user", "content": f"{m['usuario']}: {m['texto']}"}]})
        d = json.loads(re.search(r"\{.*\}", r["choices"][0]["message"]["content"], re.S).group(0))
        return {k: 1.0 if d[k] else 0.0 for k in PREGUNTAS}  # un LLM da un sí/no, no una probabilidad
    except Exception:  # noqa: BLE001  (JSON roto o error de la API)
        return None


def accion(p):
    if p is None:
        return "error", "magenta"
    peor = max(p.values())
    if peor >= 0.9:
        return "OCULTAR", "red"
    if peor >= 0.5:
        return "REVISAR", "yellow"
    return "publicar", "green"


def simular(nombre, fn, chat, ritmo, console, mostrar):
    fn(chat[0])  # calentamiento
    libre_en = 0.0  # instante (s) en que el moderador queda libre
    filas = []
    if mostrar:
        console.rule(f"[bold]{nombre}[/bold] · {ritmo} msg/s")
    for i, m in enumerate(chat):
        llegada = i / ritmo
        t0 = time.perf_counter()
        p = fn(m)
        dur = time.perf_counter() - t0
        inicio = max(llegada, libre_en)  # si está ocupado, el mensaje espera en cola
        libre_en = inicio + dur
        retraso_ms = (libre_en - llegada) * 1000
        acc, color = accion(p)
        filas.append({**m, "p": p, "accion": acc, "retraso_ms": retraso_ms, "decision_ms": dur * 1000})
        if mostrar:
            tarde = "" if retraso_ms <= PRESUPUESTO_MS else f" [red](+{retraso_ms / 1000:.1f} s de retraso)[/red]"
            probs = " ".join(f"{k}={v:.2f}" for k, v in p.items()) if p else ""
            console.print(f"[{color}]{acc:>8}[/{color}] {dur * 1000:5.0f} ms  "
                          f"[dim]{m['usuario']}:[/dim] {m['texto'][:60]}  [dim]{probs}[/dim]{tarde}")
    return filas


def resumen(filas):
    ret = [f["retraso_ms"] for f in filas]
    ok = [f for f in filas if f["p"]]
    bien = sum(all((f["p"][k] >= 0.5) == f[k] for k in PREGUNTAS) for f in ok)
    malos_publicados = sum(f["accion"] == "publicar" and (f["ofensivo"] or f["spam"]) for f in filas)
    return {
        "decision_ms": sum(f["decision_ms"] for f in filas) / len(filas),
        "retraso_p95": quantiles(ret, n=20)[-1], "retraso_max": max(ret),
        "a_tiempo": sum(r <= PRESUPUESTO_MS for r in ret) / len(ret),
        "acierto": bien / len(filas),
        "malos_publicados": malos_publicados,
        "a_humano": sum(f["accion"] == "REVISAR" for f in filas),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ritmo", type=float, default=2.0, help="mensajes por segundo")
    ap.add_argument("--solo-jev", action="store_true")
    args = ap.parse_args()

    console = Console()
    chat = json.loads(Path("chat_directo.json").read_text(encoding="utf-8"))

    resultados = {}
    with TypeSafeClient(timeout=300) as client:
        resultados[f"{MODELO} (decisión)"] = simular(MODELO, moderar_jev(client), chat, args.ritmo, console, True)
    if not args.solo_jev:
        console.print(f"\nEvaluando {LLM_RAPIDO} con el mismo chat...")
        resultados[f"{LLM_RAPIDO} (LLM)"] = simular(LLM_RAPIDO, moderar_llm, chat, args.ritmo, console, False)

    t = Table(title=f"{len(chat)} mensajes a {args.ritmo} msg/s · presupuesto {PRESUPUESTO_MS} ms", show_lines=True)
    for col in ["Moderador", "ms/decisión", "Retraso p95", "Retraso máx", "A tiempo",
                "Acierto", "Tóxico/spam publicado", "A revisión humana"]:
        t.add_column(col, justify="left" if col == "Moderador" else "right")
    for nombre, filas in resultados.items():
        s = resumen(filas)
        t.add_row(nombre, f"{s['decision_ms']:.0f}", f"{s['retraso_p95'] / 1000:.1f} s",
                  f"{s['retraso_max'] / 1000:.1f} s", f"{s['a_tiempo']:.0%}", f"{s['acierto']:.0%}",
                  str(s["malos_publicados"]), str(s["a_humano"]))
    console.print(t)


if __name__ == "__main__":
    main()
