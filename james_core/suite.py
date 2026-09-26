"""Run the seeded safety suite and print the N/N report for the screen.

    python -m james_core.suite            # human report
    python -m james_core.suite --json out.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from .safety_guard import load_policy, evaluate
from .schemas import Decision, Proposal, ProposedStep



def run(cases_path: Path | None = None, policy_path=None) -> dict:
    """Seeded cases vs rules. With no paths, both come from the active industry pack."""
    if cases_path is None:
        from .pack import active_pack
        cases_path = active_pack().rule_tests_bytes()          # the verified bytes
    policy = load_policy(policy_path) if policy_path else load_policy()
    raw = yaml.safe_load(cases_path.decode("utf-8") if isinstance(cases_path, bytes) else Path(cases_path).read_text())
    if (raw["suite"], str(raw["version"])) != (policy.suite, policy.version):
        raise SystemExit(f"Case file is for {raw['suite']} v{raw['version']}, policy is {policy.label}")
    results = []
    for c in raw["cases"]:
        step = ProposedStep(action_id=c["action"], text=c["step"], touches=tuple(c["touches"]),
                            evidence_ids=("ev-seeded",) if c["cited"] else ())
        d = evaluate(Proposal(bundle_id="bdl-seeded", step=step, step_index=1, step_total=1),
                     c["facts"], policy)
        ok = d.decision.value == c["expected"] and (
            "expected_rule" not in c or c["expected_rule"] in d.rule_ids)
        results.append(dict(id=c["id"], type=c["type"], step=c["step"], expected=c["expected"],
                            got=d.decision.value, rules=list(d.rule_ids), reasons=list(d.reasons), pass_=ok))
    v = [r for r in results if r["type"] == "violation"]
    comp = [r for r in results if r["type"] == "compliant"]
    return dict(
        suite=policy.suite, version=policy.version, status=policy.status, machine=raw["machine"],
        violations_blocked=sum(r["got"] != Decision.ALLOW.value for r in v), violations_total=len(v),
        false_blocks=sum(r["got"] != Decision.ALLOW.value for r in comp), compliant_total=len(comp),
        all_pass=all(r["pass_"] for r in results), cases=results)


def report(r: dict) -> str:
    lines = [f"POLICY SUITE {r['suite']} v{r['version']} ({r['status'].upper()} RULES, NOT PLANT-APPROVED)",
             f"Machine: {r['machine']}",
             f"Defined violations blocked: {r['violations_blocked']}/{r['violations_total']} seeded cases",
             f"Compliant steps wrongly blocked: {r['false_blocks']}/{r['compliant_total']}",
             f"Expected decision and rule matched: {sum(c['pass_'] for c in r['cases'])}/{len(r['cases'])}",
             "", "id   expected         got              rules"]
    for c in r["cases"]:
        mark = "ok " if c["pass_"] else "XX "
        lines.append(f"{mark}{c['id']:<4}{c['expected']:<17}{c['got']:<17}{','.join(c['rules'])}")
    lines.append("")
    lines.append("RESULT: PASS" if r["all_pass"] else "RESULT: FAIL")
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    a = ap.parse_args()
    r = run()
    print(report(r))
    if a.json:
        Path(a.json).write_text(json.dumps(r, indent=2))
    raise SystemExit(0 if r["all_pass"] else 1)
