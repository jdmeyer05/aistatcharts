"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

// Minimal typing for Chrome's beforeinstallprompt — not in lib.dom.
interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed"; platform: string }>;
}

interface PwaContextValue {
  /** True once the browser has fired beforeinstallprompt (Chrome/Edge/Android). */
  canInstall: boolean;
  /** Already running as an installed PWA (standalone display mode). */
  isStandalone: boolean;
  /** Trigger the browser's install prompt. Resolves to "accepted" or "dismissed". */
  promptInstall: () => Promise<"accepted" | "dismissed" | "unavailable">;
}

const PwaContext = createContext<PwaContextValue>({
  canInstall: false,
  isStandalone: false,
  promptInstall: async () => "unavailable",
});

export function usePwa() {
  return useContext(PwaContext);
}

export function PwaProvider({ children }: { children: ReactNode }) {
  const [prompt, setPrompt] = useState<BeforeInstallPromptEvent | null>(null);
  const [isStandalone, setIsStandalone] = useState(false);

  useEffect(() => {
    // Detect standalone launch (installed PWA).
    const mql = window.matchMedia("(display-mode: standalone)");
    const update = () => setIsStandalone(mql.matches);
    update();
    mql.addEventListener("change", update);

    const onPrompt = (e: Event) => {
      e.preventDefault();
      setPrompt(e as BeforeInstallPromptEvent);
    };
    const onInstalled = () => {
      setPrompt(null);
      // Fire a simple analytics breadcrumb for install rate tracking.
      try {
        const count = Number(localStorage.getItem("pwa_install_count") || "0") + 1;
        localStorage.setItem("pwa_install_count", String(count));
        localStorage.setItem("pwa_installed_at", new Date().toISOString());
      } catch {}
    };

    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);

    // Register service worker. Only in production — dev mode serves chunks
    // from the dev server and caching them would shadow hot-reload.
    let updateIntervalId: ReturnType<typeof setInterval> | null = null;
    let reloadOnControllerChange = false;
    const onControllerChange = () => {
      // A fresh SW just took control (new build deployed). Reload so the page
      // picks up the new HTML/JS instead of continuing on a stale shell.
      // Guard against Chrome's page-open-during-first-install case where
      // controllerchange fires immediately with no controller to replace.
      if (reloadOnControllerChange) window.location.reload();
    };
    if (
      "serviceWorker" in navigator &&
      process.env.NODE_ENV === "production"
    ) {
      // Only arm the reload path if the page already had a controller on
      // load — meaning a subsequent controllerchange is a genuine update.
      reloadOnControllerChange = !!navigator.serviceWorker.controller;
      navigator.serviceWorker.addEventListener("controllerchange", onControllerChange);

      navigator.serviceWorker
        .register("/sw.js", { scope: "/" })
        .then((registration) => {
          // Poll for updates every 15 minutes so long-open tabs don't miss
          // deploys. Also fires once up-front.
          const checkForUpdate = () => registration.update().catch(() => undefined);
          checkForUpdate();
          updateIntervalId = setInterval(checkForUpdate, 15 * 60_000);
        })
        .catch(() => undefined);
    }

    return () => {
      mql.removeEventListener("change", update);
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
      if ("serviceWorker" in navigator) {
        navigator.serviceWorker.removeEventListener("controllerchange", onControllerChange);
      }
      if (updateIntervalId != null) clearInterval(updateIntervalId);
    };
  }, []);

  const promptInstall = async (): Promise<"accepted" | "dismissed" | "unavailable"> => {
    if (!prompt) return "unavailable";
    await prompt.prompt();
    const { outcome } = await prompt.userChoice;
    setPrompt(null);
    return outcome;
  };

  return (
    <PwaContext.Provider
      value={{
        canInstall: prompt !== null,
        isStandalone,
        promptInstall,
      }}
    >
      {children}
    </PwaContext.Provider>
  );
}
