import { test as base, expect, type Page } from "@playwright/test";

export { expect };

export function trackUncaughtErrors(page: Page): () => void {
  const errors: Error[] = [];
  const onPageError = (error: Error) => errors.push(error);
  page.on("pageerror", onPageError);
  return () => {
    page.off("pageerror", onPageError);
    expect(errors, "excepciones no controladas en la página").toEqual([]);
  };
}

export const test = base.extend<{ noUncaughtErrors: void }>({
  noUncaughtErrors: [
    async ({ page }, use) => {
      const assertNoUncaughtErrors = trackUncaughtErrors(page);
      await use();
      assertNoUncaughtErrors();
    },
    { auto: true },
  ],
});
