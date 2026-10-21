import { createHash } from "node:crypto";
import { lstatSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { chromium } from "@playwright/test";
import { privateS15Path } from "../e2e-s15-fullstack/privatePaths.ts";

const WINDOW_MS = 8000;
const BASE = "http://127.0.0.1:3115";
const API = "http://127.0.0.1:58115";
const STORAGE = "http://127.0.0.1:18515";
const routes = ["/mapa", "/ranking", "/transparencia"];
const profiles = [
  { id: "desktop", width: 1280, height: 800, latency: 0, download: -1, upload: -1, cpu: 1 },
  { id: "mobile", width: 390, height: 844, latency: 150, download: 200000, upload: 75000, cpu: 4 },
];
const scriptPath = fileURLToPath(import.meta.url);
const frontend = resolve(dirname(scriptPath), "..");
const sha256 = (content) => createHash("sha256").update(content).digest("hex");
const writeJson = (path, value) =>
  writeFileSync(path, JSON.stringify(value, null, 2), { mode: 0o600, flag: "wx" });

function readPrivateJson(value) {
  const path = privateS15Path(value);
  if ((lstatSync(path).mode & 0o077) !== 0 || !lstatSync(path).isFile()) {
    throw new Error("La metadata de entrada debe ser un archivo privado regular.");
  }
  const bytes = readFileSync(path);
  try {
    return { value: JSON.parse(bytes.toString("utf8")), sha256: sha256(bytes) };
  } catch {
    throw new Error("No se pudo interpretar la metadata privada S15.");
  }
}

function observeWindow({ windowMs }) {
  const state = { lcp: [], shifts: [], unsupported: [] };
  window.__s15Perf = state;
  for (const [type, collect] of [
    [
      "largest-contentful-paint",
      (entry) =>
        state.lcp.push({
          startTime: entry.startTime,
          size: entry.size,
          elementTag: entry.element?.tagName ?? null,
        }),
    ],
    [
      "layout-shift",
      (entry) =>
        state.shifts.push({
          startTime: entry.startTime,
          value: entry.value,
          hadRecentInput: entry.hadRecentInput,
        }),
    ],
  ]) {
    try {
      new PerformanceObserver((entries) => {
        for (const entry of entries.getEntries()) if (entry.startTime <= windowMs) collect(entry);
      }).observe({ type, buffered: true });
    } catch {
      state.unsupported.push(type);
    }
  }
}

async function visibleObservation(page, locator, remaining) {
  try {
    await locator.waitFor({ state: "visible", timeout: remaining });
    const ms = await page.evaluate(() => performance.now());
    return ms <= WINDOW_MS ? ms : null;
  } catch {
    return null;
  }
}

function readiness(page, route) {
  if (route === "/mapa") return page.getByRole("region", { name: /Reportes visibles/ });
  if (route === "/ranking") return page.getByRole("list", { name: "Ranking mensual", exact: true });
  return page.getByRole("region", { name: "Totales del período", exact: true });
}

async function sample(browser, route, profile, repetition) {
  const context = await browser.newContext({
    viewport: { width: profile.width, height: profile.height },
    deviceScaleFactor: 1,
    isMobile: profile.id === "mobile",
    hasTouch: profile.id === "mobile",
    locale: "es-PY",
    timezoneId: "America/Asuncion",
    serviceWorkers: "block",
  });
  const page = await context.newPage();
  const client = await context.newCDPSession(page);
  const issues = [];
  const network = [];
  const requests = new Map();
  let started = 0;
  const allowed = new Set([BASE, API, STORAGE]);
  page.on("pageerror", () => issues.push("uncaught-page-error"));
  page.on("request", (request) => {
    const url = new URL(request.url());
    const row = {
      origin: allowed.has(url.origin) ? url.origin : "excluded-external",
      path: url.pathname,
      method: request.method(),
      type: request.resourceType(),
      status: null,
    };
    requests.set(request, row);
    network.push(row);
  });
  page.on("response", (response) => {
    const row = requests.get(response.request());
    if (!row) return;
    row.status = response.status();
    row.cacheControl = response.headers()["cache-control"] ?? null;
    const expectedAnonymous =
      row.origin === BASE && row.path === "/api/auth/refresh" && row.status === 401;
    if (row.status >= 400 && !expectedAnonymous) issues.push(`http-${row.status}:${row.path}`);
  });
  await context.route("**/*", async (routeRequest) => {
    const request = routeRequest.request();
    const url = new URL(request.url());
    const anonymousRefresh =
      url.origin === BASE && url.pathname === "/api/auth/refresh" && request.method() === "POST";
    if (!allowed.has(url.origin)) return routeRequest.abort("blockedbyclient");
    if (!["GET", "HEAD", "OPTIONS"].includes(request.method()) && !anonymousRefresh) {
      issues.push("unexpected-mutation");
      return routeRequest.abort("blockedbyclient");
    }
    await routeRequest.continue();
  });
  await context.routeWebSocket(/.*/, (socket) => socket.close());
  try {
    await client.send("Network.enable");
    await client.send("Network.setCacheDisabled", { cacheDisabled: true });
    await client.send("Network.emulateNetworkConditions", {
      offline: false,
      latency: profile.latency,
      downloadThroughput: profile.download,
      uploadThroughput: profile.upload,
      connectionType: profile.id === "mobile" ? "cellular3g" : "ethernet",
    });
    await client.send("Emulation.setCPUThrottlingRate", { rate: profile.cpu });
    await context.addInitScript(observeWindow, { windowMs: WINDOW_MS });
    started = Date.now();
    const response = await page.goto(BASE + route, { waitUntil: "commit", timeout: 30000 });
    const elapsed = await page.evaluate(() => performance.now());
    const remaining = Math.max(1, WINDOW_MS - elapsed);
    const observations = Promise.all([
      visibleObservation(page, page.getByRole("main").first(), remaining),
      visibleObservation(page, page.getByRole("heading", { level: 1 }).first(), remaining),
      visibleObservation(page, readiness(page, route), remaining),
    ]);
    await page.waitForTimeout(remaining);
    const [mainVisibleObservedMs, headingVisibleObservedMs, dataReadyDomObservedMs] =
      await observations;
    const timing = await page.evaluate(
      ({ windowMs }) => {
        const nav = performance.getEntriesByType("navigation")[0];
        const bounded = (value) => (value > 0 && value <= windowMs ? value : null);
        const state = window.__s15Perf;
        const fcp = performance.getEntriesByName("first-contentful-paint")[0]?.startTime;
        return {
          snapshotAtMs: performance.now(),
          navigation: {
            ttfbMs: bounded(nav?.responseStart),
            responseEndMs: bounded(nav?.responseEnd),
            domContentLoadedMs: bounded(nav?.domContentLoadedEventEnd),
            loadMs: bounded(nav?.loadEventEnd),
          },
          fcpMs: bounded(fcp),
          lcpProxyMs: state.lcp.at(-1)?.startTime ?? null,
          lcpEntries: state.lcp,
          layoutShifts: state.shifts,
          layoutShiftSumProxy: state.shifts
            .filter((entry) => !entry.hadRecentInput)
            .reduce((sum, entry) => sum + entry.value, 0),
          unsupported: state.unsupported,
          resources: performance
            .getEntriesByType("resource")
            .filter((entry) => entry.startTime <= windowMs)
            .map((entry) => ({
              initiatorType: entry.initiatorType,
              startTime: entry.startTime,
              duration: entry.duration,
              transferSize: entry.transferSize,
              encodedBodySize: entry.encodedBodySize,
            })),
        };
      },
      { windowMs: WINDOW_MS },
    );
    if (response?.status() !== 200) issues.push("navigation-not-200");
    if (timing.fcpMs === null) issues.push("fcp-unobserved-within-window");
    if (timing.lcpProxyMs === null) issues.push("lcp-unobserved-within-window");
    if (
      mainVisibleObservedMs === null ||
      headingVisibleObservedMs === null ||
      dataReadyDomObservedMs === null
    )
      issues.push("dom-not-ready-within-window");
    return {
      route,
      profile: profile.id,
      repetition,
      status: issues.length ? "ISSUES" : "OBSERVED",
      issues,
      mainVisibleObservedMs,
      headingVisibleObservedMs,
      dataReadyDomObservedMs,
      ...timing,
      network,
      wallDurationMs: Date.now() - started,
    };
  } catch (error) {
    return {
      route,
      profile: profile.id,
      repetition,
      status: "ERROR",
      error: error.message.split("\n")[0],
      issues,
      network,
      wallDurationMs: started ? Date.now() - started : null,
    };
  } finally {
    await context.close();
  }
}

const { values } = parseArgs({
  options: {
    output: { type: "string" },
    journey: { type: "string" },
    postcheck: { type: "string" },
  },
  strict: true,
});
if (!values.output || !values.journey || !values.postcheck)
  throw new Error("Se requieren --output, --journey y --postcheck.");
const output = privateS15Path(values.output);
// A new directory is mandatory: partial runs and previous evidence are preserved.
mkdirSync(output, { mode: 0o700 });
const journey = readPrivateJson(values.journey);
const postcheck = readPrivateJson(values.postcheck);
if (
  postcheck.value.status !== "PASS" ||
  postcheck.value.runId !== journey.value.runId ||
  journey.value.completedFlows?.join() !== "F1,F2,F3,F4,F5,F6,F7,F8" ||
  journey.value.contextCloseErrors !== 0
) {
  throw new Error("Se requiere el recorrido F1…F8 y su verificación SQL aprobados.");
}
const sourceHash = sha256(readFileSync(scriptPath));
const protocol = {
  version: "s15-frontend-descriptive-v1",
  createdAt: new Date().toISOString(),
  sourceHash,
  base: BASE,
  api: API,
  storage: STORAGE,
  routes,
  profiles,
  repetitions: 3,
  navigationWindowMs: WINDOW_MS,
  expectedNavigations: 18,
  order: "route, profile, repetition",
  browserContexts: "fresh, anonymous, cache-disabled",
  backendCache: {
    state: "not-cleared-or-forced",
    mapTtlSeconds: 60,
    rankingTtlSeconds: 60,
    publicMetricsTtlSeconds: 300,
  },
  world: {
    phase: "after-F8-and-independent-SQL",
    runId: journey.value.runId,
    datasetSha256: journey.value.datasetSha256,
    journeySha256: journey.sha256,
    postcheckSha256: postcheck.sha256,
  },
  buildId: readFileSync(join(frontend, ".next/BUILD_ID"), "utf8").trim(),
  limitations: [
    "Local descriptive samples, not field p75",
    "LCP proxy stops at 8 seconds",
    "Layout-shift sum is not session-window CLS",
    "No INP, TBT, Speed Index or p99",
    "External map tiles excluded",
    "Cross-origin resource timing may be restricted by TAO",
  ],
};
writeJson(join(output, "protocol.json"), protocol);
let browser;
const results = [];
try {
  browser = await chromium.launch({ headless: true });
  writeJson(join(output, "browser.json"), {
    chromium: browser.version(),
    node: process.version,
    platform: process.platform,
  });
  for (const route of routes)
    for (const profile of profiles)
      for (let repetition = 1; repetition <= 3; repetition++) {
        const result = await sample(browser, route, profile, repetition);
        results.push(result);
        writeJson(join(output, `${profile.id}-${route.slice(1)}-${repetition}.json`), result);
        console.log(
          JSON.stringify({ route, profile: profile.id, repetition, status: result.status }),
        );
      }
  const sourceUnchanged = sha256(readFileSync(scriptPath)) === sourceHash;
  writeJson(join(output, "summary.json"), {
    completed: results.length,
    observed: results.filter((result) => result.status === "OBSERVED").length,
    sourceUnchanged,
    results: results.map(
      ({
        route,
        profile,
        repetition,
        status,
        navigation,
        fcpMs,
        lcpProxyMs,
        layoutShiftSumProxy,
        headingVisibleObservedMs,
        dataReadyDomObservedMs,
      }) => ({
        route,
        profile,
        repetition,
        status,
        navigation,
        fcpMs,
        lcpProxyMs,
        layoutShiftSumProxy,
        headingVisibleObservedMs,
        dataReadyDomObservedMs,
      }),
    ),
  });
  if (!sourceUnchanged || results.some((result) => result.status !== "OBSERVED"))
    process.exitCode = 1;
} finally {
  await browser?.close();
}
