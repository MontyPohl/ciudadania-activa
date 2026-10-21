import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "leaflet/dist/leaflet.css";
import "./globals.css";

import { SkipToContent } from "@/components/ui/SkipToContent";
import { SessionProvider } from "@/features/auth/SessionProvider";
import { SessionStatus } from "@/features/auth/SessionStatus";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Ciudadanía Activa",
  description: "Plataforma de reportes urbanos ciudadanos e incentivos por participación.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="es" className={`${inter.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col">
        <SkipToContent />
        <SessionProvider>
          <SessionStatus />
          {children}
        </SessionProvider>
      </body>
    </html>
  );
}
