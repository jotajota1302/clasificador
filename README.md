# Clasificador con modelos de decisión (OpenRouter, sin GPU)

Proyecto de prueba para entender los *decision models*
([anuncio de Ollama](https://ollama.com/blog/ollama-now-supports-jev-style-decision-models)).
La versión original corría en local con Ollama (`nimble`) y una GPU; esta versión
usa **Jev**, el modelo de decisión de TypeSafe, **servido por OpenRouter**, así que
funciona en cualquier portátil con una sola API key
([guía de OpenRouter](https://openrouter.ai/docs/guides/community/jev)).

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

## Jev en OpenRouter

OpenRouter sirve Jev en el mismo endpoint que Ollama, `/v1/systemone`, con la
misma petición (`state` + `questions`) y la misma respuesta tipada. Por eso el
código original casi no cambia: el SDK oficial `typesafe-sdk` solo necesita otra
URL base y la clave de OpenRouter (lo hace `config.py`):

```
TYPESAFE_BASE_URL      = https://openrouter.ai/api     # el SDK añade /v1/systemone
TYPESAFE_API_KEY       = <tu OPENROUTER_API_KEY>
TYPESAFE_DEFAULT_MODEL = typesafe/jev-1.13             # o ~typesafe/jev-latest
```

Jev cobra solo los tokens de entrada (unos $0.00002 por ticket con 3 preguntas)
y cada respuesta trae `usage.cost`. Contexto máximo: 32k tokens. No genera texto:
para eso están los LLM normales, también por OpenRouter (`LLM_RAPIDO`, `LLM_GRANDE`).

> **¿Y los *Classifiers* del panel de OpenRouter?** (`Workspace → Classifiers`)
> Son otra cosa: etiquetan *en segundo plano* una muestra de todas las peticiones
> del workspace (departamento, tipo de tarea…) para los gráficos de *Activity*.
> No se llaman desde tu código ni sirven para decidir en tiempo real.

### La imitación (`decision.py`), solo para comparar

Para enseñar qué aporta un modelo de decisión de verdad, `decision.py` imita su API
con un LLM de chat (`openai/gpt-4o-mini`): una llamada por pregunta pidiendo **un
solo token** y leyendo sus `logprobs` como probabilidades. Se parece por fuera, pero
es más lento (~1,3 s), más caro y sus probabilidades salen casi siempre 0 o 1.
Aparece en la comparativa como `dec:`.

## Puesta en marcha

```powershell
pip install uv                 # si no lo tienes
uv sync
# La clave va en .env (está en .gitignore):
#   OPENROUTER_API_KEY=sk-or-v1-...
```

Variables opcionales: `TYPESAFE_DEFAULT_MODEL` (versión de Jev), `LLM_RAPIDO` y
`LLM_GRANDE` (generación, por defecto `openai/gpt-4o-mini` y `minimax/minimax-m3`),
`LLM_IMITACION` (modelo de `decision.py`).
`config.py` usa `truststore` para que Python confíe en los certificados de Windows
(necesario detrás de un proxy corporativo que inspecciona TLS).

## Scripts (en orden)

| Script               | Qué enseña                                                        |
|----------------------|-------------------------------------------------------------------|
| `01_api_cruda.py`    | La petición/respuesta HTTP en bruto a `/v1/systemone` (incluye `usage.cost`) |
| `02_sdk.py`          | Lo mismo con el SDK `typesafe-sdk` (respuestas tipadas)           |
| `03_clasificador.py` | Triaje en lote de `tickets.json` con latencia por ticket          |
| `04_router.py`       | *Model routing*: Jev decide si mandar la pregunta a `LLM_RAPIDO` o a `LLM_GRANDE` |
| `05_triaje_y_respuesta.py` | Flujo completo: Jev clasifica y `LLM_GRANDE` redacta el borrador; si hay dudas, va a un humano |
| `06_comparativa.py`  | 40 tickets etiquetados: Jev (`jev:`) vs imitación (`dec:`) vs LLM que escribe JSON (`json:`) |
| `07_moderacion_chat.py` | Chat en directo a 2 msg/s con presupuesto de 1 s por mensaje   |

```powershell
uv run 01_api_cruda.py
uv run 02_sdk.py
uv run 03_clasificador.py
uv run 04_router.py "Explícame paso a paso cómo funciona RSA"
uv run 05_triaje_y_respuesta.py "No puedo entrar en mi cuenta y tengo una demo en 1 hora"
uv run 06_comparativa.py
uv run 06_comparativa.py jev:typesafe/jev-1.13 json:minimax/minimax-m3
uv run 07_moderacion_chat.py
```

## Demo web

```powershell
uv run demo_web.py              # abre http://127.0.0.1:8765  (--puerto para cambiarlo)
```

Un servidor local (`demo_web.py`, solo librería estándar + httpx) sirve `web/index.html`
y hace de proxy a OpenRouter, así la clave no llega al navegador. Todo es en vivo:

- **Tres carriles con el mismo ticket**: el LLM escribiendo su JSON token a token y
  luego parseado; Jev decidiendo en una llamada con barras de probabilidad; y el flujo
  combinado, donde Jev decide, la confianza pasa por una compuerta y solo si está clara
  el LLM grande redacta el borrador (si no, va a una persona). Cada paso muestra su
  tiempo y cada carril sus ms, tokens y coste por 1.000 tickets. Con un ticket del
  dataset se marca qué coincide con la etiqueta manual.
- **La carrera**: los 40 tickets etiquetados, LLM y Jev a la vez, con acierto, ms y
  coste en directo, y un deslizador que muestra cuánto se automatiza con cada umbral.
  Al terminar aparece un resumen de calidad, tiempo y coste (con cuántas veces más
  rápido y más barato) y un gráfico de tiempo frente a acierto.

Si OpenRouter usa tu propia clave de un proveedor (BYOK, como MiniMax en esta cuenta),
el coste real se lee de `cost_details.upstream_inference_cost`.

## Resultados con Jev en OpenRouter (desde la red de la oficina)

`uv run 06_comparativa.py` (salida completa en `resultados/comparativa_openrouter_salida.txt`):

| Contendiente | Equipo | Reembolso | Urgencia | Las 3 bien | ms media | ms p95 | Coste 40 tickets |
|---|---|---|---|---|---|---|---|
| **jev: typesafe/jev-1.13** (decisión) | 98 % | 98 % | **82 %** | **80 %** | **331** | **387** | **$0.0008** |
| dec: gpt-4o-mini (imitación) | 98 % | 100 % | 68 % | 68 % | 1337 | 1981 | $0.0025 |
| json: gpt-4o-mini (LLM) | 92 % | 100 % | 70 % | 68 % | 1842 | 2638 | $0.0018 |

- Jev acierta más (sobre todo en urgencia), es **4-5 veces más rápido** con latencia
  estable, y cuesta un tercio.
- **Probabilidades útiles**: con confianza ≥ 0.8, Jev automatiza el **95 %** de los
  tickets con un **100 % de acierto** y manda 2 a un humano. La imitación con
  gpt-4o-mini casi siempre dice 1.00, así que no separa bien lo dudoso.
- **Moderación en directo** (`07`, 2 msg/s): Jev decide el **100 % a tiempo**
  (318 ms por mensaje, 0,4 s de retraso máximo, 100 % de acierto) y manda 2 mensajes
  ambiguos a revisión. gpt-4o-mini, con 97 % de acierto, acumula **hasta 39 s de cola**.

## Ideas para experimentar

- Cambia las descripciones de `criteria` y observa cómo se mueven las probabilidades.
- Añade una etiqueta `otro` y comprueba si baja la confianza en tickets raros.
- Usa `state` como objeto con varios campos (`asunto`, `cuerpo`, `plan_cliente`…).
- Mide cuánto cuesta añadir más preguntas a la misma llamada (casi nada: el
  contexto se procesa una vez).

## Resultados originales con Ollama (RTX 3060, versión anterior)

Medidos con la versión local (`nimble` en Ollama + MiniMax) en una RTX 3060 (12 GB).
El código de esa versión está en el historial de git (commit `a931b1d`).

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
