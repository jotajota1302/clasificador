// Lee un workflow de ORCHIDIA en markdown: frontmatter con `stages:` y una
// sección `## Etapa: <id>` por etapa. Sirve igual en el navegador y en Node.
//
// -> { id, nombre, descripcion, etapas: [{ id, label, artifact, checkpoint, objetivo }] }

const MAX_OBJETIVO = 600; // caracteres por etapa que se le pasan a Jev

const sinComillas = (v) => v.trim().replace(/^["']|["']$/g, "");

export function parsearWorkflow(texto) {
  const lineas = texto.replace(/\r\n/g, "\n").split("\n");
  let fin = -1;
  if (lineas[0].trim() === "---") fin = lineas.findIndex((l, i) => i > 0 && l.trim() === "---");
  const cabecera = fin > 0 ? lineas.slice(1, fin) : [];
  const cuerpo = lineas.slice(fin + 1).join("\n");

  const campo = (k) => {
    const l = cabecera.find((x) => x.startsWith(`${k}:`));
    return l ? sinComillas(l.slice(k.length + 1)) : "";
  };

  // Etapas: lo que cuelga de `stages:` (lista de mapas con sangría)
  const etapas = [];
  const ini = cabecera.findIndex((l) => /^stages:\s*$/.test(l));
  if (ini >= 0) {
    for (const l of cabecera.slice(ini + 1)) {
      if (/^\S/.test(l)) break; // siguiente clave de primer nivel
      let m;
      if ((m = l.match(/^\s*-\s+id:\s*(.+)$/))) etapas.push({ id: sinComillas(m[1]) });
      else if (etapas.length && (m = l.match(/^\s+(label|artifact|checkpoint):\s*(.+)$/))) etapas.at(-1)[m[1]] = sinComillas(m[2]);
    }
  }

  // Objetivo de cada etapa: su sección `## Etapa: <id>`, sin plantillas ni marcadores
  const secciones = {};
  const partes = cuerpo.split(/^## +Etapa:\s*/m).slice(1);
  for (const p of partes) {
    const id = p.split("\n", 1)[0].trim();
    const texto = p.slice(id.length).split(/^## /m)[0]
      .replace(/\{\{[^}]*\}\}/g, " ")
      .replace(/\[\[[A-Z]+(:[^\]]*)?\]\]/g, " ")
      .replace(/\(\s*\)/g, "")
      .replace(/[ \t]+/g, " ")
      .replace(/^ +| +$/gm, "")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
    secciones[id] = texto.length > MAX_OBJETIVO ? texto.slice(0, MAX_OBJETIVO) + "…" : texto;
  }
  for (const e of etapas) {
    e.label ||= e.id;
    e.objetivo = secciones[e.id] || e.label;
  }

  return { id: campo("id") || "workflow", nombre: campo("name") || campo("id") || "Workflow", descripcion: campo("description"), etapas };
}
