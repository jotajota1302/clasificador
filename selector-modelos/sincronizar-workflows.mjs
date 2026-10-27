// Lee los workflows de fábrica de ORCHIDIA y genera ./workflows/catalogo.json,
// que es lo que carga la web (etapas + instrucciones de cada etapa ya extraídas).
// Copia también los .md originales a ./workflows como referencia.
// Uso:  node sincronizar-workflows.mjs [ruta-a-los-workflows]
//       (por defecto C:/dev/ORCHIDIA/backend/app/workflows)
import { copyFileSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { parsearWorkflow } from "./workflow-md.js";

const origen = process.argv[2] || "C:/dev/ORCHIDIA/backend/app/workflows";
const destino = fileURLToPath(new URL("./workflows/", import.meta.url));
mkdirSync(destino, { recursive: true });

const catalogo = [];
for (const f of readdirSync(origen).filter((f) => f.endsWith(".md")).sort()) {
  const wf = parsearWorkflow(readFileSync(join(origen, f), "utf8"));
  if (!wf.etapas.length) continue;
  copyFileSync(join(origen, f), join(destino, f));
  catalogo.push({ fichero: f, ...wf });
  console.log(`${f}: ${wf.etapas.length} etapas`);
}
writeFileSync(join(destino, "catalogo.json"), JSON.stringify(catalogo) + "\n");
console.log(`${catalogo.length} workflows en ${destino}catalogo.json`);
