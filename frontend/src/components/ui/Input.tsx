"use client";
import type { ComponentPropsWithRef } from "react";
import { controlClasses, useFieldControl } from "./FormField";

export function Input({ className = "", ...props }: ComponentPropsWithRef<"input">) {
  const fieldProps = useFieldControl(props);
  return <input {...props} {...fieldProps} className={`${controlClasses} ${className}`} />;
}
