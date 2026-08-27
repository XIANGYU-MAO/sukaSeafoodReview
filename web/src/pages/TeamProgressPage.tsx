import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, request } from "../api/client";
import { parseProgressResponse, type ProgressResponse } from "../api/types";
import { TeamProgress } from "../components/TeamProgress";
import { ScreenLoader } from "../components/ScreenLoader";
import { useI18n } from "../i18n/I18nProvider";

interface TeamProgressPageProps {
  retryBootstrap: () => Promise<void>;
}

type LoadStatus = "loading" | "ready" | "error" | "auth-refresh";

export function TeamProgressPage({ retryBootstrap }: TeamProgressPageProps) {
  const { t } = useI18n();
  const [progress, setProgress] = useState<ProgressResponse | null>(null);
  const [status, setStatus] = useState<LoadStatus>("loading");
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);

  const loadProgress = useCallback(async () => {
    generation.current += 1;
    const currentGeneration = generation.current;
    controller.current?.abort();
    const nextController = new AbortController();
    controller.current = nextController;
    setStatus("loading");
    try {
      const raw = await request<unknown>("/progress", { signal: nextController.signal });
      const validated = parseProgressResponse(raw);
      if (nextController.signal.aborted || currentGeneration !== generation.current) return;
      setProgress(validated);
      setStatus("ready");
    } catch (error) {
      if (nextController.signal.aborted || currentGeneration !== generation.current) return;
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        setStatus("auth-refresh");
        await retryBootstrap();
      } else {
        setStatus("error");
      }
    } finally {
      if (controller.current === nextController) controller.current = null;
    }
  }, [retryBootstrap]);

  useEffect(() => {
    void loadProgress();
    return () => {
      generation.current += 1;
      controller.current?.abort();
      controller.current = null;
    };
  }, [loadProgress]);

  return (
    <main className="team-progress-workspace">
      {status === "loading" && progress === null ? (
        <ScreenLoader label={t("loadingProgress")} />
      ) : null}
      {status === "auth-refresh" ? (
        <ScreenLoader label={t("loadingProgress")} />
      ) : null}
      {status === "error" ? (
        <div className="notice notice--error progress-error" role="alert">
          <span>{t("progressLoadError")}</span>
          <button className="text-button" type="button" onClick={() => void loadProgress()}>
            {t("retryProgress")}
          </button>
        </div>
      ) : null}
      {progress !== null ? <TeamProgress data={progress} /> : null}
    </main>
  );
}
