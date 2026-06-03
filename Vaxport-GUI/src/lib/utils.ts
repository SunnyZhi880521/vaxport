import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge TailwindCSS classes with clsx */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Generate unique ID */
export function uid(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

/** Format timestamp to HH:MM */
export function formatTime(ts: number): string {
  const d = new Date(ts);
  return d.getHours().toString().padStart(2, "0") + ":" +
    d.getMinutes().toString().padStart(2, "0");
}

/** Truncate string with ellipsis */
export function truncate(str: string, max: number): string {
  return str.length > max ? str.slice(0, max - 1) + "…" : str;
}

/** Format token count: <1000 full, >=1000 as k (25000→25k, 3500→3.5k) */
export function formatTokens(n: number): string {
  if (n < 1000) return n.toString();
  const k = n / 1000;
  return k % 1 === 0 ? `${k}k` : `${k.toFixed(1)}k`;
}
