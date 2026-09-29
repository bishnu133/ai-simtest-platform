import { BotDetail } from "@/components/app/bot-detail";

export default async function BotPage({ params }: PageProps<"/bots/[id]">) {
  const { id } = await params;
  return <BotDetail id={id} />;
}
