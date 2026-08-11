export function formatJobDuration(createdAt: string, nowMilliseconds: number): string {
  const elapsedSeconds = Math.max(
    0,
    Math.floor((nowMilliseconds - new Date(createdAt).getTime()) / 1000),
  );
  const minutes = Math.floor(elapsedSeconds / 60);
  const seconds = elapsedSeconds % 60;
  return minutes > 0 ? `${minutes} min ${seconds.toString().padStart(2, "0")} s` : `${seconds} s`;
}

export function formatRetryTime(retryAt: string): string {
  return new Intl.DateTimeFormat("fr-FR", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(retryAt));
}
