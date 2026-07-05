import { test, expect } from "@playwright/test";
import { orbitOsBase } from "../helpers/targets";

test("PW-CAT-001 catalog page renders the unified table shell", async ({ page }) => {
  await page.goto(`${orbitOsBase}/catalog`, { waitUntil: "domcontentloaded" });
  await expect(page.locator("#tbody")).toBeVisible();
  await expect(page.locator("table.catalog-table")).toBeVisible();
  await expect(page.locator("#btnSearch")).toBeVisible();
});
