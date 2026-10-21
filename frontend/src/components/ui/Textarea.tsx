"use client";
import type { ComponentPropsWithRef } from "react";
import { controlClasses, useFieldControl } from "./FormField";

export function Textarea({ className = "", ...props }: ComponentPropsWithRef<"textarea">) {
  const fieldProps = useFieldControl(props);
  return <textarea {...props} {...fieldProps} className={`${controlClasses} ${className}`} />;
}
