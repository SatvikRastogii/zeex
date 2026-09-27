import type { Metadata } from "next";
import "./globals.css";
import { SessionProvider } from "@/lib/session";
import TopBar from "./top-bar";

export const metadata: Metadata = {
  title: "Z-Procure",
  description: "Procurement agent demo",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <SessionProvider>
          <TopBar />
          <main>{children}</main>
        </SessionProvider>
      </body>
    </html>
  );
}
