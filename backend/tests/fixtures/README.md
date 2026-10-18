# Test fixtures

`otp_plan_journal_square_to_columbia.json` is a real response recorded from
OpenTripPlanner 2.10.0 (MTA subway, MTA bus and PATH GTFS, October 2026;
OpenStreetMap via BBBike) for Journal Square to Columbia University, departing
2026-10-19 08:30 America/New_York. It is used only to test response mapping
and is not served by the application.

`mta_ene_equipments.json` and `mta_ene_outages.json` are real entries from
MTA's elevator and escalator feeds (`nyct_ene_equipments.json`, `nyct_ene.json`),
recorded on 2026-10-18. They were chosen to cover two current outages, one
scheduled future outage, a multi-station complex, an escalator and an
inactive unit.

`otp_station_departures_chambers_st.json` is a real `StationDepartures`
response for Chambers St (`MTASBWY:137`), 2026-10-19 08:30 to 09:30
America/New_York, from the same graph.
