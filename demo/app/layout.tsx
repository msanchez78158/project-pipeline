import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Project Pipeline — Synthetic Portfolio Demo",
  description: "Synthetic lending intelligence example. All businesses, loans, and addresses are fictional.",
  robots: "noindex, nofollow",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
