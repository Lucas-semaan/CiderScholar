import { useEffect, useState } from "react";

import { Timer } from "lucide-react";

import { formatResponseTime } from "./chatSession";

export function ResponseTimer({ startedAt }: { startedAt: number }) {
  const [elapsedMilliseconds, setElapsedMilliseconds] = useState(() =>
    Math.max(0, performance.now() - startedAt),
  );

  useEffect(() => {
    const timerId = window.setInterval(() => {
      setElapsedMilliseconds(performance.now() - startedAt);
    }, 100);

    return () => window.clearInterval(timerId);
  }, [startedAt]);

  return (
    <p
      aria-live="off"
      className="mt-1.5 flex items-center gap-1.5 text-xs font-semibold tabular-nums text-forest-700"
      role="timer"
    >
      <Timer aria-hidden="true" className="size-3.5" />
      Temps écoulé : {formatResponseTime(elapsedMilliseconds)}
    </p>
  );
}
