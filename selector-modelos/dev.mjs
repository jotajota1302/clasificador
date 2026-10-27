// Servidor local para probar la POC sin Vercel: sirve los estáticos y monta
// api/clasificar.js igual que lo haría Vercel. Lee la clave del ../.env del proyecto.
// Uso:  node dev.mjs   -> http://127.0.0.1:8770
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { existsSync, readFileSync } from "node:fs";
import { extname, join } from "node:path";
import { fileURLToPath } from "node:url";
import handler from "./api/clasificar.js";

const raiz = fileURLToPath(new URL(".", import.meta.url));
const env = join(raiz, "..", ".env");
if (existsSync(env)) for (const l of readFileSync(env, "utf8").split(/\r?\n/)) {
  const i = l.indexOf("=");
  if (i > 0 && !l.trimStart().startsWith("#")) process.env[l.slice(0, i).trim()] ??= l.slice(i + 1).trim();
}
const TIPOS = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".md": "text/markdown; charset=utf-8" };

createServer(async (req, res) => {
  const ruta = req.url.split("?")[0];
  if (ruta === "/api/clasificar") {
    let cuerpo = ""; for await (const c of req) cuerpo += c;
    req.body = cuerpo ? JSON.parse(cuerpo) : {};
    res.status = (c) => { res.statusCode = c; return res; };
    res.json = (o) => { res.setHeader("Content-Type", "application/json"); res.end(JSON.stringify(o)); };
    return handler(req, res);
  }
  const fichero = join(raiz, ruta === "/" ? "index.html" : ruta);
  if (!fichero.startsWith(raiz) || fichero.includes(".env")) { res.statusCode = 403; return res.end(); }
  try { res.setHeader("Content-Type", TIPOS[extname(fichero)] || "application/octet-stream"); res.end(await readFile(fichero)); }
  catch { res.statusCode = 404; res.end("no existe"); }
}).listen(8770, "127.0.0.1", () => console.log("http://127.0.0.1:8770"));
