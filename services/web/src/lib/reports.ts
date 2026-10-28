// Interactive reports in the web (reports PR, I7): pure, so it is tested without a browser.
// The composer's output choice (D3), kept per conversation in this browser like the KB toggles; the
// side panel's state as a reducer over the streamed artifact events; the live page with the
// platform's kit and its CSP (I4); the names and labels the panel shows, in Western digits (ADR-0288).
import type {
  ArtifactRef,
  ChatTurnRequest,
  ReportOutput,
  ReportVersionSummary,
  StreamEvent,
} from "@/lib/api/types";

/** Nothing picked: the platform decides (a report keyword, or a conversation that holds a report). */
export const AUTO_OUTPUT = "";

export type OutputChoice = typeof AUTO_OUTPUT | ReportOutput;

const OUTPUTS: readonly ReportOutput[] = ["text", "report"];

const STORAGE_PREFIX = "report-output:";

type Store = Pick<Storage, "getItem" | "setItem">;

export function isOutput(value: string): value is ReportOutput {
  return (OUTPUTS as readonly string[]).includes(value);
}

/** A conversation's output choice as this browser last kept it. */
export function loadOutput(conversationId: string, store: Store | undefined): OutputChoice {
  try {
    const raw = store?.getItem(STORAGE_PREFIX + conversationId) ?? "";
    return isOutput(raw) ? raw : AUTO_OUTPUT;
  } catch {
    return AUTO_OUTPUT;
  }
}

export function saveOutput(conversationId: string, output: OutputChoice, store: Store | undefined): void {
  try {
    store?.setItem(STORAGE_PREFIX + conversationId, output);
  } catch {
    // a browser without storage keeps the choice for this page only
  }
}

/** The turn's body with the chosen output, or without the field when the platform decides. */
export function withOutput(body: ChatTurnRequest, output: OutputChoice): ChatTurnRequest {
  return isOutput(output) ? { ...body, output } : body;
}

/** A report applies no skill (D1): the skill picker is off while a report is chosen. */
export function skillAllowed(output: OutputChoice): boolean {
  return output !== "report";
}

// ------------------------------------------------------------------ the side panel

/** The page being written: its HTML so far, already sanitized by the platform. */
export type LiveBuild = { id: string; version: number | null; html: string };

export type ReportPanelState = {
  open: boolean;
  /** The report shown, the same across its versions (D4); null before any. */
  id: string | null;
  title: string;
  /** The stored version shown; null while the first version is written. */
  version: number | null;
  live: LiveBuild | null;
  /** What the panel showed before the build began, restored when the build fails. */
  before: { id: string | null; title: string; version: number | null; open: boolean } | null;
};

export type ReportAction =
  | { type: "event"; event: StreamEvent }
  /** The turn's stream closed, however it ended: a build without artifact_done stored nothing. */
  | { type: "end" }
  | { type: "open"; ref: ArtifactRef }
  | { type: "select"; version: number }
  | { type: "close" };

export const CLOSED_PANEL: ReportPanelState = { open: false, id: null, title: "", version: null, live: null, before: null };

function dropBuild(state: ReportPanelState): ReportPanelState {
  if (!state.live) return state;
  const before = state.before ?? { id: null, title: "", version: null, open: false };
  // Nothing was stored: back to what was shown, or closed when nothing was.
  return { ...state, ...before, open: before.open && before.version !== null, live: null, before: null };
}

export function reportPanel(state: ReportPanelState, action: ReportAction): ReportPanelState {
  switch (action.type) {
    case "open":
      return { ...state, open: true, id: action.ref.id, title: action.ref.title, version: action.ref.version };
    case "select":
      return state.id ? { ...state, version: action.version } : state;
    case "close":
      return { ...state, open: false };
    case "end":
      return dropBuild(state);
    case "event":
      break;
  }
  const event = action.event;
  switch (event.type) {
    case "artifact_start":
      // The panel opens on its own when a page begins (I7).
      return {
        open: true,
        id: event.data.id,
        title: event.data.title,
        version: null,
        live: { id: event.data.id, version: event.data.version ?? null, html: "" },
        before: { id: state.id, title: state.title, version: state.version, open: state.open },
      };
    case "artifact_delta":
      if (!state.live || state.live.id !== event.data.id) return state;
      return { ...state, live: { ...state.live, html: state.live.html + event.data.text } };
    case "artifact_done":
      if (!state.live || state.live.id !== event.data.id) return state;
      // From now on the stored version is shown; it may differ from the streamed parts.
      return { ...state, title: event.data.title || state.title, version: event.data.version, live: null, before: null };
    case "error":
      // An error before artifact_done: no version was stored.
      return dropBuild(state);
    default:
      return state;
  }
}

// ------------------------------------------------------------------ the page in the panel (I4)

/** Where the page asks for its kit to be placed. */
export const KIT_MARKER = "<!--report-kit-->";

/** The platform's design kit, served by the gateway without a token (I2). */
export function kitUrl(gatewayUrl: string): string {
  return `${gatewayUrl}/report-kit/v1/`;
}

const attr = (value: string) => value.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");

/** The CSP, the kit's stylesheet and its two scripts (Chart.js first), as the served view has them. */
export function kitBlock(kit: string): string {
  const k = attr(kit);
  const csp =
    `default-src 'none'; script-src ${k}; style-src ${k} 'unsafe-inline'; font-src ${k}; img-src data:; ` +
    `connect-src 'none'; form-action 'none'; base-uri 'none'`;
  return (
    `<meta http-equiv="Content-Security-Policy" content="${csp}">` +
    `<link rel="stylesheet" href="${k}kit.css">` +
    `<script src="${k}vendor/chart.umd.min.js"></script>` +
    `<script src="${k}kit.js"></script>`
  );
}

/**
 * The live page as the sandboxed frame's srcdoc: the marker becomes the kit with its CSP. A page
 * whose marker has not arrived yet still gets the CSP first, after its doctype, so nothing in it
 * runs or loads unchecked.
 */
export function liveDocument(html: string, kit: string): string {
  const block = kitBlock(kit);
  const at = html.indexOf(KIT_MARKER);
  if (at !== -1) return html.slice(0, at) + block + html.slice(at + KIT_MARKER.length);
  const doctype = /^\s*<!doctype[^>]*>/i.exec(html);
  const head = doctype ? doctype[0] : "";
  return head + block + html.slice(head.length);
}

/** The frame's sandbox: scripts for the kit's charts, never the parent's origin (I4). */
export const FRAME_SANDBOX = "allow-scripts";

/** What the preview frame loads: the live page while it streams, then the stored version's view link. */
export type FrameSource = { kind: "srcdoc"; html: string } | { kind: "src"; url: string } | { kind: "loading" };

export function frameSource(state: ReportPanelState, viewUrl: string | null, kit: string): FrameSource {
  if (state.live) return { kind: "srcdoc", html: liveDocument(state.live.html, kit) };
  if (viewUrl) return { kind: "src", url: viewUrl };
  return { kind: "loading" };
}

/** The downloaded file's name: report-<first 8 of the id>-v<n>.html. */
export function reportFileName(id: string, version: number): string {
  return `report-${id.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 8)}-v${version}.html`;
}

/** The versions for the switcher, newest first. */
export function newestFirst(versions: readonly ReportVersionSummary[]): ReportVersionSummary[] {
  return [...versions].sort((a, b) => b.version - a.version);
}
