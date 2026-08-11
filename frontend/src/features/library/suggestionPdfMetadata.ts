export type SuggestionPdfMetadata = {
  title?: string;
  doi?: string;
};

export function mergeSuggestionPdfMetadata<T extends Record<string, string>>(
  previous: T,
  metadata: Partial<T>,
  modifiedFields: ReadonlySet<keyof T>,
): T {
  const next = { ...previous };
  (Object.keys(metadata) as Array<keyof T>).forEach((field) => {
    const value = metadata[field];
    if (value && !modifiedFields.has(field)) next[field] = value;
  });
  return next;
}

const METADATA_WINDOW_BYTES = 1024 * 1024;

function cleanMetadataValue(value: string): string | undefined {
  const cleaned = value
    .replace(/\\([()\\])/g, "$1")
    .replace(/\\[nrt]/g, " ")
    .split("")
    .map((character) => {
      const code = character.charCodeAt(0);
      return code < 32 || (code >= 127 && code <= 159) ? " " : character;
    })
    .join("")
    .replace(/\s+/g, " ")
    .trim();
  return cleaned && /^[\x20-\x7EÀ-ÿ]+$/.test(cleaned) ? cleaned : undefined;
}

function readTitle(source: string): string | undefined {
  const match = /\/Title\s*\(((?:\\.|[^\\)])*)\)/.exec(source);
  return match?.[1] ? cleanMetadataValue(match[1]) : undefined;
}

function readDoi(source: string): string | undefined {
  const match = /\b10\.\d{4,9}\/[\w.()/:;-]+/i.exec(source);
  let value = match?.[0]?.replace(/[.,;:]+$/, "");
  if (!value) return undefined;
  while (
    value.endsWith(")") &&
    [...value].filter((character) => character === ")").length >
      [...value].filter((character) => character === "(").length
  ) {
    value = value.slice(0, -1);
  }
  return value.toLowerCase();
}

/** Reads only printable, uncompressed PDF metadata in the browser; no file content leaves the device. */
export async function extractSuggestionPdfMetadata(file: File): Promise<SuggestionPdfMetadata> {
  const endStart = Math.max(METADATA_WINDOW_BYTES, file.size - METADATA_WINDOW_BYTES);
  const [firstBytes, lastBytes] = await Promise.all([
    file.slice(0, METADATA_WINDOW_BYTES).arrayBuffer(),
    file.slice(endStart).arrayBuffer(),
  ]);
  const decoder = new TextDecoder("windows-1252");
  const source = `${decoder.decode(firstBytes)}\n${decoder.decode(lastBytes)}`;
  const title = readTitle(source);
  const doi = readDoi(source);
  return { ...(title ? { title } : {}), ...(doi ? { doi } : {}) };
}
