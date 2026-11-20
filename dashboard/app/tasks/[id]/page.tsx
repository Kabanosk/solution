import { redirect } from "next/navigation";

// Tasks open in place inside the dashboard (#task/<id>); old /tasks/<id> links land there without a second
// full data load.
export default async function TaskPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  redirect(`/#task/${encodeURIComponent(decodeURIComponent(id))}`);
}
