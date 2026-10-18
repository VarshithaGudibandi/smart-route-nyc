import type { components } from "./api-types";

type Schemas = components["schemas"];

export type Route = Schemas["Route"];
export type Leg = Schemas["Leg"];
export type Finding = Schemas["AccessibilityFinding"];
export type Notice = Schemas["Notice"];
export type DataFreshness = Schemas["DataFreshness"];
export type SearchRequest = Schemas["RouteSearchRequest"];
export type SearchResponse = Schemas["RouteSearchResponse"];
export type InvestigationView = Schemas["InvestigationView"];
export type Suggestion = Schemas["VerifiedSuggestion"];
export type PlaceResult = Schemas["PlaceResult"];
export type PlacesResponse = Schemas["PlacesResponse"];
export type Explanation = Schemas["ExplanationResponse"];
export type InterpretResponse = Schemas["InterpretResponse"];
export type PreferenceState = Schemas["PreferenceState"];
export type FeedbackResponse = Schemas["FeedbackResponse"];
export type AccessibilityStatus = Route["accessibility_status"];
