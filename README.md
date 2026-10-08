# Clasificador con modelos de decisión (Ollama + Jev/TypeSafe)

Proyecto de prueba para entender los *decision models* de Ollama 0.35+
([anuncio](https://ollama.com/blog/ollama-now-supports-jev-style-decision-models)).

## La idea en 30 segundos

Un LLM normal **genera texto**. Un modelo de decisión **no genera**: lee un
contexto (`state`) y responde a un conjunto de preguntas con nombre
(`questions`) devolviendo **probabilidades**, todo en una sola llamada y con
~4 tokens de salida. Por eso es tan rápido (decenas de ms) y la respuesta ya
viene tipada: no hay que parsear JSON escrito por el modelo ni rezar para que
respete el formato.

```
state (texto/JSON) ──┐
                     ├──► /v1/systemone ──► answers { nombre: respuesta tipada }
questions {nombre}  ─┘
```

## Los tres tipos de pregunta

| Tipo     | Para qué                      | Devuelve                                                                 |
|----------|-------------------------------|--------------------------------------------------------------------------|
| `choice` | Elegir 1 de N etiquetas       | `choice` (la ganadora), `probabilities` por etiqueta, `confidence`       |
| `noul`   | Sí / no                       | `noul` = P(sí) entre 0 y 1 (tú eliges el umbral)                         |
| `score`  | Escala ordinal (0..n-1)       | `score` = media ponderada de los niveles, `probabilities`, `legend`, `confidence` |

Detalles que conviene tener claros:

- **`noul` no es un booleano**: es una probabilidad. Tú decides el umbral
  (0.5, 0.9…) según lo caro que sea equivocarse.
- **`score` es un valor esperado**, no un nivel: con criterios
  `["Rutina","Pronto","Urgente"]`, un 0.815 significa "entre Rutina y Pronto,
  más cerca de Pronto". Mira `probabilities` para ver la distribución.
- **`confidence`** (0 a 1) es la certeza del modelo en su respuesta. En el
  ejemplo del blog, `urgency` tiene probabilidades repartidas (0.38/0.43/0.19)
  y confianza 0.046 aunque el `score` parezca un número "concreto". Úsala para
  mandar los casos dudosos a revisión humana.
- Las descripciones de los `criteria` importan mucho: son tu "prompt".

## Modelos

| Modelo       | Tamaño | Notas                              |
|--------------|--------|------------------------------------|
| `nimble`     | 9B     | Bespoke Labs, open source. El bueno |
| `tev1`       | 4B     | Together AI, experimental          |
| `tev1:0.8b`  | 0.8B   | Together AI, experimental, rapidísimo |

## Puesta en marcha

```powershell
ollama --version        # debe ser >= 0.35
ollama pull nimble      # opcional: ollama pull tev1:0.8b
uv sync
```

## Scripts (en orden)

| Script               | Qué enseña                                                        |
|----------------------|-------------------------------------------------------------------|
| `01_api_cruda.py`    | La petición/respuesta HTTP en bruto a `/v1/systemone`             |
| `02_sdk.py`          | Lo mismo con el SDK `typesafe-sdk` (respuestas tipadas)           |
| `03_clasificador.py` | Triaje en lote de `tickets.json` con latencia por ticket          |
| `04_router.py`       | *Model routing*: Ollama decide si mandar la pregunta a `MiniMax-M3.1-Flash-Preview` o `MiniMax-M3` |
| `05_triaje_y_respuesta.py` | Flujo completo: Ollama clasifica y MiniMax redacta el borrador; si hay dudas, va a un humano |

Reparto: **las decisiones las toma el modelo local** (gratis, ~1 s, los datos no salen
de tu máquina) y **la generación de texto la hace MiniMax** en la nube. Necesita
la variable de entorno `MINIMAX_API_KEY` (endpoint `https://api.minimax.io/v1`,
compatible con OpenAI; se cambia con `MINIMAX_BASE_URL`).

```powershell
uv run 01_api_cruda.py
uv run 02_sdk.py
uv run 03_clasificador.py
uv run 04_router.py "Explícame paso a paso cómo funciona RSA"
uv run 05_triaje_y_respuesta.py "No puedo entrar en mi cuenta y tengo una demo en 1 hora"

# Comparar modelos:
$env:TYPESAFE_DEFAULT_MODEL = "tev1:0.8b"; uv run 03_clasificador.py
```

## Ideas para experimentar

- Cambia las descripciones de `criteria` y observa cómo se mueven las probabilidades.
- Añade una etiqueta `otro` y comprueba si baja la confianza en tickets raros.
- Usa `state` como objeto con varios campos (`asunto`, `cuerpo`, `plan_cliente`…).
- Mide cuánto cuesta añadir más preguntas a la misma llamada (casi nada: el
  contexto se procesa una vez).

## Comparativa y moderación en directo (pasos 6 y 7)

```powershell
uv run 06_comparativa.py          # 40 tickets etiquetados: nimble vs qwen3:4b vs MiniMax
uv run 07_moderacion_chat.py      # chat en directo a 2 msg/s con presupuesto de 1 s por mensaje
```

Resultados medidos en una RTX 3060 (12 GB):

| Modelo | Las 3 bien | ms media | ms p95 | Tokens salida |
|---|---|---|---|---|
| nimble (decisión, local) | 78 % | **698** | **860** | 4 |
| qwen3:4b (LLM, local) | 75 % | 2006 | 2449 | 26 |
| MiniMax-M3.1-Flash-Preview | 80 % | 1472 | 2787 | 46 |
| MiniMax-M3 | 82 % | 2523 | 5604 | 133 |

- En **acierto** empatan: la ventaja del modelo de decisión no es "ser más listo".
- La ventaja es **latencia estable, coste cero y probabilidades**. Con confianza ≥ 0.7,
  nimble automatiza el 80 % de los tickets con un **100 % de acierto** y deja el 20 % a
  un humano. Un LLM que escribe JSON no da una señal de duda comparable.
- En **moderación en directo** (2 msg/s): nimble decide el 100 % a tiempo (0,5 s de
  retraso máximo); MiniMax, con el mismo acierto (97 %), acumula cola hasta 26 s de retraso.
  nimble además manda "te mato si pierdes jajaja" (P=0,69) a revisión en vez de decidir a ciegas.
- Límite honesto: a 3 msg/s la 3060 tampoco da abasto (~450 ms por mensaje).
  Paralelizar las llamadas a la nube reduciría la cola, pero no los ~1,4 s de cada
  mensaje, ni el coste, ni que los datos salgan de tu máquina.
