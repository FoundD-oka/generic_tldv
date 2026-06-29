import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { ThemeProvider } from "@/components/theme-provider";
import { AppLayout } from "@/components/layout/app-layout";
import { DocumentTitle } from "@/components/layout/document-title";
import { TooltipProvider } from "@/components/ui/tooltip";
import { withBasePath } from "@/lib/base-path";
import { resolveDashboardBrand } from "@/lib/dashboard-brand";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const metadataBrand = resolveDashboardBrand(process.env);
const metadataIcon = metadataBrand.logoDark || metadataBrand.logoLight || withBasePath("/icons/vexadark.svg");

export const metadata: Metadata = {
  title: metadataBrand.locale === "ja" ? `${metadataBrand.name} ダッシュボード` : `${metadataBrand.name} Dashboard`,
  description:
    metadataBrand.locale === "ja"
      ? `${metadataBrand.name}の会議文字起こしダッシュボード`
      : `Open source meeting transcription dashboard for ${metadataBrand.name}`,
  icons: {
    icon: [
      {
        url: metadataIcon,
        type: "image/svg+xml",
      },
    ],
    apple: [
      {
        url: metadataIcon,
        type: "image/svg+xml",
      },
    ],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang={metadataBrand.locale} suppressHydrationWarning>
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        <ThemeProvider
          attribute="class"
          defaultTheme="system"
          enableSystem
          disableTransitionOnChange
        >
          <TooltipProvider delayDuration={0}>
            <DocumentTitle />
            <AppLayout>{children}</AppLayout>
          </TooltipProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
