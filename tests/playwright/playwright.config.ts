import { defineConfig } from "@playwright/test";

const artifactDir = "tests/playwright/artifacts";

export default defineConfig({
  testDir: ".",
  timeout: 45_000,
  expect: {
    timeout: 8_000,
  },
  fullyParallel: false,
  retries: 0,
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: `${artifactDir}/html-report` }],
  ],
  outputDir: `${artifactDir}/test-results`,
  use: {
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    ignoreHTTPSErrors: true,
  },
});
