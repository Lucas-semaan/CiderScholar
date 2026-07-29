const MAXIMUM_PDF_BYTES = 25 * 1024 * 1024;

export async function validateSuggestionPdf(file: File): Promise<string | null> {
  if (!file.name.toLocaleLowerCase().endsWith(".pdf")) return "Choisissez un fichier .pdf.";
  if (file.size > MAXIMUM_PDF_BYTES) return "Le PDF dépasse la limite de 25 Mo.";
  const signature = new TextDecoder().decode(await file.slice(0, 5).arrayBuffer());
  return signature === "%PDF-" ? null : "Le fichier ne possède pas une signature PDF valide.";
}
