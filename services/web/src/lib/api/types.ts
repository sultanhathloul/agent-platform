// Thin re-exports of generated API types (README §6.3). Never hand-write API
// types here; regenerate with `npm run gen:api` when the specs change.
import type { components } from "./gateway";

export type {
  AgentEvent as StreamEvent,
  Citation,
  DoneData,
  ErrorData,
  GuardData,
  TierData,
  ToolEndData,
  ToolStartData,
  Usage,
} from "./events";

type Schemas = components["schemas"];

export type ChatTurnRequest = Schemas["ChatTurnRequest"];
export type ApiError = Schemas["ApiError"];
export type ApiErrorEnvelope = Schemas["ApiErrorEnvelope"];
export type MemoryList = Schemas["MemoryList"];
export type MemoryItem = Schemas["MemoryItem"];
export type MemorySettings = Schemas["MemorySettings"];
