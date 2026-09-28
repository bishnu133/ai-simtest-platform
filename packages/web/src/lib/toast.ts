/** A small toast queue: short confirmations such as "Saved" or "Downloaded". */
export interface Toast {
  id: number;
  message: string;
  tone: "success" | "error" | "info";
}

let toasts: Toast[] = [];
let next = 1;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

export function toast(message: string, tone: Toast["tone"] = "success") {
  const id = next++;
  toasts = [...toasts.slice(-2), { id, message, tone }];
  emit();
  setTimeout(() => dismissToast(id), tone === "error" ? 6000 : 3500);
}

export function dismissToast(id: number) {
  toasts = toasts.filter((t) => t.id !== id);
  emit();
}

export function subscribeToasts(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function readToasts() {
  return toasts;
}

const EMPTY: Toast[] = [];
export function readNoToasts() {
  return EMPTY;
}
