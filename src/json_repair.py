"""Multi-strategy JSON repair for malformed LLM output.
Used by any page that calls AI models and expects JSON responses."""
import json
import logging
import re

logger = logging.getLogger(__name__)


def close_json(s: str) -> str:
    """Close any unterminated strings, arrays, and objects in a JSON fragment."""
    if s.count('"') % 2 == 1:
        s += '"'
    s += "]" * max(0, s.count("[") - s.count("]"))
    s += "}" * max(0, s.count("{") - s.count("}"))
    return s


def sanitize_json(raw: str) -> str:
    """Clean common LLM JSON mistakes before parsing."""
    s = raw
    # Strip JS-style comments
    s = re.sub(r'//[^\n]*', '', s)
    s = re.sub(r'/\*.*?\*/', '', s, flags=re.DOTALL)
    # Replace unquoted special values
    s = re.sub(r'\bundefined\b', 'null', s)
    s = re.sub(r'\bNaN\b', 'null', s)
    s = re.sub(r'\bInfinity\b', '999999', s)
    # Trailing commas before ] or }
    s = re.sub(r',\s*([}\]])', r'\1', s)
    # Missing commas at line boundaries
    s = re.sub(r'("|true|false|null|\d)\s*\n(\s*")', r'\1,\n\2', s)
    s = re.sub(r'(\})\s*\n(\s*[\{"])', r'\1,\n\2', s)
    s = re.sub(r'(\])\s*\n(\s*[\["])', r'\1,\n\2', s)
    # Missing commas on same line
    s = re.sub(r'(\})(\s*")', r'\1,\2', s)
    s = re.sub(r'(\])(\s*")', r'\1,\2', s)
    # Empty values
    s = re.sub(r':\s*,', ': null,', s)
    s = re.sub(r':\s*\}', ': null}', s)
    return s


def repair_json(raw: str, model_name: str = "") -> dict:
    """Multi-strategy JSON repair for malformed LLM output.

    Strategies tried in order:
    1. Sanitize + close structures
    2. Iteratively fix errors at reported positions (up to 15 attempts)
    3. Truncate at first error and close
    4. Extract largest valid JSON object from raw string

    Raises json.JSONDecodeError if all strategies fail.
    """
    # Strategy 1: Sanitize and try parsing
    sanitized = sanitize_json(raw)
    closed = close_json(sanitized)
    try:
        return json.loads(closed)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Iteratively fix errors at reported positions
    attempt = sanitized
    for _ in range(15):
        try:
            return json.loads(close_json(attempt))
        except json.JSONDecodeError as e:
            pos = e.pos
            if pos is None or pos >= len(attempt):
                break
            msg = str(e).lower()
            if "delimiter" in msg and "expecting value" not in msg:
                attempt = attempt[:pos] + "," + attempt[pos:]
            elif "expecting value" in msg:
                before = attempt[:pos].rstrip()
                if before.endswith(","):
                    attempt = attempt[:len(before)-1] + attempt[pos:]
                elif before.endswith(":"):
                    attempt = attempt[:pos] + "null" + attempt[pos:]
                else:
                    attempt = attempt[:pos] + "null" + attempt[pos:]
            elif "unterminated string" in msg:
                attempt = attempt[:pos] + '"' + attempt[pos:]
            else:
                break

    # Strategy 3: Truncate at first error and close
    try:
        json.loads(raw)
        pos = len(raw)
    except json.JSONDecodeError as e:
        pos = e.pos or len(raw)
    truncated = raw[:pos]
    truncated = re.sub(r',\s*"[^"]*"?\s*:?\s*[^,}\]]*$', '', truncated)
    truncated = truncated.rstrip().rstrip(",")
    truncated = close_json(truncated)
    try:
        logger.warning(f"{model_name} JSON repaired by truncating at position {pos}/{len(raw)}")
        return json.loads(truncated)
    except json.JSONDecodeError:
        pass

    # Strategy 4: Extract the largest valid JSON object
    best = None
    for i in range(len(raw)):
        if raw[i] == '{':
            depth = 0
            for j in range(i, len(raw)):
                if raw[j] == '{':
                    depth += 1
                elif raw[j] == '}':
                    depth -= 1
                if depth == 0:
                    try:
                        candidate = json.loads(raw[i:j+1])
                        if best is None or len(raw[i:j+1]) > len(str(best)):
                            best = candidate
                    except json.JSONDecodeError:
                        pass
                    break
    if best:
        logger.warning(f"{model_name} JSON recovered via largest-object extraction")
        return best

    raise json.JSONDecodeError(f"All repair strategies failed for {model_name}", raw, 0)
