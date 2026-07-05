import { test, expect } from "@playwright/test";
import { expectNoConnectionRefused, treasuryBase } from "../helpers/targets";

test("PW-SMOKE-002 Orbit Treasury opens", async ({ page }) => {
  await page.goto(`${treasuryBase}/new-product`, { waitUntil: "domcontentloaded" });
  await expectNoConnectionRefused(page);
  await expect(page.locator("#urlInput")).toBeVisible();
  await expect(page.locator("#previewButton")).toBeVisible();
});
