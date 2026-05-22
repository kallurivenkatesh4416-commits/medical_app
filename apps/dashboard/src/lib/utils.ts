import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** shadcn/ui's canonical class merger — Tailwind classes deduped at runtime. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
