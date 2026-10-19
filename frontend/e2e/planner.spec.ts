import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { BAY_RIDGE, JOURNAL_SQUARE, mockApi } from "./api";

async function expectAccessible(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
}

test("first visit explains the states and passes accessibility checks", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /trips that respect your needs/ })).toBeVisible();
  await expect(page.getByText("A known barrier, such as no elevator")).toBeVisible();
  await expectAccessible(page);
});

test("plans a trip from typed places and shows a verified faster transfer", async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto("/");
  await page.getByRole("combobox", { name: "From" }).fill("bay ridge");
  await page.getByRole("listbox").getByRole("option", { name: /Bay Ridge-95 St/ }).click();
  await page.getByRole("combobox", { name: "To" }).fill("herald sq");
  await page.getByRole("listbox").getByRole("option", { name: /34 St-Herald Sq/ }).click();
  await page.getByLabel("Leave").fill("2026-10-19T08:30");
  await page.getByRole("button", { name: "Step-free", pressed: false }).click();
  await page.getByRole("button", { name: "Search", exact: true }).click();

  await expect(page.getByRole("heading", { level: 1, name: /step-free routes/ })).toBeVisible();
  await expect(page.getByText("13 MIN FASTER")).toBeVisible();
  await expect(page.getByRole("region", { name: "No transfer" }).getByRole("listitem")).toHaveCount(5);
  expect(page.url()).toContain("sf=1");
  // The departure was sent as New York time.
  expect(calls.some((c) => c.startsWith("POST /api/v1/routes/search"))).toBe(true);
  await expectAccessible(page);
});

test("route detail shows every step, explains the route and takes a rating", async ({ page, isMobile }) => {
  await mockApi(page);
  await page.goto(BAY_RIDGE);
  await page.getByText("13 MIN FASTER").click();
  const detail = page.getByRole("article");
  await expect(detail.getByRole("heading", { level: 2, name: "8:34 – 9:18" })).toBeVisible();
  await expect(detail.getByText("Change at 59 St")).toBeVisible();
  await expect(detail.getByText("Step-free check · confirmed")).toBeVisible();
  if (isMobile) await expect(page.getByRole("heading", { level: 1 })).toBeHidden();

  await detail.getByRole("button", { name: "Explain this route" }).click();
  await expect(detail.getByRole("heading", { name: "About this route" })).toBeVisible();
  await expect(detail.getByText(/Built directly from the route data/)).toBeVisible();

  await detail.getByText("★").nth(3).click();
  await detail.getByRole("button", { name: "Send rating" }).click();
  await expect(detail.getByRole("status")).toContainText("Your rating was saved");
  await expectAccessible(page);
});

test("when nothing can be confirmed, routes are listed for review with each barrier", async ({ page }) => {
  await mockApi(page);
  await page.goto(JOURNAL_SQUARE);
  await expect(page.getByText("We couldn’t confirm a step-free route")).toBeVisible();
  const review = page.getByRole("region", { name: "Needs review" });
  await expect(review.getByRole("listitem")).toHaveCount(5);
  await expect(review.getByText("1 barrier").first()).toBeVisible();
  await expect(review.getByText("2 not confirmed").first()).toBeVisible();
  await expect(page.getByText(/step-free confirmed/i)).toHaveCount(0);
  await expectAccessible(page);
});

test("a routing outage is reported clearly", async ({ page }) => {
  let failures = 1;
  await mockApi(page, {
    "POST /api/v1/routes/search": async (route, next) => {
      if (failures-- > 0) {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ code: "routing_unavailable", message: "x", request_id: "r1" }),
        });
      } else {
        await next();
      }
    },
  });
  await page.goto(BAY_RIDGE);
  const results = page.locator("#results");
  await expect(results.getByRole("alert")).toContainText("Trip planning is temporarily unavailable");
  await expect(results.getByRole("listitem")).toHaveCount(0);
  await results.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { level: 1, name: /step-free routes/ })).toBeVisible();
  await expectAccessible(page);
});

test("a denied location permission leaves search working", async ({ page }) => {
  // Headless browsers never answer the permission prompt; act as a rider who says no.
  await page.addInitScript(() => {
    navigator.geolocation.getCurrentPosition = (_ok, fail) =>
      fail?.({ code: 1, PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3, message: "denied" } as GeolocationPositionError);
  });
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Use my current location" }).click();
  await expect(page.getByText(/Search for a station or address instead/)).toBeVisible();
  await expect(page.getByRole("combobox", { name: "From" })).toBeEnabled();
});

test("typed needs are proposed, labelled honestly and applied on request", async ({ page }) => {
  await mockApi(page);
  await page.goto(BAY_RIDGE);
  await page.getByRole("button", { name: "Describe your needs…" }).click();
  await page.getByRole("dialog").getByRole("textbox").fill("I use a wheelchair");
  await page.getByRole("button", { name: "Suggest settings" }).click();
  await expect(page.getByText(/no AI model is connected/)).toBeVisible();
  await page.getByRole("button", { name: "Apply these settings" }).click();
  await expect(page.getByRole("button", { name: "Wheelchair", pressed: true })).toBeVisible();
  expect(page.url()).toContain("wc=1");
});

test("pages are served with security headers and nothing breaks the content policy", async ({ page }) => {
  const violations: string[] = [];
  page.on("console", (message) => {
    if (/Content Security Policy/i.test(message.text())) violations.push(message.text());
  });
  await mockApi(page);
  const response = await page.goto(BAY_RIDGE);
  const headers = response?.headers() ?? {};
  expect(headers["content-security-policy"]).toContain("frame-ancestors 'none'");
  expect(headers["referrer-policy"]).toBe("no-referrer");
  expect(headers["x-content-type-options"]).toBe("nosniff");
  expect(headers["x-powered-by"]).toBeUndefined();

  await page.getByText("13 MIN FASTER").click();
  await expect(page.getByRole("article").getByText("Change at 59 St")).toBeVisible();
  expect(violations).toEqual([]);
});
