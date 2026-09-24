import { RunView } from "@/components/run-view";

export default async function SimulationPage({ params }: PageProps<"/simulations/[id]">) {
  const { id } = await params;
  return <RunView id={id} />;
}
