#!/usr/bin/env python3
"""List each count in a PR record that no report traces.

Usage: python3 record-counts.py --record <file>... --report <file>...

A count is a number followed, across whitespace only, by one or two words of which at least one
is on the noun list below. A number is a run of 1 to 6 ASCII digits or a cardinal from zero to
twenty in any case, and it stands alone: it touches no letter, digit or `_`, none of
`. - : / # § % $ @ '`, and no comma followed by a digit. That rule drops versions, decimals,
durations, dates, SHAs, `#` and `§` references, line references, percentages, labels,
possessives and ids of seven or more digits. A word is letters and hyphens with an optional
trailing colon, and any punctuation ends the window. In `N of M` and `N of the M`, N takes M's
window. Backticks and `**` are removed first.

Each listed word gives a pair (number, the noun's canonical form); a spelled number compares as
its digits. A record count traces when any one of its pairs is among the pairs of any report.
Records and reports are read by the same rule, as UTF-8; a BOM and CRLF read as their absence.

Prints `<record file>:<line>: <number and the words after it as written>` for each count that
does not trace, in record-argument order and then line order. Exit 0 when every count traces
(or the record holds none), 1 when any does not, 2 on a usage error or an unreadable file, with
the message on stderr and nothing on stdout.
"""
import argparse, re, sys

# The floor every bin/*.py shares. Exit 2, since 1 means a count did not trace.
if tuple(sys.version_info[:2]) < (3, 11):
    sys.stderr.write(f"record-counts.py needs Python 3.11+; this python is {sys.version_info[0]}.{sys.version_info[1]}\n")
    sys.exit(2)

SPELLED = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
           "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
           "eighteen", "nineteen", "twenty"]
NUMBER = re.compile(r"(?<!\w)(?:[0-9]{1,6}|(?a:" + "|".join(SPELLED) + r"))(?!\w)", re.IGNORECASE)
TOUCHING = set(".-:/#§%$@'")
WORD = re.compile(r"\s+([^\W\d_](?:[^\W\d_]|-)*:?)(?!\w)")
OF = re.compile(r"\s+of\s+(?:the\s+)?", re.IGNORECASE)

NOUNS = {}
for _noun in ("row", "finding", "file", "test", "run", "round", "gate", "claim", "mutant", "scenario",
              "item", "suite", "case", "defect", "commit", "line", "gap", "survivor"):
    NOUNS[_noun] = NOUNS[_noun + "s"] = _noun
NOUNS["entry"] = NOUNS["entries"] = "entry"
for _canon, _forms in (("pass", ("pass", "passes", "passed")), ("fail", ("fail", "fails", "failed")),
                       ("skip", ("skip", "skips", "skipped"))):
    for _form in _forms:
        NOUNS[_form] = _canon
for _outcome in ("ok", "green", "confirmed", "rejected", "untested", "true", "false", "unverifiable"):
    NOUNS[_outcome] = _outcome


def stands_alone(text, start, end):
    before = text[start - 1] if start else ""
    after = text[end] if end < len(text) else ""
    if before in TOUCHING or after in TOUCHING:
        return False
    if before == "," and text[start].isdigit():
        return False
    return not (after == "," and text[end + 1:end + 2].isdigit())


def window(text, end):
    """The one or two words after `end`, as (match end, [words])."""
    words = []
    while len(words) < 2:
        m = WORD.match(text, end)
        if not m:
            break
        words.append(m.group(1))
        end = m.end()
    return end, words


def counts(text):
    """Every count in text, in order, as (start offset, end of its window, value, {canonical nouns})."""
    nums = [m for m in NUMBER.finditer(text) if stands_alone(text, m.start(), m.end())]
    at = {m.start(): i for i, m in enumerate(nums)}
    windows = [None] * len(nums)
    # Right to left, so the M of `N of M` has its own window, borrowed or not, before N takes it.
    for i in range(len(nums) - 1, -1, -1):
        m = nums[i]
        of = OF.match(text, m.end())
        if of and of.end() in at:
            windows[i] = windows[at[of.end()]]
        else:
            windows[i] = window(text, m.end())
    found = []
    for m, (wend, words) in zip(nums, windows):
        nouns = {NOUNS[w.rstrip(":").lower()] for w in words if w.rstrip(":").lower() in NOUNS}
        if nouns:
            tok = m.group(0).lower()
            value = int(tok) if tok.isdigit() else SPELLED.index(tok)
            found.append((m.start(), wend, value, nouns))
    return found


def read(path):
    with open(path, "rb") as f:
        data = f.read()
    return data.decode("utf-8-sig").replace("\r\n", "\n").replace("`", "").replace("**", "")


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace", newline="\n")
    ap = argparse.ArgumentParser(prog="record-counts.py", description="List each count in a PR record that no report traces.")
    ap.add_argument("--record", nargs="+", action="extend", required=True, metavar="FILE",
                    help="the record: the PR body, and a file holding the squash subject and the changelog"
                         " entries")
    ap.add_argument("--report", nargs="+", action="extend", required=True, metavar="FILE",
                    help="the reports: each reviewer's and builder's report, and each command output a count is taken from")
    args = ap.parse_args()
    texts = {}
    for path in args.record + args.report:
        if path not in texts:
            try:
                texts[path] = read(path)
            except (OSError, UnicodeDecodeError) as e:
                sys.stderr.write(f"record-counts.py: cannot read {path}: {e}\n")
                return 2
    traced = {(value, noun) for path in args.report for _, _, value, nouns in counts(texts[path]) for noun in nouns}
    out = []
    for path in args.record:
        text = texts[path]
        for start, wend, value, nouns in counts(text):
            if not any((value, noun) in traced for noun in nouns):
                line = text.count("\n", 0, start) + 1
                out.append(f"{path}:{line}: {' '.join(text[start:wend].split())}\n")
    sys.stdout.write("".join(out))
    return 1 if out else 0


if __name__ == "__main__":
    sys.exit(main())
