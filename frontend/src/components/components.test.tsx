import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import bayRidgeInvestigation from "../../e2e/fixtures/investigation-bay-ridge.json";
import interpretWheelchair from "../../e2e/fixtures/interpret-wheelchair.json";
import placesBayRidge from "../../e2e/fixtures/places-bay-ridge.json";
import journalSquare from "../../e2e/fixtures/search-journal-square.json";
import type { InvestigationView, SearchResponse } from "@/lib/types";
import { NO_NEEDS } from "@/lib/trip";
import { fetchMock, jsonResponse } from "@/test/setup";

import { Feedback } from "./Feedback";
import { NeedsDialog } from "./NeedsDialog";
import { PlaceField } from "./PlaceField";
import { RouteCard } from "./RouteCard";
import { RouteDetail } from "./RouteDetail";

vi.mock("./RouteMap", () => ({ RouteMap: ({ label }: { label: string }) => <div role="img" aria-label={label} /> }));

const suggestion = (bayRidgeInvestigation as unknown as InvestigationView).suggestions![0];
const review = (journalSquare as unknown as SearchResponse).review_only_routes![0];

describe("RouteCard", () => {
  it("shows a verified saving and confirmed step-free access", () => {
    render(
      <RouteCard
        route={suggestion.route}
        savesSeconds={suggestion.saves_seconds}
        selected={false}
        onSelect={() => undefined}
        accessibilityRequired
      />,
    );
    expect(screen.getByText("13 MIN FASTER")).toBeInTheDocument();
    expect(screen.getByText("Step-free confirmed")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "R train" })).toBeInTheDocument();
    expect(screen.getByText(/change at 59 St/)).toBeInTheDocument();
  });

  it("counts barriers and unconfirmed stops in words, not only color", () => {
    render(<RouteCard route={review} selected={false} onSelect={() => undefined} accessibilityRequired />);
    expect(screen.getByText(/1 barrier/)).toBeInTheDocument();
    expect(screen.getByText(/2 not confirmed/)).toBeInTheDocument();
    expect(screen.queryByText("Step-free confirmed")).not.toBeInTheDocument();
  });
});

describe("RouteDetail", () => {
  it("lists every finding with its status in words", () => {
    render(
      <RouteDetail
        route={review}
        searchId={"a".repeat(32)}
        freshness={[]}
        notices={[]}
        accessibilityRequired
      />,
    );
    expect(screen.getByText("Step-free check · barrier found")).toBeInTheDocument();
    expect(screen.getByText(/MTA lists no elevator at this station/)).toBeInTheDocument();
    expect(screen.getAllByText(/Not confirmed:/).length).toBe(2);
    expect(screen.getByText(/Walk from your starting point to Grove Street/)).toBeInTheDocument();
    expect(screen.getByText("MTA elevator status is not available right now.", { exact: false })).toBeInTheDocument();
  });
});

describe("PlaceField", () => {
  it("suggests stations and picks one with the keyboard", async () => {
    fetchMock.mockResolvedValue(jsonResponse(placesBayRidge));
    const onChange = vi.fn();
    render(<PlaceField label="From" value={null} onChange={onChange} />);
    const input = screen.getByRole("combobox", { name: "From" });
    await userEvent.type(input, "bay ridge");
    expect(await screen.findByRole("option", { name: /Bay Ridge-95 St/ })).toBeInTheDocument();
    expect(screen.getByText("Address search is unavailable right now; stations still work.")).toBeInTheDocument();
    await userEvent.keyboard("{ArrowDown}{Enter}");
    expect(onChange).toHaveBeenLastCalledWith({ lat: 40.616622, lon: -74.030876, label: "Bay Ridge-95 St" });
    expect(input).toHaveAttribute("aria-expanded", "false");
  });

  it("explains a denied location permission and keeps search available", async () => {
    Object.defineProperty(navigator, "geolocation", {
      configurable: true,
      value: {
        getCurrentPosition: (_ok: unknown, fail: (e: { code: number; PERMISSION_DENIED: number }) => void) =>
          fail({ code: 1, PERMISSION_DENIED: 1 }),
      },
    });
    render(<PlaceField label="From" value={null} onChange={() => undefined} allowCurrentLocation />);
    await userEvent.click(screen.getByRole("button", { name: "Use my current location" }));
    expect(screen.getByRole("status")).toHaveTextContent("Location permission was not given");
    expect(screen.getByRole("combobox", { name: "From" })).toBeEnabled();
  });
});

describe("NeedsDialog", () => {
  it("proposes changes, says how it read them, and applies only on request", async () => {
    fetchMock.mockResolvedValue(jsonResponse(interpretWheelchair));
    const onApply = vi.fn();
    render(<NeedsDialog open needs={NO_NEEDS} onClose={() => undefined} onApply={onApply} />);
    await userEvent.type(screen.getByRole("textbox"), "I use a wheelchair");
    await userEvent.click(screen.getByRole("button", { name: "Suggest settings" }));
    expect(await screen.findByText(/Wheelchair access/)).toBeInTheDocument();
    expect(screen.getByText(/no AI model is connected/)).toBeInTheDocument();
    expect(onApply).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Apply these settings" }));
    expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ wheelchair: true, stepFree: true }));
  });

  it("falls back to the on-page options when the service is unavailable", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ code: "ai_unavailable", message: "x", request_id: null }, 503));
    render(<NeedsDialog open needs={NO_NEEDS} onClose={() => undefined} onApply={() => undefined} />);
    await userEvent.type(screen.getByRole("textbox"), "stroller");
    await userEvent.click(screen.getByRole("button", { name: "Suggest settings" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Use the options on the page instead");
  });
});

describe("Feedback", () => {
  it("sends stars and a comment, then confirms", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ feedback_id: "f", status: "recorded" }, 201));
    render(<Feedback searchId={"a".repeat(32)} routeId={"b".repeat(16)} />);
    await userEvent.click(screen.getByLabelText(/4 stars, Good/));
    await userEvent.type(screen.getByRole("textbox"), "Clear directions");
    await userEvent.click(screen.getByRole("button", { name: "Send rating" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Your rating was saved"));
    const request = fetchMock.mock.calls[0][0] as Request;
    expect(await request.clone().json()).toEqual({
      search_id: "a".repeat(32),
      route_id: "b".repeat(16),
      stars: 4,
      comment: "Clear directions",
    });
  });

  it("says when a result has expired", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ code: "not_found", message: "gone", request_id: null }, 404));
    render(<Feedback searchId={"a".repeat(32)} routeId={"b".repeat(16)} />);
    await userEvent.click(screen.getByLabelText(/2 stars/));
    await userEvent.click(screen.getByRole("button", { name: "Send rating" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Search again to rate it");
  });
});
