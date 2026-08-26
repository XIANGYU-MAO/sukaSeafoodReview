import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../i18n/I18nProvider";
import { ImageStage } from "./ImageStage";

const props = {
  previewUrl: "https://images.example.test/fish.jpg",
  sourceUrl: "https://source.example.test/record",
  originalUrl: "https://images.example.test/original.jpg",
  alt: "测试鱼（Piscis probatio）",
  onImageUnavailable: vi.fn(),
};

function renderStage(overrides = {}) {
  return render(
    <I18nProvider initialLocale="zh">
      <ImageStage {...props} {...overrides} />
    </I18nProvider>,
  );
}

describe("ImageStage", () => {
  it("lets the browser load the validated external image and stops the spinner on load", () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    renderStage();

    expect(screen.getByRole("status", { name: "正在加载图片" })).toBeVisible();
    const image = screen.getByRole("img", { name: props.alt });
    expect(image).toHaveAttribute("src", props.previewUrl);
    expect(screen.getByRole("link", { name: "打开来源页面" })).toHaveAttribute("href", props.sourceUrl);
    expect(screen.getByRole("link", { name: "打开原图" })).toHaveAttribute("href", props.originalUrl);
    fireEvent.load(image);

    expect(screen.queryByRole("status", { name: "正在加载图片" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "打开来源页面" })).toHaveLength(1);
    expect(screen.getAllByRole("link", { name: "打开原图" })).toHaveLength(1);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("ends failed loading with finite safe actions and a structured unavailable callback", async () => {
    const onImageUnavailable = vi.fn();
    const user = userEvent.setup();
    renderStage({ onImageUnavailable });

    fireEvent.error(screen.getByRole("img"));
    expect(screen.queryByRole("status", { name: "正在加载图片" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新加载图片" })).toBeVisible();
    expect(screen.getByRole("button", { name: "图片链接失效" })).toBeVisible();
    expect(screen.getAllByRole("link", { name: "打开来源页面" })).toHaveLength(1);
    expect(screen.getAllByRole("link", { name: "打开原图" })).toHaveLength(1);
    for (const link of screen.getAllByRole("link")) {
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noopener noreferrer");
    }

    await user.click(screen.getByRole("button", { name: "图片链接失效" }));
    expect(onImageUnavailable).toHaveBeenCalledTimes(1);
  });

  it("shows the retained image-unavailable payload with an explicit pressed and visible cue", () => {
    renderStage({ imageUnavailableSelected: true });
    fireEvent.error(screen.getByRole("img"));

    const unavailable = screen.getByRole("button", { name: "图片链接失效" });
    expect(unavailable).toHaveAttribute("aria-pressed", "true");
    expect(unavailable).toHaveTextContent("✓");
  });

  it("remounts on retry and ignores stale load/error events from the prior attempt", async () => {
    const user = userEvent.setup();
    renderStage();
    const oldImage = screen.getByRole("img");
    fireEvent.error(oldImage);
    await user.click(screen.getByRole("button", { name: "重新加载图片" }));

    const newImage = screen.getByRole("img");
    expect(newImage).not.toBe(oldImage);
    expect(screen.getByRole("status", { name: "正在加载图片" })).toBeVisible();
    fireEvent.load(oldImage);
    fireEvent.error(oldImage);
    expect(screen.getByRole("status", { name: "正在加载图片" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "重新加载图片" })).not.toBeInTheDocument();

    fireEvent.load(newImage);
    expect(screen.queryByRole("status", { name: "正在加载图片" })).not.toBeInTheDocument();
  });

  it("settles an already-cached image synchronously after mount", async () => {
    vi.spyOn(HTMLImageElement.prototype, "complete", "get").mockReturnValue(true);
    vi.spyOn(HTMLImageElement.prototype, "naturalWidth", "get").mockReturnValue(640);
    renderStage();

    expect(await screen.findByRole("img")).toBeVisible();
    expect(screen.queryByRole("status", { name: "正在加载图片" })).not.toBeInTheDocument();
  });
});
