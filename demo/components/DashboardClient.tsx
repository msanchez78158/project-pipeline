"use client";

import { useState, useMemo, useCallback } from "react";
import type { PipelineData, Prospect } from "@/lib/types";
import {
  formatCurrency,
  formatCurrencyFull,
  formatDate,
  formatDateFull,
  calcAnnualSavings,
  priorityColor,
  rateDeltaColor,
  truncateNaics,
} from "@/lib/utils";

type SortKey = "priority_score" | "gross_approval" | "months_since_approval" | "rate_delta" | "annual_savings" | "borrower_name";
type SortDir = "asc" | "desc";
type TabType = "refi" | "watch";

interface ProspectBrief {
  hook: string;
  opener: string;
  opportunity: string;
}

export default function DashboardClient({ data }: { data: PipelineData }) {
  // State
  const [bankRate, setBankRate] = useState<number>(
    parseFloat((data.meta.prime_rate.rate + 2.75).toFixed(2))
  );
  const [rateInput, setRateInput] = useState(String(parseFloat((data.meta.prime_rate.rate + 2.75).toFixed(2))));
  const [countyFilter, setCountyFilter] = useState<string>("ALL");
  const [lenderTypeFilter, setLenderTypeFilter] = useState<string>("ALL");
  const [priorityFilter, setPriorityFilter] = useState<string>("ALL");
  const [searchQuery, setSearchQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("priority_score");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [activeTab, setActiveTab] = useState<TabType>("refi");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [briefs, setBriefs] = useState<Record<string, ProspectBrief>>({});
  const [loadingBrief, setLoadingBrief] = useState<string | null>(null);

  // Derive current rate delta recalculation
  const recalcDelta = useCallback(
    (prospect: Prospect): number | null => {
      if (!prospect.initial_interest_rate) return null;
      // Benchmark based on loan size and current bank rate offset
      const benchmarkOffset =
        prospect.gross_approval >= 350_000 ? 2.75 :
        prospect.gross_approval >= 50_000  ? 3.25 : 4.75;
      const prime = bankRate - benchmarkOffset;
      const benchmark = parseFloat((prime + benchmarkOffset).toFixed(2));
      return parseFloat((prospect.initial_interest_rate - benchmark).toFixed(2));
    },
    [bankRate]
  );

  const recalcAnnualSavings = useCallback(
    (prospect: Prospect): number | null => {
      const delta = recalcDelta(prospect);
      if (delta === null || delta <= 0) return null;
      return calcAnnualSavings(prospect.gross_approval, delta);
    },
    [recalcDelta]
  );

  // Filtered + sorted prospects
  const sourceList = activeTab === "refi" ? data.refi_candidates : data.watch_list;

  const filtered = useMemo(() => {
    let list = [...sourceList];
    if (countyFilter !== "ALL") list = list.filter((p) => p.county === countyFilter);
    if (lenderTypeFilter !== "ALL") list = list.filter((p) => p.lender_type === lenderTypeFilter);
    if (priorityFilter !== "ALL") list = list.filter((p) => p.priority === priorityFilter);
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      list = list.filter(
        (p) =>
          p.borrower_name.toLowerCase().includes(q) ||
          p.borrower_city.toLowerCase().includes(q) ||
          p.lender_name.toLowerCase().includes(q) ||
          p.naics_description.toLowerCase().includes(q)
      );
    }
    list.sort((a, b) => {
      // String sort for name
      if (sortKey === "borrower_name") {
        const cmp = a.borrower_name.localeCompare(b.borrower_name);
        return sortDir === "asc" ? cmp : -cmp;
      }
      let aVal: number, bVal: number;
      if (sortKey === "rate_delta") {
        aVal = recalcDelta(a) ?? -999;
        bVal = recalcDelta(b) ?? -999;
      } else if (sortKey === "annual_savings") {
        aVal = recalcAnnualSavings(a) ?? 0;
        bVal = recalcAnnualSavings(b) ?? 0;
      } else {
        aVal = (a[sortKey] as number) ?? 0;
        bVal = (b[sortKey] as number) ?? 0;
      }
      return sortDir === "desc" ? bVal - aVal : aVal - bVal;
    });
    return list;
  }, [sourceList, countyFilter, lenderTypeFilter, priorityFilter, searchQuery, sortKey, sortDir, recalcDelta, recalcAnnualSavings]);

  // Summary stats — recalculate when rate changes
  const summaryStats = useMemo(() => {
    const candidates = data.refi_candidates;
    const totalVolume = candidates.reduce((s, p) => s + p.gross_approval, 0);
    const savingsArr = candidates.map((p) => recalcAnnualSavings(p) ?? 0);
    const totalSavings = savingsArr.reduce((s, n) => s + n, 0);
    const highPriority = candidates.filter((p) => p.priority === "HIGH").length;
    const outOfState = candidates.filter((p) => p.lender_type === "out_of_state").length;
    return { totalVolume, totalSavings, highPriority, outOfState };
  }, [data.refi_candidates, recalcAnnualSavings]);

  function handleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    else { setSortKey(key); setSortDir("desc"); }
  }

  function handleRateChange(val: string) {
    setRateInput(val);
    const n = parseFloat(val);
    if (!isNaN(n) && n > 0 && n < 30) setBankRate(n);
  }

  async function fetchBrief(prospect: Prospect) {
    if (briefs[prospect.id]) return;
    setLoadingBrief(prospect.id);
    try {
      const res = await fetch("/api/prospect-brief", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prospect, bankRate }),
      });
      const json = await res.json();
      setBriefs((prev) => ({ ...prev, [prospect.id]: json }));
    } catch {
      setBriefs((prev) => ({ ...prev, [prospect.id]: { hook: "Failed to load brief.", opener: "", opportunity: "" } }));
    } finally {
      setLoadingBrief(null);
    }
  }

  function toggleRow(prospect: Prospect) {
    if (expandedId === prospect.id) {
      setExpandedId(null);
    } else {
      setExpandedId(prospect.id);
    }
  }

  const counties = ["ALL", ...data.meta.target_counties];
  const SortIcon = ({ k }: { k: SortKey }) =>
    sortKey === k ? (sortDir === "desc" ? " ↓" : " ↑") : " ↕";

  return (
    <div style={{ minHeight: "100vh", background: "var(--color-bg-primary)" }}>

      {/* ── HEADER ── */}
      <header style={{
        background: "var(--color-bg-card)",
        borderTop: "3px solid var(--color-copper-core)",
        borderBottom: "1px solid var(--color-border-medium)",
        padding: "20px 48px",
      }}>
        <div style={{ maxWidth: 1400, margin: "0 auto", display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 4 }}>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, letterSpacing: "0.15em", textTransform: "uppercase", color: "var(--color-copper-core)", fontWeight: 500 }}>
                RedomaWorks
              </span>
              <span style={{ color: "var(--color-border-medium)" }}>·</span>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-text-stone)" }}>
                Project Pipeline
              </span>
            </div>
            <h1 style={{ fontFamily: "var(--font-serif)", fontSize: 26, fontWeight: 600, color: "var(--color-copper-core)", margin: 0, lineHeight: 1.1 }}>
              SYNTHETIC Market Intelligence
            </h1>
            <p style={{ fontFamily: "var(--font-sans)", fontSize: 12, color: "var(--color-text-stone)", margin: "4px 0 0" }}>
              {data.meta.target_counties.join(" · ")} · Data as of {data.meta.data_as_of}
            </p>
          </div>

          {/* Rate Control */}
          <div style={{
            background: "var(--color-bg-primary)",
            border: "1px solid var(--color-border-light)",
            borderRadius: 6,
            padding: "12px 18px",
            minWidth: 220,
          }}>
            <div style={{ fontFamily: "var(--font-sans)", fontSize: 10, fontWeight: 600, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-text-stone)", marginBottom: 6 }}>
              Your Bank&apos;s Current Rate
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input
                type="number"
                value={rateInput}
                onChange={(e) => handleRateChange(e.target.value)}
                step="0.05"
                min="1"
                max="20"
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 22,
                  fontWeight: 500,
                  color: "var(--color-copper-core)",
                  background: "transparent",
                  border: "none",
                  outline: "none",
                  width: 80,
                }}
              />
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 18, color: "var(--color-text-stone)" }}>%</span>
            </div>
            <div style={{ fontFamily: "var(--font-sans)", fontSize: 10, color: "var(--color-text-dust)", marginTop: 2 }}>
              Illustrative prime {data.meta.prime_rate.rate}% · {data.meta.prime_rate.date}
            </div>
          </div>
        </div>
      </header>

      <div style={{ maxWidth: 1400, margin: "0 auto", padding: "0 48px 48px" }}>

        {/* ── SUMMARY STATS ── */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 16, padding: "28px 0 24px" }}>
          {[
            { label: "Refi Candidates", value: data.summary.total_refi_candidates.toLocaleString(), sub: "businesses in the 18–36 month window" },
            { label: "High Priority", value: summaryStats.highPriority.toLocaleString(), sub: "score ≥ 80 — contact this week" },
            { label: "Out-of-State Lenders", value: summaryStats.outOfState.toLocaleString(), sub: "competitor loans from outside MO" },
            { label: "Total Refi Volume", value: formatCurrency(summaryStats.totalVolume), sub: "addressable loan volume in market" },
            { label: "Annual Savings Available", value: formatCurrency(summaryStats.totalSavings), sub: "at your current rate vs. origination" },
          ].map((stat) => (
            <div
              key={stat.label}
              style={{
                background: "var(--color-bg-section)",
                border: "1px solid var(--color-border-light)",
                borderLeft: "3px solid var(--color-copper-core)",
                borderRadius: 6,
                padding: "18px 20px",
              }}
            >
              <div style={{ fontFamily: "var(--font-sans)", fontSize: 10, fontWeight: 600, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-text-stone)", marginBottom: 6 }}>
                {stat.label}
              </div>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 26, fontWeight: 500, color: "var(--color-text-ink)", lineHeight: 1.1 }}>
                {stat.value}
              </div>
              <div style={{ fontFamily: "var(--font-sans)", fontSize: 11, color: "var(--color-text-dust)", marginTop: 4 }}>
                {stat.sub}
              </div>
            </div>
          ))}
        </div>

        {/* ── TABS ── */}
        <div style={{ display: "flex", gap: 0, marginBottom: 20, borderBottom: "1px solid var(--color-border-medium)" }}>
          {([["refi", `Refi Prospects (${data.summary.total_refi_candidates})`], ["watch", `Watch List (${data.meta.total_watch_list})`]] as [TabType, string][]).map(([tab, label]) => (
            <button
              key={tab}
              onClick={() => { setActiveTab(tab); setExpandedId(null); }}
              style={{
                fontFamily: "var(--font-sans)",
                fontSize: 13,
                fontWeight: 600,
                padding: "10px 20px",
                background: "transparent",
                border: "none",
                borderBottom: activeTab === tab ? "2px solid var(--color-copper-core)" : "2px solid transparent",
                color: activeTab === tab ? "var(--color-copper-core)" : "var(--color-text-stone)",
                cursor: "pointer",
                marginBottom: -1,
                transition: "color 0.15s",
              }}
            >
              {label}
            </button>
          ))}
        </div>

        {/* ── FILTERS ── */}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 20, alignItems: "center" }}>
          <input
            type="text"
            placeholder="Search business, city, lender, industry..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{
              fontFamily: "var(--font-sans)",
              fontSize: 13,
              padding: "8px 14px",
              background: "var(--color-bg-primary)",
              border: "1px solid var(--color-border-light)",
              borderRadius: 4,
              color: "var(--color-text-ink)",
              outline: "none",
              minWidth: 280,
              flex: 1,
            }}
          />
          {[
            { label: "County", value: countyFilter, set: setCountyFilter, options: counties },
            { label: "Lender", value: lenderTypeFilter, set: setLenderTypeFilter, options: ["ALL", "out_of_state", "in_state"] },
            { label: "Priority", value: priorityFilter, set: setPriorityFilter, options: ["ALL", "HIGH", "MEDIUM", "MONITOR"] },
          ].map((f) => (
            <select
              key={f.label}
              value={f.value}
              onChange={(e) => f.set(e.target.value)}
              style={{
                fontFamily: "var(--font-sans)",
                fontSize: 12,
                padding: "8px 12px",
                background: "var(--color-bg-primary)",
                border: "1px solid var(--color-border-light)",
                borderRadius: 4,
                color: "var(--color-text-charcoal)",
                cursor: "pointer",
              }}
            >
              {f.options.map((o) => (
                <option key={o} value={o}>
                  {o === "ALL" ? `All ${f.label}s` : o === "out_of_state" ? "Out-of-State" : o === "in_state" ? "In-State" : o}
                </option>
              ))}
            </select>
          ))}
          <span style={{ fontFamily: "var(--font-sans)", fontSize: 12, color: "var(--color-text-stone)", marginLeft: 4 }}>
            {filtered.length} prospects
          </span>
        </div>

        {/* ── TABLE ── */}
        <div style={{ background: "var(--color-bg-section)", border: "1px solid var(--color-border-light)", borderRadius: 6, overflow: "hidden" }}>

          {/* Desktop table header */}
          <div className="hidden md:grid" style={{
            gridTemplateColumns: "100px 1fr 130px 140px 100px 110px 120px 36px",
            background: "var(--color-bg-card)",
            borderBottom: "1px solid var(--color-border-medium)",
            padding: "0 20px",
          }}>
            {[
              { key: null,                          label: "Priority" },
              { key: "borrower_name" as SortKey,    label: "Business" },
              { key: "gross_approval" as SortKey,   label: "Loan Amount" },
              { key: "months_since_approval" as SortKey, label: "Months In Window" },
              { key: "rate_delta" as SortKey,       label: "Rate Delta" },
              { key: "annual_savings" as SortKey,   label: "Annual Savings" },
              { key: null,                          label: "Maturity" },
              { key: null,                          label: "" },
            ].map((col) => (
              <div
                key={col.label}
                onClick={() => col.key && handleSort(col.key)}
                style={{
                  fontFamily: "var(--font-sans)",
                  fontSize: 10,
                  fontWeight: 600,
                  letterSpacing: "0.1em",
                  textTransform: "uppercase",
                  color: "var(--color-text-stone)",
                  padding: "12px 8px",
                  cursor: col.key ? "pointer" : "default",
                  userSelect: "none",
                  whiteSpace: "nowrap",
                }}
              >
                {col.label}{col.key && <SortIcon k={col.key} />}
              </div>
            ))}
          </div>

          {/* Rows */}
          {filtered.length === 0 ? (
            <div style={{ padding: "48px 24px", textAlign: "center", fontFamily: "var(--font-sans)", fontSize: 14, color: "var(--color-text-stone)" }}>
              No prospects match your filters.
            </div>
          ) : (
            filtered.map((prospect, idx) => {
              const isExpanded = expandedId === prospect.id;
              const liveDelta = recalcDelta(prospect);
              const liveSavings = recalcAnnualSavings(prospect);
              const brief = briefs[prospect.id];
              const isLoadingThis = loadingBrief === prospect.id;

              return (
                <div key={prospect.id} className="fade-up" style={{ animationDelay: `${Math.min(idx * 20, 200)}ms`, opacity: 0 }}>

                  {/* ── Desktop row ── */}
                  <div
                    className="prospect-row hidden md:grid"
                    onClick={() => toggleRow(prospect)}
                    style={{
                      gridTemplateColumns: "100px 1fr 130px 140px 100px 110px 120px 36px",
                      padding: "0 20px",
                      borderBottom: isExpanded ? "none" : "1px solid var(--color-border-light)",
                      background: idx % 2 === 0 ? "var(--color-bg-primary)" : "var(--color-bg-section)",
                      alignItems: "center",
                    }}
                  >
                    {/* Priority */}
                    <div style={{ padding: "14px 8px" }}>
                      <span style={{
                        fontFamily: "var(--font-sans)",
                        fontSize: 10,
                        fontWeight: 600,
                        letterSpacing: "0.06em",
                        padding: "3px 8px",
                        borderRadius: 4,
                        border: "1px solid",
                      }} className={priorityColor(prospect.priority)}>
                        {prospect.priority}
                      </span>
                    </div>

                    {/* Business */}
                    <div style={{ padding: "14px 8px" }}>
                      <div style={{ fontFamily: "var(--font-serif)", fontSize: 15, fontWeight: 600, color: "var(--color-text-ink)" }}>
                        {prospect.borrower_name}
                      </div>
                      <div style={{ fontFamily: "var(--font-sans)", fontSize: 11, color: "var(--color-text-stone)", marginTop: 2 }}>
                        {prospect.borrower_city} · {prospect.county} Co.
                      </div>
                      <div style={{ fontFamily: "var(--font-sans)", fontSize: 11, color: "var(--color-text-dust)", marginTop: 1 }}>
                        {truncateNaics(prospect.naics_description)}
                      </div>
                    </div>

                    {/* Loan Amount */}
                    <div style={{ padding: "14px 8px", fontFamily: "var(--font-mono)", fontSize: 14, color: "var(--color-text-charcoal)", textAlign: "right" }}>
                      {formatCurrency(prospect.gross_approval)}
                    </div>

                    {/* Months */}
                    <div style={{ padding: "14px 8px", textAlign: "center" }}>
                      <div style={{ fontFamily: "var(--font-mono)", fontSize: 14, color: "var(--color-text-charcoal)" }}>
                        {prospect.months_since_approval} mo
                      </div>
                      <div style={{ fontFamily: "var(--font-sans)", fontSize: 10, color: "var(--color-text-dust)", marginTop: 2 }}>
                        since origination
                      </div>
                    </div>

                    {/* Rate Delta */}
                    <div style={{ padding: "14px 8px", textAlign: "right" }}>
                      <div style={{ fontFamily: "var(--font-mono)", fontSize: 14 }} className={rateDeltaColor(liveDelta)}>
                        {liveDelta !== null ? (liveDelta > 0 ? `+${liveDelta}%` : `${liveDelta}%`) : "—"}
                      </div>
                      {prospect.initial_interest_rate && (
                        <div style={{ fontFamily: "var(--font-sans)", fontSize: 10, color: "var(--color-text-dust)", marginTop: 2 }}>
                          at {prospect.initial_interest_rate}%
                        </div>
                      )}
                    </div>

                    {/* Annual Savings */}
                    <div style={{ padding: "14px 8px", textAlign: "right" }}>
                      {liveSavings && liveSavings > 0 ? (
                        <div style={{ fontFamily: "var(--font-mono)", fontSize: 14, color: "var(--color-forest)", fontWeight: 500 }}>
                          {formatCurrency(liveSavings)}/yr
                        </div>
                      ) : (
                        <div style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: "var(--color-text-dust)" }}>—</div>
                      )}
                    </div>

                    {/* Maturity */}
                    <div style={{ padding: "14px 8px", textAlign: "right" }}>
                      <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--color-text-charcoal)" }}>
                        {prospect.maturity_date ? formatDate(prospect.maturity_date) : "—"}
                      </div>
                      {prospect.months_remaining !== null && (
                        <div style={{ fontFamily: "var(--font-sans)", fontSize: 10, color: prospect.months_remaining <= 18 ? "var(--color-alert)" : "var(--color-text-dust)", marginTop: 2 }}>
                          {prospect.months_remaining} mo left
                        </div>
                      )}
                    </div>

                    {/* Expand */}
                    <div style={{ padding: "14px 8px", textAlign: "center", fontFamily: "var(--font-sans)", fontSize: 14, color: "var(--color-copper-core)" }}>
                      {isExpanded ? "▲" : "▼"}
                    </div>
                  </div>

                  {/* ── Mobile card ── */}
                  <div
                    className="prospect-row md:hidden"
                    onClick={() => toggleRow(prospect)}
                    style={{
                      padding: "16px 20px",
                      borderBottom: isExpanded ? "none" : "1px solid var(--color-border-light)",
                      background: idx % 2 === 0 ? "var(--color-bg-primary)" : "var(--color-bg-section)",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                          <span style={{ fontFamily: "var(--font-sans)", fontSize: 10, fontWeight: 600, letterSpacing: "0.06em", padding: "2px 6px", borderRadius: 3, border: "1px solid" }} className={priorityColor(prospect.priority)}>
                            {prospect.priority}
                          </span>
                          {prospect.lender_type === "out_of_state" && (
                            <span style={{ fontFamily: "var(--font-sans)", fontSize: 10, color: "var(--color-amber)", fontWeight: 500 }}>OUT-OF-STATE</span>
                          )}
                        </div>
                        <div style={{ fontFamily: "var(--font-serif)", fontSize: 15, fontWeight: 600, color: "var(--color-text-ink)" }}>
                          {prospect.borrower_name}
                        </div>
                        <div style={{ fontFamily: "var(--font-sans)", fontSize: 11, color: "var(--color-text-stone)", marginTop: 2 }}>
                          {prospect.borrower_city} · {truncateNaics(prospect.naics_description)}
                        </div>
                      </div>
                      <div style={{ textAlign: "right", flexShrink: 0 }}>
                        <div style={{ fontFamily: "var(--font-mono)", fontSize: 15, color: "var(--color-text-ink)", fontWeight: 500 }}>
                          {formatCurrency(prospect.gross_approval)}
                        </div>
                        {liveDelta !== null && (
                          <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, marginTop: 2 }} className={rateDeltaColor(liveDelta)}>
                            {liveDelta > 0 ? `+${liveDelta}%` : `${liveDelta}%`} delta
                          </div>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* ── Expanded detail panel ── */}
                  {isExpanded && (
                    <div
                      className="fade-up"
                      style={{
                        background: "var(--color-bg-tint)",
                        borderLeft: "3px solid var(--color-copper-core)",
                        borderBottom: "1px solid var(--color-border-medium)",
                        padding: "20px 28px",
                        marginLeft: 0,
                      }}
                    >
                      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 20, marginBottom: 20 }}>
                        {/* Detail fields */}
                        {[
                          { label: "Full Address", value: `${prospect.borrower_street}, ${prospect.borrower_city}, MO ${prospect.borrower_zip}` },
                          { label: "Current Lender", value: `${prospect.lender_name} ${prospect.lender_type === "out_of_state" ? `(${prospect.lender_state} — out of state)` : "(in-state)"}` },
                          { label: "Loan Program", value: `SBA ${prospect.program}` },
                          { label: "Original Rate", value: prospect.initial_interest_rate ? `${prospect.initial_interest_rate}% ${prospect.rate_type || ""}` : "Unavailable" },
                          { label: "Approval Date", value: formatDateFull(prospect.approval_date) },
                          { label: "Maturity Date", value: prospect.maturity_date ? formatDateFull(prospect.maturity_date) : "—" },
                          { label: "Term", value: prospect.term_months ? `${prospect.term_months} months` : "—" },
                          { label: "Jobs Supported", value: prospect.jobs_supported?.toString() || "—" },
                          { label: "NAICS Code", value: `${prospect.naics_code} — ${prospect.naics_description}` },
                          { label: "Loan Status", value: prospect.loan_status || "—" },
                          ...(liveSavings && liveSavings > 0 ? [
                            { label: "Annual Savings at Your Rate", value: formatCurrencyFull(liveSavings) },
                            ...(prospect.months_remaining ? [{ label: "Remaining Term Savings", value: formatCurrencyFull(Math.round(liveSavings * (prospect.months_remaining / 12))) }] : []),
                          ] : []),
                        ].map((field) => (
                          <div key={field.label}>
                            <div style={{ fontFamily: "var(--font-sans)", fontSize: 10, fontWeight: 600, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-text-stone)", marginBottom: 3 }}>
                              {field.label}
                            </div>
                            <div style={{ fontFamily: "var(--font-serif)", fontSize: 14, color: "var(--color-text-charcoal)" }}>
                              {field.value}
                            </div>
                          </div>
                        ))}
                      </div>

                      {/* AI Brief section */}
                      <div style={{ borderTop: "1px solid var(--color-border-medium)", paddingTop: 16 }}>
                        {!brief && !isLoadingThis && (
                          <button
                            onClick={(e) => { e.stopPropagation(); fetchBrief(prospect); }}
                            style={{
                              fontFamily: "var(--font-sans)",
                              fontSize: 12,
                              fontWeight: 600,
                              letterSpacing: "0.04em",
                              padding: "8px 18px",
                              background: "var(--color-copper-core)",
                              color: "#fff",
                              border: "none",
                              borderRadius: 4,
                              cursor: "pointer",
                              display: "flex",
                              alignItems: "center",
                              gap: 6,
                            }}
                          >
                            ✦ Generate AI Prospect Brief
                          </button>
                        )}

                        {isLoadingThis && (
                          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                            <div className="shimmer" style={{ width: 200, height: 36, borderRadius: 4 }} />
                            <span style={{ fontFamily: "var(--font-sans)", fontSize: 12, color: "var(--color-text-stone)" }}>
                              Generating brief...
                            </span>
                          </div>
                        )}

                        {brief && !isLoadingThis && (
                          <div className="fade-up" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 16 }}>
                            {[
                              { label: "The Hook", icon: "◎", value: brief.hook },
                              { label: "Suggested Opener", icon: "◈", value: brief.opener },
                              { label: "The Opportunity", icon: "◆", value: brief.opportunity },
                            ].map((section) => (
                              <div
                                key={section.label}
                                style={{
                                  background: "var(--color-bg-primary)",
                                  border: "1px solid var(--color-border-light)",
                                  borderRadius: 6,
                                  padding: "14px 16px",
                                }}
                              >
                                <div style={{ fontFamily: "var(--font-sans)", fontSize: 10, fontWeight: 600, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-copper-core)", marginBottom: 8 }}>
                                  {section.icon} {section.label}
                                </div>
                                <div style={{ fontFamily: "var(--font-serif)", fontSize: 14, color: "var(--color-text-charcoal)", lineHeight: 1.6 }}>
                                  {section.value}
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>

        {/* ── FOOTER ── */}
        <div style={{
          marginTop: 40,
          paddingTop: 20,
          borderTop: "1px solid var(--color-border-medium)",
          display: "flex",
          flexWrap: "wrap",
          gap: 16,
          justifyContent: "space-between",
          alignItems: "flex-start",
        }}>
          <div>
            <div style={{ fontFamily: "var(--font-sans)", fontSize: 11, color: "var(--color-text-stone)", marginBottom: 4 }}>
              SYNTHETIC portfolio sample · All businesses and loans are fictional · As of {data.meta.data_as_of}
            </div>
            <div style={{ fontFamily: "var(--font-sans)", fontSize: 11, color: "var(--color-text-dust)" }}>
              Refi window: {data.meta.refi_window_months} months from SBA approval · Approval date ≠ closing date (30–90 day lag typical)
            </div>
            <div style={{ fontFamily: "var(--font-sans)", fontSize: 11, color: "var(--color-text-dust)", marginTop: 2 }}>
              Rate delta vs. SBA benchmark at current prime {data.meta.prime_rate.rate}% · Variable rate loans reflect origination rate only
            </div>
          </div>
          <div style={{ textAlign: "right" }}>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--color-copper-core)", letterSpacing: "0.1em" }}>
              REDOMAWORKS · PROJECT PIPELINE
            </div>
            <div style={{ fontFamily: "var(--font-sans)", fontSize: 10, color: "var(--color-text-dust)", marginTop: 2 }}>
              redomaworks.com · St. Louis, MO
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
