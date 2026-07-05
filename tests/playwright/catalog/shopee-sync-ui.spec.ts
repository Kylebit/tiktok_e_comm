import { test, expect } from "@playwright/test";
import { orbitOsBase } from "../helpers/targets";

test("PW-CAT-003 catalog shows TK -> Shopee sync entry points", async ({ page }) => {
  await page.goto(`${orbitOsBase}/catalog`, { waitUntil: "domcontentloaded" });
  await expect(page.locator("#btnSync")).toBeVisible();
  await expect(page.locator("#btnSyncFull")).toBeVisible();
  await expect(page.locator("#syncProgressWrap")).toHaveAttribute("hidden", "");
});
