import { useRef, useState } from "react";

import { ChevronDown, FileUp, Send, ShieldCheck } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Field, Input, Select, Textarea } from "@/components/ui/Form";
import {
  extractSuggestionPdfMetadata,
  mergeSuggestionPdfMetadata,
} from "@/features/library/suggestionPdfMetadata";
import { validateSuggestionPdf } from "@/features/library/suggestionPdfValidation";
import { api } from "@/lib/api";
import type { SuggestionReferenceSource, SuggestionSubmissionResult } from "@/types/api";

type SuggestionKind = "doi" | "url" | "pdf" | "manual";
type PdfMetadataField = "title" | "doi" | "abstract";
type PdfMetadataValues = Record<PdfMetadataField, string>;

const emptyPdfMetadata: PdfMetadataValues = { title: "", doi: "", abstract: "" };

function optional(value: FormDataEntryValue | null): string | undefined {
  const cleaned = String(value ?? "").trim();
  return cleaned || undefined;
}

function referenceSource(kind: Exclude<SuggestionKind, "pdf">, data: FormData) {
  const title = optional(data.get("title"));
  const abstract = optional(data.get("abstract"));
  if (kind === "doi") {
    return {
      kind,
      doi: String(data.get("doi") ?? ""),
      ...(title ? { title } : {}),
      ...(abstract ? { abstract } : {}),
    } as const;
  }
  if (kind === "url") {
    return {
      kind,
      url: String(data.get("url") ?? ""),
      ...(title ? { title } : {}),
      ...(abstract ? { abstract } : {}),
    } as const;
  }
  return {
    kind,
    title: String(data.get("title") ?? ""),
    reference: String(data.get("reference") ?? ""),
    ...(optional(data.get("doi")) ? { doi: optional(data.get("doi")) as string } : {}),
    ...(abstract ? { abstract } : {}),
  } as const;
}

export function SuggestionForm() {
  const [expanded, setExpanded] = useState(false);
  const [kind, setKind] = useState<SuggestionKind>("doi");
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [pdfMetadata, setPdfMetadata] = useState<PdfMetadataValues>(emptyPdfMetadata);
  const modifiedPdfFields = useRef<Set<PdfMetadataField>>(new Set());
  const metadataReadId = useRef(0);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SuggestionSubmissionResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const chooseFile = async (candidate: File | null) => {
    const readId = ++metadataReadId.current;
    setResult(null);
    if (!candidate) {
      setFile(null);
      setFileError(null);
      setPdfMetadata(emptyPdfMetadata);
      modifiedPdfFields.current = new Set();
      return;
    }
    const validation = await validateSuggestionPdf(candidate);
    if (readId !== metadataReadId.current) return;
    setFileError(validation);
    if (validation) {
      setFile(null);
      setPdfMetadata(emptyPdfMetadata);
      modifiedPdfFields.current = new Set();
      return;
    }
    setFile(candidate);
    setPdfMetadata(emptyPdfMetadata);
    modifiedPdfFields.current = new Set();
    try {
      const metadata = await extractSuggestionPdfMetadata(candidate);
      if (readId !== metadataReadId.current) return;
      setPdfMetadata((previous) =>
        mergeSuggestionPdfMetadata(previous, metadata, modifiedPdfFields.current),
      );
    } catch {
      if (readId === metadataReadId.current) {
        setFileError(
          "Le PDF est valide, mais ses métadonnées n’ont pas pu être lues automatiquement.",
        );
      }
    }
  };

  const setPdfMetadataField = (field: PdfMetadataField, value: string) => {
    modifiedPdfFields.current = new Set(modifiedPdfFields.current).add(field);
    setPdfMetadata((previous) => ({ ...previous, [field]: value }));
  };

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const comment = String(data.get("scientific_comment") ?? "");
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const response =
        kind === "pdf"
          ? file
            ? await api.suggestions.submitPdf(
                file,
                comment,
                data.get("transmit_pdf_confirmed") === "on",
                {
                  title: optional(data.get("title")),
                  doi: optional(data.get("doi")),
                  abstract: optional(data.get("abstract")),
                },
              )
            : null
          : await api.suggestions.submitReference(
              referenceSource(kind, data) satisfies SuggestionReferenceSource,
              comment,
            );
      if (!response) {
        setFileError("Choisissez un PDF valide avant l’envoi.");
        return;
      }
      setResult(response);
      if (response.state === "accepted") {
        metadataReadId.current += 1;
        form.reset();
        setFile(null);
        setFileError(null);
        setPdfMetadata(emptyPdfMetadata);
        modifiedPdfFields.current = new Set();
      }
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : "La suggestion n’a pas pu être évaluée.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <button
          aria-controls="suggestion-form-panel"
          aria-expanded={expanded}
          className="-m-2 flex w-[calc(100%+1rem)] items-start justify-between gap-3 rounded-lg p-2 text-left focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-forest-600"
          onClick={() => setExpanded((previous) => !previous)}
          type="button"
        >
          <div>
            <h2 className="font-bold text-slate-900">Proposer un document scientifique</h2>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              Évaluation immédiate par ARGO, puis transmission à l’administrateur si elle est
              retenue.
            </p>
          </div>
          <ChevronDown
            aria-hidden="true"
            className={`mt-0.5 size-5 shrink-0 text-slate-500 transition-transform ${expanded ? "rotate-180" : ""}`}
          />
        </button>
      </CardHeader>
      {expanded && (
        <CardBody id="suggestion-form-panel">
          <form className="grid gap-5" onSubmit={(event) => void submit(event)}>
            <Field label="Type de proposition">
              <Select
                onChange={(event) => {
                  setKind(event.target.value as SuggestionKind);
                  setResult(null);
                  setError(null);
                }}
                value={kind}
              >
                <option value="doi">DOI</option>
                <option value="url">URL HTTPS</option>
                <option value="pdf">Fichier PDF</option>
                <option value="manual">Référence manuelle</option>
              </Select>
            </Field>

            <div className="grid gap-4 md:grid-cols-2">
              {kind === "doi" && (
                <Field label="DOI">
                  <Input name="doi" placeholder="10.1000/article" required />
                </Field>
              )}
              {kind === "url" && (
                <Field label="URL HTTPS">
                  <Input name="url" placeholder="https://…" required type="url" />
                </Field>
              )}
              {kind !== "pdf" && (
                <Field label={kind === "manual" ? "Titre" : "Titre (facultatif)"}>
                  <Input maxLength={500} name="title" required={kind === "manual"} />
                </Field>
              )}
              {kind === "manual" && (
                <>
                  <Field label="Référence complète">
                    <Textarea maxLength={2000} name="reference" required />
                  </Field>
                  <Field label="DOI (facultatif)">
                    <Input name="doi" />
                  </Field>
                </>
              )}
              {kind !== "pdf" && (
                <Field className="md:col-span-2" label="Abstract (facultatif)">
                  <Textarea maxLength={4000} name="abstract" />
                </Field>
              )}
            </div>

            {kind === "pdf" && (
              <div className="space-y-3">
                <label
                  className="flex min-h-32 cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed border-slate-300 bg-slate-50 px-5 text-center focus-within:border-forest-600"
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={(event) => {
                    event.preventDefault();
                    void chooseFile(event.dataTransfer.files[0] ?? null);
                  }}
                >
                  <FileUp className="mb-2 size-6 text-forest-600" />
                  <span className="text-sm font-semibold text-slate-800">
                    {file?.name ?? "Déposer un PDF ou parcourir"}
                  </span>
                  <span className="mt-1 text-xs text-slate-500">PDF signé, 25 Mo maximum</span>
                  <input
                    accept="application/pdf,.pdf"
                    className="sr-only"
                    onChange={(event) => void chooseFile(event.target.files?.[0] ?? null)}
                    type="file"
                  />
                </label>
                {fileError && <p className="text-sm font-semibold text-red-700">{fileError}</p>}
                <div className="grid gap-4 md:grid-cols-2">
                  <Field label="Titre (facultatif)">
                    <Input
                      maxLength={500}
                      name="title"
                      onChange={(event) => setPdfMetadataField("title", event.target.value)}
                      value={pdfMetadata.title}
                    />
                  </Field>
                  <Field label="DOI (facultatif)">
                    <Input
                      name="doi"
                      onChange={(event) => setPdfMetadataField("doi", event.target.value)}
                      value={pdfMetadata.doi}
                    />
                  </Field>
                  <Field className="md:col-span-2" label="Abstract (facultatif)">
                    <Textarea
                      maxLength={4000}
                      name="abstract"
                      onChange={(event) => setPdfMetadataField("abstract", event.target.value)}
                      value={pdfMetadata.abstract}
                    />
                  </Field>
                </div>
                <label className="flex items-start gap-3 text-sm leading-5 text-slate-700">
                  <input
                    className="mt-1 size-4"
                    name="transmit_pdf_confirmed"
                    required
                    type="checkbox"
                  />
                  Je confirme disposer du droit de transmettre ce PDF à l’espace SharePoint protégé.
                </label>
              </div>
            )}

            <Field
              hint="Ce texte est traité comme une donnée non fiable, jamais comme une instruction."
              label="Commentaire scientifique (facultatif)"
            >
              <Textarea maxLength={1500} name="scientific_comment" />
            </Field>

            {(result || error) && (
              <div
                className={`rounded-xl border px-4 py-3 text-sm ${
                  result?.state === "accepted"
                    ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                    : "border-amber-200 bg-amber-50 text-amber-800"
                }`}
                role="status"
              >
                {error ?? result?.message}
                {result?.action === "settings" && (
                  <a className="ml-2 font-bold underline" href="/parametres">
                    Ouvrir Paramètres
                  </a>
                )}
              </div>
            )}
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="flex items-center gap-2 text-xs text-slate-500">
                <ShieldCheck className="size-4" /> Aucun PDF complet n’est envoyé à ARGO.
              </span>
              <Button loading={busy} type="submit">
                <Send className="size-4" /> Évaluer et proposer
              </Button>
            </div>
          </form>
        </CardBody>
      )}
    </Card>
  );
}
