import { test, expect } from "@playwright/test";
import { treasuryBase } from "../helpers/targets";

test("PW-TR-001 treasury input shell is usable", async ({ page }) => {
  await page.goto(`${treasuryBase}/new-product`, { waitUntil: "domcontentloaded" });
  await expect(page.locator("#urlInput")).toBeVisible();
  await expect(page.locator("#overseasInput")).toBeVisible();
  await expect(page.locator("#previewButton")).toBeVisible();
  await page.locator("#urlInput").fill("967648348081");
  await expect(page.locator("#urlInput")).toHaveValue("967648348081");
});
