import { TestLauncher } from "@/components/app/test-launcher";

export default async function NewTestPage({ searchParams }: PageProps<"/new">) {
  const { type } = await searchParams;
  return <TestLauncher initialType={typeof type === "string" ? type : undefined} />;
}
