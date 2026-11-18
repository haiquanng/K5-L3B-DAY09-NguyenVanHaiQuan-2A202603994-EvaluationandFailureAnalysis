"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import evalset, stats
from lab9.eval import SPEC, aggregate, load_spec, per_item
from lab9.graders import GRADERS


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems: list[str] = []
    rows = pr.get("rows")
    if not isinstance(rows, list) or not rows:
        return ["PR report has no rows: nothing to grade"]
    man, bman = pr.get("manifest") or {}, base.get("manifest") or {}

    # 1. metric values must be real numbers (NaN compares False with every threshold).
    for name, rep in (("PR", pr), ("baseline", base)):
        for k, v in (rep.get("metrics") or {}).items():
            if v is None:
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                problems.append(f"{name} metric {k} = {v!r} is not a number")
            elif not math.isfinite(v):
                problems.append(f"{name} metric {k} = {v} is NaN/inf")

    # 2. graders must not have crashed, here or in the baseline.
    if pr.get("evaluator_failures"):
        problems.append(f"{len(pr['evaluator_failures'])} evaluator failure(s) in the PR run, "
                        f"first: {str(pr['evaluator_failures'][0])[:80]}")
    if base.get("evaluator_failures"):
        problems.append("baseline has evaluator failures: re-run it, a broken run cannot be a baseline")

    # 3. row count and the (item, seed) grid: no dropped, duplicated or cherry-picked trials.
    keys = [(r.get("item_id"), r.get("seed")) for r in rows]
    dupes = sorted({k for k in keys if keys.count(k) > 1}, key=str)
    if dupes:
        problems.append(f"{len(dupes)} duplicated (item, seed) row(s), e.g. {dupes[0]}")
    seeds = man.get("seeds") or []
    want = (man.get("items") or 0) * len(seeds)
    if len(rows) != want:
        problems.append(f"{len(rows)} rows != manifest {man.get('items')} items x {len(seeds)} seeds = {want}")
    if bman and (man.get("items") != bman.get("items") or list(seeds) != list(bman.get("seeds") or [])):
        problems.append(f"manifest differs from the baseline: items {man.get('items')} vs {bman.get('items')}, "
                        f"seeds {seeds} vs {bman.get('seeds')}")
    if spec.get("seeds") and list(seeds) != list(spec["seeds"]):
        problems.append(f"PR seeds {seeds} != spec seeds {spec['seeds']}")
    base_keys = {(r.get("item_id"), r.get("seed")) for r in base.get("rows") or []}
    if base_keys and set(keys) != base_keys:
        miss, extra = base_keys - set(keys), set(keys) - base_keys
        problems.append(f"(item, seed) set differs from the baseline: {len(miss)} missing, {len(extra)} extra"
                        + (f", e.g. missing {sorted(miss, key=str)[0]}" if miss else ""))

    # 4. every graded spec metric has a real verdict on every row (None = a crashed grader).
    graded = [m["id"] for m in spec.get("metrics") or [] if m["id"] in GRADERS]
    graded += [sl.get("metric", "pass_rate") for sl in spec.get("slices") or [] if sl.get("metric", "pass_rate") in GRADERS]
    for mid in dict.fromkeys(graded):
        bad = [r for r in rows if not isinstance(r.get(mid), bool)]
        if bad:
            problems.append(f"{len(bad)} row(s) without a true/false verdict for {mid} "
                            f"(e.g. {bad[0].get('item_id')}@{bad[0].get('seed')} = {bad[0].get(mid)!r})")

    # 5. headline metrics must be what the rows say (no hand-edited numbers).
    row_metrics = [m for m in GRADERS if any(m in r for r in rows)]
    if not problems:
        has_cost = all("cost" in r for r in rows)
        try:
            filled = [{"cost": 0.0, **{m: None for m in row_metrics}, **r} for r in rows]
            again = aggregate(filled, row_metrics, k_values=spec.get("pass_k", []))
            if not has_cost:
                again.pop("cost_per_success", None)
        except Exception as e:  # noqa: BLE001 -- a report we cannot re-aggregate is not trustworthy
            problems.append(f"cannot recompute metrics from rows: {e}")
        else:
            for k, v in (pr.get("metrics") or {}).items():
                w = again.get(k)
                if k in again and (v is None) != (w is None):
                    problems.append(f"metric {k} = {v} but rows give {w}")
                elif isinstance(v, (int, float)) and isinstance(w, (int, float)) and abs(v - w) > 1e-6:
                    problems.append(f"metric {k} = {v} but rows give {w}")

    # 6. an item's slice tags are fixed by its label; relabelling hides a slice regression.
    def tags(rep: dict) -> dict:
        out: dict = {}
        for r in rep.get("rows") or []:
            out.setdefault(r.get("item_id"), set()).add(tuple(sorted(r.get("slices") or [])))
        return out
    pt, bt = tags(pr), tags(base)
    for item, t in sorted(pt.items(), key=lambda kv: str(kv[0])):
        if len(t) > 1:
            problems.append(f"{item}: rows disagree on slice tags {sorted(t)}")
        elif item in bt and t != bt[item]:
            problems.append(f"{item}: slice tags {sorted(t)} differ from the baseline's {sorted(bt[item])}")
    eval_set = Path(spec.get("eval_set") or "")
    if eval_set.is_file() and evalset.sha12(eval_set) == man.get("eval_set_sha"):
        truth = {it["item_id"]: tuple(sorted(it.get("slices") or [])) for it in evalset.load_jsonl(eval_set)}
        for item, t in pt.items():
            if item not in truth:
                problems.append(f"{item}: not an item of {eval_set}")
            elif t != {truth[item]}:
                problems.append(f"{item}: slice tags {sorted(t)} differ from the eval set's {truth[item]}")

    # 7. ratchet: on the same eval-set version thresholds may only get stricter.
    if base_spec is not None:
        problems += ratchet(spec, base_spec)
    return problems


def ratchet(spec: dict, base_spec: dict) -> list[str]:
    """Same eval-set version (or the same eval-set file under a new version name): no threshold
    may loosen, no blocking metric/slice may become advisory or disappear."""
    same = (spec.get("eval_set_version") == base_spec.get("eval_set_version")
            or (spec.get("eval_set") and spec.get("eval_set") == base_spec.get("eval_set")))
    if not same:
        return []
    out = []
    for key in ("eval_set", "labels", "seeds", "pass_k", "adversarial_pack"):
        if base_spec.get(key) is not None and spec.get(key) != base_spec.get(key):
            out.append(f"RATCHET {key} changed {base_spec.get(key)!r} -> {spec.get(key)!r} on the same eval-set version")
    if (spec.get("bootstrap") or {}).get("confidence", 0.95) < (base_spec.get("bootstrap") or {}).get("confidence", 0.95):
        out.append("RATCHET bootstrap confidence lowered")

    def compare(where: str, new: dict | None, old: dict) -> None:
        if new is None:
            out.append(f"RATCHET {where} removed")
            return
        if old.get("blocking") and not new.get("blocking"):
            out.append(f"RATCHET {where} no longer blocking")
        nt, ot = new.get("threshold", new), old.get("threshold", old)
        for k in ("max", "max_increase", "max_new_failures"):
            if k in ot and (k not in nt or nt[k] > ot[k]):
                out.append(f"RATCHET {where} {k} loosened {ot[k]} -> {nt.get(k)}")
        if "min" in ot and ("min" not in nt or nt["min"] < ot["min"]):
            out.append(f"RATCHET {where} min loosened {ot['min']} -> {nt.get('min')}")
        if ot.get("no_significant_drop") and not nt.get("no_significant_drop"):
            out.append(f"RATCHET {where} dropped no_significant_drop")

    new_metrics = {m["id"]: m for m in spec.get("metrics") or []}
    for m in base_spec.get("metrics") or []:
        compare(f"metric {m['id']}", new_metrics.get(m["id"]), m)
    key = lambda sl: (sl.get("name"), sl.get("metric", "pass_rate"))
    new_slices = {key(sl): sl for sl in spec.get("slices") or []}
    for sl in base_spec.get("slices") or []:
        compare(f"slice {sl.get('name')}/{sl.get('metric', 'pass_rate')}", new_slices.get(key(sl)), sl)
    return out


def new_failures(pr: dict, base: dict, metric: str, bad_is_true: bool = False) -> list[tuple[str, int]]:
    """(item, seed) trials that were good on the baseline and are bad on the PR. Averages cancel a new
    failure against a fixed one; a trial-level diff does not. Only meaningful when re-running the same
    (item, seed) is deterministic -- true for MockLLM (A/A delta 0), not for a sampled LLM."""
    bad = (lambda v: v is True) if bad_is_true else (lambda v: v is False)
    was = {(r.get("item_id"), r.get("seed")): r.get(metric) for r in base.get("rows") or []}
    return sorted(((r.get("item_id"), r.get("seed")) for r in pr.get("rows") or []
                   if bad(r.get(metric)) and (r.get("item_id"), r.get("seed")) in was
                   and was[(r.get("item_id"), r.get("seed"))] is not None
                   and not bad(was[(r.get("item_id"), r.get("seed"))])), key=str)


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for sl in spec.get("slices") or []:
        name, metric = sl["name"], sl.get("metric", "pass_rate")
        sid = f"slice:{name}/{metric}"
        a, b = per_item(pr, metric, slice_name=name), per_item(base, metric, slice_name=name)
        fail, note = False, ""
        if not a:
            # A slice that lost all its items cannot vouch for anything: treat as a failure.
            val, fail, note = None, True, "no items in this slice"
        else:
            val = round(sum(a.values()) / len(a), 4)
            if "min" in sl and val < sl["min"]:
                fail = True
            if "max" in sl and val > sl["max"]:
                fail = True
            keys = sorted(set(a) & set(b))
            if sl.get("no_significant_drop") and keys:
                d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                   n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027),
                                                   confidence=bs.get("confidence", 0.95))
                note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}] n={len(keys)}"
                if hi < 0:
                    fail = True
            elif not note:
                note = f"n={len(a)}"
        if fail and sl.get("blocking"):
            blocked.append(sid)
        th = {k: sl[k] for k in ("min", "max", "no_significant_drop") if k in sl}
        label = "ok" if not fail else ("BLOCK" if sl.get("blocking") else "warn")
        lines.append(f"  {sid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if "max_new_failures" in th and val is not None:
            new = new_failures(pr, base, mid, bad_is_true="max" in th)
            note = f"{len(new)} new failing trial(s)" + (f", e.g. {new[0][0]}@{new[0][1]}" if new else "")
            if len(new) > th["max_new_failures"]:
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027),
                                               confidence=bs.get("confidence", 0.95))
            note = (note + " · " if note else "") + f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
