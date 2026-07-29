import type { ChatbotSource } from "@/types/api";

export function sourceOriginLabel(source: ChatbotSource): string {
  if (source.origin === "external_api") return "API en direct";
  return source.scope === "private" ? "Document privé" : "Corpus commun";
}

export function sourceEvidenceLabel(source: ChatbotSource): string {
  return source.evidence_level === "full_text" ? "Preuve : texte intégral" : "Preuve : abstract";
}
