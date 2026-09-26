import type { ReactNode } from "react";
import { ProgressStepper } from "@/components/progress-stepper";

type WizardLayoutProps = {
  children: ReactNode;
  /** Current step in the 8-step flow; omit to hide the stepper */
  stepIndex?: number;
};

/** A run's pages inside the app shell: the 8-step stepper above the content. */
export function WizardLayout({ children, stepIndex }: WizardLayoutProps) {
  return (
    <div className="flex min-h-full flex-col">
      {stepIndex !== undefined && <ProgressStepper currentStepIndex={stepIndex} />}
      <div className="flex-1 p-4 md:p-8">
        <div className="mx-auto w-full max-w-6xl">{children}</div>
      </div>
    </div>
  );
}
