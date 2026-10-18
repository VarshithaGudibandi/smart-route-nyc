"""HTTP client for the OpenTripPlanner GTFS GraphQL API."""

import httpx


class OtpClient:
    GRAPHQL_PATH = "/otp/gtfs/v1"

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    @classmethod
    def create(cls, base_url: str, timeout_s: float) -> "OtpClient":
        return cls(httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(timeout_s)))

    async def is_ready(self, timeout_s: float) -> bool:
        """True if OTP answers a trivial GraphQL query (graph loaded, API up)."""
        try:
            response = await self._http.post(
                self.GRAPHQL_PATH, json={"query": "{ __typename }"}, timeout=timeout_s
            )
        except httpx.HTTPError:
            return False
        return response.status_code == 200 and "errors" not in response.json()

    async def close(self) -> None:
        await self._http.aclose()
