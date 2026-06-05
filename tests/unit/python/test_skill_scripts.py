"""Unit tests for the skill helper scripts under skills/specseed/scripts/.

These are stdlib-only spec-local helpers (reqs generation, cycle detection, critical
path, sprint packing). They never touch the remote or the tracker DB, so the tests are
pure in-memory / tmp_path. Loaded by file path since the scripts are not a package.
"""

from __future__ import annotations

import importlib.util
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


if __name__ == "__main__":
    unittest.main()
