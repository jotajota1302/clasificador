// Lógica compartida por las dos páginas: catálogo de modelos, política de
// selección y utilidades de pintado.
//
// Jev NO elige el modelo: describe la tarea (complejidad, riesgo, tipo,
// razonamiento, contexto). La política de abajo, determinista y a la vista,
// convierte esas señales + la prioridad del usuario en modelo y esfuerzo.
// Por eso mover los sliders recalcula al instante sin volver a llamar a Jev.

// Catálogo de Axet (precios USD por millón de tokens, de la tabla de referencia).
// `nivel` es la capacidad relativa y `tps` la velocidad de salida: SUPUESTOS de la POC.
export const MODELOS = [
  { id: "gpt-5.6-luna",  nombre: "Luna",  familia: "GPT-5.6", nivel: 0.30, entrada: 0.2, salida: 1.2, tps: 160, var: "--luna" },
  { id: "gpt-5.6-terra", nombre: "Terra", familia: "GPT-5.6", nivel: 0.55, entrada: 2,   salida: 12,  tps: 110, var: "--terra" },
  { id: "gpt-5.6-sol",   nombre: "Sol",   familia: "GPT-5.6", nivel: 0.78, entrada: 4,   salida: 20,  tps: 80,  var: "--sol" },
  { id: "gpt-6-astra",   nombre: "Astra", familia: "GPT-6",   nivel: 1.00, entrada: 10,  salida: 50,  tps: 45,  var: "--astra" },
];
export const CLI = "codex"; // habla la API OpenAI de Axet; también valdría opencode

// Tokens típicos por tipo de trabajo (miles). SUPUESTOS: en la versión buena
// saldrían del reporting de ORCHIDIA (tokens reales por etapa).
const PERFIL = {
  planificar:  { in: 60,  out: 8 },
  disenar:     { in: 80,  out: 12 },
  implementar: { in: 250, out: 30 },
  depurar:     { in: 300, out: 20 },
  revisar:     { in: 150, out: 8 },
  probar:      { in: 120, out: 10 },
  documentar:  { in: 50,  out: 8 },
  rutina:      { in: 30,  out: 3 },
};
const AJUSTE_TIPO = { planificar: 0.08, disenar: 0.10, depurar: 0.08, revisar: 0.03, implementar: 0, probar: -0.08, documentar: -0.12, rutina: -0.15 };
const CACHE = 0.7, FACTOR_CACHE = 0.1;
const ESFUERZO = { low: { mult: 0.6, txt: "bajo" }, medium: { mult: 1, txt: "medio" }, high: { mult: 1.7, txt: "alto" } };
export const ETIQUETA_TIPO = { planificar: "Planificar", disenar: "Diseñar", implementar: "Implementar", depurar: "Depurar", revisar: "Revisar", probar: "Probar", documentar: "Documentar", rutina: "Rutina" };

export const PRESETS = {
  ahorro:     { coste: 70, calidad: 20, tiempo: 10 },
  equilibrio: { coste: 34, calidad: 33, tiempo: 33 },
  calidad:    { coste: 10, calidad: 80, tiempo: 10 },
  rapidez:    { coste: 20, calidad: 20, tiempo: 60 },
};

const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));

export function senales(a) {
  return {
    cx: a.complejidad.score / 3,
    rg: a.riesgo.score / 2,
    rz: a.razonamiento.noul,
    ctx: a.contexto.score / 2,
    tipo: a.tipo.choice,
    // El riesgo suele salir repartido (confianza baja) aun en tareas claras: su duda ya
    // va en el valor esperado, así que no cuenta para escalar.
    conf: Math.min(a.complejidad.confidence ?? 1, a.tipo.confidence ?? 1),
  };
}

function pesos(p) {
  const t = p.coste + p.calidad + p.tiempo || 1;
  return { c: p.coste / t, q: p.calidad / t, t: p.tiempo / t };
}

export function estimar(modelo, s, esfuerzo) {
  const perfil = PERFIL[s.tipo] || PERFIL.implementar;
  const tin = perfil.in * 1000 * (0.5 + s.ctx * 1.5);
  const tout = perfil.out * 1000 * ESFUERZO[esfuerzo].mult;
  const coste = (tin * (1 - CACHE) * modelo.entrada + tin * CACHE * modelo.entrada * FACTOR_CACHE + tout * modelo.salida) / 1e6;
  return { coste, segundos: tout / modelo.tps, tin, tout };
}

// Señales de Jev + prioridad -> { modelo, esfuerzo, requerido, porQue, opciones }
export function decidir(answers, prioridad) {
  const s = senales(answers);
  const w = pesos(prioridad);
  const necesidad = clamp(0.45 * s.cx + 0.25 * s.rg + 0.30 * s.rz + (AJUSTE_TIPO[s.tipo] || 0));
  let requerido = clamp(necesidad + 0.35 * (w.q - 1 / 3) - 0.15 * (w.c - 1 / 3));
  const dudoso = s.conf < 0.45;
  if (dudoso) requerido = clamp(requerido + 0.1); // ante la duda, un nivel más: un reintento sale más caro

  const base = clamp(0.6 * s.cx + 0.4 * s.rz - 0.4 * (w.t + w.c - 2 / 3) + 0.2 * (w.q - 1 / 3));
  const esfuerzo = base < 0.35 ? "low" : base < 0.7 ? "medium" : "high";

  const opciones = MODELOS.map((m) => ({ modelo: m, ...estimar(m, s, esfuerzo), llega: m.nivel >= requerido - 0.06 }));
  const maxC = Math.max(...opciones.map((o) => o.coste)), maxT = Math.max(...opciones.map((o) => o.segundos));
  for (const o of opciones) o.utilidad = w.q * o.modelo.nivel - w.c * (o.coste / maxC) - w.t * (o.segundos / maxT);
  const candidatas = opciones.filter((o) => o.llega);
  const elegida = candidatas.length ? candidatas.reduce((a, b) => (b.utilidad > a.utilidad ? b : a)) : opciones.at(-1);
  elegida.elegida = true;

  return { s, w, necesidad, requerido, dudoso, esfuerzo, elegida, opciones, porQue: explicar(s, requerido, elegida, candidatas, dudoso, w) };
}

const nivelTxt = (x) => (x < 0.35 ? "bajo" : x < 0.6 ? "medio" : x < 0.8 ? "alto" : "muy alto");
const pct = (x) => `${Math.round(x * 100)} %`;

function explicar(s, requerido, elegida, candidatas, dudoso, w) {
  const motivos = [
    `complejidad ${["trivial", "baja", "media", "alta"][Math.round(s.cx * 3)]}`,
    `riesgo ${["bajo", "medio", "alto"][Math.round(s.rg * 2)]}`,
    `razonamiento profundo ${pct(s.rz)}`,
  ];
  let t = `Pide un nivel ${nivelTxt(requerido)} (${motivos.join(", ")}).`;
  const masBarata = candidatas.reduce((a, b) => (b.coste < a.coste ? b : a), candidatas[0] || elegida);
  if (elegida === masBarata) t += ` ${elegida.modelo.nombre} es el más barato que llega.`;
  else if (w.q >= w.c && w.q >= w.t) t += ` Prioridad en calidad: se sube de ${masBarata.modelo.nombre} a ${elegida.modelo.nombre}.`;
  else t += ` Prioridad en tiempo: ${elegida.modelo.nombre} compensa frente a ${masBarata.modelo.nombre}.`;
  if (dudoso) t += ` Jev duda (confianza ${pct(s.conf)}), así que se sube un nivel por seguridad.`;
  return t;
}

// ---------- llamadas ----------
let ultima = null; // última llamada real, para la ayuda
let leerPrioridad = () => ({ ...PRESETS.equilibrio });

// Código de demo: solo se pide si la función tiene DEMO_CODIGO configurado
const leerCodigo = () => { try { return localStorage.getItem("demo-codigo") || ""; } catch { return ""; } };
const guardarCodigo = (c) => { try { localStorage.setItem("demo-codigo", c); } catch {} };

export async function llamarJev(items, reintento = false) {
  const r = await fetch("/api/clasificar", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Demo-Codigo": leerCodigo() },
    body: JSON.stringify({ items }),
  });
  const d = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  if (d.pideCodigo && !reintento) {
    const c = window.prompt("Esta demo pide un código de acceso. Pídeselo a quien te pasó el enlace:");
    if (c) { guardarCodigo(c.trim()); return llamarJev(items, true); }
  }
  if (!r.ok || d.error) throw new Error(d.error || `HTTP ${r.status}`);
  ultima = { enviados: items, d };
  return d;
}

// ---------- pintado ----------
export const usd = (x) => (x < 0.01 ? `${(x * 100).toFixed(2)} ¢` : `${x.toFixed(x < 1 ? 3 : 2)} $`);
export const dur = (sg) => (sg < 60 ? `${Math.round(sg)} s` : `${Math.floor(sg / 60)} min ${Math.round(sg % 60)} s`);
export const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
export const txtEsfuerzo = (e) => ESFUERZO[e].txt;

export function chipModelo(m, extra = "") {
  return `<span class="chip" style="--c:var(${m.var})"><b>${m.nombre}</b><small>${m.familia}</small>${extra}</span>`;
}

// Panel de prioridades: tres sliders + presets. Llama a onCambio(prioridad).
export function montarPrioridad(raiz, onCambio) {
  const p = { ...PRESETS.equilibrio };
  raiz.innerHTML = `
    <div class="presets">${Object.keys(PRESETS).map((k) => `<button type="button" data-p="${k}" class="${k === "equilibrio" ? "on" : ""}">${k[0].toUpperCase() + k.slice(1)}</button>`).join("")}</div>
    ${["coste", "calidad", "tiempo"].map((k) => `
      <label class="slider"><span>${{ coste: "Coste", calidad: "Calidad", tiempo: "Tiempo" }[k]}</span>
        <input type="range" min="0" max="100" value="${p[k]}" data-k="${k}"><output>${p[k]}</output></label>`).join("")}`;
  const sync = () => raiz.querySelectorAll("input").forEach((i) => { i.value = p[i.dataset.k]; i.nextElementSibling.value = p[i.dataset.k]; });
  raiz.querySelectorAll("input").forEach((i) => i.addEventListener("input", () => {
    p[i.dataset.k] = +i.value; i.nextElementSibling.value = i.value;
    raiz.querySelectorAll(".presets button").forEach((b) => b.classList.remove("on"));
    onCambio({ ...p });
  }));
  raiz.querySelectorAll(".presets button").forEach((b) => b.addEventListener("click", () => {
    Object.assign(p, PRESETS[b.dataset.p]); sync();
    raiz.querySelectorAll(".presets button").forEach((x) => x.classList.toggle("on", x === b));
    onCambio({ ...p });
  }));
  leerPrioridad = () => ({ ...p });
  return leerPrioridad;
}

// ---------- ayuda: cómo se llama a Jev por detrás ----------
const resaltar = (o) => esc(JSON.stringify(o, null, 2)).replace(
  /(&quot;(?:[^&]|&(?!quot;))*?&quot;)(\s*:)?|\b(-?\d+(?:\.\d+)?(?:e-?\d+)?)\b|\b(true|false|null)\b/g,
  (m, s, dosPuntos, n, lit) => s ? `<span class="${dosPuntos ? "k" : "s"}">${s}</span>${dosPuntos || ""}`
    : n ? `<span class="n">${n}</span>` : `<span class="l">${lit}</span>`);
const f2 = (x) => x.toFixed(2);

function detalleLlamada(i) {
  const { enviados, d } = ultima, it = d.items[i];
  const peticion = { model: d.modelo, state: enviados[i].state, questions: d.preguntas };
  const respuesta = { model: it.modeloServido, answers: it.answers, usage: it.usage };
  const r = decidir(it.answers, leerPrioridad()), s = r.s, w = r.w;
  const ajuste = AJUSTE_TIPO[s.tipo] || 0;
  return `
    <h3>2 · La petición que sale hacia OpenRouter</h3>
    <p class="nota"><code>POST ${esc(d.endpoint)}</code> con la cabecera <code>Authorization: Bearer sk-or-…</code>. La clave la pone la función de Vercel y el navegador nunca la ve. Fíjate en que no hay prompt: hay un <b>state</b>, que es el contexto, y unas <b>questions</b> con nombre, de tres tipos: <code>score</code> es una escala, <code>choice</code> es elegir una etiqueta y <code>noul</code> es sí o no.</p>
    <pre class="json">${resaltar(peticion)}</pre>
    <h3>3 · Lo que contesta Jev (${it.ms} ms)</h3>
    <p class="nota">No genera texto: devuelve cada respuesta ya tipada, con probabilidades y su <code>confidence</code>. En <code>score</code>, el valor es la media ponderada de los niveles, así que un 1,4 en una escala de 0 a 3 cae entre «baja» y «media». Solo se cobran los tokens de entrada.</p>
    <pre class="json">${resaltar(respuesta)}</pre>
    <h3>4 · Cómo se convierte en un modelo (en tu navegador)</h3>
    <p class="nota">Jev describe la tarea y una política determinista elige el modelo. Por eso al mover los sliders todo se recalcula sin volver a llamar a Jev.</p>
    <div class="formula">
      <div>complejidad = ${f2(it.answers.complejidad.score)} / 3 = <b>${f2(s.cx)}</b> · riesgo = ${f2(it.answers.riesgo.score)} / 2 = <b>${f2(s.rg)}</b> · razonamiento = <b>${f2(s.rz)}</b> · tipo = <b>${ETIQUETA_TIPO[s.tipo]}</b> (ajuste ${ajuste >= 0 ? "+" : ""}${f2(ajuste)})</div>
      <div>necesidad = 0,45·${f2(s.cx)} + 0,25·${f2(s.rg)} + 0,30·${f2(s.rz)} ${ajuste >= 0 ? "+" : "−"} ${f2(Math.abs(ajuste))} = <b>${f2(r.necesidad)}</b></div>
      <div>prioridad: coste ${Math.round(w.c * 100)} % · calidad ${Math.round(w.q * 100)} % · tiempo ${Math.round(w.t * 100)} % → nivel requerido = <b>${f2(r.requerido)}</b>${r.dudoso ? ` (incluye +0,10 porque la confianza es ${f2(s.conf)} &lt; 0,45)` : ""}</div>
      <div>esfuerzo = <b>${txtEsfuerzo(r.esfuerzo)}</b> (sube con la complejidad y el razonamiento y baja si priorizas coste o tiempo)</div>
    </div>
    <table class="tabla"><thead><tr><th>Modelo</th><th class="num">Nivel</th><th>¿Llega a ${f2(r.requerido)}?</th><th class="num">Coste</th><th class="num">Utilidad</th></tr></thead><tbody>
      ${r.opciones.map((o) => `<tr${o.elegida ? ' class="elegida"' : ""}><td>${chipModelo(o.modelo)}</td><td class="num">${f2(o.modelo.nivel)}</td><td>${o.llega ? "sí" : "no"}</td><td class="num">${usd(o.coste)}</td><td class="num">${o.llega ? f2(o.utilidad) : "—"}${o.elegida ? " ← elegido" : ""}</td></tr>`).join("")}
    </tbody></table>
    <p class="nota">Entre los modelos que llegan gana el de mayor utilidad: calidad × nivel − coste × coste relativo − tiempo × tiempo relativo.</p>`;
}

function contenidoAyuda() {
  const varias = ultima && ultima.d.items.length > 1;
  return `
    <div class="ayuda-cab"><h2>Cómo se llama a Jev por detrás</h2><button type="button" class="cerrar" aria-label="Cerrar">×</button></div>
    <h3>1 · El recorrido</h3>
    <ol class="recorrido">
      <li><b>Navegador</b><span>Tu tarea, o cada etapa del workflow, se manda a <code>/api/clasificar</code></span></li>
      <li><b>Función de Vercel</b><span>Añade la clave de OpenRouter y las 5 preguntas fijas</span></li>
      <li><b>Jev en OpenRouter</b><span><code>/api/v1/systemone</code>: una llamada por tarea o etapa, unos 300-700 ms</span></li>
      <li><b>Respuesta tipada</b><span>Probabilidades y confianza, sin texto que parsear</span></li>
      <li><b>Política</b><span>En tu navegador: señales + prioridad → modelo y esfuerzo</span></li>
    </ol>
    ${ultima ? `
      ${varias ? `<label class="selector-llamada">Llamada que quieres ver: <select id="ayuda-item">${ultima.d.items.map((it, i) => `<option value="${i}">${esc(ultima.enviados[i].state.etapa || it.id)}</option>`).join("")}</select></label>
        <p class="nota">Se hicieron ${ultima.d.items.length} llamadas en paralelo, una por etapa. Cada una lleva el mismo caso y cambia la etapa y su objetivo.</p>` : ""}
      <div id="ayuda-detalle">${detalleLlamada(0)}</div>`
    : `<p class="nota aviso">Todavía no has clasificado nada. Lanza una clasificación y vuelve a abrir esta ayuda: verás la petición y la respuesta reales de tu llamada.</p>`}
    <h3>Probarlo tú mismo</h3>
    <pre class="json">curl https://openrouter.ai/api/v1/systemone \\
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"model": "typesafe/jev-1.13",
       "state": {"tarea": "Renombrar una variable en el login"},
       "questions": {"complejidad": {"type": "score",
         "instructions": "¿Cómo de compleja es?",
         "criteria": ["Trivial", "Baja", "Media", "Alta"]}}}'</pre>`;
}

function montarAyuda() {
  const cab = document.querySelector("header.cabecera");
  if (!cab) return;
  const boton = document.createElement("button");
  boton.type = "button"; boton.className = "boton-ayuda"; boton.textContent = "¿Cómo se llama a Jev?";
  cab.appendChild(boton);
  const dlg = document.createElement("dialog");
  dlg.className = "ayuda";
  document.body.appendChild(dlg);
  boton.addEventListener("click", () => {
    dlg.innerHTML = contenidoAyuda();
    dlg.querySelector(".cerrar").addEventListener("click", () => dlg.close());
    dlg.querySelector("#ayuda-item")?.addEventListener("change", (e) => { dlg.querySelector("#ayuda-detalle").innerHTML = detalleLlamada(+e.target.value); });
    dlg.showModal();
    dlg.scrollTop = 0;
  });
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); }); // clic fuera cierra
}
montarAyuda();
