"use client";
import type { ComponentPropsWithRef } from "react";
import { controlClasses, useFieldControl } from "./FormField";

export function Select({ className = "", ...props }: ComponentPropsWithRef<"select">) {
  const fieldProps = useFieldControl(props);
  return <select {...props} {...fieldProps} className={`${controlClasses} ${className}`} />;
}
