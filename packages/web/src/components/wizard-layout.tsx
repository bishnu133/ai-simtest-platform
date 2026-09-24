import Link from "next/link";
import type { ReactNode } from "react";
import { Bot } from "lucide-react";
import { ProgressStepper } from "@/components/progress-stepper";

type WizardLayoutProps = {
  children: ReactNode;
  /** Current step in the 8-step flow; omit to hide the stepper */
  stepIndex?: number;
};

export function WizardLayout({ children, stepIndex }: WizardLayoutProps) {
  return (
    <div className="min-h-[100dvh] flex flex-col bg-background font-sans">
      <header className="flex-none h-16 border-b bg-card flex items-center justify-between px-4 md:px-8">
        <Link href="/" className="flex items-center gap-2">
          <div className="bg-primary p-1.5 rounded-lg">
            <Bot className="w-5 h-5 text-primary-foreground" />
          </div>
          <span className="font-bold text-lg text-foreground tracking-tight">AI SimTest</span>
        </Link>
        <nav className="flex items-center gap-1 sm:gap-4 text-sm">
          <Link href="/" className="px-2 py-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted">
            New Test
          </Link>
          <Link href="/overview" className="px-2 py-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted">
            Workspace
          </Link>
          <Link
            href="/simulations"
            className="px-2 py-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted"
          >
            Runs
          </Link>
          <span className="hidden md:inline px-2 py-1 bg-muted rounded-md font-medium text-xs text-muted-foreground">
            QA AUTOMATION WORKSPACE
          </span>
        </nav>
      </header>

      {stepIndex !== undefined && <ProgressStepper currentStepIndex={stepIndex} />}

      <main className="flex-1 p-4 md:p-8">
        <div className="max-w-6xl mx-auto w-full">{children}</div>
      </main>
    </div>
  );
}
