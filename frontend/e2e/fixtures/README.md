# Browser test fixtures

Real responses recorded from the SmartRouteNYC API on 2026-10-18, running
against the OpenTripPlanner graph, MTA elevator status and MTA service
alerts of that day. They are replayed by `e2e/api.ts` so browser tests run
without a backend.

| File | Request |
|---|---|
| `search-bay-ridge.json` | Bay Ridge-95 St → 34 St-Herald Sq, step-free, Mon 2026-10-19 8:30 AM |
| `investigation-bay-ridge.json` | Its finished investigation: two verified faster transfers |
| `search-journal-square.json` | Journal Square → Columbia University, step-free, same time: no confirmed route, five to review |
| `investigation-journal-square.json` | Its investigation: not applicable |
| `explain-*.json` | An explanation for a route of each search |
| `places-*.json` | Station search for "bay ridge 95" and "herald sq" (address search was unavailable while recording, so the notice is included) |
| `interpret-wheelchair.json` | Typed needs "I use a wheelchair", read by the keyword baseline |
