import { TestLauncher } from "@/components/app/test-launcher";

export default async function NewTestPage({ searchParams }: PageProps<"/new">) {
  const { type, suite, bot, persona, policy, workflow } = await searchParams;
  return (
    <TestLauncher
      initialType={typeof type === "string" ? type : undefined}
      initialSuite={typeof suite === "string" ? suite : undefined}
      initialBot={typeof bot === "string" ? bot : undefined}
      initialPersona={typeof persona === "string" ? persona : undefined}
      initialPolicy={typeof policy === "string" ? policy : undefined}
      initialWorkflow={typeof workflow === "string" ? workflow : undefined}
    />
  );
}
