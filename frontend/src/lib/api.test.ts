import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "@/lib/api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("API client", () => {
  it("builds an encoded local PDF URL from an article id", () => {
    expect(api.corpus.pdfUrl("article/id")).toBe("/api/corpus/article%2Fid/pdf");
  });

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
      availability: "metadata_only",
      limit: 25,
      offset: 50,
    });

    const requestedUrl = String(fetchMock.mock.calls[0]?.[0]);
    expect(requestedUrl).toContain("query=polyph%C3%A9nols+cidre");
    expect(requestedUrl).toContain("statuses=accepted%2Creview");
    expect(requestedUrl).toContain("theme=biochimie");
    expect(requestedUrl).toContain("availability=metadata_only");
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

  it("extracts stable messages from structured backend errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: { code: "invalid_correction", message: "Correction invalide" },
          }),
          {
            status: 422,
            headers: { "Content-Type": "application/json" },
          },
        ),
      ),
    );

    await expect(api.system.overview()).rejects.toEqual(new ApiError("Correction invalide", 422));
  });

  it("loads local runtime diagnostics from the dedicated system route", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ active_jobs: 0, worker: {}, process: {}, warnings: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.system.diagnostics();

    expect(fetchMock).toHaveBeenCalledWith("/api/system/diagnostics", expect.anything());
  });

  it("loads expert-memory release history with a resumable cursor", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          releases: [],
          active_release_id: null,
          active_generation: 0,
          next_cursor: null,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.expertMemory.releases("2026-09-24T12:00:00+00:00|release/id", 10);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/expert-memory/releases?limit=10&cursor=2026-09-24T12%3A00%3A00%2B00%3A00%7Crelease%2Fid",
      expect.anything(),
    );
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

  it("submits private expert corrections through the chatbot route", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "correction-1", revision: 1 }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.chatbot.submitExpertCorrection("message/id", {
      client_request_id: "request-1",
      selected_text: "Texte cité",
      problem: "Une distinction méthodologique est absente.",
      proposed_correction: "Ajouter cette distinction dans la réponse.",
      scope: "this_answer",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/chatbot/messages/message%2Fid/expert-corrections",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          client_request_id: "request-1",
          selected_text: "Texte cité",
          problem: "Une distinction méthodologique est absente.",
          proposed_correction: "Ajouter cette distinction dans la réponse.",
          scope: "this_answer",
        }),
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

  it("uses neutral provider routes without exposing a key in reads or activation", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({ active_provider: "custom", providers: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.llmProviders.list();
    await api.llmProviders.save("custom", {
      base_url: "https://llm.example.test/v1",
      key: "personal-token",
      model: "my-model",
    });
    await api.llmProviders.activate("custom");

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/llm-providers",
      "/api/llm-providers/custom",
      "/api/llm-providers/active",
    ]);
    expect(fetchMock.mock.calls[1]?.[1]).toEqual(
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({
          base_url: "https://llm.example.test/v1",
          key: "personal-token",
          model: "my-model",
        }),
      }),
    );
    expect(fetchMock.mock.calls[2]?.[1]).toEqual(
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ provider: "custom" }) }),
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
      analyze_figures: true,
      mode: "quick",
      interaction_mode: "auto",
      answer_effort: "balanced",
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
          analyze_figures: true,
          mode: "quick",
          interaction_mode: "auto",
          answer_effort: "balanced",
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

  it("submits evaluation cells through the conversation-isolating endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({}), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.jobs.enqueueEvaluation({
      message: "Question immuable",
      client_request_id: "request-1",
      run_id: "run-1",
      question_id: "Q1",
      profile: "p0",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/chatbot/evaluation/jobs",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          message: "Question immuable",
          client_request_id: "request-1",
          run_id: "run-1",
          question_id: "Q1",
          profile: "p0",
        }),
      }),
    );
  });

  it("sends corpus operations to the common corpus routes", async () => {
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
    await api.corpus.index(false);
    await api.corpus.reindex("article/id");
    await api.corpus.remove("article/id");

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/corpus/folder",
      "/api/corpus/index",
      "/api/corpus/article/id/reindex",
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
