import { STEPS } from "@/lib/engine/stages";

/** `skipped` steps (a replay has no test plan or personas) are shown struck through. */
export function ProgressStepper({ currentStepIndex, skipped = [] }: { currentStepIndex: number; skipped?: number[] }) {
  return (
    <div className="w-full py-4 border-b bg-card" aria-label="Progress">
      <ol className="max-w-5xl mx-auto px-4 md:px-6 flex items-center justify-between">
        {STEPS.map((label, index) => {
          const isSkipped = skipped.includes(index);
          const isCompleted = index < currentStepIndex && !isSkipped;
          const isCurrent = index === currentStepIndex;

          return (
            <li
              key={label}
              className="flex flex-col items-center relative w-full"
              aria-current={isCurrent ? "step" : undefined}
            >
              {index !== 0 && (
                <div
                  className={`absolute left-[-50%] right-[50%] top-3 h-[2px] z-0 ${
                    isCompleted || isCurrent ? "bg-primary" : "bg-muted"
                  }`}
                />
              )}
              <div
                className={`relative z-10 w-6 h-6 rounded-full flex items-center justify-center text-xs font-semibold mb-2 transition-colors duration-200 ${
                  isCompleted || isCurrent ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"
                } ${isCurrent ? "ring-4 ring-primary/20" : ""}`}
              >
                {isSkipped ? "–" : isCompleted ? "✓" : index + 1}
              </div>
              <span
                className={`hidden sm:block text-xs font-medium uppercase tracking-wider text-center ${
                  isCurrent ? "text-foreground" : "text-muted-foreground"
                } ${isSkipped ? "line-through opacity-60" : ""}`}
              >
                {label}
                {isSkipped && <span className="sr-only"> (not needed)</span>}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
