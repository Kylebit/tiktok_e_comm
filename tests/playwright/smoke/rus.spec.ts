import { test, expect } from "@playwright/test";
import { expectNoConnectionRefused, rusBase } from "../helpers/targets";

test("PW-SMOKE-003 Orbit Rus opens", async ({ page }) => {
  await page.goto(`${rusBase}/`, { waitUntil: "domcontentloaded" });
  await expectNoConnectionRefused(page);
  await expect(page.locator("body")).toContainText(/Ozon|Rus|俄罗斯|未搬运|搬运/);
});
