import type { BadgeTone } from "@/components/ui/Badge";

export function statusTone(status: string): BadgeTone {
  if (["accepted", "indexed", "completed", "chunks_ready", "validated"].includes(status)) {
    return "success";
  }
  if (["review", "pending", "processing", "ocr_required"].includes(status)) return "warning";
  if (["rejected", "failed"].includes(status)) return "danger";
  return "neutral";
}
