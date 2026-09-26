import ast
from pathlib import Path

from james_core.suite import run

FORBIDDEN = {"ollama", "langchain", "langgraph", "google", "openai", "anthropic", "requests",
             "httpx", "transformers", "torch"}


def test_seeded_suite_all_pass():
    r = run()
    assert r["all_pass"], [c for c in r["cases"] if not c["pass_"]]
    assert r["violations_blocked"] == r["violations_total"] == 8
    assert r["false_blocks"] == 0 and r["compliant_total"] == 4


def test_warden_imports_no_model_or_network_client():
    src = Path(__file__).resolve().parents[1] / "james_core" / "safety_guard.py"
    tree = ast.parse(src.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    assert not names & FORBIDDEN, names & FORBIDDEN


def test_unknown_rule_kind_fails_closed(tmp_path):
    from james_core.safety_guard import load_policy, evaluate
    from james_core.schemas import Proposal, ProposedStep
    p = tmp_path / "bad.yaml"
    p.write_text('suite: X\nversion: "0"\nallowlisted_actions: [SHOW_STEP]\n'
                 'rules:\n  - {id: R1, kind: vibes, reason: unknown}\n')
    d = evaluate(Proposal(bundle_id="b", step=ProposedStep(action_id="SHOW_STEP", text="t",
                 evidence_ids=("e",)), step_index=1, step_total=1), {}, load_policy(p))
    assert d.decision.value == "BLOCK" and d.rule_ids == ("R1",)
