// Congela el catálogo de modelos de Axet en catalogo-axet.js para la web.
//
// La fuente es ORCHIDIA en local (GET /api/settings/axet-models?family=all), que ya
// aplica sus filtros: solo públicos, sin vetados ni familias retiradas. Pide sesión
// Axet activa en ORCHIDIA; sin ella el catálogo llega vacío y no se sobrescribe nada.
//
//   node sincronizar-axet.mjs [url-base-de-orchidia]
import { writeFileSync } from "node:fs";

const base = process.argv[2] || "http://127.0.0.1:8000";
const r = await fetch(`${base}/api/settings/axet-models?family=all`);
if (!r.ok) throw new Error(`ORCHIDIA respondió ${r.status}`);
const { models = [] } = await r.json();
if (!models.length) throw new Error("Catálogo vacío: ¿hay sesión Axet activa en ORCHIDIA?");

const modelos = models
  .filter((m) => m.inputPrice != null && m.outputPrice != null)
  .map((m) => ({
    id: m.id, familia: m.family, entrada: m.inputPrice, salida: m.outputPrice,
    maxTokens: m.maxTokens ?? null, porDefecto: !!m.isDefault,
    incompatibleCon: Object.keys(m.incompatible_con || {}),
  }))
  .sort((a, b) => a.salida - b.salida);

const fecha = new Date().toISOString().slice(0, 10);
writeFileSync(new URL("./catalogo-axet.js", import.meta.url),
  `// Generado por sincronizar-axet.mjs el ${fecha} desde ORCHIDIA. No editar a mano.\n` +
  `export const FECHA_AXET = ${JSON.stringify(fecha)};\n` +
  `export const MODELOS_AXET = ${JSON.stringify(modelos, null, 2)};\n`);
console.log(`${modelos.length} modelos de Axet → catalogo-axet.js`);
