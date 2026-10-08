"""Demo web: LLM vs Jev vs combinado, en vivo.

Servidor local mínimo (solo librería estándar + httpx) que sirve web/index.html y
hace de proxy a OpenRouter, así la API key nunca llega al navegador.

Uso:  uv run demo_web.py            -> abre http://127.0.0.1:8765
      uv run demo_web.py --puerto 9000
"""

import argparse
import json
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from config import LLM_GRANDE, LLM_RAPIDO, MODELO, OPENROUTER_URL, cabeceras

RAIZ = Path(__file__).parent
WEB = RAIZ / "web"

EQUIPOS = {
    "facturacion": "Pagos, cobros, facturas y devoluciones",
    "tecnico": "Errores, caídas, seguridad, acceso e integraciones",
    "comercial": "Precios, descuentos, presupuestos, ampliaciones y altas nuevas",
    "otro": "Nada de lo anterior",
}
URGENCIAS = ["Rutina: puede esperar", "Pronto: hay que atenderlo hoy o mañana", "Urgente: impacto grave ahora mismo"]

# Las mismas preguntas que 06_comparativa.py, para poder medir acierto contra dataset_etiquetado.json
PREGUNTAS_JEV = {
    "equipo": {"type": "choice", "instructions": "¿Qué equipo debe gestionar este ticket?", "criteria": EQUIPOS},
    "reembolso": {"type": "noul", "instructions": "¿El cliente pide explícitamente que le devuelvan dinero?"},
    "urgencia": {"type": "score", "instructions": "¿Cómo de urgente es este ticket?", "criteria": URGENCIAS},
}

PROMPT_LLM = f"""Clasifica el ticket de soporte. Responde SOLO con un JSON con esta forma exacta:
{{"equipo": "<una de: {', '.join(EQUIPOS)}>", "reembolso": <true|false>, "urgencia": <0|1|2>}}

Equipos: {json.dumps(EQUIPOS, ensure_ascii=False)}
reembolso: true solo si el cliente pide explícitamente que le devuelvan dinero.
urgencia: {json.dumps(dict(enumerate(URGENCIAS)), ensure_ascii=False)}"""

MODELOS_LLM = [LLM_RAPIDO, LLM_GRANDE]

http = httpx.Client(headers=cabeceras(), timeout=180)


def jev(ticket: str) -> dict:
    peticion = {"model": MODELO, "state": {"ticket": ticket}, "questions": PREGUNTAS_JEV}
    t0 = time.perf_counter()
    r = http.post(f"{OPENROUTER_URL}/v1/systemone", json=peticion)
    ms = (time.perf_counter() - t0) * 1000
    r.raise_for_status()
    d = r.json()
    return {"ms": ms, "peticion": peticion, "respuesta": d}


def stream_chat(modelo: str, mensajes: list[dict], **extra):
    """Genera eventos {'tipo': 'token'|'razonando'|'fin', ...} a partir del streaming de OpenRouter."""
    t0 = time.perf_counter()
    primer = None
    texto = []
    uso = {}
    with http.stream("POST", f"{OPENROUTER_URL}/v1/chat/completions", json={
        "model": modelo, "messages": mensajes, "stream": True,
        "stream_options": {"include_usage": True}, "usage": {"include": True}, **extra,
    }) as r:
        if r.is_error:
            r.read()
            raise RuntimeError(f"OpenRouter {r.status_code}: {r.text[:300]}")
        for linea in r.iter_lines():
            if not linea.startswith("data: ") or linea == "data: [DONE]":
                continue
            d = json.loads(linea[6:])
            if "error" in d:
                raise RuntimeError(str(d["error"])[:300])
            if d.get("usage"):
                uso = d["usage"]
            for c in d.get("choices", []):
                delta = c.get("delta") or {}
                if delta.get("reasoning"):
                    yield {"tipo": "razonando", "t": delta["reasoning"]}
                if delta.get("content"):
                    if primer is None:
                        primer = (time.perf_counter() - t0) * 1000
                    texto.append(delta["content"])
                    yield {"tipo": "token", "t": delta["content"]}
    yield {
        "tipo": "fin", "ms": (time.perf_counter() - t0) * 1000, "ms_primer_token": primer,
        "texto": "".join(texto), "modelo": modelo,
        "tokens_entrada": uso.get("prompt_tokens", 0), "tokens_salida": uso.get("completion_tokens", 0),
        "tokens_razonamiento": (uso.get("completion_tokens_details") or {}).get("reasoning_tokens", 0),
        # Con BYOK (clave propia del proveedor) OpenRouter cobra 0 y el coste real va en cost_details
        "coste": uso.get("cost") or (uso.get("cost_details") or {}).get("upstream_inference_cost") or 0,
    }


def borrador_mensajes(ticket: str, c: dict) -> list[dict]:
    """Igual que 05_triaje_y_respuesta.py: la clasificación se convierte en instrucciones."""
    pautas = [f"Respondes en nombre del equipo de {c['equipo']} de una empresa de software."]
    if c.get("reembolso"):
        pautas.append("Pide reembolso: confirma que se revisará el cargo y que el reembolso se tramita en 3-5 días hábiles.")
    if c.get("urgencia", 0) >= 1.4:
        pautas.append("Es urgente: indica que el caso se ha escalado con prioridad alta.")
    pautas.append("Máximo 4 frases, en español, tono profesional y cercano. Firma como 'Equipo de soporte'.")
    return [{"role": "system", "content": "\n".join(pautas)}, {"role": "user", "content": ticket}]


class Manejador(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)

    def _json(self, codigo: int, obj) -> None:
        cuerpo = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _stream(self, eventos) -> None:
        """Envía NDJSON (un evento JSON por línea) en chunked encoding."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def chunk(obj):
            datos = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
            self.wfile.write(f"{len(datos):X}\r\n".encode() + datos + b"\r\n")
            self.wfile.flush()

        try:
            for ev in eventos:
                chunk(ev)
        except Exception as e:  # noqa: BLE001
            chunk({"tipo": "error", "mensaje": str(e)})
        self.wfile.write(b"0\r\n\r\n")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            cuerpo = (WEB / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)
        elif self.path == "/api/config":
            self._json(200, {
                "jev": MODELO, "llms": MODELOS_LLM, "llm_borrador": LLM_GRANDE,
                "preguntas": PREGUNTAS_JEV, "prompt_llm": PROMPT_LLM,
                "tickets": json.loads((RAIZ / "dataset_etiquetado.json").read_text(encoding="utf-8")),
            })
        else:
            self._json(404, {"error": "No existe"})

    def do_POST(self):
        cuerpo = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        try:
            if self.path == "/api/jev":
                self._json(200, jev(cuerpo["ticket"]))
            elif self.path == "/api/llm":
                modelo = cuerpo.get("modelo") if cuerpo.get("modelo") in MODELOS_LLM else LLM_RAPIDO
                mensajes = [{"role": "system", "content": PROMPT_LLM}, {"role": "user", "content": cuerpo["ticket"]}]
                self._stream(stream_chat(modelo, mensajes, temperature=0))
            elif self.path == "/api/borrador":
                self._stream(stream_chat(LLM_GRANDE, borrador_mensajes(cuerpo["ticket"], cuerpo["clasificacion"])))
            else:
                self._json(404, {"error": "No existe"})
        except Exception as e:  # noqa: BLE001
            self._json(502, {"error": str(e)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--puerto", type=int, default=8765)
    ap.add_argument("--sin-navegador", action="store_true")
    args = ap.parse_args()
    url = f"http://127.0.0.1:{args.puerto}"
    try:
        servidor = ThreadingHTTPServer(("127.0.0.1", args.puerto), Manejador)
    except OSError:
        raise SystemExit(f"El puerto {args.puerto} está ocupado. Prueba con: uv run demo_web.py --puerto 9000")
    print(f"Demo en {url}  (Ctrl+C para parar)")
    if not args.sin_navegador:
        webbrowser.open(url)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
