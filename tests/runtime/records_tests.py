"""The pure record and issue-body helpers, without a driver fixture."""
import re
import unittest

from ouro import records as recmod


def finding(sev="H", claim="the total drops the last item"):
    return {"severity": sev, "file": "src/app.py", "line": 2, "claim": claim, "evidence": "probe"}


class RecordsTests(unittest.TestCase):
    def check(self, what, cond):
        self.assertTrue(cond, what)

    def test_may_merge(self):
        check, mm = self.check, recmod.may_merge
        check("stop-at-pr never merges, not even a trivial issue", mm("stop-at-pr", None, ["trivial"], "express") is False)
        check("trivial-merge merges a trivial issue", mm("trivial-merge", None, ["trivial"], "full") is True)
        check("trivial-merge leaves an issue without the label to the owner", mm("trivial-merge", None, ["agent-ready"], "express") is False)
        check("merge-on-green default classes include express", mm("merge-on-green", None, [], "express") is True)
        check("merge-on-green default classes include standard", mm("merge-on-green", None, [], "standard") is True)
        check("merge-on-green default classes leave full to the owner", mm("merge-on-green", None, [], "full") is False)
        check("merge-on-green with full listed merges full", mm("merge-on-green", ["full"], [], "full") is True)
        check("a listed class does not admit its neighbour", mm("merge-on-green", ["full"], [], "standard") is False)
        check("an empty class list merges nothing", mm("merge-on-green", [], [], "express") is False)
        check("an unknown policy merges nothing", mm("merge-on-everything", None, ["trivial"], "express") is False)

    def test_review_plan_and_size(self):
        check, plan = self.check, recmod.review_plan
        cells = {("adversarial-review", "S"): ("node", False), ("copilot", "S"): (None, True), ("external-audit", "S"): ("reader", False), ("none", "S"): (None, False),
                 ("adversarial-review", "M"): ("node", False), ("copilot", "M"): ("node", True), ("external-audit", "M"): ("node", False), ("none", "M"): ("node", False)}
        for (value, size), want in cells.items():
            check(f"review_plan: {size} x {value} is {want}", plan(value, size) == want)
        ds = recmod.derive_size
        check("three files and sixty lines stay S", ds(["a", "b", "c"], 60) == ("S", ""))
        check("a fourth file makes it M", ds(["a", "b", "c", "d"], 5)[0] == "M")
        check("a changelog mirrored beside three files does not count toward the files", ds(["a", "b", "c", "CHANGELOG.md"], 5) == ("S", ""))
        check("a changelog still counts toward the lines", ds(["a", "CHANGELOG.md"], 61)[0] == "M")
        check("no repo.release_notes: the default is the CHANGELOG.md basename, case and all",
              recmod.release_notes(["d/CHANGELOG.md", "CHANGELOG.md", "CHANGELOG.zh-CN.md", "changelog.md", "XCHANGELOG.md"]) == ["d/CHANGELOG.md", "CHANGELOG.md"])
        fragment = ["a", "b", "c", "changes/app/fix.md"]
        check("a repo.release_notes pattern keeps three files and a fragment at S", ds(fragment, 20, [r"^changes/"]) == ("S", "") and ds(fragment, 20)[0] == "M")
        check("a set repo.release_notes replaces the default", ds(["a", "b", "c", "CHANGELOG.md"], 5, [r"^changes/"])[0] == "M")
        check("sixty-one changed lines make it M", ds(["a"], 61)[0] == "M")
        check("a workflow path makes it M whatever its size", ds([".github/workflows/ci.yml"], 1)[0] == "M")
        check("parse_direct reads the vehicle and whether a push happened", recmod.parse_direct("vehicle: pr\npushed direct/1 to origin at abc\n") == {"vehicle": "pr", "pushed": True}
              and recmod.parse_direct("fallback: express\n") == {"vehicle": None, "pushed": False})
        f = recmod.reader_findings({"critiques": [{"severity": "blocker", "claim": "c1", "evidence": "src/app.py:2 text"}, {"severity": "major", "claim": "c2"}, {"severity": "minor", "claim": "c3"}, {"severity": "odd", "claim": "c4"}]})
        check("reader critiques map blocker H, major M, minor L, unknown M, with file and line from the evidence",
              [x["severity"] for x in f] == ["H", "M", "L", "M"] and f[0]["file"] == "src/app.py" and f[0]["line"] == 2)

    def test_body_lines_and_flow(self):
        check = self.check
        check("slugify keeps five kebab words", recmod.slugify("Fix the total of the whole thing now please") == "fix-the-total-of-the")
        check("slugify of punctuation alone has a name", recmod.slugify("!!!") == "change")
        body = "- [x] 1. **One (Flow: express).** a\n- [ ] 2. **Two (Flow: full).** b\n- [ ]3. no space\n"
        check("parse_parts reads number, tick and text; a malformed item is no part", [(k, t) for k, t, _ in recmod.parse_parts(body)] == [(1, True), (2, False)])
        check("a numbered task-list item without the part form is no part, anywhere in the body; one of the form is, anywhere",
              recmod.parse_parts("## Deliverable\n- [ ] 1. read the value\n- [ ] 2. drop the field\n") == []
              and [k for k, _, _ in recmod.parse_parts("## Exit\n- [ ] 1. **X (Flow: full).** x\n")] == [1])
        check("body_line takes the first Flow line", recmod.body_line("Flow: full\nFlow: express\n", "Flow") == "full")
        check("body_line is case-sensitive", recmod.body_line("flow: full\n", "Flow") is None)
        check("derive_flow: docs only is express", recmod.derive_flow(["docs/a.md"], set()) == "express")
        check("derive_flow: a script is standard", recmod.derive_flow(["bin/x.ps1"], set()) == "standard")
        check("derive_flow: a hook is full", recmod.derive_flow(["hooks/x.py"], set()) == "full")
        check("derive_flow: a gate's own file is full", recmod.derive_flow(["tests/t.py"], {"tests/t.py"}) == "full")
        check("flow only tightens", recmod.tighter("full", "express") == "full" and recmod.tighter("express", "standard") == "standard")
        check("without_build_lines drops every Ouro-Build line and nothing else", recmod.without_build_lines("a\nOuro-Build: {}\nb\nOuro-Build: {\"x\": 1}") == "a\nb")

    def test_review_records_and_line(self):
        check = self.check
        recs = [{"round": 1, "findings": [finding("H"), finding("L", "an off-by-one")], "false": 0},
                {"round": 2, "findings": [{**finding("L", "an off-by-one"), "scope": "STANDS"}, finding("L", "a nit")], "false": 0}]
        check("the Review line counts a finding that still stands once, in the round that first reported it",
              recmod.review_line(recs, True) == "Review: rounds:1 findings:H1/M0/L2 false-sentences:0")
        check("and an unscoped restatement of the same finding once",
              recmod.review_line([recs[0], {"round": 2, "findings": [finding("L", "an off-by-one")], "false": 0}], True) == "Review: rounds:1 findings:H1/M0/L1 false-sentences:0")
        r1o, r2o = {"round": 1, "findings": [finding("H")], "false": 0}, {"round": 2, "findings": [{**finding("M", "an old helper"), "scope": "OUTSIDE"}], "false": 0}
        r3o = {"round": 3, "findings": [finding("M", "an old helper")], "false": 0}
        check("a verification's OUTSIDE finding is not counted, nor a later round's restatement of it with no scope",
              "findings:H1/M0/L0" in recmod.review_line([r1o, r2o], True) and "findings:H1/M0/L0" in recmod.review_line([r1o, r2o, r3o], True))
        check("a standing M still needs a fix", recmod.needs_fix({"round": 2, "findings": [{**finding("M"), "scope": "STANDS"}]}))
        check("a finding whose line is a list, or a line spelled 3 then '3', does not crash and counts once",
              recmod.review_line([{"round": 1, "findings": [{**finding("M"), "line": [3, 5]}], "false": 0}, {"round": 2, "findings": [{**finding("M"), "line": [3, 5]}], "false": 0}], True)
              == "Review: rounds:1 findings:H0/M1/L0 false-sentences:0"
              and recmod.review_line([{"round": 1, "findings": [{**finding("L"), "line": "2"}], "false": 0}, {"round": 2, "findings": [finding("L")], "false": 0}], True).endswith("L1 false-sentences:0"))
        sha = "a" * 40
        check("the review heading is the verdict the driver acts on: CLEAN on L-only findings, which are still listed; FINDINGS on an M",
              recmod.review_comment(2, {"findings": [finding("L")]}, sha).startswith(f"**Review** round 2: CLEAN @ {sha}\n")
              and "\n- L " in recmod.review_comment(2, {"findings": [finding("L")]}, sha)
              and recmod.review_comment(2, {"findings": [finding("M")]}, sha).startswith(f"**Review** round 2: FINDINGS @ {sha}\n"))
        good = {"author": {"login": "O"}, "body": f"**Review** round 2: FINDINGS @ {sha}\n\ntext\n```json\n{{\"findings\": [{{\"severity\": \"M\"}}]}}\n```\n"}
        trusted = {"o"}
        rec = recmod.parse_review_comment(good, trusted)
        check("a review marker from a trusted author (case-insensitive) parses", rec and rec["round"] == 2 and rec["verdict"] == "FINDINGS" and rec["sha"] == sha)
        check("its findings come from the json block", rec and recmod.needs_fix(rec))
        near = {
            "an untrusted author": {**good, "author": {"login": "stranger"}},
            "a null author": {**good, "author": None},
            "a trailing space on line 1": {**good, "body": good["body"].replace("FINDINGS @ " + sha, "FINDINGS @ " + sha + " ")},
            "a lower-case marker": {**good, "body": good["body"].replace("**Review**", "**review**")},
            "a short sha": {**good, "body": good["body"].replace(sha, "a" * 7)},
            "an upper-case sha": {**good, "body": good["body"].replace(sha, "A" * 40)},
            "the marker on line 2": {**good, "body": "intro\n" + good["body"]},
            "an unknown verdict": {**good, "body": good["body"].replace("FINDINGS", "MAYBE")},
            "a null body": {**good, "body": None},
        }
        for what, c in near.items():
            check(f"near miss refused: {what}", recmod.parse_review_comment(c, trusted) is None)
        check("a CRLF body still parses", recmod.parse_review_comment({**good, "body": good["body"].replace("\n", "\r\n")}, trusted) is not None)
        dup = recmod.review_records([good, good], trusted)
        check("a duplicated round is counted once", len(dup) == 1)
        def ruling(who="O", text="**Ruling**\n\nNo change needed."):
            return {"author": {"login": who}, "body": text}
        later = {**good, "body": good["body"].replace("round 2", "round 3").replace(sha, "b" * 40)}
        ruled = recmod.review_records([good, ruling()], trusted, {"o"})
        check("a trusted ruling after a FINDINGS record rules it: no fix, and no fix round in the Review line",
              ruled[0]["ruled"] == "O" and not recmod.needs_fix(ruled[0]) and recmod.review_line(ruled + [{"round": 3, "findings": [], "false": 0}], True).startswith("Review: rounds:0 "))
        both = recmod.review_records([good, ruling(), later], trusted, {"o"})
        check("a ruled record followed by a later FINDINGS record leaves the later one needing a fix", not recmod.needs_fix(both[0]) and recmod.needs_fix(both[1]))
        for what, cs in {"an untrusted author's ruling": [good, ruling("stranger")], "a ruling posted before the record": [ruling(), good],
                         "a ruling not on the first line": [good, ruling(text="thanks\n**Ruling**")],
                         "a trusted author who is no approver": [good, ruling("bot")]}.items():
            check(f"{what} rules nothing", recmod.needs_fix(recmod.review_records(cs, trusted | {"bot"}, {"o"})[0]))
        check("the ruling's first line is trimmed", not recmod.needs_fix(recmod.review_records([good, ruling(text="  **Ruling**  \nx")], trusted, {"o"})[0]))
        r1 = {"round": 1, "findings": [finding("H"), finding("l"), {"severity": "M"}], "false": 1}
        r2 = {"round": 2, "findings": [finding("L")], "false": "unread"}
        check("review_line counts severities, fix rounds and false sentences, a restated finding once",
              recmod.review_line([r1, r2], True) == "Review: rounds:1 findings:H1/M1/L1 false-sentences:1")
        check("review_line is `none` with no reviewer", recmod.review_line([], False) == "Review: none")
        check("review_line says unread when no round counted false sentences",
              recmod.review_line([{"round": 1, "findings": [], "false": None}], True).endswith("false-sentences:unread"))
        pattern = re.compile(r"^Review: (?:none|rounds:([0-9]+) findings:H([0-9]+)/M([0-9]+)/L([0-9]+) false-sentences:([0-9]+|unread))[ \t\r]*$", re.M)
        body = recmod.render_body({"part_line": "Part 1/2 of #5", "fixes_line": "", "gates": [], "usage": ""}, recmod.review_line([r1], True))
        check("the PR body's Review line matches the pattern the loop metrics read", pattern.search(body) is not None)
        check("the record survives the body", recmod.read_record(body).get("part_line") == "Part 1/2 of #5")
        check("severity 'M ' is not H or M by accident", recmod.sev({"severity": "low"}) == "L" and recmod.sev(None) == "")

    def test_drawn_body(self):
        drawn = recmod.drawn_body({"summary": "s1", "residue": ["Nothing here has been executed"]}, "r1")
        rec = recmod.read_record(drawn)
        rec.update(summary="s2")
        again = recmod.drawn_body(rec, "r2", drawn.replace("- Nothing here has been executed", "- the tests ran"))
        self.check("drawn_body keeps a section a person edited and redraws the one they left alone",
                   "- the tests ran" in again and "Nothing here" not in again.split("<!--")[0] and "## Summary\ns2" in again)
