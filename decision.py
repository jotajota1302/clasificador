"""IMITACIÓN de un modelo de decisión con un LLM de chat normal (solo para comparar).

El modelo de decisión de verdad es Jev (ver config.py). Esto imita su API (/v1/systemone):
le das un `state` y unas `questions` (Choice / Noul / Score) y devuelve
respuestas tipadas con PROBABILIDADES, no texto que haya que parsear.

Cómo lo consigue con un LLM normal:
  1. Cada pregunta se convierte en un prompt cuyas opciones son UN carácter
     (A/B/C... para choice, 1/0 para noul, 0..n-1 para score).
  2. Se pide max_tokens=1 con logprobs=True y top_logprobs=5 (el máximo que aceptan todos).
  3. Las probabilidades del primer token, restringidas a las opciones válidas
     y renormalizadas, son la distribución de la respuesta.
  4. confidence = 1 - entropía normalizada (1 = seguro, 0 = reparto uniforme).

Las preguntas se lanzan en paralelo, así que la latencia total es la de la más lenta.
"""

import json
import math
import string
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import httpx

import os

from config import OPENROUTER_URL, cabeceras

# Tiene que ser un modelo cuyo proveedor devuelva logprobs (gpt-4o-mini, qwen3, llama-3.1, mistral-nemo...)
LLM_IMITACION = os.environ.get("LLM_IMITACION", "openai/gpt-4o-mini")

SISTEMA = ("Eres un clasificador preciso. Lees el contexto y respondes a la pregunta "
           "con UN ÚNICO carácter, el de la opción correcta. Sin explicaciones.")


# --- Preguntas ---------------------------------------------------------------

@dataclass
class Choice:
    instructions: str
    criteria: dict[str, str]  # etiqueta -> descripción


@dataclass
class Noul:
    instructions: str


@dataclass
class Score:
    instructions: str
    criteria: list[str]  # niveles ordenados de menor a mayor


# --- Respuestas --------------------------------------------------------------

@dataclass
class ChoiceAnswer:
    choice: str
    probabilities: dict[str, float]
    confidence: float


@dataclass
class NoulAnswer:
    noul: float  # P(sí)
    confidence: float


@dataclass
class ScoreAnswer:
    score: float  # valor esperado sobre los índices 0..n-1
    probabilities: dict[int, float]
    legend: list[str]
    confidence: float


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0  # USD, según OpenRouter


@dataclass
class Resultado:
    choices: dict[str, ChoiceAnswer] = field(default_factory=dict)
    nouls: dict[str, NoulAnswer] = field(default_factory=dict)
    scores: dict[str, ScoreAnswer] = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)


# --- Lógica ------------------------------------------------------------------

def _opciones(q) -> list[tuple[str, str]]:
    """(token, texto mostrado) para cada opción de la pregunta."""
    if isinstance(q, Choice):
        return [(string.ascii_uppercase[i], f"{k}: {v}") for i, (k, v) in enumerate(q.criteria.items())]
    if isinstance(q, Noul):
        return [("1", "Sí"), ("0", "No")]
    return [(str(i), nivel) for i, nivel in enumerate(q.criteria)]


def _mensajes(state, q) -> list[dict]:
    contexto = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False, indent=1)
    opciones = "\n".join(f"{t}) {texto}" for t, texto in _opciones(q))
    validos = ", ".join(t for t, _ in _opciones(q))
    pregunta = (f"Pregunta: {q.instructions}\n\nOpciones:\n{opciones}\n\n"
                f"Responde solo con uno de estos caracteres: {validos}")
    # Medido con dataset_etiquetado.json (06_comparativa.py): en las escalas (score) el modelo
    # sobrestima un nivel si el contexto va pegado a la pregunta, así que ahí la pregunta va en
    # el sistema y el contexto aparte. En choice/noul acierta más con todo en el mismo mensaje.
    if isinstance(q, Score):
        return [{"role": "system", "content": f"{SISTEMA}\n\n{pregunta}"}, {"role": "user", "content": contexto}]
    return [{"role": "system", "content": SISTEMA},
            {"role": "user", "content": f"Contexto:\n{contexto}\n\n{pregunta}"}]


def _confianza(probs: list[float]) -> float:
    if len(probs) < 2:
        return 1.0
    h = -sum(p * math.log(p) for p in probs if p > 0)
    return max(0.0, 1 - h / math.log(len(probs)))


def _distribucion(respuesta: dict, tokens: list[str]) -> list[float]:
    """Probabilidad de cada token válido a partir de los top_logprobs del primer token."""
    masa = dict.fromkeys(tokens, 0.0)
    lp = (respuesta["choices"][0].get("logprobs") or {}).get("content") or []
    candidatos = lp[0]["top_logprobs"] if lp else []
    for c in candidatos:
        t = c["token"].strip().strip("().").upper()
        if t in masa:
            masa[t] += math.exp(c["logprob"])
    total = sum(masa.values())
    if total == 0:  # el proveedor no dio logprobs útiles: usamos el texto generado
        t = (respuesta["choices"][0]["message"]["content"] or "").strip()[:1].upper()
        masa = {k: float(k == t) for k in tokens}
        total = sum(masa.values()) or 1.0
    return [masa[t] / total for t in tokens]


class DecisionClient:
    def __init__(self, model: str = LLM_IMITACION, timeout: float = 60):
        self.model = model
        self._http = httpx.Client(base_url=f"{OPENROUTER_URL}/v1", headers=cabeceras(), timeout=timeout)
        self._pool = ThreadPoolExecutor(max_workers=16)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self._pool.shutdown()
        self._http.close()

    def _preguntar(self, state, q, model):
        payload = {
            "model": model,
            "messages": _mensajes(state, q),
            "max_tokens": 1, "temperature": 0, "logprobs": True, "top_logprobs": 5,  # algunos proveedores no aceptan más de 5
            # solo proveedores que soporten logprobs; si no, OpenRouter daría error en vez de ignorarlo
            "provider": {"require_parameters": True},
        }
        for intento in range(4):  # reintenta límites de tasa (429) y errores del proveedor (5xx)
            r = self._http.post("/chat/completions", json=payload)
            if r.status_code != 429 and r.status_code < 500:
                break
            time.sleep(2 ** intento)
        if r.is_error:
            raise RuntimeError(f"OpenRouter {r.status_code}: {r.text[:500]}")
        d = r.json()
        if "error" in d:
            raise RuntimeError(d["error"])
        return d

    def system_one(self, state, questions: dict, model: str | None = None) -> Resultado:
        model = model or self.model
        futuros = {k: self._pool.submit(self._preguntar, state, q, model) for k, q in questions.items()}
        res = Resultado()
        for nombre, q in questions.items():
            d = futuros[nombre].result()
            u = d.get("usage") or {}
            res.usage.input_tokens += u.get("prompt_tokens", 0)
            res.usage.output_tokens += u.get("completion_tokens", 0)
            res.usage.cost += u.get("cost", 0) or 0

            tokens = [t for t, _ in _opciones(q)]
            p = _distribucion(d, tokens)
            conf = _confianza(p)
            if isinstance(q, Choice):
                probs = dict(zip(q.criteria, p))
                res.choices[nombre] = ChoiceAnswer(max(probs, key=probs.get), probs, conf)
            elif isinstance(q, Noul):
                res.nouls[nombre] = NoulAnswer(p[0], conf)
            else:
                probs = dict(enumerate(p))
                res.scores[nombre] = ScoreAnswer(sum(i * pi for i, pi in probs.items()), probs,
                                                 list(q.criteria), conf)
        return res
