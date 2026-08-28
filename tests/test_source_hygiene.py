"""Guard against invisible bytes in source — the trap that keeps recurring.

TWICE NOW a regex escape written through a shell heredoc has landed in a source
file as a literal control byte instead of the intended escape. On 2026-08-28 the
`\\b` word boundaries in `prompt_replay._RETRYABLE` became two 0x08 bytes, so the
pattern silently required a BACKSPACE character before "429" and no numeric
status code ever matched. Nothing failed loudly: the retry logic simply did not
retry, and the only visible symptom would have been an experiment that kept
dying to vendor errors.

These bytes are invisible in a terminal, invisible in a diff, and invisible in
review. The only reliable defence is a test.
"""
import glob
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Bytes that have no business in Python source. Tab and newline are legitimate;
# carriage return is normal on Windows checkouts.
FORBIDDEN = {
    0x00: "NUL", 0x07: "BEL", 0x08: "BACKSPACE", 0x0B: "VERTICAL TAB",
    0x0C: "FORM FEED", 0x1A: "SUB", 0x1B: "ESC",
}


def _sources():
    for pat in ("*.py", "src/**/*.py", "api/**/*.py", "tests/*.py"):
        for f in glob.glob(os.path.join(ROOT, pat), recursive=True):
            if "node_modules" in f or "__pycache__" in f:
                continue
            yield f


def test_no_control_bytes_in_python_source():
    offenders = []
    for f in _sources():
        with open(f, "rb") as fh:
            blob = fh.read()
        for code, name in FORBIDDEN.items():
            n = blob.count(bytes([code]))
            if n:
                line = blob[:blob.index(bytes([code]))].count(b"\n") + 1
                offenders.append(f"{os.path.relpath(f, ROOT)}:{line} contains {n}x {name} (0x{code:02X})")
    assert not offenders, (
        "Invisible control bytes in source — almost certainly a regex escape "
        "mangled by a shell heredoc. Rewrite the literal without backslashes "
        "(lookarounds instead of \\b) or edit the file with a tool, not a heredoc:\n  "
        + "\n  ".join(offenders))


def test_the_retry_pattern_still_matches_real_vendor_errors():
    """A companion to the byte check: the escape can be wrong without being invisible."""
    from src import prompt_replay
    for msg in ("429 too many requests", "503 UNAVAILABLE", "500 internal",
                "overloaded_error", "connection reset by peer", "deadline exceeded"):
        assert prompt_replay._retryable(Exception(msg)), msg
    for msg in ("400 invalid_request_error", "401 authentication_error",
                "no such model: gpt-9", "error code 1429 items"):
        assert not prompt_replay._retryable(Exception(msg)), msg
