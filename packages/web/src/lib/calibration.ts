/**
 * The quality pass mark a reviewer chose in Judge review, carried to the
 * setup form for the next run. Per browser only: a convenience, not a record
 * (the engine records the pass mark each run actually used).
 */
const KEY = "simtest.suggestedQualityThreshold";
const EVENT = "simtest-calibration";

export function saveSuggestedQualityThreshold(value: number | null) {
  try {
    if (value === null) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, String(value));
    window.dispatchEvent(new Event(EVENT));
  } catch {
    /* storage unavailable: nothing to carry over */
  }
}

export function readSuggestedQualityThreshold(): number | null {
  try {
    const raw = localStorage.getItem(KEY);
    const value = raw === null ? NaN : Number(raw);
    return Number.isFinite(value) && value >= 0 && value <= 1 ? value : null;
  } catch {
    return null;
  }
}

export function subscribeCalibration(onChange: () => void) {
  window.addEventListener(EVENT, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}
