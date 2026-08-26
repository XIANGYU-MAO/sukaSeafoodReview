import { useEffect, useRef, useState } from "react";

import { useI18n } from "../i18n/I18nProvider";

interface ImageStageProps {
  previewUrl: string;
  sourceUrl?: string | null;
  originalUrl?: string | null;
  alt: string;
  onImageUnavailable: () => void;
  pending?: boolean;
}

type ImageState = "loading" | "loaded" | "error";

export function ImageStage({
  previewUrl,
  sourceUrl = null,
  originalUrl = null,
  alt,
  onImageUnavailable,
  pending = false,
}: ImageStageProps) {
  const { t } = useI18n();
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<ImageState>("loading");
  const imageRef = useRef<HTMLImageElement | null>(null);
  const activeAttempt = useRef(attempt);
  activeAttempt.current = attempt;

  useEffect(() => {
    setState("loading");
  }, [previewUrl]);

  useEffect(() => {
    const image = imageRef.current;
    if (image?.complete && image.naturalWidth > 0) setState("loaded");
  }, [attempt, previewUrl]);

  function retry() {
    setState("loading");
    setAttempt((current) => current + 1);
  }

  const uniqueLinks = [
    sourceUrl ? { href: sourceUrl, label: t("openSource") } : null,
    originalUrl && originalUrl !== sourceUrl
      ? { href: originalUrl, label: t("openOriginal") }
      : null,
  ].filter((entry): entry is { href: string; label: string } => entry !== null);

  return (
    <section className="image-stage" aria-label={alt}>
      <div className="image-stage__viewport">
        {state === "loading" ? (
          <div className="image-stage__loading" role="status" aria-label={t("loadingImage")}>
            <span className="spinner" aria-hidden="true" />
            <span>{t("loadingImage")}</span>
          </div>
        ) : null}
        <img
          key={`${previewUrl}:${attempt}`}
          ref={imageRef}
          className={`candidate-image${state === "loaded" ? " candidate-image--visible" : ""}`}
          src={previewUrl}
          alt={alt}
          onLoad={() => {
            if (activeAttempt.current === attempt) setState("loaded");
          }}
          onError={() => {
            if (activeAttempt.current === attempt) setState("error");
          }}
        />
        {state === "error" ? (
          <div className="image-stage__error">
            <p>{t("imageError")}</p>
            <div className="image-actions">
              <button className="secondary-button" type="button" disabled={pending} onClick={retry}>
                {t("retryImage")}
              </button>
              {uniqueLinks.map((link) => (
                <a
                  className="secondary-button external-link"
                  href={link.href}
                  key={link.href}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {link.label}
                </a>
              ))}
              <button
                className="danger-button"
                type="button"
                disabled={pending}
                onClick={onImageUnavailable}
              >
                {t("imageUnavailable")}
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
