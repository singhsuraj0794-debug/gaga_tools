/** Python interpreter used to spawn the local ML/scraper scripts.
 *
 * Render/Linux/macOS have a real `python3`. On Windows `python3` resolves to
 * the Microsoft Store shim, which does NOT have our packages — every
 * python-backed route then silently produced empty results (e.g. the
 * sheet-duplicates check hashed 0/146 images and reported "no duplicates").
 * Set PYTHON_BIN to the venv interpreter on Windows.
 */
export function pythonBin(): string {
  return process.env.PYTHON_BIN || "python3";
}
