import type { PipelineData } from "./types";
import prospectsRaw from "../data/prospects.json";

export const pipelineData = prospectsRaw as unknown as PipelineData;

export function formatCurrency(n: number): string {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(0)}K`;
  return `$${n.toLocaleString()}`;
}

export function formatCurrencyFull(n: number): string {
  return `$${n.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
}

export function formatDate(dateStr: string): string {
  if (!dateStr) return "—";
  const d = new Date(dateStr);
  return d.toLocaleDateString("en-US", { month: "short", year: "numeric" });
}

export function formatDateFull(dateStr: string): string {
  if (!dateStr) return "—";
  const d = new Date(dateStr);
  return d.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
}

export function calcAnnualSavings(grossApproval: number, rateDelta: number): number {
  return Math.round(grossApproval * (rateDelta / 100));
}

export function calcLifetimeSavings(
  grossApproval: number,
  rateDelta: number,
  monthsRemaining: number
): number {
  const years = monthsRemaining / 12;
  return Math.round(grossApproval * (rateDelta / 100) * years);
}

export function priorityColor(priority: string): string {
  switch (priority) {
    case "HIGH":    return "text-[#C0392B] bg-[#FDECEA] border-[#C0392B]";
    case "MEDIUM":  return "text-[#D68910] bg-[#FEF5E4] border-[#D68910]";
    default:        return "text-[#7A6A58] bg-[#F0ECE8] border-[#B0A090]";
  }
}

export function rateDeltaColor(delta: number | null): string {
  if (delta === null) return "text-[#7A6A58]";
  if (delta > 0.5)  return "text-[#C0392B] font-semibold";
  if (delta > 0)    return "text-[#D68910]";
  if (delta < 0)    return "text-[#27734A]";
  return "text-[#7A6A58]";
}

export function truncateNaics(desc: string): string {
  if (!desc) return "—";
  const clean = desc.replace(/\b\w/g, l => l.toUpperCase());
  return clean.length > 36 ? clean.slice(0, 34) + "…" : clean;
}
