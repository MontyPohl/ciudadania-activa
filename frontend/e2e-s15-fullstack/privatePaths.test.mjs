import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { afterEach, beforeEach, test } from "node:test";

import { privateS15Path } from "./privatePaths.ts";

const root = resolve(homedir(), ".local/share/ciudadania-activa/s15");
let scratch;

beforeEach(() => {
  mkdirSync(root, { recursive: true, mode: 0o700 });
  scratch = mkdtempSync(join(root, "path-regression-"));
});
afterEach(() => rmSync(scratch, { recursive: true, force: true }));

test("accepts a canonical regular path inside private S15 evidence", () => {
  const file = join(scratch, "actors.json");
  writeFileSync(file, "{}", { mode: 0o600 });
  assert.equal(privateS15Path(file), file);
});

test("rejects link/../ before normalization can conceal the actual file read", () => {
  const elsewhere = join(scratch, "elsewhere");
  const deep = join(elsewhere, "deep");
  mkdirSync(deep, { recursive: true, mode: 0o700 });
  symlinkSync(deep, join(scratch, "link"), "dir");
  writeFileSync(join(scratch, "actors.json"), "expected", { mode: 0o600 });
  writeFileSync(join(elsewhere, "actors.json"), "different", { mode: 0o600 });
  const input = `${scratch}/link/../actors.json`;
  // The OS follows the symlink before '..'; lexical resolve takes another path.
  assert.notEqual(readFileSync(input, "utf8"), readFileSync(resolve(input), "utf8"));
  assert.throws(() => privateS15Path(input));
});

test("rejects noncanonical aliases even when their normalized path is allowed", () => {
  assert.throws(() => privateS15Path(`${scratch}//actors.json`));
  assert.throws(() => privateS15Path(`${scratch}/./actors.json`));
  assert.throws(() => privateS15Path(`${scratch}/unused/../actors.json`));
});

test("rejects a direct file symlink", () => {
  const file = join(scratch, "actors.json");
  writeFileSync(file, "{}", { mode: 0o600 });
  const alias = join(scratch, "alias.json");
  symlinkSync(file, alias);
  assert.throws(() => privateS15Path(alias));
});

test("rejects an ancestor symlink before creating a new output directory", () => {
  const directory = join(scratch, "real");
  mkdirSync(directory, { mode: 0o700 });
  symlinkSync(directory, join(scratch, "alias"), "dir");
  assert.throws(() => privateS15Path(join(scratch, "alias", "new-output")));
});

test("rejects root, relative paths and destinations outside private S15 evidence", () => {
  assert.throws(() => privateS15Path(root));
  assert.throws(() => privateS15Path("relative-output"));
  assert.throws(() => privateS15Path("/tmp/s15-output"));
});
