import os from "node:os";
import path from "node:path";

/**
 * Cross-platform temp file path.
 *
 * The API was developed on Linux/macOS where a bare "/tmp" exists. On Windows
 * "/tmp/foo" resolves to "C:\tmp\foo", which does not exist, so every
 * python-backed route (sheet-duplicates, clip-verify, category-validate, ...)
 * failed with ENOENT. Always build temp paths with the OS temp dir.
 */
export function tmpPath(name: string): string {
  return path.join(os.tmpdir(), name);
}
