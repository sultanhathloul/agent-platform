import { afterEach, describe, expect, it, vi } from "vitest";
import { GatewayError, deleteMemoryItem, getMemory, setMemoryEnabled } from "./gateway";

const gatewayUrl = "http://gateway.test";
const item = {
  id: "3f2c1b7e-9a4d-4c1e-8b2f-5d6e7a8b9c0d",
  kind: "preference",
  text: "يفضّل المستخدم الإجابات المختصرة.",
  confidence: 0.9,
  created_at: "2026-09-15T10:00:00Z",
};

function reply(status: number, body?: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type FetchCall = [string, RequestInit & { headers: Record<string, string> }];

function stubFetch(response: Response | Error) {
  const mock = response instanceof Error ? vi.fn().mockRejectedValue(response) : vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", mock);
  return () => {
    const call = mock.mock.calls[0];
    if (!call) throw new Error("fetch was not called");
    return call as FetchCall;
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("memory client", () => {
  it("reads the caller's memory from the envelope", async () => {
    const firstCall = stubFetch(reply(200, { request_id: "r", data: { enabled: true, items: [item] } }));

    await expect(getMemory({ gatewayUrl, token: "tok" })).resolves.toEqual({ enabled: true, items: [item] });

    const [url, init] = firstCall();
    expect(url).toBe(`${gatewayUrl}/v1/memory`);
    expect(init.method).toBe("GET");
    expect(init.headers).toMatchObject({ Authorization: "Bearer tok", Accept: "application/json" });
    expect(init.headers).not.toHaveProperty("Content-Type");
  });

  it("deletes an item by id", async () => {
    const firstCall = stubFetch(new Response(null, { status: 204 }));

    await expect(deleteMemoryItem({ gatewayUrl, token: "tok", id: item.id })).resolves.toBeUndefined();

    const [url, init] = firstCall();
    expect(url).toBe(`${gatewayUrl}/v1/memory/${item.id}`);
    expect(init.method).toBe("DELETE");
  });

  it("turns a missing item into memory_not_found", async () => {
    stubFetch(
      reply(404, {
        request_id: "r",
        error: { code: "memory_not_found", message_ar: "غير موجود", message_en: "not found" },
      }),
    );

    const failure = await deleteMemoryItem({ gatewayUrl, token: "tok", id: item.id }).catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(GatewayError);
    expect((failure as GatewayError).status).toBe(404);
    expect((failure as GatewayError).info.code).toBe("memory_not_found");
  });

  it("sends the memory switch as JSON", async () => {
    const firstCall = stubFetch(reply(200, { request_id: "r", data: { enabled: false } }));

    await expect(setMemoryEnabled({ gatewayUrl, token: "tok", enabled: false })).resolves.toEqual({ enabled: false });

    const [url, init] = firstCall();
    expect(url).toBe(`${gatewayUrl}/v1/memory/settings`);
    expect(init.method).toBe("PUT");
    expect(init.body).toBe('{"enabled":false}');
    expect(init.headers).toMatchObject({ "Content-Type": "application/json" });
  });

  it("reports an unreachable gateway as network_error", async () => {
    stubFetch(new TypeError("fetch failed"));

    const failure = await getMemory({ gatewayUrl, token: "tok" }).catch((e: unknown) => e);
    expect((failure as GatewayError).info.code).toBe("network_error");
  });
});
