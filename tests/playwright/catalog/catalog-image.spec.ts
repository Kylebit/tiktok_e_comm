import { test, expect } from "@playwright/test";
import { orbitOsBase } from "../helpers/targets";

test("PW-CAT-002 catalog exposes image cells or clear fallbacks", async ({ page }) => {
  await page.goto(`${orbitOsBase}/catalog`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("#tbody", { state: "visible" });
  const body = page.locator("#tbody");
  await expect(body).not.toContainText("加载中");
  const thumbs = page.locator("td.thumb");
  await expect(thumbs.first()).toBeVisible();
  const thumbLike = page.locator("td.thumb img, td.thumb .no-img");
  await expect(thumbLike.first()).toBeVisible();
});
