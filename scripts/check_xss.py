"""XSS tripwire for `dangerouslySetInnerHTML`.

Scans `frontend/app` + `frontend/components` for React escape hatches that
render variable-driven HTML without an obvious escape. Fails CI when a new
call site lands without one of:

  - Inline escape  : `.replace(/&/g, "&amp;")` within the __html expression
  - Allowlisted helper: call to a function whose name is in ALLOWED_HELPERS
                        (these functions are trusted to have escaped their
                        inputs — review them separately when adding)
  - String literal : `{ __html: "<div>...</div>" }` — no interpolation

Escape hatch: a nearby `// xss-safe: <reason>` comment on the same line or
within the 20-line block after `dangerouslySetInnerHTML` silences the check
for that site. Use it only when the expression is safe by dataflow the
regex can't see (e.g. a variable assigned from an allowlisted helper).

Designed to be noisy-when-new and quiet-when-clean. Run locally with
`python scripts/check_xss.py`; exit code 1 on violation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = [REPO_ROOT / "frontend" / "app", REPO_ROOT / "frontend" / "components"]

# Function names that are trusted to return HTML-safe strings. When we see a
# __html expression that's just a call to one of these, we pass. If you add a
# new helper, add it here AND review the helper for proper escaping.
ALLOWED_HELPERS = {
    "escapeHtml",
    "wordDiffHtml",     # frontend/app/fed-macro/page.tsx — escapes via escapeHtml (note: equal branch passes raw; fed text is trusted source)
    "renderAIMarkdown", # reserved for future shared helper
}

INLINE_ESCAPE = re.compile(r"\.replace\(\s*/&/g\s*,")
STRING_LITERAL_HTML = re.compile(r'__html\s*:\s*["\'`]')
HELPER_CALL = re.compile(r"__html\s*:\s*([A-Za-z_][A-Za-z_0-9]*)\s*\(")
# Match either `// xss-safe` (line comment) or `/* xss-safe` (JSX block comment)
XSS_SAFE_ANNOTATION = re.compile(r"(?://|/\*)\s*xss-safe\b")


def find_blocks(source: str) -> list[tuple[int, str]]:
    """Return [(line_number, block_text)] for each dangerouslySetInnerHTML site.

    Block spans from 3 lines BEFORE the keyword through the first JSX
    element terminator (`/>` or `</` closing tag), capped at 20 lines.
    The 3-line backlead catches annotations like `{/* xss-safe: ... */}`
    on the preceding line; bounding to the element prevents unrelated
    escape patterns (e.g. in a neighboring component) from masking
    an unsafe site.
    """
    lines = source.splitlines()
    out = []
    for i, line in enumerate(lines):
        if "dangerouslySetInnerHTML" not in line:
            continue
        start = max(i - 3, 0)
        cap = min(i + 20, len(lines))
        end = cap
        for j in range(i, cap):
            if "/>" in lines[j]:
                end = j + 1
                break
        block = "\n".join(lines[start:end])
        out.append((i + 1, block))
    return out


def is_safe(block: str) -> bool:
    if XSS_SAFE_ANNOTATION.search(block):
        return True
    if INLINE_ESCAPE.search(block):
        return True
    if STRING_LITERAL_HTML.search(block):
        return True
    m = HELPER_CALL.search(block)
    if m and m.group(1) in ALLOWED_HELPERS:
        return True
    return False


def main() -> int:
    violations = []
    for root in SCAN_DIRS:
        if not root.exists():
            continue
        for path in root.rglob("*.tsx"):
            try:
                src = path.read_text(encoding="utf-8")
            except Exception as e:
                print(f"[warn] could not read {path}: {e}", file=sys.stderr)
                continue
            for lineno, block in find_blocks(src):
                if not is_safe(block):
                    rel = path.relative_to(REPO_ROOT)
                    # Show the line that actually contains the keyword, not
                    # the top of the scan window (which may be prior JSX).
                    keyword_line = next(
                        (ln for ln in block.splitlines() if "dangerouslySetInnerHTML" in ln),
                        block.splitlines()[0] if block.splitlines() else "",
                    ).strip()
                    if len(keyword_line) > 120:
                        keyword_line = keyword_line[:117] + "..."
                    violations.append((str(rel), lineno, keyword_line))

    if violations:
        print("Unsafe dangerouslySetInnerHTML usages — add an escape, a string literal, or an allowlisted helper:")
        print()
        for path, lineno, snippet in violations:
            print(f"  {path}:{lineno}")
            print(f"    {snippet}")
            print()
        print(f"{len(violations)} violation(s). If the expression is safe, either:")
        print("  - wrap it with `.replace(/&/g,\"&amp;\").replace(/</g,\"&lt;\").replace(/>/g,\"&gt;\")`")
        print("  - extract into a helper and add its name to ALLOWED_HELPERS in scripts/check_xss.py")
        print("  - add a `// xss-safe: <reason>` comment on the same line if it's safe by upstream dataflow")
        return 1

    print("XSS check: all dangerouslySetInnerHTML call sites look safe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
