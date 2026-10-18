/**
 * Typed client for the SmartRouteNYC API. Types are generated from the
 * backend's OpenAPI document (`npm run api:types`), so a changed field
 * fails the type check here instead of at runtime.
 */
import createClient from "openapi-fetch";

import type { paths } from "./api-types";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export const api = createClient<paths>({ baseUrl: API_BASE_URL });

/** An error response from the API, in its documented shape. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId: string | null = null,
  ) {
    super(message);
  }
}

type Failure = { code?: string; message?: string; request_id?: string | null } | undefined;

/** Unwraps an openapi-fetch result, turning any failure into an ApiError. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.data !== undefined && result.response.ok) {
    return result.data;
  }
  const error = result.error as Failure;
  throw new ApiError(
    result.response.status,
    error?.code ?? "unexpected_error",
    error?.message ?? "Something went wrong. Please try again.",
    error?.request_id ?? null,
  );
}

/** Words a rider can act on, for the errors a rider can see. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "routing_unavailable":
        return "Trip planning is temporarily unavailable. Please try again in a minute.";
      case "routing_timeout":
        return "Trip planning took too long. Please try again.";
      case "rate_limited":
        return "Too many requests in a short time. Please wait a moment and try again.";
      default:
        return error.message;
    }
  }
  return "Could not reach SmartRouteNYC. Check your connection and try again.";
}
