import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "@/lib/api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("API client", () => {
  it("encodes library filters without losing repeated statuses", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ records: [], total: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.library.records({
      query: "polyphénols cidre",
      statuses: ["accepted", "review"],
      theme: "biochimie",
      source: "openalex",
      abstract: "with",
      limit: 25,
      offset: 50,
    });

    const requestedUrl = String(fetchMock.mock.calls[0]?.[0]);
    expect(requestedUrl).toContain("query=polyph%C3%A9nols+cidre");
    expect(requestedUrl).toContain("statuses=accepted%2Creview");
    expect(requestedUrl).toContain("theme=biochimie");
  });

  it("turns backend failures into typed errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Requête invalide" }), {
          status: 422,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(api.system.overview()).rejects.toEqual(new ApiError("Requête invalide", 422));
  });

  it("sends review rejections without a second confirmation payload", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          record_id: "review/id",
          title: "Notice test",
          decision: "rejected",
          deleted: true,
          vectors_deleted: 1,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.library.decideReview("review/id", "rejected");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/library/records/review%2Fid/decision",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ decision: "rejected" }),
      }),
    );
  });

  it("sends the ARGO key only in the local replacement request", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ configured: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.argoKey.save("personal-token");

    expect(result).toEqual({ configured: true });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/argo-key",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ key: "personal-token" }),
      }),
    );
  });

  it("sends exact durable-job enqueue, poll, cancel and retry payloads", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({}), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.jobs.enqueue("conversation/id", {
      message: "Question",
      client_request_id: "request-1",
      use_external_sources: false,
      mode: "quick",
      interaction_mode: "auto",
    });
    await api.jobs.poll("job/id");
    await api.jobs.cancel("job/id");
    await api.jobs.retry("job/id", "request-2");

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/chatbot/conversations/conversation%2Fid/jobs",
      "/api/jobs/job%2Fid",
      "/api/jobs/job%2Fid/cancel",
      "/api/jobs/job%2Fid/retry",
    ]);
    expect(fetchMock.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          message: "Question",
          client_request_id: "request-1",
          use_external_sources: false,
          mode: "quick",
          interaction_mode: "auto",
        }),
      }),
    );
    expect(fetchMock.mock.calls[3]?.[1]).toEqual(
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ client_request_id: "request-2" }),
      }),
    );
  });

  it("keeps common and private corpus operations scoped to their respective routes", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({}), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.corpus.folder("C:\\Articles", true);
    await api.privateCorpus.index(false);
    await api.privateCorpus.reindex("article/id");
    await api.corpus.remove("article/id");

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/corpus/folder",
      "/api/private-corpus/index",
      "/api/private-corpus/article/id/reindex",
      "/api/corpus/article/id",
    ]);
    expect(fetchMock.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ folder: "C:\\Articles", recursive: true }),
      }),
    );
    expect(fetchMock.mock.calls[3]?.[1]).toEqual(expect.objectContaining({ method: "DELETE" }));
  });

  it("uses the same typed error path when a download request fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Export indisponible" }), {
          status: 503,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(api.chatbot.export([], [], "markdown")).rejects.toEqual(
      new ApiError("Export indisponible", 503),
    );
  });
});
