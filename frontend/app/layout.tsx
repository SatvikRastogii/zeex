import type { Metadata } from "next";
import "./globals.css";
import TopBar from "./top-bar";

export const metadata: Metadata = {
  title: "Z-Procure",
  description: "Procurement agent demo",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <TopBar />
        <main>{children}</main>
      </body>
    </html>
  );
}
