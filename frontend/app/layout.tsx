import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { Providers } from "@/components/providers";
import { AuthGate } from "@/components/auth-gate";
import { AppChrome } from "@/components/layout/app-chrome";
// import { MatrixBackground } from "@/components/matrix-background";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const SITE_URL = "https://www.aistatcharts.com";
const SITE_NAME = "AI Statcharts";
const SITE_DESC =
  "Institutional-grade quantitative trading platform: cross-asset volatility analytics, CFTC positioning + CTA modeling, Smart Money tracking (insiders, 13F, activists, congressional), Fama-French factor decomposition, and AI-driven interpretation via Claude Opus 4.7 and GPT-5.";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: `${SITE_NAME} — Quantitative trading & options analytics`,
    template: `%s | ${SITE_NAME}`,
  },
  description: SITE_DESC,
  applicationName: SITE_NAME,
  manifest: "/manifest.webmanifest",
  keywords: [
    "quantitative trading",
    "options analytics",
    "implied volatility",
    "volatility surface",
    "CFTC commitments of traders",
    "CTA positioning",
    "managed money positioning",
    "smart money tracking",
    "insider trading",
    "13F holdings",
    "Fama-French factors",
    "Fed macro signals",
    "institutional research",
    "Claude AI trading",
  ],
  authors: [{ name: SITE_NAME }],
  creator: SITE_NAME,
  publisher: SITE_NAME,
  openGraph: {
    type: "website",
    url: SITE_URL,
    siteName: SITE_NAME,
    title: `${SITE_NAME} — Quantitative trading & options analytics`,
    description: SITE_DESC,
    images: [
      { url: "/icon-512.png", width: 512, height: 512, alt: SITE_NAME },
    ],
  },
  twitter: {
    card: "summary",
    title: SITE_NAME,
    description: SITE_DESC,
    images: ["/icon-512.png"],
  },
  robots: {
    index: true,
    follow: true,
    googleBot: { index: true, follow: true, "max-image-preview": "large", "max-snippet": -1 },
  },
  alternates: {
    canonical: SITE_URL,
  },
  appleWebApp: {
    capable: true,
    title: "Statcharts",
    statusBarStyle: "black-translucent",
  },
  icons: {
    icon: "/favicon.png",
    apple: "/icon-192.png",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 5,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#1a2332" },
    { media: "(prefers-color-scheme: dark)", color: "#161b22" },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <body className="min-h-full flex flex-col bg-bg text-text">
        <Providers>
          <AuthGate>
            <AppChrome>{children}</AppChrome>
          </AuthGate>
        </Providers>
      </body>
    </html>
  );
}
