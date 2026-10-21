import { readFileSync, statSync, realpathSync } from "node:fs";
import { resolve, relative, extname } from "node:path";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { validateManifest, canonicalBbox } from "./contracts.js";

export const repoRoot = resolve(
  fileURLToPath(new URL("../../..", import.meta.url)),
);
export const sha256 = (bytes) =>
  createHash("sha256").update(bytes).digest("hex");

export function privatePath(path, label = "artefacto") {
  if (typeof path !== "string" || !path.startsWith("/"))
    throw new Error(`${label}: path absoluto requerido`);
  const real = realpathSync(path);
  const rel = relative(repoRoot, real);
  if (!rel.startsWith("../"))
    throw new Error(`${label}: debe quedar fuera del repositorio`);
  const stat = statSync(real);
  if (!stat.isFile() || (stat.mode & 0o077) !== 0)
    throw new Error(
      `${label}: requiere archivo privado sin permisos de grupo/otros (0600)`,
    );
  return real;
}

export function readPrivateJson(path, label) {
  return readPrivateJsonDocument(path, label).value;
}

export function readPrivateJsonDocument(path, label) {
  const real = privatePath(path, label);
  const bytes = readFileSync(real);
  try {
    return {
      value: JSON.parse(bytes.toString("utf8")),
      bytes,
      sha256: sha256(bytes),
    };
  } catch {
    throw new Error(`${label}: JSON inválido`);
  }
}

export function validateTokenClaims(actor, nowSeconds = Date.now() / 1000) {
  let claims;
  try {
    claims = JSON.parse(
      Buffer.from(actor.accessToken.split(".")[1], "base64url").toString(
        "utf8",
      ),
    );
  } catch {
    throw new Error("JWT ilegible; regenerar sesiones sintéticas");
  }
  if (!Number.isFinite(claims.exp) || claims.exp <= nowSeconds + 120)
    throw new Error(
      "JWT vencido o sin 120s de vigencia; regenerar fuera de medición",
    );
  if (
    String(claims.user_id ?? claims.sub) !== actor.userId ||
    (claims.token_type && claims.token_type !== "access")
  )
    throw new Error("Claims de actor/accessToken no coinciden");
  return { expiresAt: claims.exp, signatureVerified: false };
}

// Pillow decodes locally in a separate process. No Django settings, uploads, or providers.
// This verifies full JPEG/PNG decoding and the size after the product's normalization.
const DECODE = `import hashlib, io, json, sys
sys.path.insert(0, sys.argv[1])
from apps.reports_app.services.photos import prepare_report_photo
rows = json.load(sys.stdin)
result = []
for row in rows:
    try:
        raw = open(row['path'], 'rb').read()
        photo = io.BytesIO(raw)
        photo.name = row['path']
        photo.content_type = row['mime']
        normalized = prepare_report_photo(photo).content
        result.append({'sha256': hashlib.sha256(normalized).hexdigest(), 'bytes': len(normalized)})
    except Exception:
        print('Imagen no decodificable o normalización fuera de límite', file=sys.stderr)
        sys.exit(2)
print(json.dumps(result))
`;

export function preflight(
  manifestPath,
  { python = "python3", nowSeconds } = {},
) {
  const manifestDocument = readPrivateJsonDocument(manifestPath, "manifiesto");
  const manifest = manifestDocument.value;
  const actorDocument = readPrivateJson(manifest.actorsFile, "actores");
  const validation = validateManifest(manifest, actorDocument);
  Object.values(validation.actors).forEach((a) =>
    validateTokenClaims(a, nowSeconds),
  );
  const payloads = manifest.create?.payloads || [];
  for (const p of payloads) {
    const path = privatePath(p.photo.path, "foto");
    if (statSync(path).size > 5 * 1024 * 1024)
      throw new Error("Foto: tamaño fuera de límite");
    const bytes = readFileSync(path);
    if (
      !bytes.length ||
      bytes.length > 5 * 1024 * 1024 ||
      sha256(bytes) !== p.photo.sha256
    )
      throw new Error("Foto: tamaño/hash inválido");
    const png = bytes
      .subarray(0, 8)
      .equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
    const jpeg = bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255;
    const ext = extname(path).toLowerCase();
    if (!(
      (png && p.photo.mime === "image/png" && ext === ".png") ||
      (jpeg && p.photo.mime === "image/jpeg" && [".jpg", ".jpeg"].includes(ext))
    ))
      throw new Error("Foto: MIME/extensión/firma no coinciden");
  }
  let normalized = [];
  if (payloads.length) {
    const decoded = spawnSync(
      python,
      ["-c", DECODE, resolve(repoRoot, "backend")],
      {
        input: JSON.stringify(
          payloads.map((p) => ({ path: p.photo.path, mime: p.photo.mime })),
        ),
        encoding: "utf8",
        maxBuffer: 1024 * 1024,
      },
    );
    if (decoded.status !== 0)
      throw new Error(
        "Verificación local de imágenes falló; requiere Python con Pillow e imágenes válidas",
      );
    try {
      normalized = JSON.parse(decoded.stdout);
    } catch {
      throw new Error("Verificador de imágenes inválido");
    }
    if (new Set(normalized.map((p) => p.sha256)).size !== payloads.length)
      throw new Error("Fotos coinciden después de normalizar; variar píxeles");
  }
  return {
    manifest,
    manifestBytes: manifestDocument.bytes,
    actors: actorDocument,
    publicResult: {
      protocol: manifest.protocol,
      runId: manifest.runId,
      purpose: manifest.purpose,
      target: manifest.baseUrl,
      datasetId: manifest.datasetId,
      datasetSha256: manifest.datasetSha256,
      manifestSha256: manifestDocument.sha256,
      bindingVersion: 1,
      actorCount: actorDocument.actors.length,
      payloadCount: payloads.length,
      budgets: validation.budgets,
      mapBbox: manifest.map
        ? {
            input: manifest.map.bbox,
            canonical: canonicalBbox(manifest.map.bbox),
          }
        : null,
      offlineOnly: true,
      signatureVerified: false,
      httpAuthVerified: false,
      databaseVerified: false,
      cacheVerified: false,
      providersVerified: false,
      normalizedPhotosChecked: normalized.length,
    },
  };
}
