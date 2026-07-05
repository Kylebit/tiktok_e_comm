import { test, expect } from "@playwright/test";
import { expectNoConnectionRefused, expectTopNav, orbitOsBase } from "../helpers/targets";

test("PW-SMOKE-001 Orbit OS opens", async ({ page }) => {
  await page.goto(`${orbitOsBase}/`, { waitUntil: "domcontentloaded" });
  await expectNoConnectionRefused(page);
  await expectTopNav(page);
  await expect(page.locator("body")).toContainText("商品目录");
});
