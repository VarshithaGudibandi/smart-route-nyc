# Security Policy

## Reporting a vulnerability

Please do not open a public issue for security problems. Use GitHub's
[private vulnerability reporting](https://github.com/VarshithaGudibandi/smart-route-nyc/security/advisories/new)
for this repository instead. Include steps to reproduce and the affected component.

## Handling of secrets and user data

- Provider keys (LLM, database, transit APIs) live only on the server and are loaded from the environment.
- The only browser-visible credential is a Mapbox public token restricted to approved URLs.
- The application has no accounts and does not store visitor locations or route histories.
