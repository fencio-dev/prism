import { clsx } from "clsx";
import { twMerge } from "tailwind-merge"

export function cn(...inputs) {
  return twMerge(clsx(inputs));
}

export function truncatePolicyName(name, maxLength = 120) {
  if (!name) return name;
  return name.length > maxLength ? name.slice(0, maxLength) + '…' : name;
}
