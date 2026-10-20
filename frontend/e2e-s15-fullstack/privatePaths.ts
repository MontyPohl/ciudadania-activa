import { lstatSync } from "node:fs";
import { homedir } from "node:os";
import { isAbsolute, parse, resolve, sep } from "node:path";

/** Restrict all harness input/output paths to the private S15 evidence tree. */
export function privateS15Path(value: string): string {
  const root = resolve(homedir(), ".local/share/ciudadania-activa/s15");
  const target = resolve(value);
  if (!isAbsolute(value) || value !== target || !target.startsWith(root + sep)) {
    throw new Error("La ruta debe pertenecer al área privada de evidencia S15.");
  }
  let current = parse(target).root;
  for (const part of target.slice(current.length).split(sep)) {
    current = resolve(current, part);
    try {
      if (lstatSync(current).isSymbolicLink()) {
        throw new Error("La evidencia S15 no admite enlaces simbólicos en sus rutas.");
      }
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") break;
      throw error;
    }
  }
  return target;
}
