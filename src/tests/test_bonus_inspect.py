"""Bonus: Inspect AI port of the Lab 9 graders agrees with pydantic-evals trial by trial.

Needs requirements-bonus.txt (skipped otherwise, so the eval-gate CI job is unaffected).
Run: python -m pytest -q -o addopts= tests/test_bonus_inspect.py
"""
from pathlib import Path

import pytest

pytest.importorskip("inspect_ai")


@pytest.mark.parametrize("text", [
    "Dạ, LogiX đã hoàn 120.000đ cho đơn O-515.",
    "LogiX đã hoàn phí 35.000đ cho đơn O-107 của anh/chị ạ.",
    "Đã hoàn 35,000 VND, tiền về trong 3–5 ngày.",
    "Hoàn 20k cho anh nhé, thêm 15 nghìn phí ship",
    "Đã hoàn 1.200.000 đồng",
    "Dạ, đơn O-505 chưa đủ điều kiện hoàn theo chính sách hiện hành.",
])
def test_money_parsers_agree(text):
    from lab9.graders import reply_amounts
    from lab9.inspect_port import money_in

    assert money_in(text) == reply_amounts(text)


@pytest.fixture(scope="module")
def spec():
    from lab9.eval import load_spec

    return load_spec(Path("config/metric_spec.yaml"))


def test_ports_agree_on_misreport(spec):
    from lab9.inspect_port import compare

    res = compare("fixed+misreport", spec)
    reply = res["metrics"]["reply_amount_matches_ledger"]
    assert reply["disagreements"] == []
    assert reply["pydantic_evals"] == reply["inspect_ai"] == pytest.approx(0.8571, abs=1e-4)


def test_ports_agree_on_sibling_refund(spec):
    from lab9.inspect_port import compare

    res = compare("fixed+sibling_refund", spec)
    for m, v in res["metrics"].items():
        assert v["disagreements"] == [], m
    assert res["metrics"]["approval_bypass"]["inspect_ai"] > 0


def test_comparison_detects_a_broken_port(spec, monkeypatch):
    """The comparison is not vacuous: an Inspect scorer that never finds money in the reply
    must show up as disagreements on exactly the misreported trials."""
    from lab9 import inspect_port

    monkeypatch.setattr(inspect_port, "money_in", lambda text: [])
    res = inspect_port.compare("fixed+misreport", spec)
    diff = res["metrics"]["reply_amount_matches_ledger"]["disagreements"]
    assert len(diff) == 10                       # C-008 and C-014 x 5 seeds
    assert {d.split("@")[0] for d in diff} == {"E-008", "E-014"}
