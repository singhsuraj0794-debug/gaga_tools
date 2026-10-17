// storage-shim.mjs - a minimal stand-in for Supabase Storage, plus the proxy
// that puts it and PostgREST behind ONE origin.
//
// supabase-js (and the raw REST calls in the app) expect a single base URL that
// serves both /rest/v1 and /storage/v1. PostgREST owns /rest/v1, so this process
// serves /storage/v1 from a local folder and forwards everything else to
// PostgREST. That keeps the client code unchanged apart from the base URL.
//
// Storage surface actually used by this repo (from the code inventory):
//   PUT  /storage/v1/object/<bucket>/<key>            upload (raw REST + supabase-py)
//   POST /storage/v1/object/<bucket>/<key>            upload (supabase-js .upload())
//   GET  /storage/v1/object/public/<bucket>/<key>     public download (recordings,
//                                                     screenshots, gajab_session.json)
//   GET  /storage/v1/object/<bucket>/<key>            authenticated download
//   POST /storage/v1/object/list/<bucket>             list  {prefix, limit, offset}
//   DELETE /storage/v1/object/<bucket>                remove {prefixes: [...]}
//   GET  /storage/v1/object/<bucket>/<key>            (supabase-js .download())
//
// Run:  node storage-shim.mjs
// Env:  STORAGE_ROOT (default C:\gajab\storage), STORAGE_PORT (3001),
//       POSTGREST_URL (http://127.0.0.1:3000), SHIM_KEYS (comma-separated JWTs
//       allowed to write/delete; empty = allow anything, local-only box)
import http from "node:http";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";

const ROOT = process.env.STORAGE_ROOT || "C:\\gajab\\storage";
const PORT = Number(process.env.STORAGE_PORT || 3001);
const POSTGREST = process.env.POSTGREST_URL || "http://127.0.0.1:3000";
const ALLOWED_KEYS = (process.env.SHIM_KEYS || "").split(",").map((s) => s.trim()).filter(Boolean);

const MIME = {
  ".webm": "video/webm", ".png": "image/png", ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg", ".webp": "image/webp", ".json": "application/json",
  ".txt": "text/plain", ".avif": "image/avif", ".gif": "image/gif",
};

function cors(res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS, HEAD");
  res.setHeader("Access-Control-Allow-Headers",
    "apikey, Authorization, Content-Type, Range, Prefer, X-Client-Info, ngrok-skip-browser-warning, Accept, Accept-Profile, Content-Profile");
  res.setHeader("Access-Control-Expose-Headers", "Content-Range, Content-Length, Range");
}

function json(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) });
  res.end(body);
}

function presentedKey(req) {
  const auth = req.headers["authorization"] || "";
  return (auth.startsWith("Bearer ") ? auth.slice(7) : "") || req.headers["apikey"] || "";
}

function canWrite(req) {
  if (ALLOWED_KEYS.length === 0) return true; // local-only box
  const k = presentedKey(req);
  return ALLOWED_KEYS.includes(k);
}

/** Reject a key that would escape the storage root (path traversal). */
function safePath(bucket, key) {
  const full = path.resolve(ROOT, bucket, key.replace(/^\/+/, ""));
  const base = path.resolve(ROOT, bucket);
  if (full !== base && !full.startsWith(base + path.sep)) return null;
  return full;
}

async function readBody(req) {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  return Buffer.concat(chunks);
}

async function statObject(bucket, key) {
  const full = safePath(bucket, key);
  if (!full) return null;
  try {
    const st = await fsp.stat(full);
    if (!st.isFile()) return null;
    return { full, st };
  } catch {
    return null;
  }
}

function objectMeta(name, st) {
  return {
    name,
    id: null,
    updated_at: st.mtime.toISOString(),
    created_at: st.birthtime.toISOString(),
    last_accessed_at: st.atime.toISOString(),
    metadata: {
      eTag: null,
      size: st.size,
      mimetype: MIME[path.extname(name).toLowerCase()] || "application/octet-stream",
      cacheControl: "max-age=3600",
      lastModified: st.mtime.toISOString(),
      contentLength: st.size,
      httpStatusCode: 200,
    },
  };
}

// ---- storage handlers -------------------------------------------------------

async function handleUpload(req, res, bucket, key) {
  if (!canWrite(req)) return json(res, 403, { statusCode: "403", error: "Unauthorized", message: "Invalid key" });
  const full = safePath(bucket, key);
  if (!full) return json(res, 400, { statusCode: "400", error: "Bad Request", message: "Invalid key" });
  const body = await readBody(req);
  await fsp.mkdir(path.dirname(full), { recursive: true });
  await fsp.writeFile(full, body);
  const name = path.basename(key);
  json(res, 200, { Key: `${bucket}/${key}`, Id: null, name, size: body.length });
}

async function handleDownload(req, res, bucket, key) {
  const hit = await statObject(bucket, key);
  if (!hit) return json(res, 404, { statusCode: "404", error: "Not Found", message: "Object not found" });
  res.writeHead(200, {
    "Content-Type": MIME[path.extname(key).toLowerCase()] || "application/octet-stream",
    "Content-Length": hit.st.size,
    "Last-Modified": hit.st.mtime.toUTCString(),
    "Cache-Control": "public, max-age=3600",
  });
  fs.createReadStream(hit.full).pipe(res);
}

async function handleList(req, res, bucket) {
  const body = await readBody(req);
  let opts = {};
  try { opts = body.length ? JSON.parse(body.toString("utf8")) : {}; } catch { opts = {}; }
  const prefix = String(opts.prefix || "").replace(/^\/+/, "");
  const limit = Number(opts.limit ?? 100);
  const offset = Number(opts.offset ?? 0);
  const dir = path.resolve(ROOT, bucket, prefix);
  const base = path.resolve(ROOT, bucket);
  if (dir !== base && !dir.startsWith(base + path.sep)) return json(res, 400, { message: "Invalid prefix" });

  let entries = [];
  try {
    entries = await fsp.readdir(dir, { withFileTypes: true });
  } catch {
    return json(res, 200, []);
  }
  const out = [];
  for (const e of entries) {
    if (!e.isFile()) continue; // Supabase list() is non-recursive: files only
    const st = await fsp.stat(path.join(dir, e.name));
    out.push(objectMeta(e.name, st));
  }
  out.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  json(res, 200, out.slice(offset, offset + limit));
}

async function handleRemove(req, res, bucket) {
  if (!canWrite(req)) return json(res, 403, { statusCode: "403", error: "Unauthorized", message: "Invalid key" });
  const body = await readBody(req);
  let prefixes = [];
  try { prefixes = JSON.parse(body.toString("utf8")).prefixes || []; } catch { prefixes = []; }
  const removed = [];
  for (const p of prefixes) {
    const full = safePath(bucket, String(p));
    if (!full) continue;
    try {
      await fsp.unlink(full);
      removed.push({ name: path.basename(String(p)) });
    } catch { /* already gone */ }
  }
  json(res, 200, removed);
}

// ---- proxy to PostgREST -----------------------------------------------------

function proxy(req, res) {
  // Strip Supabase's /rest/v1 prefix: PostgREST serves the API at its ROOT
  // (/products), and /rest/v1 is the Kong routing convention in front of it.
  // Forwarding it verbatim gives PGRST125 "Invalid path specified in request URL".
  let pathname = new URL(req.url, "http://localhost").pathname;
  if (pathname === "/rest/v1" || pathname === "/rest/v1/") pathname = "/";
  else if (pathname.startsWith("/rest/v1/")) pathname = pathname.slice("/rest/v1".length);
  const target = new URL(pathname + (req.url.includes("?") ? req.url.slice(req.url.indexOf("?")) : ""), POSTGREST);
  const headers = { ...req.headers, host: target.host };
  const preq = http.request(
    { hostname: target.hostname, port: target.port, path: target.pathname + target.search, method: req.method, headers },
    (pres) => {
      cors(res);
      res.writeHead(pres.statusCode || 502, pres.headers);
      pres.pipe(res);
    },
  );
  preq.on("error", (e) => json(res, 502, { message: "PostgREST unreachable: " + e.message }));
  req.pipe(preq);
}

// ---- router -----------------------------------------------------------------

const server = http.createServer(async (req, res) => {
  cors(res);
  if (req.method === "OPTIONS") { res.writeHead(204); return res.end(); }
  const url = new URL(req.url, "http://localhost");
  const p = url.pathname;

  try {
    if (p.startsWith("/storage/v1/")) {
      const rest = p.slice("/storage/v1/".length); // e.g. object/public/monitoring/x.png
      if (rest.startsWith("object/list/")) {
        return await handleList(req, res, rest.slice("object/list/".length));
      }
      if (rest === "object/" || rest.startsWith("object/")) {
        let tail = rest.slice("object/".length); // [public/]<bucket>/<key...>
        if (tail.startsWith("public/")) tail = tail.slice("public/".length);
        const slash = tail.indexOf("/");
        if (slash < 0) {
          // /storage/v1/object/<bucket>  -> DELETE {prefixes}
          if (req.method === "DELETE") return await handleRemove(req, res, tail);
          return json(res, 400, { message: "bucket and key required" });
        }
        const bucket = tail.slice(0, slash);
        const key = decodeURIComponent(tail.slice(slash + 1));
        if (req.method === "PUT" || req.method === "POST") return await handleUpload(req, res, bucket, key);
        if (req.method === "GET" || req.method === "HEAD") return await handleDownload(req, res, bucket, key);
        if (req.method === "DELETE") return await handleRemove(req, res, bucket);
        return json(res, 405, { message: "method not allowed" });
      }
      // health/info endpoints supabase-js may probe
      if (rest.startsWith("bucket")) return json(res, 200, []);
      return json(res, 404, { message: "unsupported storage route: " + rest });
    }
    // everything else (notably /rest/v1/*) goes to PostgREST
    return proxy(req, res);
  } catch (e) {
    return json(res, 500, { message: String(e && e.message || e) });
  }
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`[storage-shim] listening on http://127.0.0.1:${PORT}`);
  console.log(`[storage-shim] storage root: ${ROOT}`);
  console.log(`[storage-shim] /rest/v1 -> ${POSTGREST}`);
  console.log(`[storage-shim] write auth: ${ALLOWED_KEYS.length ? ALLOWED_KEYS.length + " key(s)" : "open (local box)"}`);
});
