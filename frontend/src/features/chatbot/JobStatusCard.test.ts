import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { DurableJob } from "@/types/api";
import { formatJobDuration } from "@/lib/time";

import { JobStatusCard } from "./JobStatusCard";

const job: DurableJob = {
  id: "job-1",
  conversation_id: "conversation-1",
  type: "chat_answer",
  state: "running",
  step: "search",
  attempt: 1,
  available_at: "2026-07-22T10:00:00Z",
  created_at: "2026-07-22T10:00:00Z",
  updated_at: "2026-07-22T10:00:00Z",
  result_message_id: null,
  error: null,
};

describe("JobStatusCard", () => {
  it("exposes state, step and duration to assistive technology", () => {
    const markup = renderToStaticMarkup(createElement(JobStatusCard, { job }));

    expect(markup).toContain('role="status"');
    expect(markup).toContain('aria-live="polite"');
    expect(markup).toContain("En cours");
    expect(markup).toContain("Recherche locale");
    expect(markup).toContain("Durée");
  });

  it("formats elapsed durations without negative values", () => {
    expect(formatJobDuration(job.created_at, Date.parse("2026-07-22T10:01:05Z"))).toBe(
      "1 min 05 s",
    );
    expect(formatJobDuration(job.created_at, Date.parse("2026-07-22T09:59:00Z"))).toBe("0 s");
  });

  it("shows the durable reranking stage", () => {
    const markup = renderToStaticMarkup(
      createElement(JobStatusCard, { job: { ...job, step: "reranking" } }),
    );

    expect(markup).toContain("Classement et fusion des passages");
  });

  it("distinguishes planning from final answer generation", () => {
    const planning = renderToStaticMarkup(
      createElement(JobStatusCard, { job: { ...job, step: "planning" } }),
    );
    const generation = renderToStaticMarkup(
      createElement(JobStatusCard, { job: { ...job, step: "generation" } }),
    );

    expect(planning).toContain("Analyse et planification de la question");
    expect(planning).not.toContain("Génération de la réponse finale");
    expect(generation).toContain("Génération de la réponse finale");
  });

  it("keeps a failed job visible with its retry action", () => {
    const failedJob: DurableJob = {
      ...job,
      state: "failed",
      error: { code: "validation", message: "Réponse non conforme", retry_at: null },
    };
    const markup = renderToStaticMarkup(
      createElement(JobStatusCard, { job: failedJob, onRetry: async () => undefined }),
    );

    expect(markup).toContain("Réponse non conforme");
    expect(markup).toContain("Relancer");
  });

  it("explains immediate and cooperative cancellation limits", () => {
    const onCancel = async () => undefined;
    const queuedMarkup = renderToStaticMarkup(
      createElement(JobStatusCard, { job: { ...job, state: "queued" }, onCancel }),
    );
    const runningMarkup = renderToStaticMarkup(createElement(JobStatusCard, { job, onCancel }));

    expect(queuedMarkup).toContain("Annuler le travail");
    expect(runningMarkup).toContain("Demander l’annulation");
    expect(runningMarkup).toContain("prochaine étape sûre");
  });

  it("distinguishes the worker queue from an ARGO quota wait", () => {
    const markup = renderToStaticMarkup(
      createElement(JobStatusCard, {
        job: { ...job, state: "queued", step: "waiting" },
      }),
    );

    expect(markup).toContain("File d’attente — créneaux de traitement occupés");
    expect(markup).toContain("sans consommer de requête ARGO pendant l’attente");
    expect(markup).not.toContain("Quota ARGO temporairement atteint");
  });

  it("explains a scheduled complete scientific regeneration", () => {
    const markup = renderToStaticMarkup(
      createElement(JobStatusCard, {
        job: {
          ...job,
          state: "queued",
          error: {
            code: "timeout",
            message: "Une nouvelle génération scientifique complète est planifiée.",
            retry_at: "2026-07-22T14:30:00Z",
          },
        },
      }),
    );

    expect(markup).toContain("Une nouvelle génération scientifique complète est planifiée.");
    expect(markup).not.toContain("sans consommer de requête ARGO");
  });

  it("shows the quota retry time without a sensitive counter", () => {
    const quotaJob: DurableJob = {
      ...job,
      state: "queued",
      error: {
        code: "quota",
        message: "Quota temporairement atteint.",
        retry_at: "2026-07-22T14:30:00Z",
      },
    };
    const markup = renderToStaticMarkup(createElement(JobStatusCard, { job: quotaJob }));

    expect(markup).toContain("Quota ARGO temporairement atteint");
    expect(markup).toContain("Attente du quota ARGO");
    expect(markup).toContain('dateTime="2026-07-22T14:30:00Z"');
    expect(markup).not.toMatch(/requêtes|tokens|utilisées/i);
  });

  it("announces wait, success and failure while keeping actions keyboard-native", () => {
    const onAction = async () => undefined;
    const waitingMarkup = renderToStaticMarkup(
      createElement(JobStatusCard, { job: { ...job, state: "queued" }, onCancel: onAction }),
    );
    const successMarkup = renderToStaticMarkup(
      createElement(JobStatusCard, {
        job: { ...job, state: "succeeded", step: "persistence" },
      }),
    );
    const failureMarkup = renderToStaticMarkup(
      createElement(JobStatusCard, {
        job: {
          ...job,
          state: "failed",
          error: { code: "validation", message: "Réponse non conforme", retry_at: null },
        },
        onRetry: onAction,
      }),
    );

    for (const markup of [waitingMarkup, successMarkup, failureMarkup]) {
      expect(markup).toContain('role="status"');
      expect(markup).toContain('aria-live="polite"');
      expect(markup).not.toContain("autoFocus");
      expect(markup).not.toContain('tabindex="-1"');
    }
    expect(waitingMarkup).toContain('<button type="button"');
    expect(failureMarkup).toContain('<button type="button"');
    expect(successMarkup).toContain("Terminé");
  });
});
