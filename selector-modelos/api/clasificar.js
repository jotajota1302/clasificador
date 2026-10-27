// Función serverless de Vercel: proxy a Jev (modelo de decisión) en OpenRouter.
// La clave vive en la variable de entorno OPENROUTER_API_KEY y nunca llega al navegador.
//
// POST /api/clasificar  { items: [{ id, state }] }
//   -> { modelo, items: [{ id, ms, coste, answers }] }
// Cada item es una tarea (o una etapa de un workflow) y se clasifica en UNA llamada a Jev.
// Las preguntas viven aquí para que el cliente no pueda cambiarlas.

const URL_JEV = "https://openrouter.ai/api/v1/systemone";
const MODELO = process.env.JEV_MODEL || "typesafe/jev-1.13";

// Frenos contra el uso masivo (la demo es pública y la paga la clave de OpenRouter).
// El tope de verdad es el límite de crédito de la clave en OpenRouter; esto evita
// que alguien lo agote en un rato con un script.
const MAX_ITEMS = 12;                 // llamadas a Jev por petición (un workflow largo)
const MAX_CHARS_ITEM = 6000;          // tamaño del state de cada llamada
const VENTANA_MS = 10 * 60 * 1000;    // ventana del límite por IP
const MAX_PETICIONES = Number(process.env.MAX_PETICIONES_IP || 20); // por IP y ventana
const vistas = new Map();             // ip -> [instantes]; vive mientras viva la instancia

function limitePorIp(req) {
  const ip = String(req.headers["x-forwarded-for"] || req.socket?.remoteAddress || "?").split(",")[0].trim();
  const ahora = Date.now();
  const recientes = (vistas.get(ip) || []).filter((t) => ahora - t < VENTANA_MS);
  if (recientes.length >= MAX_PETICIONES) {
    return Math.ceil((VENTANA_MS - (ahora - recientes[0])) / 1000);
  }
  recientes.push(ahora);
  vistas.set(ip, recientes);
  if (vistas.size > 5000) vistas.clear(); // que no crezca sin fin
  return 0;
}

export const PREGUNTAS = {
  complejidad: {
    type: "score",
    instructions: "¿Cómo de compleja es esta tarea de programación para un agente de IA?",
    criteria: [
      "Trivial: cambio mecánico, rutina o pasos ya escritos",
      "Baja: tarea acotada y conocida, un fichero o poco más",
      "Media: varios ficheros o decisiones de diseño moderadas",
      "Alta: arquitectura, mucha ambigüedad o un problema novedoso",
    ],
  },
  riesgo: {
    type: "score",
    instructions: "Si el agente se equivoca en esta tarea, ¿cuánto cuesta el error?",
    criteria: [
      "Bajo: el fallo se ve enseguida y se corrige sin coste",
      "Medio: hay que rehacer trabajo o el fallo llega a revisión",
      "Alto: el fallo se propaga a etapas siguientes, a producción, a datos o a seguridad",
    ],
  },
  tipo: {
    type: "choice",
    instructions: "¿Qué tipo de trabajo es principalmente?",
    criteria: {
      planificar: "Entender un problema, analizar requisitos o planificar pasos",
      disenar: "Diseñar arquitectura, interfaces o especificaciones",
      implementar: "Escribir o modificar código",
      depurar: "Investigar y corregir un fallo cuyo origen no se conoce",
      revisar: "Revisar código o un cambio ajeno y detectar problemas",
      probar: "Escribir o ejecutar pruebas y verificar resultados",
      documentar: "Redactar documentación, specs de cierre o resúmenes",
      rutina: "Operación mecánica: git, ramas, MR, mover ficheros, formatear",
    },
  },
  razonamiento: {
    type: "noul",
    instructions: "¿Requiere razonamiento profundo de varios pasos o sopesar alternativas no evidentes?",
  },
  contexto: {
    type: "score",
    instructions: "¿Cuánto contexto tiene que leer el agente para hacerla bien?",
    criteria: [
      "Poco: un texto corto o un fichero",
      "Medio: varios ficheros o un módulo",
      "Mucho: gran parte del repositorio o documentación extensa",
    ],
  },
};

async function clasificar(state, clave) {
  const t0 = Date.now();
  const r = await fetch(URL_JEV, {
    method: "POST",
    headers: { Authorization: `Bearer ${clave}`, "Content-Type": "application/json", "X-Title": "selector-modelos-poc" },
    body: JSON.stringify({ model: MODELO, state, questions: PREGUNTAS }),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok || d.error) throw new Error(d.error?.message || JSON.stringify(d.error) || `HTTP ${r.status}`);
  return { ms: Date.now() - t0, coste: d.usage?.cost ?? null, answers: d.answers, usage: d.usage, modeloServido: d.model };
}

export default async function handler(req, res) {
  if (req.method !== "POST") return res.status(405).json({ error: "Usa POST" });
  const clave = process.env.OPENROUTER_API_KEY;
  if (!clave) return res.status(500).json({ error: "Falta OPENROUTER_API_KEY en el entorno" });

  const codigo = process.env.DEMO_CODIGO; // opcional: si está, hay que mandarlo
  if (codigo && req.headers["x-demo-codigo"] !== codigo) {
    return res.status(401).json({ error: "Código de demo incorrecto", pideCodigo: true });
  }

  const espera = limitePorIp(req);
  if (espera) {
    res.setHeader("Retry-After", String(espera));
    return res.status(429).json({ error: `Demasiadas clasificaciones seguidas: prueba otra vez en ${Math.ceil(espera / 60)} min` });
  }

  let body;
  try { body = typeof req.body === "string" ? JSON.parse(req.body || "{}") : req.body || {}; }
  catch { return res.status(400).json({ error: "JSON no válido" }); }
  const items = Array.isArray(body.items) ? body.items.slice(0, MAX_ITEMS) : [];
  if (!items.length) return res.status(400).json({ error: "Envía items: [{ id, state }]" });
  if (items.some((it) => JSON.stringify(it.state ?? "").length > MAX_CHARS_ITEM)) {
    return res.status(413).json({ error: `Cada tarea o etapa admite como mucho ${MAX_CHARS_ITEM} caracteres` });
  }

  try {
    const salida = await Promise.all(
      items.map(async (it) => ({ id: it.id, ...(await clasificar(it.state, clave)) }))
    );
    // Las preguntas van de vuelta para que la ayuda de la web enseñe la petición exacta
    res.status(200).json({ modelo: MODELO, endpoint: URL_JEV, preguntas: PREGUNTAS, items: salida });
  } catch (e) {
    res.status(502).json({ error: String(e.message || e) });
  }
}
