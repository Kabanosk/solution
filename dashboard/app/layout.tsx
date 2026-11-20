import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Scout · Your autonomous CFO",
  description: "Scout, your autonomous CFO: agent tasks with their evidence, bank analytics and cash forecast for Pizzeria Amalfi.",
};
export const viewport: Viewport = { themeColor: "#f7f7f7" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script id="scout-theme-init" dangerouslySetInnerHTML={{ __html: `try{if(localStorage.getItem("scout-theme")==="dark"){document.documentElement.classList.add("dark");document.querySelector('meta[name="theme-color"]')?.setAttribute("content","#101010")}}catch{}` }} />
      </head>
      <body>
        {children}
      </body>
    </html>
  );
}
