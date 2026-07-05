import { test, expect } from "@playwright/test";
import { pathToFileURL } from "url";
import { desktopHtml } from "../helpers/targets";

test("PW-BOT-001 desktop bot panel shell exists", async ({ page }) => {
  await page.goto(pathToFileURL(desktopHtml).href, { waitUntil: "domcontentloaded" });
  await expect(page.locator("#mainArea")).toBeVisible();
  await expect(page.locator("#botPanel")).toHaveCount(1);
  await expect(page.locator("#mainArea")).toBeVisible();
});
