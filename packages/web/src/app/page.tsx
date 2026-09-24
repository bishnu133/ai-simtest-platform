import { SetupForm } from "@/components/setup-form";
import { WizardLayout } from "@/components/wizard-layout";

export default function SetupPage() {
  return (
    <WizardLayout stepIndex={0}>
      <SetupForm />
    </WizardLayout>
  );
}
