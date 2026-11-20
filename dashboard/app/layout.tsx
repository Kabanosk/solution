import type { Metadata, Viewport } from "next";
import { DomIds } from "@/components/dom-ids";
import "./globals.css";

export const metadata: Metadata = {
  title: "Lantern · Autonomous CFO",
  description: "An interconnected finance workspace with evidence-based investigations, bank-statement analytics and a simulated autonomous CFO loop.",
};
export const viewport: Viewport = { themeColor: "#101010" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body>
        {children}
        <DomIds />
      </body>
    </html>
  );
}
