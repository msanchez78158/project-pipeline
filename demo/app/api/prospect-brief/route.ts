import { NextRequest, NextResponse } from "next/server";
import type { Prospect } from "@/lib/types";

export async function POST(req: NextRequest) {
  const apiKey = process.env.OPENROUTER_API_KEY;

  if (!apiKey || apiKey === "your_openrouter_key_here" || apiKey === "your_actual_key_here") {
    console.error("OPENROUTER_API_KEY is missing or still set to placeholder");
    return NextResponse.json({ error: "API key not configured" }, { status: 500 });
  }

  let prospect: Prospect;
  let bankRate: number;

  try {
    const body = await req.json();
    prospect = body.prospect;
    bankRate = body.bankRate;
  } catch {
    console.error("Invalid prospect brief request");
    return NextResponse.json({ error: "Invalid request body" }, { status: 400 });
  }

  const lenderContext = prospect.lender_type === "out_of_state"
    ? `The current lender (${prospect.lender_name}) is headquartered OUTSIDE Missouri. This is a strong competitive angle — a local community bank can offer relationship banking and local decision-making that out-of-state lenders cannot.`
    : `The current lender (${prospect.lender_name}) is an in-state lender.`;

  const rateDeltaContext = prospect.rate_delta && prospect.rate_delta > 0
    ? `This borrower is paying ${prospect.rate_delta}% ABOVE current market rate. At ${prospect.initial_interest_rate}% vs. your rate of ${bankRate}%, the annual savings to this borrower would be approximately $${Math.round(prospect.gross_approval * (prospect.rate_delta / 100)).toLocaleString()}.`
    : prospect.rate_delta && prospect.rate_delta < 0
    ? `This borrower locked in a rate ${Math.abs(prospect.rate_delta)}% BELOW current market. Refi pitch is weaker on rate alone — focus on relationship, terms, or additional services.`
    : `Rate delta data is unavailable for this borrower.`;

  const maturityContext = prospect.months_remaining
    ? `The loan matures in approximately ${prospect.months_remaining} months (${prospect.maturity_date ? new Date(prospect.maturity_date).toLocaleDateString("en-US", { month: "long", year: "numeric" }) : "unknown"}). ${prospect.months_remaining <= 18 ? "This is an URGENT window — borrower will need to refinance or renew soon." : "Good lead time to build a relationship before maturity."}`
    : "";

  const prompt = `You are a senior commercial lending officer briefing tool for a community bank in St. Louis, MO.

Generate a concise 3-part prospect brief for the following borrower. Be specific, direct, and actionable. No filler.

BORROWER DATA:
- Business: ${prospect.borrower_name}
- Location: ${prospect.borrower_city}, ${prospect.county} County, MO
- Industry: ${prospect.naics_description}
- Loan Amount: $${prospect.gross_approval.toLocaleString()}
- Original Rate: ${prospect.initial_interest_rate ? `${prospect.initial_interest_rate}% ${prospect.rate_type || ""}` : "unavailable"}
- Your Bank Rate: ${bankRate}%
- Months Since Origination: ${prospect.months_since_approval}
- ${rateDeltaContext}
- ${lenderContext}
- ${maturityContext}
- Jobs Supported: ${prospect.jobs_supported || "unknown"}
- Loan Program: SBA ${prospect.program}

Respond with ONLY a valid JSON object in this exact structure. No markdown, no explanation:
{
  "hook": "One sentence — the single strongest angle for pursuing this prospect (out-of-state lender, rate above market, maturity urgency, or combination). Make it specific to the numbers.",
  "opener": "One suggested opening line for a cold call or email to this specific business owner. Sound like a local banker, not a salesperson. Reference their industry or situation naturally.",
  "opportunity": "Two sentences max. Frame the financial opportunity from the borrower's perspective — what they stand to save or gain. Include the dollar amount if rate delta is available. End with one sentence about what the banker's next step should be."
}`;

  try {
    const response = await fetch("https://openrouter.ai/api/v1/chat/completions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${apiKey}`,
        "X-Title": "Project Pipeline Demo",
      },
      body: JSON.stringify({
        model: "deepseek/deepseek-chat",
        max_tokens: 500,
        temperature: 0.3,
        messages: [{ role: "user", content: prompt }],
      }),
    });

    const data = await response.json();


    if (!response.ok) {
      console.error("OpenRouter request failed", response.status);
      return NextResponse.json(
        { error: "Model provider request failed" },
        { status: response.status }
      );
    }

    const content = data.choices?.[0]?.message?.content;

    if (!content) {
      console.error("Empty model response");
      return NextResponse.json({ error: "No content returned from model" }, { status: 500 });
    }

    // Strip markdown fences if model adds them
    const clean = content.replace(/```json\s*/g, "").replace(/```\s*/g, "").trim();

    try {
      const parsed = JSON.parse(clean);
      return NextResponse.json(parsed);
    } catch {
      console.error("Invalid model response format");
      return NextResponse.json({ error: "Invalid model response format" }, { status: 502 });
    }

  } catch {
    console.error("Prospect brief request failed");
    return NextResponse.json(
      { error: "Prospect brief request failed" },
      { status: 500 }
    );
  }
}
