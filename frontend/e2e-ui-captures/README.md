Capturas locales S15-UI, fuera del testDir de CI. Solo HTTP simulado; sin cuentas,
backend ni servicios externos. Reutiliza fixtures de e2e/ y el control de errores
no capturados de e2e/support. Fecha fija: 20/10/2026. Panel, ranking y auditoría
usan estados vacíos; las otras pantallas reutilizan datos sintéticos existentes.
Las teselas son PNG sintéticos, no cartografía descargada.

Desde frontend/, con Chromium de Playwright ya disponible:

```bash
NEXT_PUBLIC_API_URL=http://127.0.0.1:3106 NEXT_PUBLIC_GOOGLE_CLIENT_ID=e2e-ficticio.apps.googleusercontent.com npm run build
npx playwright test --config=playwright.ui-captures.config.ts --project=chromium
S15_UI_CAPTURE_STAGE=despues npx playwright test --config=playwright.ui-captures.config.ts --project=chromium
```

Genera 28 PNG de página completa (14 rutas × 375/1280 px, alto de viewport 900)
en ../.s15-ui-captures/{antes,despues}/, excluido de Git. Se rechazan APIs sin
fixture y se bloquea la red externa. El servidor es siempre next start y no se
reutiliza ningún servidor existente. No ejecutar automáticamente en CI.
