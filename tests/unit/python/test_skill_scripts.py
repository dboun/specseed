"""Unit tests for the skill helper scripts under skills/specseed/scripts/.

These are stdlib-only spec-local helpers (reqs generation, cycle detection, critical
path, sprint packing). They never touch the remote or the tracker DB, so the tests are
pure in-memory / tmp_path. Loaded by file path since the scripts are not a package.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[3] / "skills" / "specseed" / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"skillscript_{name}", _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


reqgen = _load("requirements_generate_json")
reqan = _load("requirements_analyze")
cp = _load("critical_path")
sp = _load("sprint_pack")
dv = _load("dependencies_validate")
gp = _load("generate_prompt_from_skill")
# Helpers are private (`__`-prefixed); bind at module scope (no name mangling here) so
# the tests inside the class can still reach them.
_gen = gp.generate_prompt_from_skill
_first_cells = getattr(gp, "__first_cells")
_cell_path = getattr(gp, "__cell_path")
_preamble = getattr(gp, "__preamble")
_gp_main = getattr(gp, "__main")


_SRS = """# API SRS

| ID | Requirement | Type | Priority | Depends on |
|----|-------------|------|----------|------------|
| SRS-API-001 | Accept a reset request | functional | must | - |
| SRS-API-002 | Email a reset link | functional | should | SRS-API-001 |
"""


class RequirementsGenerateTest(unittest.TestCase):
    def _spec(self, text: str = _SRS) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        spec = Path(tmp.name)
        (spec / "api-srs.md").write_text(text, encoding="utf-8")
        return spec

    def test_parses_rows_with_component_and_deps(self):
        reqs, errors = reqgen.generate(self._spec())
        self.assertEqual(errors, [])
        self.assertEqual(set(reqs), {"SRS-API-001", "SRS-API-002"})
        self.assertEqual(reqs["SRS-API-001"]["component"], "API")
        self.assertEqual(reqs["SRS-API-001"]["priority"], "must")
        self.assertEqual(reqs["SRS-API-002"]["depends_on"], ["SRS-API-001"])

    def test_invalid_type_is_error(self):
        bad = _SRS.replace("| functional | must |", "| bogus | must |")
        _, errors = reqgen.generate(self._spec(bad))
        self.assertTrue(any("invalid type" in e for e in errors))

    def test_duplicate_id_is_error(self):
        dup = _SRS + "| SRS-API-001 | dup | functional | must | - |\n"
        _, errors = reqgen.generate(self._spec(dup))
        self.assertTrue(any("duplicate ID" in e for e in errors))

    def test_main_writes_reqs_json(self):
        spec = self._spec()
        rc = reqgen.main(["prog", str(spec)])
        self.assertEqual(rc, 0)
        data = json.loads((spec / "reqs.json").read_text(encoding="utf-8"))
        self.assertIn("SRS-API-002", data)

    def test_main_missing_dir(self):
        self.assertEqual(reqgen.main(["prog", "/no/such/dir"]), 2)


class RequirementsAnalyzeTest(unittest.TestCase):
    def test_ok_chain(self):
        reqs = {"A": {"depends_on": []}, "B": {"depends_on": ["A"]}}
        result = reqan.analyze(reqs)
        self.assertTrue(result["ok"])
        self.assertLess(result["topo_order"].index("A"), result["topo_order"].index("B"))

    def test_dangling_dep(self):
        result = reqan.analyze({"A": {"depends_on": ["Z"]}})
        self.assertFalse(result["ok"])
        self.assertTrue(any("unknown Z" in e for e in result["errors"]))

    def test_cycle(self):
        reqs = {"A": {"depends_on": ["B"]}, "B": {"depends_on": ["A"]}}
        result = reqan.analyze(reqs)
        self.assertFalse(result["ok"])
        self.assertTrue(any("cycle" in e for e in result["errors"]))

    def test_orphan_warning_and_unknown_satisfy(self):
        reqs = {"A": {"depends_on": []}, "B": {"depends_on": []}}
        tickets = {"PROJ-1": {"satisfies_reqs": ["A", "Q"]}}
        result = reqan.analyze(reqs, tickets)
        self.assertTrue(any("B not satisfied" in w for w in result["warnings"]))
        self.assertTrue(any("unknown req Q" in e for e in result["errors"]))


class CriticalPathTest(unittest.TestCase):
    def test_longest_by_effort(self):
        # A->B->D (1+2+1=4) vs A->C->D (1+5+1=7); CP is the heavier branch.
        tickets = {
            "A": {"depends_on": [], "effort": 1},
            "B": {"depends_on": ["A"], "effort": 2},
            "C": {"depends_on": ["A"], "effort": 5},
            "D": {"depends_on": ["B", "C"], "effort": 1},
        }
        result = cp.critical_path(tickets)
        self.assertEqual(result["critical_path"], ["A", "C", "D"])
        self.assertEqual(result["total_effort"], 7.0)

    def test_cycle_raises(self):
        with self.assertRaises(ValueError):
            cp.critical_path({"A": {"depends_on": ["B"]}, "B": {"depends_on": ["A"]}})

    def test_empty(self):
        self.assertEqual(cp.critical_path({}), {"critical_path": [], "total_effort": 0.0})

    def test_load_from_plan_creates(self):
        plan = {"creates": [
            {"tier": "ticket", "id": "PROJ-1", "depends_on": [], "effort": 3},
            {"tier": "issue", "id": "FEAT-1"},
        ]}
        tickets = cp.load_tickets(plan)
        self.assertEqual(set(tickets), {"PROJ-1"})


class SprintPackTest(unittest.TestCase):
    def test_no_backward_dependency(self):
        tickets = {
            "A": {"depends_on": [], "effort": 100},
            "B": {"depends_on": ["A"], "effort": 100},
            "C": {"depends_on": ["B"], "effort": 100},
        }
        sprints = sp.pack(tickets, budget=150)["sprints"]
        # each 100h ticket forces its own sprint; deps must precede.
        flat = [t for s in sprints for t in s]
        self.assertLess(flat.index("A"), flat.index("B"))
        self.assertLess(flat.index("B"), flat.index("C"))
        for s in sprints:
            self.assertLessEqual(len(s), 1)

    def test_budget_fills_then_splits(self):
        tickets = {
            "A": {"depends_on": [], "effort": 100},
            "B": {"depends_on": [], "effort": 50},
            "C": {"depends_on": [], "effort": 100},
        }
        sprints = sp.pack(tickets, budget=160)["sprints"]
        self.assertEqual(len(sprints), 2)
        self.assertEqual(sum(len(s) for s in sprints), 3)

    def test_critical_path_pulled_early(self):
        tickets = {
            "A": {"depends_on": [], "effort": 10, "priority": "low", "on_critical_path": True},
            "B": {"depends_on": [], "effort": 10, "priority": "high"},
        }
        sprints = sp.pack(tickets, budget=5)["sprints"]  # each its own sprint
        self.assertEqual(sprints[0], ["A"])  # CP beats priority for ordering

    def test_single_oversized_ticket_gets_its_own_sprint(self):
        tickets = {"A": {"depends_on": [], "effort": 999}}
        self.assertEqual(sp.pack(tickets, budget=10), {"sprints": [["A"]]})


class DependenciesValidateTest(unittest.TestCase):
    """Validate the issue dependency graph in plan.json - dangling/malformed/cyclic
    deps fail; a consumer-shaped issue with no dep warns. Mirrors the real plan.json
    shape: deps live in body text as `Depends on: #{id:<title>}` placeholders."""

    def _issue(self, title, body="", labels=None):
        return {
            "tier": "issue",
            "title": title,
            "labels": labels or ["issue", "issue:status:todo"],
            "body": body,
        }

    def _ticket(self, title, body=""):
        return {"tier": "ticket", "title": title, "labels": ["ticket", "ticket:status:todo"], "body": body}

    def _epic(self, title):
        return {"tier": "epic", "title": title, "labels": ["epic", "epic:status:todo"], "body": ""}

    def test_clean_plan_ok(self):
        plan = {"creates": [
            self._epic("EPIC-001 Core"),
            self._ticket("TICKET-001 Storage", "Epic: #{id:EPIC-001 Core}\n"),
            self._issue("FEAT-001 Write storage.py", "Parent: #{id:TICKET-001 Storage}\n"),
            self._issue(
                "FEAT-002 Write tests for storage.py",
                "Parent: #{id:TICKET-001 Storage}\nDepends on: #{id:FEAT-001 Write storage.py}\n",
            ),
        ]}
        r = dv.validate(plan)
        self.assertTrue(r["ok"], r["errors"])
        self.assertEqual(r["errors"], [])
        self.assertEqual(r["warnings"], [])  # tests issue HAS a dep -> no nag
        self.assertEqual(r["stats"]["n_dep_edges"], 1)
        self.assertLess(
            r["topo_order"].index("FEAT-001 Write storage.py"),
            r["topo_order"].index("FEAT-002 Write tests for storage.py"),
        )

    def test_dangling_placeholder_is_error(self):
        plan = {"creates": [
            self._issue("FEAT-002 Tests", "Depends on: #{id:FEAT-001 Nope}\n"),
        ]}
        r = dv.validate(plan)
        self.assertFalse(r["ok"])
        self.assertTrue(any("no created item has that title" in e for e in r["errors"]))

    def test_bare_placeholder_missing_hash_is_error(self):
        plan = {"creates": [
            self._issue("A", "Depends on: {id:B}\n"),
            self._issue("B"),
        ]}
        r = dv.validate(plan)
        self.assertFalse(r["ok"])
        self.assertTrue(any("leading # is mandatory" in e for e in r["errors"]))

    def test_empty_depends_line_is_error(self):
        plan = {"creates": [self._issue("A", "Depends on:\n")]}
        r = dv.validate(plan)
        self.assertFalse(r["ok"])
        self.assertTrue(any("no #ref" in e for e in r["errors"]))

    def test_cycle_is_error(self):
        plan = {"creates": [
            self._issue("A", "Depends on: #{id:B}\n"),
            self._issue("B", "Depends on: #{id:A}\n"),
        ]}
        r = dv.validate(plan)
        self.assertFalse(r["ok"])
        self.assertTrue(any("cycle" in e for e in r["errors"]))

    def test_literal_dep_to_existing_post_ok(self):
        # A real #NN points at an already-existing post (not in this plan): valid, no
        # intra-plan edge, no dangling.
        plan = {"creates": [self._issue("FEAT-003 Build on shipped code", "Depends on: #12\n")]}
        r = dv.validate(plan)
        self.assertTrue(r["ok"], r["errors"])
        self.assertEqual(r["stats"]["n_dep_edges"], 0)

    def test_tests_issue_without_dep_warns_but_passes(self):
        # The reported bug: a "tests for X" issue with no Depends on. Warn, do not fail.
        # Standalone issues (no ticket in the plan) so the parent-link rule stays clear.
        plan = {"creates": [
            self._issue("FEAT-001 Write storage.py"),
            self._issue("FEAT-002 Write tests for storage.py"),
        ]}
        r = dv.validate(plan)
        self.assertTrue(r["ok"])  # warning only
        self.assertTrue(any("reads as a tests issue" in w for w in r["warnings"]))

    def test_qa_issue_without_dep_warns(self):
        plan = {"creates": [
            self._issue("VERIFY-001 Acceptance pass", labels=["issue", "issue:status:todo", "type:qa"]),
        ]}
        r = dv.validate(plan)
        self.assertTrue(r["ok"])
        self.assertTrue(any("reads as a QA issue" in w for w in r["warnings"]))

    def test_placeholder_not_miscounted_as_literal_id(self):
        # `#{id:FEAT-001 ...}` must resolve as a placeholder, never leak a literal id.
        plan = {"creates": [
            self._issue("FEAT-001 Impl"),
            self._issue("FEAT-002 Tests", "Depends on: #{id:FEAT-001 Impl}\n"),
        ]}
        deps = dv._parse_deps("Depends on: #{id:FEAT-001 Impl}\n")
        self.assertEqual(deps["placeholders"], ["FEAT-001 Impl"])
        self.assertEqual(deps["literals"], [])
        self.assertTrue(dv.validate(plan)["ok"])

    def test_issue_without_ticket_in_decomposition_is_error(self):
        # The reported failure: a plan that creates tickets but an issue carries no parent
        # link -> it orphans ("Issues without a ticket"). Error, not warning.
        plan = {"creates": [
            self._epic("EPIC-001 Core"),
            self._ticket("TICKET-001 Storage", "Epic: #{id:EPIC-001 Core}\n"),
            self._issue("FEAT-001 Write storage.py"),  # no parent link
        ]}
        r = dv.validate(plan)
        self.assertFalse(r["ok"])
        self.assertTrue(any("no parent ticket link" in e for e in r["errors"]), r["errors"])

    def test_ticket_without_epic_is_error(self):
        # The reported failure: tickets created with no epic link -> "Tickets without an
        # epic". A ticket always belongs to an epic, even in a tiny project.
        plan = {"creates": [
            self._ticket("TICKET-001 Storage"),
            self._issue("FEAT-001 Write storage.py", "Ticket: #{id:TICKET-001 Storage}\n"),
        ]}
        r = dv.validate(plan)
        self.assertFalse(r["ok"])
        self.assertTrue(any("no parent epic link" in e for e in r["errors"]), r["errors"])

    def test_parent_word_link_is_accepted(self):
        # `Parent:` is a valid synonym (matches the runtime parser) - not flagged.
        plan = {"creates": [
            self._epic("EPIC-001 Core"),
            self._ticket("TICKET-001 Storage", "Parent: #{id:EPIC-001 Core}\n"),
            self._issue("FEAT-001 Impl", "Parent: #{id:TICKET-001 Storage}\n"),
        ]}
        r = dv.validate(plan)
        self.assertTrue(r["ok"], r["errors"])

    def test_dangling_parent_placeholder_is_error(self):
        plan = {"creates": [
            self._ticket("TICKET-001 Storage", "Epic: #{id:EPIC-404 Nope}\n"),
        ]}
        r = dv.validate(plan)
        self.assertFalse(r["ok"])
        self.assertTrue(any("would orphan" in e for e in r["errors"]), r["errors"])

    def test_parent_pointing_at_wrong_tier_is_error(self):
        # An issue whose parent placeholder resolves to an epic, not a ticket.
        plan = {"creates": [
            self._epic("EPIC-001 Core"),
            self._issue("FEAT-001 Impl", "Parent: #{id:EPIC-001 Core}\n"),
        ]}
        r = dv.validate(plan)
        self.assertFalse(r["ok"])
        self.assertTrue(any("not a ticket" in e for e in r["errors"]), r["errors"])

    def test_standalone_issue_with_literal_ticket_ok(self):
        # inject adds an issue under an already-existing ticket via a literal id. Valid,
        # no created ticket needed.
        plan = {"creates": [self._issue("BUG-007 Fix crash", "Ticket: #42\n")]}
        r = dv.validate(plan)
        self.assertTrue(r["ok"], r["errors"])

    def test_lone_issue_no_tickets_is_exempt(self):
        # A standalone bug/chore filed with no ticket in the plan: parentless is fine.
        plan = {"creates": [self._issue("BUG-007 Fix crash")]}
        r = dv.validate(plan)
        self.assertTrue(r["ok"], r["errors"])

    def test_create_with_no_labels_is_error(self):
        # THE reported bug: apply.py's plan.json had every create with labels:None, so the
        # posts were born without a tier/status label and the runtime never worked them
        # (assignment + every other event was a silent no-op). Catch it before apply.py runs.
        plan = {"creates": [
            {"tier": "issue", "title": "FEAT-0001 Create HTML structure", "body": ""},
        ]}
        r = dv.validate(plan)
        self.assertFalse(r["ok"])
        self.assertTrue(any("no tier label" in e for e in r["errors"]), r["errors"])
        self.assertTrue(any("no status label" in e for e in r["errors"]), r["errors"])

    def test_missing_tier_label_is_error(self):
        # Has a status label but no tier-bearing one.
        plan = {"creates": [
            {"tier": "issue", "title": "FEAT-001 Impl", "labels": ["status:todo"], "body": ""},
        ]}
        r = dv.validate(plan)
        self.assertFalse(r["ok"])
        self.assertTrue(any("no tier label" in e for e in r["errors"]), r["errors"])
        self.assertFalse(any("no status label" in e for e in r["errors"]), r["errors"])

    def test_missing_status_label_is_error(self):
        plan = {"creates": [
            {"tier": "issue", "title": "FEAT-001 Impl", "labels": ["issue"], "body": ""},
        ]}
        r = dv.validate(plan)
        self.assertFalse(r["ok"])
        self.assertTrue(any("no status label" in e for e in r["errors"]), r["errors"])
        self.assertFalse(any("no tier label" in e for e in r["errors"]), r["errors"])

    def test_combined_status_label_satisfies_both(self):
        # A lone issue whose only label is the combined `issue:status:todo`: the tier and
        # the status both resolve from it, so the label gate passes (mirrors fix 3 in
        # entities/entity_base.tier_from_labels).
        plan = {"creates": [
            {"tier": "issue", "title": "FEAT-001 Impl", "labels": ["issue:status:todo"], "body": ""},
        ]}
        r = dv.validate(plan)
        self.assertTrue(r["ok"], r["errors"])

    def test_main_exit_codes_and_file_input(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        good = Path(tmp.name) / "good.json"
        good.write_text(json.dumps({"creates": [
            self._issue("A"), self._issue("B", "Depends on: #{id:A}\n"),
        ]}), encoding="utf-8")
        self.assertEqual(dv.main(["prog", str(good)]), 0)
        bad = Path(tmp.name) / "bad.json"
        bad.write_text(json.dumps({"creates": [self._issue("B", "Depends on: #{id:missing}\n")]}), encoding="utf-8")
        self.assertEqual(dv.main(["prog", str(bad)]), 1)


_SKILL_ROOT = _SCRIPTS.parent
_RH = "## Mandatory skill reads"
_SH = "## Mandatory skill script preamble reads"


class GeneratePromptTest(unittest.TestCase):
    _TABLE = (
        f"{_RH}\n\n"
        "| Read | Why |\n|------|-----|\n"
        "| `references/a.md` | x |\n"
        "| `templates/d/` | y |\n\n"
        f"{_SH}\n\n"
        "| Script | Use |\n|--------|-----|\n"
        "| `foo.py` | z |\n\n"
        "## Other\n\n| Col | Col2 |\n|--|--|\n| nope | nope |\n"
    )

    def test_first_cells_drops_header_separator_and_other_sections(self):
        self.assertEqual(
            _first_cells(self._TABLE, _RH),
            ["`references/a.md`", "`templates/d/`"],
        )
        self.assertEqual(_first_cells(self._TABLE, _SH), ["`foo.py`"])
        self.assertEqual(_first_cells(self._TABLE, "## Missing"), [])

    def test_cell_path_extracts_backticked_path_or_none(self):
        self.assertEqual(_cell_path("`references/a.md`"), "references/a.md")
        self.assertEqual(_cell_path("`templates/d/`"), "templates/d/")
        self.assertIsNone(_cell_path("no backticks here"))

    def test_preamble_is_first_triple_quote_block(self):
        self.assertEqual(_preamble('"""hi\nthere"""\ncode = 1\n'), "hi\nthere")
        self.assertEqual(_preamble("x = 1\n"), "(no module docstring)")

    @staticmethod
    def _emitted_paths(out: str) -> set[str]:
        paths = set()
        for line in out.splitlines():
            if line.startswith("===== ") and line.endswith(" ====="):
                label = line[len("===== "):-len(" =====")]
                if label.endswith(" (preamble)"):
                    label = label[:-len(" (preamble)")]
                paths.add(label)
        return paths

    def test_public_fn_header_has_version_and_dedupes(self):
        out = _gen("specseed-ui", "spec", "adapt")
        self.assertIsInstance(out, str)
        self.assertIn("Specseed skill (v", out)
        self.assertIn("Mode: specseed-ui. Route: spec. Subroute: adapt.", out)
        self.assertIn("===== SKILL.md =====", out)
        self.assertIn("===== routes/spec.md =====", out)
        self.assertIn("===== spec_subroutes/adapt.md =====", out)
        self.assertEqual(out.count("===== references/spec-change-protocol.md ====="), 1)
        self.assertEqual(out.count("===== scripts/dependencies_validate.py (preamble) ====="), 1)
        # preamble only: script code (never in a docstring) is excluded
        self.assertNotIn("from __future__ import annotations", out)
        # trailing end-of-skill marker
        self.assertIn("END OF SPECSEED SKILL FILES", out)
        self.assertIn("PROMPT FOLLOWS (IF EMPTY STOP AND WAIT)", out)

    def test_spec_without_subroute_includes_all_subroutes(self):
        out = _gen("chat", "spec")
        for sub in _SKILL_ROOT.glob("spec_subroutes/*.md"):
            self.assertIn(f"===== spec_subroutes/{sub.name} =====", out)

    def test_adr_csv_is_included(self):
        out = _gen("specseed-ui", "spec")
        self.assertIn("===== templates/spec_doc_templates/adr.csv =====", out)

    def test_no_route_emits_whole_skill(self):
        out = _gen("specseed-ui")
        self.assertIn("Mode: specseed-ui. Route: (all).", out)
        self.assertIn("===== SKILL.md =====", out)
        for r in _SKILL_ROOT.glob("routes/*.md"):
            self.assertIn(f"===== routes/{r.name} =====", out)

    def test_public_fn_raises_on_bad_mode_or_missing_route(self):
        self.assertRaises(ValueError, _gen, "bogus", "spec")
        self.assertRaises(ValueError, _gen, "chat", "nosuchroute")
        self.assertRaises(ValueError, _gen, "chat", "spec", "nosuchsubroute")
        # a subroute with no route is invalid
        self.assertRaises(ValueError, _gen, "chat", None, "adopt")

    def test_cli_main_exit_codes(self):
        self.assertEqual(_gp_main(["bogus", "spec"]), 2)
        self.assertEqual(_gp_main(["chat", "nosuchroute"]), 2)
        self.assertEqual(_gp_main([]), 2)  # usage: no mode
        for argv, needle in (
            (["specseed-ui", "impl"], "===== routes/impl.md ====="),
            (["specseed-ui"], "Route: (all)."),  # mode-only -> whole skill
        ):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = _gp_main(argv)
            self.assertEqual(rc, 0)
            self.assertIn(needle, buf.getvalue())

    _INSTR_HEADER = "===== INSTRUCTION FILES (MANDATORY READ BEFORE THE WORK) ====="
    _IDIR = "/home/u/.specseed/repos/x/instructions"

    def test_instructions_dir_emits_block_with_route_subdir(self):
        out = _gen("github", "impl", instructions_dir=self._IDIR)
        self.assertIn(self._INSTR_HEADER, out)
        self.assertIn(f"{self._IDIR}/impl/repo.md", out)
        self.assertIn(f"{self._IDIR}/impl/custom.md", out)
        self.assertIn(f"{self._IDIR}/custom.md", out)
        # block sits between the end-of-skill marker and the prompt-follows marker
        self.assertLess(out.index("END OF SPECSEED SKILL FILES"), out.index(self._INSTR_HEADER))
        self.assertLess(out.index(self._INSTR_HEADER), out.index("PROMPT FOLLOWS"))

    def test_subroute_inherits_parent_route_subdir(self):
        out = _gen("specseed-ui", "spec", "adapt", instructions_dir=self._IDIR)
        self.assertIn(f"{self._IDIR}/spec/repo.md", out)
        self.assertNotIn("/adapt/repo.md", out)

    def test_chat_mode_skips_instruction_block(self):
        out = _gen("chat", "impl", instructions_dir=self._IDIR)
        self.assertNotIn(self._INSTR_HEADER, out)

    def test_no_instructions_dir_skips_instruction_block(self):
        self.assertNotIn(self._INSTR_HEADER, _gen("github", "impl"))

    def test_whole_skill_skips_instruction_block(self):
        # no single route -> cannot pick which instruction files to read
        self.assertNotIn(self._INSTR_HEADER, _gen("github", instructions_dir=self._IDIR))

    def test_route_without_instruction_files_skips_block(self):
        # operate owns no per-route instruction files
        self.assertNotIn(self._INSTR_HEADER, _gen("github", "operate", instructions_dir=self._IDIR))

    def test_runtime_internal_routes_resolve_and_skip_instruction_block(self):
        # merge-conflicts / platform-error are real routes but own no instruction files.
        for route in ("merge-conflicts", "platform-error"):
            out = _gen("github", route, instructions_dir=self._IDIR)
            self.assertIn(f"===== routes/{route}.md =====", out)
            self.assertIn(f"Route: {route}.", out)
            self.assertNotIn(self._INSTR_HEADER, out)

    def test_cli_instructions_dir_flag(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = _gp_main(["github", "impl", f"--instructions-dir={self._IDIR}"])
        self.assertEqual(rc, 0)
        self.assertIn(f"{self._IDIR}/impl/repo.md", buf.getvalue())

    def test_whole_skill_reaches_every_file(self):
        # Sanity: the whole-skill prompt (no route) inlines every skill file except
        # version.txt and the generator itself.
        exclude = {"version.txt", "scripts/generate_prompt_from_skill.py"}
        all_files = set()
        for p in _SKILL_ROOT.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(_SKILL_ROOT).as_posix()
            if "__pycache__" in rel or rel.endswith(".pyc") or rel in exclude:
                continue
            all_files.add(rel)

        seen = self._emitted_paths(_gen("specseed-ui"))
        missing = all_files - seen
        self.assertEqual(missing, set(), f"unreachable skill files: {sorted(missing)}")


if __name__ == "__main__":
    unittest.main()
