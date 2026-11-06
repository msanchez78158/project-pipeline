import { pipelineData } from "@/lib/utils";
import DashboardClient from "@/components/DashboardClient";

export default function DashboardPage() {
  return <DashboardClient data={pipelineData} />;
}
