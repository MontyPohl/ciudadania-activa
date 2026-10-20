#!/usr/bin/env node
import {
  readFileSync,
  writeFileSync,
  mkdirSync,
  realpathSync,
  existsSync,
} from "node:fs";
import { resolve, relative, join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import {
  preflight,
  readPrivateJson,
  readPrivateJsonDocument,
  privatePath,
  repoRoot,
  sha256,
} from "./lib/files.mjs";
import { createTemplate } from "./lib/template.mjs";
import { parseSeries, buildReport } from "./lib/report.js";

process.umask(0o077);
const args = process.argv.slice(2);
const command = args.shift();
const option = (name, fallback) => {
  const i = args.indexOf(name);
  if (i < 0) return fallback;
  const value = args[i + 1];
  if (!value || value.startsWith("--"))
    throw new Error(`Falta valor de ${name}`);
  return value;
};
const positional = args.filter(
  (x, i) => !x.startsWith("--") && (i === 0 || !args[i - 1].startsWith("--")),
);
const write = (path, content) =>
  writeFileSync(path, `${JSON.stringify(content, null, 2)}\n`, {
    mode: 0o600,
    flag: "wx",
  });

function privateOutputDirectory(output) {
  if (!output || !output.startsWith("/"))
    throw new Error("--out requiere path absoluto privado");
  const parent = realpathSync(dirname(output));
  if (!relative(repoRoot, parent).startsWith("../"))
    throw new Error("La salida debe quedar fuera del repositorio");
  mkdirSync(output, { mode: 0o700 });
  return output;
}

try {
  if (command === "template") {
    const output = option("--out");
    if (!output || !output.startsWith("/") || existsSync(output))
      throw new Error(
        "--out requiere directorio absoluto nuevo fuera del repositorio",
      );
    const parent = realpathSync(dirname(output));
    if (!relative(repoRoot, parent).startsWith("../"))
      throw new Error("Template privado fuera del repositorio");
    const result = createTemplate(output);
    console.log(
      JSON.stringify({
        directory: result.directory,
        state:
          "TEMPLATE_ONLY: replace actors/tokens/catalog/dataset/expectations before preflight",
      }),
    );
  } else if (command === "preflight") {
    console.log(
      JSON.stringify(
        preflight(positional[0], { python: option("--python", "python3") })
          .publicResult,
        null,
        2,
      ),
    );
  } else if (command === "run") {
    const manifestPath = positional[0],
      profile = positional[1];
    if (!["create", "map", "ranking", "redeem", "negative"].includes(profile))
      throw new Error(
        "run MANIFEST create|map|ranking|redeem|negative --out DIR",
      );
    const checked = preflight(manifestPath, {
      python: option("--python", "python3"),
    });
    const section =
      profile === "negative"
        ? checked.manifest.negatives
        : checked.manifest[profile];
    if (!section) throw new Error("Perfil ausente del manifiesto");
    const output = privateOutputDirectory(option("--out"));
    write(join(output, "preflight.json"), checked.publicResult);
    writeFileSync(
      join(output, "manifest.snapshot.json"),
      checked.manifestBytes,
      { mode: 0o600, flag: "wx" },
    );
    const executable = option("--k6", "k6");
    const env = {
      PATH: process.env.PATH || "/usr/bin:/bin",
      TZ: "America/Asuncion",
      K6_NO_USAGE_REPORT: "true",
      S15_MANIFEST: realpathSync(manifestPath),
      S15_MANIFEST_SHA256: checked.publicResult.manifestSha256,
      S15_PROFILE: profile,
      S15_SUMMARY: join(output, "summary.json"),
    };
    const version = spawnSync(executable, ["version"], {
      encoding: "utf8",
      env,
    });
    if (version.status !== 0)
      throw new Error(
        "k6 no está disponible; instalar/ejecutar herramienta local por separado",
      );
    const k6Version = version.stdout.trim();
    write(join(output, "execution.json"), {
      k6Version,
      bindingVersion: 1,
      manifestSha256: checked.publicResult.manifestSha256,
      runId: checked.manifest.runId,
      datasetSha256: checked.manifest.datasetSha256,
      purpose: checked.manifest.purpose,
      profile,
      scope: "local synthetic; no remote requests authorized",
    });
    write(join(output, "k6-config.json"), {});
    const script = fileURLToPath(
      new URL(`./k6/${profile}.js`, import.meta.url),
    );
    const execution = spawnSync(
      executable,
      [
        "run",
        "--config",
        join(output, "k6-config.json"),
        "--out",
        `json=${join(output, "series.jsonl")}`,
        "--no-usage-report",
        script,
      ],
      { env, stdio: "inherit" },
    );
    let report;
    if (
      sha256(readFileSync(privatePath(manifestPath, "manifiesto"))) !==
      checked.publicResult.manifestSha256
    )
      throw new Error(
        "El manifiesto cambió durante la ejecución; conservar snapshot y serie sin promover",
      );
    if (existsSync(join(output, "series.jsonl"))) {
      report = buildReport(
        checked.manifest,
        parseSeries(readFileSync(join(output, "series.jsonl"), "utf8")),
        { profile, k6Version, manifestBytes: checked.manifestBytes },
      );
      report.executionExitCode = execution.status;
      write(join(output, "report.json"), report);
    }
    console.log(
      JSON.stringify({
        output,
        executionExitCode: execution.status,
        contractPass: report?.contractPass ?? false,
        manifestSha256: report?.manifestSha256 ?? null,
        promotable: report?.promotable ?? false,
        persistence: report?.creation?.status ?? "not assessed",
        scope: "local harness; no production verdict",
      }),
    );
    process.exitCode =
      execution.status === 0 &&
      report?.contractPass &&
      report.manifestBinding.status === "BOUND"
        ? 0
        : 1;
  } else if (command === "report") {
    const manifestDocument = readPrivateJsonDocument(
      positional[0],
      "manifiesto",
    );
    const manifest = manifestDocument.value;
    const series = readFileSync(privatePath(positional[1], "series"), "utf8");
    const sqlPath = option("--sql");
    const report = buildReport(manifest, parseSeries(series), {
      profile: option("--profile"),
      sql: sqlPath ? readPrivateJson(sqlPath, "SQL export") : undefined,
      k6Version: option("--k6-version", "unrecorded"),
      manifestBytes: manifestDocument.bytes,
    });
    const output = option("--out");
    if (output) {
      if (
        !output.startsWith("/") ||
        !relative(repoRoot, realpathSync(dirname(output))).startsWith("../")
      )
        throw new Error("Reporte privado fuera del repositorio");
      write(output, report);
    } else console.log(JSON.stringify(report, null, 2));
    process.exitCode =
      report.contractPass &&
      report.promotable &&
      report.observedLatencyPass !== false &&
      (report.profile !== "create" || report.throughputPass)
        ? 0
        : 1;
  } else {
    throw new Error(
      "Uso: node tools/performance/cli.mjs template --out DIR | preflight MANIFEST [--python PYTHON] | run MANIFEST PERFIL --out DIR [--k6 BIN] | report MANIFEST SERIES --profile PERFIL [--sql EXPORT] [--out FILE]",
    );
  }
} catch (error) {
  // Never print object dumps, process environments, headers, tokens, or response bodies.
  console.error(
    `S15: ${error.code ? "Error local de archivo/proceso" : error.message}`,
  );
  process.exitCode = 1;
}
