import { expect, type Page } from "@playwright/test";
import path from "path";

export const orbitOsBase = process.env.ORBIT_OS_BASE_URL || "http://127.0.0.1:8765";
export const treasuryBase = process.env.ORBIT_TREASURY_BASE_URL || "http://127.0.0.1:8766";
export const rusBase = process.env.ORBIT_RUS_BASE_URL || "http://127.0.0.1:8767";
export const desktopHtml = process.env.ORBIT_DESKTOP_HTML ||
  path.resolve(__dirname, "../../../desktop/orbit_desktop.html");

export async function expectNoConnectionRefused(page: Page) {
  await expect(page.locator("body")).not.toContainText("ERR_CONNECTION_REFUSED");
  await expect(page.locator("body")).not.toContainText("This site can’t be reached");
  await expect(page.locator("body")).not.toContainText("拒绝了我们的连接请求");
}

export async function expectTopNav(page: Page) {
  await expect(page.locator("header.topbar")).toBeVisible();
}

