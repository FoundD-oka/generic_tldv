"use client";

import { useEffect } from "react";
import { useRuntimeConfig } from "@/hooks/use-runtime-config";
import { DEFAULT_DASHBOARD_BRAND } from "@/lib/dashboard-brand";

export function DocumentTitle() {
  const { config } = useRuntimeConfig();
  const brand = config?.brand || DEFAULT_DASHBOARD_BRAND;
  const title = brand.locale === "ja" ? `${brand.name} ダッシュボード` : `${brand.name} Dashboard`;

  useEffect(() => {
    const applyTitle = () => {
      if (document.title !== title) {
        document.title = title;
      }
    };

    applyTitle();

    const observer = new MutationObserver(applyTitle);
    observer.observe(document.head, {
      childList: true,
      subtree: true,
      characterData: true,
    });

    const frame = window.requestAnimationFrame(applyTitle);
    const timeout = window.setTimeout(applyTitle, 50);

    return () => {
      observer.disconnect();
      window.cancelAnimationFrame(frame);
      window.clearTimeout(timeout);
    };
  }, [title]);

  return null;
}
