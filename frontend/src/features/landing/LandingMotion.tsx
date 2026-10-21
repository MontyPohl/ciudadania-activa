"use client";

import { useEffect, useRef, useCallback } from "react";
import type { ReactNode } from "react";

export function Reveal({ children, className = "" }: { children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = ref.current;
    // Sin matchMedia (navegadores antiguos o jsdom) no se anima: el contenido queda estático.
    if (!node || typeof window.matchMedia !== "function" || !("IntersectionObserver" in window))
      return;
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (motion.matches) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          node.removeAttribute("data-reveal");
          observer.disconnect();
        }
      },
      { threshold: 0.08 },
    );
    const show = () => {
      if (motion.matches) {
        node.removeAttribute("data-reveal");
        observer.disconnect();
      }
    };
    node.setAttribute("data-reveal", "pending");
    observer.observe(node);
    motion.addEventListener("change", show);
    return () => {
      observer.disconnect();
      motion.removeEventListener("change", show);
      node.removeAttribute("data-reveal");
    };
  }, []);
  return (
    <div ref={ref} className={`landing-reveal ${className}`}>
      {children}
    </div>
  );
}

export function AnimatedNumber({ value, percent = false }: { value: number; percent?: boolean }) {
  const ref = useRef<HTMLSpanElement>(null);
  const format = useCallback(
    (n: number) => (percent ? `${Math.round(n)} %` : Math.round(n).toLocaleString("es-PY")),
    [percent],
  );
  const label = format(value);
  useEffect(() => {
    const node = ref.current;
    // Sin matchMedia (navegadores antiguos o jsdom) no se anima: el contenido queda estático.
    if (!node || typeof window.matchMedia !== "function" || !("IntersectionObserver" in window))
      return;
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (motion.matches) return;
    let frame = 0;
    const finish = () => {
      cancelAnimationFrame(frame);
      node.textContent = label;
    };
    const onMotion = () => {
      if (motion.matches) {
        finish();
        observer.disconnect();
      }
    };
    const observer = new IntersectionObserver(([entry]) => {
      if (!entry.isIntersecting) return;
      observer.disconnect();
      const start = performance.now();
      const tick = (now: number) => {
        const progress = Math.min((now - start) / 800, 1);
        node.textContent = format(value * (1 - (1 - progress) ** 3));
        if (progress < 1) frame = requestAnimationFrame(tick);
      };
      frame = requestAnimationFrame(tick);
    });
    observer.observe(node);
    motion.addEventListener("change", onMotion);
    return () => {
      observer.disconnect();
      finish();
      motion.removeEventListener("change", onMotion);
    };
  }, [value, format, label]);
  return (
    <span>
      <span className="sr-only">{label}</span>
      <span ref={ref} aria-hidden="true">
        {label}
      </span>
    </span>
  );
}
