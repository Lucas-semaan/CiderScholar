export { formatJobDuration } from "@/lib/time";

export function formatRetryTime(retryAt: string): string {
  return new Intl.DateTimeFormat("fr-FR", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(retryAt));
}
