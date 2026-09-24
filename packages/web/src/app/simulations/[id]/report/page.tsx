import { RunView } from "@/components/run-view";

export default async function DetailedReportPage({ params }: PageProps<"/simulations/[id]/report">) {
  const { id } = await params;
  return <RunView id={id} detailed />;
}
