import { afterEach, describe, expect, it, vi } from "vitest";
import type { ChatTurnRequest, ReportVersionSummary, SkillView, StreamEvent } from "@/lib/api/types";
import { turnsFromHistory } from "@/lib/conversations";
import { GatewayError, createReportView, downloadReport, getReport } from "@/lib/gateway";
import {
  AUTO_OUTPUT,
  CLOSED_PANEL,
  FRAME_SANDBOX,
  KIT_MARKER,
  type ReportAction,
  frameSource,
  kitUrl,
  liveDocument,
  loadOutput,
  newestFirst,
  reportFileName,
  reportPanel,
  saveOutput,
  skillAllowed,
  withOutput,
} from "@/lib/reports";
import { NO_SKILL } from "@/lib/skills";
import { applyEvent, initialAssistant } from "@/lib/stream";
import { renderIn } from "@/test/render";
import { AssistantMessageView } from "./AssistantMessageView";
import { OutputPicker } from "./OutputPicker";
import { ReportPanelView, type ReportPanelViewProps } from "./ReportPanel";
import { SkillPicker } from "./SkillPicker";

const gatewayUrl = "http://gateway.test";
const KIT = kitUrl(gatewayUrl);
const ID = "5b1d7e3a9c2f4e8d";
const BODY: ChatTurnRequest = { conversation_id: "c", message: "س", selected_kbs: [], attachment_ids: [], locale: "ar" };
const SKILLS: SkillView[] = [
  { slug: "fixture_form", name: { ar: "نموذج الاختبار", en: "Test form" }, description: { ar: "وصف", en: "about" } },
];

let seq = 0;
const meta = { request_id: "r", turn_id: "t" };
const start = (version = 1): StreamEvent => ({ ...meta, seq: seq++, type: "artifact_start", data: { id: ID, title: "لوحة التضخم", version } });
const delta = (text: string): StreamEvent => ({ ...meta, seq: seq++, type: "artifact_delta", data: { id: ID, text } });
const stored = (version = 1): StreamEvent => ({
  ...meta,
  seq: seq++,
  type: "artifact_done",
  data: { id: ID, version, size: 2048, title: "لوحة التضخم" },
});
const failure: StreamEvent = {
  ...meta,
  seq: 99,
  type: "error",
  data: { code: "turn_timeout", message_ar: "م", message_en: "m", retryable: true },
};

const run = (events: StreamEvent[], from = CLOSED_PANEL) =>
  events.map((event): ReportAction => ({ type: "event", event })).reduce(reportPanel, from);

function text(html: string): string {
  return html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

function view(extra: Partial<ReportPanelViewProps> = {}): ReportPanelViewProps {
  const noop = () => {};
  return {
    title: "لوحة التضخم",
    building: false,
    source: { kind: "loading" },
    code: null,
    versions: [],
    version: 1,
    tab: "preview",
    error: null,
    copied: false,
    onTab: noop,
    onSelect: noop,
    onDownload: noop,
    onFullscreen: noop,
    onCopy: noop,
    onClose: noop,
    ...extra,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the composer's output picker (D3)", () => {
  it("offers automatic, text and interactive report, automatic by default, in both languages", () => {
    const ar = renderIn("ar", <OutputPicker value={AUTO_OUTPUT} onChange={() => {}} />);
    const options = [...ar.matchAll(/<option[^>]*value="([^"]*)"[^>]*>([^<]*)<\/option>/g)].map((m) => [m[1], m[2]]);
    expect(options).toEqual([["", "تلقائي"], ["text", "نص"], ["report", "تقرير تفاعلي"]]);
    expect(ar).toContain('<option value="" selected="">');
    expect(/[A-Za-z]/.test(text(ar))).toBe(false);
    const en = text(renderIn("en", <OutputPicker value="report" onChange={() => {}} />));
    expect(en).toContain("Interactive report");
    expect(renderIn("en", <OutputPicker value="report" onChange={() => {}} />)).toContain('<option value="report" selected="">');
  });

  it("sends the chosen output, and nothing when the platform decides", () => {
    expect(withOutput(BODY, "report")).toEqual({ ...BODY, output: "report" });
    expect(withOutput(BODY, "text")).toEqual({ ...BODY, output: "text" });
    expect("output" in withOutput(BODY, AUTO_OUTPUT)).toBe(false);
  });

  it("turns the skill picker off while a report is chosen (D1)", () => {
    expect(skillAllowed("report")).toBe(false);
    expect(skillAllowed("text")).toBe(true);
    expect(skillAllowed(AUTO_OUTPUT)).toBe(true);
    const off = renderIn(
      "ar",
      <SkillPicker skills={SKILLS} value={NO_SKILL} onChange={() => {}} disabled={!skillAllowed("report")} disabledHint="لا مهارة" />,
    );
    expect(off).toMatch(/<select[^>]*disabled=""/);
    expect(off).toContain('title="لا مهارة"');
  });

  it("keeps the choice per conversation in this browser", () => {
    const store = new Map<string, string>();
    const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
    saveOutput("c1", "report", storage);
    expect(loadOutput("c1", storage)).toBe("report");
    expect(loadOutput("c2", storage)).toBe(AUTO_OUTPUT);
    store.set("report-output:c3", "pdf");
    expect(loadOutput("c3", storage)).toBe(AUTO_OUTPUT);
    expect(loadOutput("c1", undefined)).toBe(AUTO_OUTPUT);
  });
});

describe("the report panel's live build (I7)", () => {
  const page = `<!doctype html><html dir="rtl"><head>${KIT_MARKER}<title>t</title></head><body><h1>التضخم</h1></body></html>`;

  it("opens on artifact_start and gathers the deltas in order", () => {
    const opened = run([start()]);
    expect(opened).toMatchObject({ open: true, id: ID, title: "لوحة التضخم", version: null });
    const built = run([start(), delta(page.slice(0, 40)), delta(page.slice(40))]);
    expect(built.live?.html).toBe(page);
    // Another report's part is not this page's.
    expect(reportPanel(built, { type: "event", event: { ...meta, seq: seq++, type: "artifact_delta", data: { id: "other", text: "x" } } }).live?.html).toBe(page);
  });

  it("starts over on a page written again, and an error still returns to the version shown before (ADR-0390)", () => {
    const showing = reportPanel(CLOSED_PANEL, { type: "open", ref: { id: ID, title: "لوحة التضخم", version: 1 } });
    const again = run([start(2), delta("<p>في拉斯</p>"), start(2), delta("<p>نص</p>")], showing);
    expect(again.live?.html).toBe("<p>نص</p>");
    expect(run([failure], again)).toMatchObject({ open: true, id: ID, version: 1, live: null });
  });

  it("renders the page as srcdoc with the marker replaced by the CSP and the kit, in order", () => {
    const source = frameSource(run([start(), delta(page)]), null, KIT);
    expect(source.kind).toBe("srcdoc");
    const html = source.kind === "srcdoc" ? source.html : "";
    expect(html).not.toContain(KIT_MARKER);
    expect(html).toContain(
      `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src ${KIT}; style-src ${KIT} 'unsafe-inline'; ` +
        `font-src ${KIT}; img-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'">`,
    );
    const css = html.indexOf(`<link rel="stylesheet" href="${KIT}kit.css">`);
    const chart = html.indexOf(`<script src="${KIT}vendor/echarts.min.js"></script>`);
    const kit = html.indexOf(`<script src="${KIT}kit.js"></script>`);
    expect(css).toBeGreaterThan(html.indexOf("<head>"));
    expect(chart).toBeGreaterThan(css);
    expect(kit).toBeGreaterThan(chart);
    expect(html.indexOf("Content-Security-Policy")).toBeLessThan(html.indexOf("<h1>"));
  });

  it("puts the CSP first, after the doctype, while the marker has not arrived", () => {
    const html = liveDocument("<!doctype html><html><body><img src=x>", KIT);
    expect(html.startsWith('<!doctype html><meta http-equiv="Content-Security-Policy"')).toBe(true);
    expect(liveDocument("<p>", KIT).startsWith("<meta http-equiv")).toBe(true);
  });

  it("frames the page with exactly sandbox=allow-scripts, building indicator on, download off", () => {
    expect(FRAME_SANDBOX).toBe("allow-scripts");
    const source = frameSource(run([start(), delta(page)]), null, KIT);
    const html = renderIn("ar", <ReportPanelView {...view({ building: true, source, version: 1, code: page })} />);
    const frames = [...html.matchAll(/<iframe[^>]*>/g)].map((m) => m[0]);
    expect(frames).toHaveLength(1);
    expect(frames[0]).toContain('sandbox="allow-scripts"');
    expect(frames[0]).toContain("srcDoc=");
    expect(frames[0]).toContain('title="لوحة التضخم"');
    expect(frames[0]).not.toContain("allow-same-origin");
    expect(text(html)).toContain("جارٍ بناء التقرير…");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*aria-label="تنزيل التقرير"/);
  });

  it("shows the stored version through its view link after artifact_done", () => {
    const done = run([start(), delta(page), stored()]);
    expect(done).toMatchObject({ open: true, live: null, version: 1 });
    expect(frameSource(done, null, KIT)).toEqual({ kind: "loading" });
    const link = `${gatewayUrl}/report-views/tk1`;
    const source = frameSource(done, link, KIT);
    expect(source).toEqual({ kind: "src", url: link });
    const html = renderIn("en", <ReportPanelView {...view({ source, code: page })} />);
    const frame = html.match(/<iframe[^>]*>/)![0];
    expect(frame).toContain(`src="${link}"`);
    expect(frame).toContain('sandbox="allow-scripts"');
    expect(frame).toContain('referrerPolicy="no-referrer"');
    expect(frame).not.toContain("srcDoc");
    expect(text(html)).not.toContain("Building the report…");
  });

  it("drops the build when an error comes before artifact_done, back to what was shown", () => {
    expect(run([start(), delta(page), failure])).toMatchObject({ open: false, live: null, version: null });
    // An iteration that fails keeps the version shown before it.
    const first = run([start(), delta(page), stored()]);
    expect(run([start(2), delta("<html>"), failure], first)).toMatchObject({ open: true, live: null, id: ID, version: 1 });
    // A stream that closes without artifact_done stored nothing either.
    expect(reportPanel(run([start(), delta(page)]), { type: "end" })).toMatchObject({ open: false, live: null });
    // After artifact_done, the end of the stream changes nothing.
    expect(reportPanel(first, { type: "end" })).toEqual(first);
  });

  it("shows the code tab as read-only LTR text and every button labelled", () => {
    const html = renderIn("ar", <ReportPanelView {...view({ tab: "code", code: page })} />);
    expect(html).toMatch(/<pre dir="ltr"[^>]*>&lt;!doctype html&gt;/);
    for (const label of ["تنزيل التقرير", "ملء الشاشة", "نسخ الكود", "إغلاق اللوحة"]) expect(html).toContain(`aria-label="${label}"`);
    expect(html).toMatch(/role="tab"[^>]*aria-selected="false"[^>]*>معاينة/);
    expect(html).toMatch(/role="tab"[^>]*aria-selected="true"[^>]*>الكود/);
    expect(text(renderIn("en", <ReportPanelView {...view({ copied: true, code: page })} />))).toContain("Copied");
  });
});

describe("the version switcher (D4)", () => {
  const versions: ReportVersionSummary[] = [
    { version: 1, title: "أ", size: 10, locale: "ar", created_at: "2026-10-27T09:00:00Z", figures: 3, general: 1 },
    { version: 3, title: "أ", size: 10, locale: "ar", created_at: "2026-10-28T11:00:00Z", figures: 3, general: 0 },
    { version: 2, title: "أ", size: 10, locale: "ar", created_at: "2026-10-27T15:00:00Z", figures: 3, general: 0 },
  ];

  it("lists the versions newest first, with their dates in Western digits", () => {
    const html = renderIn("ar", <ReportPanelView {...view({ versions: newestFirst(versions), version: 2 })} />);
    const options = [...html.matchAll(/<option[^>]*value="(\d+)"[^>]*>([^<]*)<\/option>/g)];
    expect(options.map((m) => m[1])).toEqual(["3", "2", "1"]);
    expect(options[0]![2]).toMatch(/^الإصدار 3 · /);
    expect(options.map((m) => m[2]).join("")).not.toMatch(/[٠-٩]/);
    expect(html).toContain('<option value="2" selected="">');
    const en = renderIn("en", <ReportPanelView {...view({ versions: newestFirst(versions), version: 3 })} />);
    expect(en).toMatch(/<option value="3" selected="">Version 3 · /);
  });

  it("switching shows that version", () => {
    const shown = run([start(), stored(3)]);
    expect(reportPanel(shown, { type: "select", version: 1 })).toMatchObject({ id: ID, version: 1, open: true });
    expect(reportPanel(CLOSED_PANEL, { type: "select", version: 1 })).toEqual(CLOSED_PANEL);
  });
});

describe("the report card in the chat", () => {
  const ref = { id: ID, kind: "report" as const, title: "لوحة التضخم", version: 2 };

  it("comes with the reply's done event and keeps the reply's text line", () => {
    const message = applyEvent(applyEvent(initialAssistant(), { ...meta, seq: 1, type: "token", data: { text: "أُنشئ التقرير." } }), {
      ...meta,
      seq: 2,
      type: "done",
      data: { answer: "", usage: {}, state_digest: { turn: 1, capabilities: ["general"], output: "report" }, artifact: ref },
    });
    expect(message.artifact).toEqual(ref);
    const html = renderIn("ar", <AssistantMessageView message={message} />);
    expect(text(html)).toContain("أُنشئ التقرير.");
    expect(html).toContain(`data-report="${ID}"`);
    expect(html).toContain('aria-label="فتح التقرير «لوحة التضخم»"');
    expect(text(html)).toContain("الإصدار 2");
    expect(text(renderIn("en", <AssistantMessageView message={message} />))).toContain("Version 2");
  });

  it("comes from the history, and a text reply shows none", () => {
    const turns = turnsFromHistory([
      { role: "user", text: "س", attachments: [], citations: [] },
      { role: "assistant", text: "أُنشئ التقرير.", turn_id: "t1", attachments: [], citations: [], artifact: ref },
      { role: "user", text: "س٢", attachments: [], citations: [] },
      { role: "assistant", text: "ج", turn_id: "t2", attachments: [], citations: [] },
    ]);
    expect(turns[0]!.answer.artifact).toEqual(ref);
    expect(turns[1]!.answer.artifact).toBeUndefined();
    expect(renderIn("ar", <AssistantMessageView message={turns[1]!.answer} />)).not.toContain("data-report");
  });

  it("reopens the panel at its version, which stays closed until then", () => {
    expect(CLOSED_PANEL.open).toBe(false);
    expect(reportPanel(CLOSED_PANEL, { type: "open", ref })).toMatchObject({ open: true, id: ID, version: 2, title: "لوحة التضخم" });
    expect(reportPanel(reportPanel(CLOSED_PANEL, { type: "open", ref }), { type: "close" }).open).toBe(false);
  });
});

describe("the report calls (I6)", () => {
  function stubFetch(response: Response) {
    const mock = vi.fn().mockResolvedValue(response);
    vi.stubGlobal("fetch", mock);
    return () => mock.mock.calls[0] as [string, RequestInit & { headers: Record<string, string> }];
  }
  const json = (status: number, body: unknown) =>
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

  it("asks for a view link with the token, by POST", async () => {
    const call = stubFetch(json(200, { request_id: "r", data: { url: `${gatewayUrl}/report-views/tk`, expires_at: "x" } }));
    await expect(createReportView({ gatewayUrl, token: "tok", id: ID, version: 2 })).resolves.toMatchObject({ url: `${gatewayUrl}/report-views/tk` });
    const [url, init] = call();
    expect(url).toBe(`${gatewayUrl}/v1/reports/${ID}/versions/2/view`);
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({ Authorization: "Bearer tok" });
  });

  it("downloads the file with the token, named report-<id8>-v<n>.html", async () => {
    const call = stubFetch(new Response("<!doctype html>", { status: 200, headers: { "Content-Type": "text/html" } }));
    const blob = await downloadReport({ gatewayUrl, token: "tok", id: ID, version: 2 });
    expect(await blob.text()).toBe("<!doctype html>");
    expect(call()[0]).toBe(`${gatewayUrl}/v1/reports/${ID}/versions/2/download`);
    expect(call()[1].headers).toMatchObject({ Authorization: "Bearer tok" });
    expect(reportFileName(ID, 2)).toBe("report-5b1d7e3a-v2.html");
  });

  it("shows report_unavailable and report_not_found as the envelope's message, never the code", async () => {
    stubFetch(json(503, { request_id: "r", error: { code: "report_unavailable", message_ar: "التقارير غير متاحة.", message_en: "Reports are unavailable." } }));
    const error = await getReport({ gatewayUrl, token: "tok", id: ID }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(GatewayError);
    const info = (error as GatewayError).info;
    const ar = text(renderIn("ar", <ReportPanelView {...view({ error: info })} />));
    expect(ar).toContain("التقارير غير متاحة.");
    expect(ar).not.toContain("report_unavailable");
    expect(text(renderIn("en", <ReportPanelView {...view({ error: info })} />))).toContain("Reports are unavailable.");
    // Without the envelope's text, the catalog's.
    expect(text(renderIn("ar", <ReportPanelView {...view({ error: { code: "report_not_found" } })} />))).toContain("لم يُعثر على التقرير.");
  });
});
