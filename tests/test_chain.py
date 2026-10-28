import asyncio

import pytest

from imnotarobot.chain import Chain, State, today
from imnotarobot.layers import Unsolvable
from helpers import FakeLayer

TASK = {"type": "ImageToTextTask", "body": "aGVsbG8="}


def solve(chain, task=TASK):
    return asyncio.run(chain.solve(task))


def test_confident_first_layer_wins_and_later_layers_are_not_called():
    local, vlm = FakeLayer("local", [("abc", 0.95)]), FakeLayer("vlm", [("xyz", 0.99)])
    r = solve(Chain([local, vlm]))
    assert (r.layer, r.solution) == ("local", {"text": "abc"})
    assert vlm.calls == 0


def test_unsure_layer_escalates_to_next():
    local, vlm = FakeLayer("local", [("abc", 0.4)]), FakeLayer("vlm", [("abd", 0.9)])
    r = solve(Chain([local, vlm]))
    assert r.layer == "vlm"
    assert "local: не уверен (0.40)" in r.meta["tried"]


def test_two_unsure_layers_that_agree_are_accepted():
    local, vlm, human = FakeLayer("local", [("abc", 0.4)]), FakeLayer("vlm", [("abc", 0.5)]), FakeLayer("human", [("zzz", 1.0)])
    r = solve(Chain([local, vlm, human]))
    assert (r.layer, r.meta["agreed_with"]) == ("vlm", "local")
    assert human.calls == 0


def test_failures_and_missing_config_fall_through_to_the_last_layer():
    local = FakeLayer("local", [Unsolvable("bad constraints")])
    vlm = FakeLayer("vlm", [("x", 1.0)], available=False)
    human = FakeLayer("human", [("final", 1.0)], min_confidence=1.0)
    r = solve(Chain([local, vlm, human]))
    assert r.layer == "human"
    assert r.meta["tried"] == ["local: bad constraints", "vlm: нет ключа"]


def test_best_unsure_answer_is_returned_flagged_when_nothing_else_is_left():
    r = solve(Chain([FakeLayer("local", [("abc", 0.3)]), FakeLayer("vlm", [("abd", 0.6)])]))
    assert (r.layer, r.meta["low_confidence"]) == ("vlm", True)


def test_all_layers_failing_raises_with_every_reason():
    chain = Chain([FakeLayer("local", [Unsolvable("no")]), FakeLayer("vlm", [Unsolvable("nope")])])
    with pytest.raises(Unsolvable, match="local: no; vlm: nope"):
        solve(chain)


def test_report_skips_that_layer_for_the_same_image_and_is_forwarded():
    local, vlm = FakeLayer("local", [("abc", 0.95)]), FakeLayer("vlm", [("abd", 0.9)])
    chain = Chain([local, vlm])
    first = solve(chain)
    asyncio.run(chain.report_incorrect(TASK, first))
    second = solve(chain)
    assert (first.layer, second.layer, local.reports) == ("local", "vlm", 1)
    other_image = solve(chain, {**TASK, "body": "b3RoZXI="})
    assert other_image.layer == "local"


def test_layer_is_skipped_for_a_kind_after_most_of_its_answers_were_reported():
    local, vlm = FakeLayer("local", [("abc", 0.95)]), FakeLayer("vlm", [("abd", 0.9)])
    chain = Chain([local, vlm], State(skip_after_reports=3))
    for i in range(3):
        task = {**TASK, "body": f"img{i}"}
        asyncio.run(chain.report_incorrect(task, solve(chain, task)))
    assert solve(chain, {**TASK, "body": "fresh"}).layer == "vlm"
    other_kind = {**TASK, "body": "fresh2", "comment": "type the red letters"}
    assert solve(chain, other_kind).layer == "local"


def test_state_survives_a_restart(tmp_path):
    path = str(tmp_path / "state.json")
    local, vlm = FakeLayer("local", [("abc", 0.95)]), FakeLayer("vlm", [("abd", 0.9)])
    chain = Chain([local, vlm], State(path))
    asyncio.run(chain.report_incorrect(TASK, solve(chain)))
    reborn = Chain([local, vlm], State(path))
    assert solve(reborn).layer == "vlm"
    assert reborn.state.daily[today()]["local"]["solved"] == 1


def test_daily_limit_stops_a_paid_layer():
    class Paid(FakeLayer):
        async def solve(self, task):
            r = await super().solve(task)
            r.cost = 0.6
            return r

    human = Paid("human", [("ok", 1.0)], min_confidence=1.0)
    chain = Chain([human], daily_limits={"human": 1.0})
    solve(chain, {**TASK, "body": "a"})
    solve(chain, {**TASK, "body": "b"})
    with pytest.raises(Unsolvable, match="дневной лимит"):
        solve(chain, {**TASK, "body": "c"})
    assert human.calls == 2


def test_rejected_memory_is_bounded():
    state = State(max_rejected=3)
    for i in range(5):
        state.bad("k", "local", f"img{i}")
    assert list(state.rejected) == ["img2", "img3", "img4"]


def test_shared_cloud_limit_covers_every_model():
    class Paid(FakeLayer):
        async def solve(self, task):
            r = await super().solve(task)
            r.cost = 0.6
            return r

    a = Paid("vlm:a", [("x", 0.95)])
    b = Paid("vlm:b", [("y", 0.95)])
    chain = Chain([a, b], daily_limits={"cloud": 1.0})
    solve(chain, {**TASK, "body": "1"})
    solve(chain, {**TASK, "body": "2"})
    with pytest.raises(Unsolvable, match="общий дневной лимит"):
        solve(chain, {**TASK, "body": "3"})
    assert (a.calls, b.calls) == (2, 0)


def test_unsure_answers_are_passed_to_layers_that_take_them():
    class Taker(FakeLayer):
        accepts_prior = True

        async def solve(self, task, prior=None):
            self.prior = prior
            return await super().solve(task)

    local, cloud = FakeLayer("local", [("abc", 0.3)]), Taker("cloud", [("abc", 0.5)])
    r = asyncio.run(Chain([local, cloud], State()).solve({"type": "ImageToTextTask", "body": ""}))
    assert cloud.prior == [{"text": "abc"}] and r.layer == "cloud" and r.meta["agreed_with"] == "local"


def test_silent_service_skips_its_other_models_and_goes_to_the_next_service():
    from imnotarobot.layers import Unavailable

    low = FakeLayer("low", [Unavailable("All accounts failed")])
    high = FakeLayer("high", [("abc", 1.0)])
    low.upstream = high.upstream = "antigravity"
    human = FakeLayer("human", [("xyz", 1.0)])
    r = asyncio.run(Chain([low, high, human], State()).solve({"type": "ImageToTextTask", "body": ""}))
    assert r.layer == "human" and high.calls == 0


def test_every_layer_a_captcha_reaches_is_counted():
    local, human = FakeLayer("local", [Unsolvable("no")] * 2), FakeLayer("human", [("a", 1.0)] * 2)
    chain = Chain([local, human])
    solve(chain)
    solve(chain)
    assert chain.sent_since("human", 0) == 2 and chain.sent_since("local", 0) == 2


def test_a_total_failure_keeps_its_notes_for_the_journal():
    chain = Chain([FakeLayer("local", [Unsolvable("нет текста")]), FakeLayer("vlm", [Unsolvable("нет ответа")])])
    with pytest.raises(Unsolvable) as info:
        solve(chain)
    assert info.value.notes == ["local: нет текста", "vlm: нет ответа"]
