import type { ChatbotResponse, ChatJobTerminalNotice } from "@/types/api";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  response?: ChatbotResponse;
  terminalNotice?: ChatJobTerminalNotice;
  responseTimeMilliseconds?: number;
  helpful?: boolean | null;
}

export function formatResponseTime(durationMilliseconds: number): string {
  const safeDuration = Number.isFinite(durationMilliseconds)
    ? Math.max(0, durationMilliseconds)
    : 0;

  return `${(safeDuration / 1000).toFixed(1).replace(".", ",")} s`;
}
