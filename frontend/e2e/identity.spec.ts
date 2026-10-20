import { Buffer } from "node:buffer";

import {
  createIdentityState,
  IDENTITY_CITIZEN,
  IDENTITY_DOCUMENT,
  IDENTITY_ID,
  IDENTITY_SELFIE,
  mockIdentityApi,
} from "./identity-fixtures";
import { installGoogleIdentityStub } from "./support/google-identity";
import { expect, test, trackUncaughtErrors } from "./support/test";

test("@F1 registro con Google simulado, verificación y aprobación", async ({
  page,
  browser,
  baseURL,
}) => {
  if (baseURL === undefined) throw new Error("F1 requiere la baseURL del frontend.");
  const state = createIdentityState();
  await mockIdentityApi(page, state, baseURL);
  await installGoogleIdentityStub(page);

  await test.step("inicia sesión con la credencial ficticia de Google", async () => {
    await page.goto("/login");
    await page.getByRole("button", { name: "Ingresar con Google (simulado)", exact: true }).click();
    await expect(page.getByRole("main", { name: "", exact: true })).toContainText(
      `Ya iniciaste sesión como ${IDENTITY_CITIZEN.email}.`,
    );
    await page.getByText("Mi cuenta", { exact: true }).click();
    await expect(page.getByRole("banner", { name: "", exact: true })).toContainText(
      "Identidad sin verificar",
    );
    await page.keyboard.press("Escape");
    expect(state.googleBodies).toEqual([{ token_google: "e2e-credencial-ficticia" }]);
  });

  await test.step("envía la cédula ficticia y la selfie en multipart", async () => {
    await page.goto("/verificacion");
    await expect(
      page.getByRole("heading", { name: "Todavía no enviaste tu verificación", exact: true }),
    ).toBeVisible();
    await page
      .getByRole("textbox", { name: "Número de cédula", exact: true })
      .fill(IDENTITY_DOCUMENT);
    // Los input file nativos no tienen rol ARIA: se usa su etiqueta accesible exacta.
    await page.getByLabel("Selfie sosteniendo tu cédula", { exact: true }).setInputFiles({
      name: "selfie-ficticia.png",
      mimeType: "image/png",
      buffer: IDENTITY_SELFIE,
    });
    await page.getByRole("button", { name: "Enviar verificación", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "Estado de tu verificación", exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("main", { name: "", exact: true })).toContainText(
      "Pendiente de revisión",
    );
    await expect(
      page.getByRole("button", { name: "Enviar verificación", exact: true }),
    ).toHaveCount(0);

    expect(state.submitContentTypes).toHaveLength(1);
    expect(state.submitContentTypes[0]).toMatch(/^multipart\/form-data; boundary=/);
    expect(state.submitBodies).toHaveLength(1);
    const body = state.submitBodies[0];
    expect([...body.keys()].sort()).toEqual(["document_number", "selfie"]);
    expect(body.get("document_number")).toBe(IDENTITY_DOCUMENT);
    const selfie = body.get("selfie");
    if (selfie === null || typeof selfie === "string") {
      throw new Error("El campo selfie debe contener un archivo.");
    }
    expect(selfie.name).toBe("selfie-ficticia.png");
    expect(selfie.type).toBe("image/png");
    expect(Buffer.from(await selfie.arrayBuffer())).toEqual(IDENTITY_SELFIE);
  });

  await test.step("el admin coteja la cédula y aprueba la solicitud compartida", async () => {
    const adminContext = await browser.newContext({ baseURL });
    const adminPage = await adminContext.newPage();
    const assertNoAdminErrors = trackUncaughtErrors(adminPage);
    try {
      await mockIdentityApi(adminPage, state, baseURL, "admin");
      await adminPage.goto("/identidades");
      await expect(
        adminPage.getByRole("heading", { name: "Verificaciones de identidad", exact: true }),
      ).toBeVisible();
      await expect(adminPage.getByRole("table", { name: "", exact: true })).toContainText(
        IDENTITY_CITIZEN.email,
      );
      await adminPage.getByRole("link", { name: "Revisar", exact: true }).click();
      await expect(adminPage).toHaveURL(`/identidades/${IDENTITY_ID}`);
      await expect(
        adminPage.getByRole("heading", { name: IDENTITY_CITIZEN.full_name, exact: true }),
      ).toBeVisible();
      const selfie = adminPage.getByRole("img", {
        name: "Imagen de verificación de identidad enviada por el ciudadano",
        exact: true,
      });
      await expect(selfie).toBeVisible();
      await expect(selfie).toHaveJSProperty("naturalWidth", 1);
      await adminPage
        .getByRole("textbox", { name: "Número de cédula visible en la selfie", exact: true })
        .fill(IDENTITY_DOCUMENT);
      await adminPage.getByRole("button", { name: "Aprobar", exact: true }).click();
      await expect(adminPage.getByRole("main", { name: "", exact: true })).toContainText(
        "Aprobada",
      );
      await expect(adminPage.getByRole("button", { name: "Aprobar", exact: true })).toHaveCount(0);
      expect(state.reviewBodies).toEqual([
        { decision: "APROBADO", document_number: IDENTITY_DOCUMENT },
      ]);
    } finally {
      try {
        assertNoAdminErrors();
      } finally {
        await adminContext.close();
      }
    }
  });

  await test.step("el ciudadano recarga y ve su identidad verificada", async () => {
    await page.reload();
    await expect(
      page.getByRole("heading", { name: "Estado de tu verificación", exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("main", { name: "", exact: true })).toContainText(
      "Tu verificación de identidad fue aprobada. Tu cuenta ya figura como verificada para las funcionalidades que lo requieren.",
    );
    await page.getByText("Mi cuenta", { exact: true }).click();
    await expect(page.getByRole("banner", { name: "", exact: true })).toContainText(
      "Identidad verificada",
    );
    await page.keyboard.press("Escape");
    await expect(
      page.getByRole("button", { name: "Enviar verificación", exact: true }),
    ).toHaveCount(0);
  });
});

test("cola de identidad contiene filtros y paginación con conteos largos", async ({
  page,
  baseURL,
}) => {
  const state = createIdentityState();
  state.signedIn = true;
  await mockIdentityApi(page, state, baseURL!, "admin");
  await page.route("**/api/v1/admin/identity/?*", async (route) => {
    await route.fulfill({
      json: {
        count: 123456789,
        previous: null,
        next: "/api/v1/admin/identity/?page=2",
        results: [
          {
            id: IDENTITY_ID,
            user: {
              id: IDENTITY_CITIZEN.id,
              full_name: IDENTITY_CITIZEN.full_name,
              email: IDENTITY_CITIZEN.email,
            },
            verification_status: "PENDIENTE",
            created_at: "2026-10-20T12:00:00Z",
          },
        ],
      },
    });
  });
  await page.goto("/identidades");
  await expect(page.getByText("Página 1 · 123456789 en total", { exact: true })).toBeVisible();
  for (const width of [320, 375, 768, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(page.getByRole("button", { name: "Siguiente", exact: true })).toBeVisible();
    await expect(page.locator("#contenido-principal")).toHaveCount(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
      width,
    );
  }
});
