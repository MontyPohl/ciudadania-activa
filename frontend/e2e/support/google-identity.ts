import type { Page } from "@playwright/test";

export async function installGoogleIdentityStub(page: Page): Promise<void> {
  await page.route("https://accounts.google.com/gsi/client", (route) =>
    route.fulfill({
      contentType: "application/javascript",
      body: `
        (() => {
          let callback;
          window.google = {
            accounts: {
              id: {
                initialize(options) {
                  callback = options.callback;
                },
                renderButton(parent) {
                  const button = document.createElement("button");
                  button.type = "button";
                  button.textContent = "Ingresar con Google (simulado)";
                  button.addEventListener("click", () => {
                    callback({ credential: "e2e-credencial-ficticia" });
                  });
                  parent.appendChild(button);
                },
              },
            },
          };
        })();
      `,
    }),
  );
}
