import { mkdirSync, writeFileSync } from "node:fs";
import { resolve, join } from "node:path";
import { deflateSync } from "node:zlib";
import { sha256 } from "./files.mjs";
import { PROTOCOL } from "./contracts.js";

const uid = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const crc32 = (bytes) => {
  let c = 0xffffffff;
  for (const byte of bytes) {
    c ^= byte;
    for (let i = 0; i < 8; i++) c = (c >>> 1) ^ (c & 1 ? 0xedb88320 : 0);
  }
  return (c ^ 0xffffffff) >>> 0;
};
function chunk(type, body) {
  const kind = Buffer.from(type),
    length = Buffer.alloc(4),
    crc = Buffer.alloc(4);
  length.writeUInt32BE(body.length);
  crc.writeUInt32BE(crc32(Buffer.concat([kind, body])));
  return Buffer.concat([length, kind, body, crc]);
}
export function syntheticPng(index) {
  const width = 64,
    height = 64,
    ihdr = Buffer.alloc(13),
    rows = Buffer.alloc(height * (1 + width * 3));
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 2;
  for (let y = 0; y < height; y++)
    for (let x = 0; x < width; x++) {
      const p = y * (1 + width * 3) + 1 + x * 3;
      rows[p] = (x * 19 + index * 31) % 256;
      rows[p + 1] = (y * 23 + index * 47) % 256;
      rows[p + 2] = (x * y + index * 17) % 256;
    }
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(rows)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

export function createTemplate(
  output,
  { commit = "0".repeat(40), month = "2026-10" } = {},
) {
  const directory = resolve(output);
  mkdirSync(directory, { mode: 0o700 });
  mkdirSync(join(directory, "photos"), { mode: 0o700 });
  const actorsFile = join(directory, "actors.json");
  const actors = Array.from({ length: 10 }, (_, i) => ({
    id: `load_citizen_${i + 1}`,
    userId: uid(i + 1),
    role: "citizen",
    verified: true,
    accessToken: "REPLACE.PRIVATE_ACCESS_TOKEN.REPLACE",
  }));
  const payloads = Array.from({ length: 50 }, (_, i) => {
    const bytes = syntheticPng(i + 1),
      path = join(directory, "photos", `create_${i + 1}.png`);
    writeFileSync(path, bytes, { mode: 0o600, flag: "wx" });
    return {
      caseId: `create_${i + 1}`,
      actorId: actors[Math.floor(i / 5)].id,
      category: uid(100),
      description: `S15 sintético, incidencia de carga ${i + 1}.`,
      lat: -25.3,
      lng: -57.6,
      photo: { path, mime: "image/png", sha256: sha256(bytes) },
    };
  });
  const manifest = {
    schemaVersion: 1,
    protocol: PROTOCOL,
    purpose: "harness-smoke",
    runId: "REPLACE_unique_window_id",
    baseUrl: "http://127.0.0.1:8000",
    datasetId: "REPLACE_WITH_ISOLATED_DATASET",
    datasetSha256: "0".repeat(64),
    referenceMonth: month,
    actorsFile,
    isolation: {
      synthetic: true,
      externalProviders: "substituted-or-disabled",
      workers: "stopped",
      beat: "stopped",
    },
    environment: {
      commit,
      debug: true,
      applicationServer: "django-runserver-harness-only",
      mapCacheTtlSeconds: 60,
      rankingCacheTtlSeconds: 60,
    },
    create: { actorIds: actors.map((a) => a.id), payloads },
    map: {
      requests: 90,
      durationSeconds: 60,
      precheckRequests: 0,
      warmupRequests: 0,
      verificationRequests: 6,
      cacheState: "unverified",
      cacheEvidence:
        "No hit/miss observado; declarar evidencia externa antes de etiquetar cold/warm",
      bbox: [-57.7, -25.4, -57.4, -25.2],
      stateFilter: "all",
      page: 1,
      expected: {
        count: 1,
        ids: [uid(200)],
        detailId: uid(200),
        detail: { id: uid(200), status: "RESUELTO", confirmation_count: 0 },
        categories: [{ id: uid(100), slug: "REPLACE_catalog" }],
      },
    },
    ranking: {
      requests: 90,
      durationSeconds: 60,
      precheckRequests: 0,
      warmupRequests: 0,
      verificationRequests: 2,
      cacheState: "unverified",
      cacheEvidence: "No hit/miss observado",
      expected: { period: month, entries: [] },
    },
  };
  writeFileSync(
    actorsFile,
    `${JSON.stringify({ schemaVersion: 1, actors }, null, 2)}\n`,
    { mode: 0o600, flag: "wx" },
  );
  writeFileSync(
    join(directory, "manifest.json"),
    `${JSON.stringify(manifest, null, 2)}\n`,
    { mode: 0o600, flag: "wx" },
  );
  return { directory, manifest, actors: { schemaVersion: 1, actors } };
}
