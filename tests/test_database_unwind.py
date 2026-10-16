"""Tests for unwind-related DB operations."""
from services.user_context import require_user_id
import asyncio
import os
import tempfile
import pytest


@pytest.fixture
def temp_db(monkeypatch):
    """Use a temp SQLite file for tests."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr("config.config.db_path", path)
    # Reset cached module state if any
    from database import init_db
    asyncio.run(init_db())
    from tests.portfolio_fixtures import seed_business_owner
    seed_business_owner(path)
    yield path
    os.unlink(path)


def test_create_and_get_unwind_plan(temp_db):
    from database import save_unwind_plan, get_unwind_plan

    asyncio.run(save_unwind_plan("601212", total_budget=10000.0, user_id=require_user_id()))
    plan = asyncio.run(get_unwind_plan("601212", user_id=require_user_id()))
    assert plan is not None
    assert plan["stock_code"] == "601212"
    assert plan["total_budget"] == 10000.0
    assert plan["used_budget"] == 0
    assert plan["status"] == "active"


def test_add_tranches(temp_db):
    from database import save_unwind_plan, add_tranche, get_tranches

    asyncio.run(save_unwind_plan("601212", total_budget=10000.0, user_id=require_user_id()))
    asyncio.run(add_tranche("601212", idx=1, trigger_price=7.80, shares=200, requires_health="any", user_id=require_user_id()))
    asyncio.run(add_tranche("601212", idx=2, trigger_price=7.20, shares=300, requires_health="yellow", user_id=require_user_id()))

    rows = asyncio.run(get_tranches("601212", user_id=require_user_id()))
    assert len(rows) == 2
    assert rows[0]["idx"] == 1
    assert rows[0]["trigger_price"] == 7.80
    assert rows[1]["requires_health"] == "yellow"


def test_mark_tranche_executed(temp_db):
    from database import save_unwind_plan, add_tranche, mark_tranche_executed, get_tranches

    asyncio.run(save_unwind_plan("601212", total_budget=10000.0, user_id=require_user_id()))
    asyncio.run(add_tranche("601212", idx=1, trigger_price=7.80, shares=200, user_id=require_user_id()))

    tranches = asyncio.run(get_tranches("601212", user_id=require_user_id()))
    tranche_id = tranches[0]["id"]

    asyncio.run(mark_tranche_executed(tranche_id, executed_price=7.82, user_id=require_user_id()))

    tranches = asyncio.run(get_tranches("601212", user_id=require_user_id()))
    assert tranches[0]["status"] == "executed"
    assert tranches[0]["executed_price"] == 7.82


def test_position_action_log(temp_db):
    from database import log_position_action, get_position_actions

    asyncio.run(log_position_action("601212", "ADD", price=7.82, shares=200, tranche_id=1, user_id=require_user_id()))
    asyncio.run(log_position_action("601212", "T_BUY", price=8.04, shares=100, user_id=require_user_id()))

    actions = asyncio.run(get_position_actions("601212", user_id=require_user_id()))
    assert len(actions) == 2
    assert actions[0]["action_type"] in ("ADD", "T_BUY")
