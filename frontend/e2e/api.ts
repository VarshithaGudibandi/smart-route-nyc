/**
 * Serves recorded API responses (see fixtures/README.md) for a test page,
 * and blocks map tiles so tests never depend on the network.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import type { Page, Route } from "@playwright/test";

export const API = "http://api.test";

export function fixture(name: string): unknown {
  return JSON.parse(readFileSync(join(__dirname, "fixtures", name), "utf8"));
}

// An override can answer itself or call `next` for the recorded response.
type Handler = (route: Route, next: () => Promise<void>) => Promise<void> | void;

export async function mockApi(page: Page, overrides: Record<string, Handler> = {}): Promise<string[]> {
  const calls: string[] = [];
  await page.route(/tiles\.openfreemap\.org|openfreemap/, (route) => route.abort());
  await page.route(`${API}/**`, async (route) => {
    const url = new URL(route.request().url());
    const key = `${route.request().method()} ${url.pathname}`;
    calls.push(`${key}${url.search}`);
    const recorded = () => serve(route, url, key);
    for (const [pattern, handler] of Object.entries(overrides)) {
      if (key.startsWith(pattern)) return handler(route, recorded);
    }
    return recorded();
  });
  return calls;
}

async function serve(route: Route, url: URL, key: string): Promise<void> {
  const json = (body: unknown, status = 200) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

  if (key === "GET /api/v1/places") {
    const q = url.searchParams.get("q") ?? "";
    return json(fixture(q.startsWith("bay") ? "places-bay-ridge.json" : "places-herald-sq.json"));
  }
  if (key === "POST /api/v1/routes/search") {
    const body = route.request().postDataJSON() as { origin: { lat: number } };
    return json(fixture(body.origin.lat < 40.7 ? "search-bay-ridge.json" : "search-journal-square.json"));
  }
  if (key.startsWith("GET /api/v1/routes/investigations/")) {
    const bay = fixture("search-bay-ridge.json") as { investigation: { id: string } };
    return json(fixture(url.pathname.endsWith(bay.investigation.id) ? "investigation-bay-ridge.json" : "investigation-journal-square.json"));
  }
  if (key.startsWith("DELETE /api/v1/routes/investigations/")) return route.fulfill({ status: 204 });
  if (key === "POST /api/v1/routes/explain") {
    const body = route.request().postDataJSON() as { search_id: string };
    const bay = fixture("search-bay-ridge.json") as { search_id: string };
    return json(fixture(body.search_id === bay.search_id ? "explain-bay-ridge.json" : "explain-journal-square.json"));
  }
  if (key === "POST /api/v1/feedback") return json({ feedback_id: "00000000-0000-0000-0000-000000000000", status: "recorded" }, 201);
  if (key === "POST /api/v1/preferences/interpret") return json(fixture("interpret-wheelchair.json"));
  return json({ code: "not_found", message: "Not mocked", request_id: null }, 404);
}

export const AT = "2026-10-19T12:30:00.000Z";
export const BAY_RIDGE = `/?from=40.6168,-74.0308,Bay%20Ridge-95%20St&to=40.7496,-73.9877,34%20St-Herald%20Sq&at=${AT}&sf=1`;
export const JOURNAL_SQUARE = `/?from=40.733,-74.0629,Journal%20Square&to=40.8076,-73.9645,Columbia%20University&at=${AT}&sf=1`;
