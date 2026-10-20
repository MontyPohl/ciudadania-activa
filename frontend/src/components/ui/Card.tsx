import { createElement, type ComponentPropsWithRef } from "react";

type CardElement = "div" | "section" | "article" | "figure" | "li";
export function Card<T extends CardElement = "div">({
  as,
  className = "",
  ...props
}: { as?: T } & ComponentPropsWithRef<T>) {
  return createElement(as ?? "div", {
    ...props,
    className: `min-w-0 rounded-2xl bg-surface p-5 text-ink shadow-[0_2px_12px_rgb(0_0_0_/_8%)] wrap-anywhere sm:p-6 ${className}`,
  });
}
