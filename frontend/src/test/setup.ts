import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// openapi-fetch keeps a reference to fetch, so route every call through a
// mock each test can replace.
export const fetchMock = vi.fn<typeof fetch>();
globalThis.fetch = ((...args: Parameters<typeof fetch>) => fetchMock(...args)) as typeof fetch;

// jsdom has no <dialog> behavior.
HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) {
  this.setAttribute("open", "");
};
HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement) {
  this.removeAttribute("open");
};

afterEach(() => {
  cleanup();
  fetchMock.mockReset();
});

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
