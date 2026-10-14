"""Quote Floor Control: requirements-to-configuration model.

Mirrors two Salesforce validation rules on a simulated Premium_Finance_Quote__c
object, runs the test matrix against them, and checks that every requirement
traces to a configuration item and at least one test.

Simulated client and data. No employer rules, rates, or records are used.
Not deployed to a Salesforce org; the formulas in SPEC are the deliverable and
this file checks their logic.
"""
import json
import sys
from dataclasses import dataclass, replace
from typing import Optional


@dataclass(frozen=True)
class Quote:
    status: str = "Draft"                    # Draft | Pending Review | Released
    sell_rate: Optional[float] = 9.50        # percent APR
    floor_rate: Optional[float] = 8.00       # authorized floor, percent APR
    broker_fee: float = 25.0
    broker_fee_cap: float = 50.0
    override_approved: bool = False


def rule_floor(old: Optional[Quote], new: Quote) -> Optional[str]:
    """QF_001: block release below the authorized floor without approval."""
    if new.status != "Released":
        return None
    changed = (
        old is None
        or old.status != new.status
        or old.sell_rate != new.sell_rate
        or old.floor_rate != new.floor_rate
    )
    if not changed:
        return None
    if new.sell_rate is None or new.floor_rate is None:
        return "Sell rate and authorized floor are required to release a quote."
    if new.sell_rate < new.floor_rate and not new.override_approved:
        return "Sell rate is below the authorized floor. Request override approval."
    return None


def rule_fee(old: Optional[Quote], new: Quote) -> Optional[str]:
    """QF_002: block release when the broker fee exceeds the cap."""
    if new.status != "Released":
        return None
    changed = (
        old is None
        or old.status != new.status
        or old.broker_fee != new.broker_fee
        or old.broker_fee_cap != new.broker_fee_cap
    )
    if not changed:
        return None
    if new.broker_fee > new.broker_fee_cap and not new.override_approved:
        return "Broker fee exceeds the cap. Request override approval."
    return None


RULES = {"QF_001": rule_floor, "QF_002": rule_fee}


def save(old: Optional[Quote], new: Quote):
    """Return (allowed, [rule ids that fired])."""
    fired = [rid for rid, fn in RULES.items() if fn(old, new)]
    return (not fired, fired)


Draft = Quote()
Pending = replace(Draft, status="Pending Review")
Released_ok = replace(Draft, status="Released")

# (id, requirement, description, old, new, expect_allowed, expect_fired)
TESTS = [
    ("T01", "R1", "Release at a sell rate above the floor", Pending,
     replace(Pending, status="Released", sell_rate=9.50), True, []),
    ("T02", "R1", "Release exactly at the floor (boundary)", Pending,
     replace(Pending, status="Released", sell_rate=8.00), True, []),
    ("T03", "R1", "Release one basis point below the floor", Pending,
     replace(Pending, status="Released", sell_rate=7.99), False, ["QF_001"]),
    ("T04", "R2", "Release below the floor with override approved", Pending,
     replace(Pending, status="Released", sell_rate=7.50, override_approved=True), True, []),
    ("T05", "R3", "Release with a blank sell rate", Pending,
     replace(Pending, status="Released", sell_rate=None), False, ["QF_001"]),
    ("T06", "R3", "Release with a blank authorized floor", Pending,
     replace(Pending, status="Released", floor_rate=None), False, ["QF_001"]),
    ("T07", "R4", "Save a Draft below the floor (work in progress)", Draft,
     replace(Draft, sell_rate=7.00), True, []),
    ("T08", "R4", "Move to Pending Review below the floor", Draft,
     replace(Draft, status="Pending Review", sell_rate=7.00), True, []),
    ("T09", "R5", "Edit the sell rate on a released quote to below the floor",
     Released_ok, replace(Released_ok, sell_rate=7.00), False, ["QF_001"]),
    ("T10", "R5", "Edit an unrelated field on a released quote", Released_ok,
     replace(Released_ok, broker_fee_cap=60.0), True, []),
    ("T11", "R6", "Release with the broker fee at the cap (boundary)", Pending,
     replace(Pending, status="Released", broker_fee=50.0), True, []),
    ("T12", "R6", "Release with the broker fee over the cap", Pending,
     replace(Pending, status="Released", broker_fee=50.01), False, ["QF_002"]),
    ("T13", "R2", "Release over the fee cap with override approved", Pending,
     replace(Pending, status="Released", broker_fee=75.0, override_approved=True), True, []),
    ("T14", "R1", "Release below the floor and over the fee cap, no override", Pending,
     replace(Pending, status="Released", sell_rate=7.00, broker_fee=75.0), False, ["QF_001", "QF_002"]),
    ("T15", "R7", "Create a new quote directly as Released below the floor", None,
     replace(Draft, status="Released", sell_rate=7.00), False, ["QF_001"]),
    ("T16", "R7", "Create a new quote directly as Released within limits", None,
     replace(Draft, status="Released"), True, []),
]

# requirement -> (statement, configuration items)
REQUIREMENTS = {
    "R1": ("A quote cannot be released with a sell rate below its authorized floor.",
           ["Validation rule QF_001"]),
    "R2": ("A floor or fee exception can be released only with approval recorded on the quote.",
           ["Field Override_Approved__c", "Validation rules QF_001, QF_002"]),
    "R3": ("A quote cannot be released if the sell rate or the floor is blank.",
           ["Validation rule QF_001"]),
    "R4": ("Sales can save and review draft quotes below the floor while negotiating.",
           ["Validation rules fire on Released status only"]),
    "R5": ("Changing the rate on an already released quote is checked again.",
           ["Validation rules use ISCHANGED on rate and floor fields"]),
    "R6": ("A quote cannot be released when the broker fee exceeds the agency's cap.",
           ["Validation rule QF_002", "Field Broker_Fee_Cap__c"]),
    "R7": ("A quote created directly as Released is checked the same way.",
           ["ISNEW() in the rule formulas"]),
}


def run_matrix():
    out = []
    for tid, req, desc, old, new, exp_ok, exp_fired in TESTS:
        ok, fired = save(old, new)
        out.append((tid, (ok == exp_ok) and (sorted(fired) == sorted(exp_fired))))
    return out


def mutation_check():
    """Break the rules on purpose and confirm the matrix notices."""
    global RULES
    original = dict(RULES)
    mutants = {}

    def floor_lte(old, new):  # '<' changed to '<='
        if new.status != "Released":
            return None
        if new.sell_rate is None or new.floor_rate is None:
            return "x"
        return "x" if new.sell_rate <= new.floor_rate and not new.override_approved else None

    def floor_ignores_override(old, new):
        if new.status != "Released":
            return None
        if new.sell_rate is None or new.floor_rate is None:
            return "x"
        return "x" if new.sell_rate < new.floor_rate else None

    def floor_always_on(old, new):  # fires on drafts too
        if new.sell_rate is None or new.floor_rate is None:
            return "x"
        return "x" if new.sell_rate < new.floor_rate and not new.override_approved else None

    def floor_no_create(old, new):  # forgets ISNEW()
        return None if old is None else original["QF_001"](old, new)

    for name, fn in [("Floor check uses <= instead of <", floor_lte),
                     ("Override flag ignored", floor_ignores_override),
                     ("Rule fires on drafts, not only on release", floor_always_on),
                     ("ISNEW() missing, new records skip the rule", floor_no_create)]:
        RULES = dict(original)
        RULES["QF_001"] = fn
        failed = [tid for tid, ok in run_matrix() if not ok]
        mutants[name] = failed
    RULES = original
    return mutants


def main():
    results, failures = [], 0
    for tid, req, desc, old, new, exp_ok, exp_fired in TESTS:
        ok, fired = save(old, new)
        passed = (ok == exp_ok) and (sorted(fired) == sorted(exp_fired))
        failures += 0 if passed else 1
        results.append({
            "id": tid, "req": req, "desc": desc,
            "expected": "Saves" if exp_ok else "Blocked by " + ", ".join(exp_fired),
            "actual": "Saves" if ok else "Blocked by " + ", ".join(fired),
            "passed": passed,
        })

    covered = {t[1] for t in TESTS}
    untraced = [r for r in REQUIREMENTS if r not in covered]
    unknown = sorted({t[1] for t in TESTS} - set(REQUIREMENTS))

    summary = {
        "tests": len(results),
        "passed": len(results) - failures,
        "failed": failures,
        "requirements": len(REQUIREMENTS),
        "requirements_without_tests": untraced,
        "tests_citing_unknown_requirements": unknown,
        "mutations": mutation_check(),
        "results": results,
        "requirements_detail": {k: {"statement": v[0], "config": v[1]} for k, v in REQUIREMENTS.items()},
    }
    json.dump(summary, open(sys.argv[1], "w") if len(sys.argv) > 1 else sys.stdout, indent=2)

    for r in results:
        print(("PASS " if r["passed"] else "FAIL ") + r["id"] + " " + r["req"] + " " + r["desc"])
    for name, failed in summary["mutations"].items():
        print(("CAUGHT " if failed else "MISSED ") + name + " -> " + (", ".join(failed) or "no test failed"))
    print(f"\n{summary['passed']}/{summary['tests']} tests passed; "
          f"{len(REQUIREMENTS) - len(untraced)}/{len(REQUIREMENTS)} requirements covered")
    return 1 if failures or untraced or unknown or any(not v for v in summary['mutations'].values()) else 0


if __name__ == "__main__":
    sys.exit(main())
