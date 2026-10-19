import { describe, expect, it } from "vitest";

import { clock, distance, duration, findingPlace, friendlyText, newYorkLocalToIso, isoToNewYorkLocal } from "./format";
import { lineName, lineStyle } from "./lines";
import { decodePolyline } from "./polyline";
import { parseTrip, searchKey, searchRequest, tripParams } from "./trip";

describe("trip URL state", () => {
  const url = "from=40.61681,-74.03082,Bay Ridge-95 St&to=40.7496,-73.9877,34 St-Herald Sq&wc=1&walk=800&route=abc";

  it("parses endpoints, needs and the open route", () => {
    const trip = parseTrip(new URLSearchParams(url));
    expect(trip.from).toEqual({ lat: 40.61681, lon: -74.03082, label: "Bay Ridge-95 St" });
    expect(trip.needs).toMatchObject({ wheelchair: true, stepFree: true, maxWalk: 800 });
    expect(trip.routeId).toBe("abc");
  });

  it("rounds coordinates to about 11 m when writing them back", () => {
    const params = tripParams(parseTrip(new URLSearchParams(url)));
    expect(params.get("from")).toBe("40.6168,-74.0308,Bay Ridge-95 St");
  });

  it("keys a search independently of which route is open", () => {
    const a = parseTrip(new URLSearchParams(url));
    expect(searchKey(a)).toBe(searchKey({ ...a, routeId: "other" }));
    expect(searchKey({ ...a, from: null })).toBeNull();
  });

  it("builds the API request, with wheelchair implying step-free", () => {
    const request = searchRequest(parseTrip(new URLSearchParams(url)));
    expect(request?.accessibility).toEqual({
      step_free_required: true,
      wheelchair_required: true,
      max_walk_meters: 800,
    });
  });

  it("ignores malformed coordinates", () => {
    expect(parseTrip(new URLSearchParams("from=abc,def,X")).from).toBeNull();
  });
});

describe("New York time", () => {
  it("shows times in New York whatever the device zone", () => {
    expect(clock("2026-10-19T12:34:00Z")).toBe("8:34");
  });

  it("reads a picked time as New York time, across daylight saving", () => {
    expect(newYorkLocalToIso("2026-10-19T08:30")).toBe("2026-10-19T12:30:00.000Z"); // EDT
    expect(newYorkLocalToIso("2026-11-02T08:30")).toBe("2026-11-02T13:30:00.000Z"); // EST
    expect(isoToNewYorkLocal("2026-11-02T13:30:00.000Z")).toBe("2026-11-02T08:30");
  });

  it("formats durations and distances", () => {
    expect(duration(2666)).toBe("44 min");
    expect(duration(4860)).toBe("1 h 21 min");
    expect(distance(81.1)).toBe("80 m");
    expect(distance(2550)).toBe("2.6 km");
  });

  it("replaces OTP endpoint names", () => {
    expect(friendlyText("Walk from Origin to Grove Street")).toBe("Walk from your starting point to Grove Street");
  });
});

describe("lines", () => {
  it("uses line colors with readable text", () => {
    expect(lineStyle("MTASBWY:N", "N")).toMatchObject({ background: "#FCCC0A", color: "#111111", shape: "circle" });
    expect(lineStyle("MTASBWY:6X", "6X")).toMatchObject({ background: "#00933C", label: "6" });
    expect(lineStyle("PATH:RED", "RED")).toMatchObject({ label: "PATH", shape: "pill" });
    expect(lineName("MTASBWY:R", "R", "SUBWAY")).toBe("R train");
    expect(lineName("MTABC:Q32", "Q32", "BUS")).toBe("Q32 bus");
  });
});

describe("polyline", () => {
  it("decodes the reference example", () => {
    expect(decodePolyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")).toEqual([
      [-120.2, 38.5],
      [-120.95, 40.7],
      [-126.453, 43.252],
    ]);
  });
});

describe("findingPlace", () => {
  it("names a walk by where it goes", () => {
    expect(findingPlace("Walk from Origin to Grove Street")).toBe("Walk to Grove Street");
    expect(findingPlace("Walk from WTC Cortlandt to Destination")).toBe("Walk to your destination");
    expect(findingPlace("59 St")).toBe("59 St");
  });
});
