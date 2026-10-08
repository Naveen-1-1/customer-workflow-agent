"""The graph itself documents the rules: each workflow's nodes name the checks it runs."""

from itertools import pairwise

import pytest

from customer_workflow_agent.graph.export import compiled_graph

SHARED = {
    "start",
    "interpret",
    "prepare_summary",
    "ask",
    "wait",
    "deny",
    "confirm",
    "after_confirm",
    "execute",
    "dropped",
    "finish",
}
APPROVAL = {"approval_gate", "approval", "after_approval", "offer_transfer", "after_offer"}
ORDER_PENDING = ["find_order", "check_pending"]
ORDER_DELIVERED = ["find_order", "check_delivered"]
SWAP = ["find_items", "choose_new_options", "choose_payment_method", "check_gift_card_balance"]

CHECKS = {
    "cancel_order": [*ORDER_PENDING, "check_reason"],
    "modify_order_address": [*ORDER_PENDING, "check_new_address"],
    "modify_order_payment": [
        *ORDER_PENDING,
        "check_single_payment",
        "choose_payment_method",
        "check_gift_card_balance",
    ],
    "modify_order_items": [*ORDER_PENDING, *SWAP],
    "exchange_items": [*ORDER_DELIVERED, *SWAP],
    "return_items": [
        *ORDER_DELIVERED,
        "check_returns_not_blocked",
        "find_items",
        "choose_refund_method",
    ],
    "modify_default_address": ["check_new_address"],
}


@pytest.fixture(scope="module")
def graph():
    return compiled_graph().get_graph(xray=True)


def nodes_of(graph, workflow: str) -> set[str]:
    prefix = f"{workflow}:"
    return {n[len(prefix) :] for n in graph.nodes if n.startswith(prefix)}


@pytest.mark.parametrize("workflow", CHECKS)
def test_each_workflow_names_its_checks(graph, workflow):
    expected = SHARED | set(CHECKS[workflow])
    if workflow == "return_items":
        expected |= APPROVAL
    assert nodes_of(graph, workflow) == expected


@pytest.mark.parametrize("workflow", CHECKS)
def test_checks_run_in_order(graph, workflow):
    chain = ["interpret", *CHECKS[workflow], "prepare_summary"]
    edges = {(e.source, e.target) for e in graph.edges}
    for a, b in pairwise(chain):
        assert (f"{workflow}:{a}", f"{workflow}:{b}") in edges


def test_only_returns_have_the_approval_path(graph):
    with_approval = [w for w in CHECKS if nodes_of(graph, w) & APPROVAL]
    assert with_approval == ["return_items"]
