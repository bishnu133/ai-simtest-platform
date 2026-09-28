/**
 * Light, dark or the system's choice, per browser. A convenience only:
 * unreadable storage means the system's choice.
 */
export type ThemeChoice = "light" | "dark" | "system";

const KEY = "simtest.theme";
const EVENT = "simtest-theme";

export function readTheme(): ThemeChoice {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

export function subscribeTheme(onChange: () => void) {
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const update = () => {
    applyTheme(readTheme());
    onChange();
  };
  window.addEventListener(EVENT, update);
  media.addEventListener("change", update);
  return () => {
    window.removeEventListener(EVENT, update);
    media.removeEventListener("change", update);
  };
}

export function applyTheme(choice: ThemeChoice) {
  const dark = choice === "dark" || (choice === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
  document.documentElement.style.colorScheme = dark ? "dark" : "light";
}

export function setTheme(choice: ThemeChoice) {
  try {
    if (choice === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, choice);
  } catch {
    /* this visit only */
  }
  applyTheme(choice);
  window.dispatchEvent(new Event(EVENT));
}

/** Runs before the page paints, so a dark theme never flashes light. */
export const THEME_BOOT_SCRIPT = `(function(){try{var t=localStorage.getItem("${KEY}");var d=t==="dark"||(t!=="light"&&window.matchMedia("(prefers-color-scheme: dark)").matches);var e=document.documentElement;if(d)e.classList.add("dark");e.style.colorScheme=d?"dark":"light";}catch(_){}})();`;
