import { RunDiffView } from "@/components/app/run-diff";

export default async function CompareRunsPage({ searchParams }: PageProps<"/runs/compare">) {
  const { before, after } = await searchParams;
  return (
    <RunDiffView before={typeof before === "string" ? before : ""} after={typeof after === "string" ? after : ""} />
  );
}
