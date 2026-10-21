import Link from "next/link";
import type { ComponentPropsWithRef } from "react";

const variants = {
  primary: "bg-primary text-on-primary hover:bg-primary/90 focus-visible:bg-primary/90",
  secondary:
    "border border-primary bg-surface text-primary hover:bg-primary/5 focus-visible:bg-primary/5",
  ghost: "text-primary hover:bg-primary/5 focus-visible:bg-primary/5",
  danger: "bg-danger-ink text-on-primary hover:bg-danger-ink/90 focus-visible:bg-danger-ink/90",
};
type Shared = { variant?: keyof typeof variants; className?: string };
type ButtonProps = Shared &
  (
    | (ComponentPropsWithRef<"button"> & { href?: never })
    | (Omit<ComponentPropsWithRef<typeof Link>, "href"> & { href: string; disabled?: boolean })
  );

export function Button({ variant = "primary", className = "", ...props }: ButtonProps) {
  const classes = `inline-flex min-h-11 min-w-11 max-w-full items-center justify-center gap-2 rounded-lg px-4 py-2 text-center font-semibold wrap-anywhere transition-colors duration-200 motion-reduce:transition-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus disabled:cursor-not-allowed disabled:opacity-50 aria-disabled:cursor-not-allowed aria-disabled:opacity-50 ${variants[variant]} ${className}`;
  if (props.href !== undefined) {
    const { disabled, ...linkProps } = props;
    if (disabled) {
      const { href, onClick, ...rest } = linkProps;
      // An unavailable destination must not be reachable by pointer or keyboard.
      void href;
      void onClick;
      return <a {...rest} className={classes} aria-disabled="true" tabIndex={-1} />;
    }
    return <Link {...linkProps} className={classes} />;
  }
  return <button type="button" {...props} className={classes} />;
}
