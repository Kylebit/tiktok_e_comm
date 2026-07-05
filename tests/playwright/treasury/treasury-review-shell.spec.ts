import { test, expect } from "@playwright/test";
import { treasuryBase } from "../helpers/targets";

test("PW-TR-002 treasury review containers exist", async ({ page }) => {
  await page.goto(`${treasuryBase}/new-product`, { waitUntil: "domcontentloaded" });
  await expect(page.locator("#reviewStage")).toBeVisible();
  await expect(page.locator("#stageHint")).toBeVisible();
  await expect(page.locator("#stageButton")).toBeVisible();
  await expect(page.locator("#preview")).toBeVisible();
});
