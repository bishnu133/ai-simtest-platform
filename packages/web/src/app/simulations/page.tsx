import { RunsList } from "@/components/runs-list";
import { WizardLayout } from "@/components/wizard-layout";

export default function SimulationsPage() {
  return (
    <WizardLayout>
      <RunsList />
    </WizardLayout>
  );
}
