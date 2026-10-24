"""IDENTIFY -> the 4 common problems -> pinch one -> fix steps (pinch a step to expand) -> web search.
Runs without the camera, the model or the network (the model and the web are mocked)."""
import time

import numpy as np
import pytest

from agents import llm, problems as PR, scout, vision


def _james(monkeypatch):
    import james as JM
    monkeypatch.setattr(JM.llm, "available", lambda *a, **k: False)
    return JM, JM.James()


PHONE = {"object": "smartphone", "brand": None, "model": None, "model_confidence": 0.0,
         "problem": "no visible damage", "problem_confidence": 1.0, "part_needed": None, "fix": [],
         "common_problems": [{"title": "Cracked screen", "sign": "cracks on the glass", "seen": False},
                             {"title": "Battery drains fast", "sign": "needs charging twice a day", "seen": False},
                             {"title": "Camera not focusing", "sign": "blurry photos", "seen": True}]}


# ---------- the list ----------

def test_every_catalog_kind_has_four_problems_with_steps():
    for kind, c in PR.CATALOG.items():
        assert len(c["problems"]) == 4, kind
        for p in c["problems"]:
            assert p["title"] and p["sign"] and len(p["steps"]) >= 3, (kind, p["title"])


@pytest.mark.parametrize("obj,kind", [("Smartphone", "phone"), ("plastic water bottle", "bottle"),
                                      ("sunglasses", "glasses"), ("wireless earbuds", "audio"),
                                      ("headphones", "audio"), ("wrist watch", "watch"), ("MacBook", "laptop"),
                                      ("USB-C cable", "cable"), ("banana", "generic")])
def test_category(obj, kind):
    assert PR.category(obj) == kind


def test_merge_no_visible_damage_means_nothing_is_marked_seen():
    """The model said 'seen' for the camera but also 'no visible damage': trust neither as seen."""
    out = PR.merge("smartphone", PHONE["common_problems"], "no visible damage")
    assert len(out) == 4 and not any(p["seen"] for p in out)
    assert [p["source"] for p in out][:3] == ["model"] * 3 and out[3]["source"] == "catalog"


def test_merge_visible_damage_goes_first_and_duplicates_are_dropped():
    out = PR.merge("smartphone", [{"title": "Battery drains fast", "sign": "", "seen": False}], "Cracked screen glass")
    assert out[0]["title"] == "Cracked screen glass" and out[0]["seen"]
    titles = [p["title"] for p in out]
    assert "Cracked screen or back glass" not in titles                 # same problem as the seen one
    assert len(out) == 4


def test_merge_with_no_model_uses_the_catalog():
    out = PR.merge("water bottle", None, None)
    assert [p["title"] for p in out] == [p["title"] for p in PR.CATALOG["bottle"]["problems"]]


# ---------- the fix ----------

def test_fix_from_model(monkeypatch):
    monkeypatch.setattr(vision.llm, "chat_json", lambda *a, **k: {
        "steps": [{"title": "Clean the lens", "detail": "Wipe it."}, {"title": "Restart", "detail": "Restart it."},
                  {"title": " ", "detail": "dropped: no title"}], "safety": None, "pro_when": "If black.",
        "part_needed": ""})
    f = vision.fix_for("smartphone", "", {"title": "Camera not focusing"})
    assert f["source"] == "model" and [s["title"] for s in f["steps"]] == ["Clean the lens", "Restart"]
    assert f["part_needed"] is None and f["pro_when"] == "If black."


def test_fix_falls_back_to_the_catalog_when_the_model_is_down(monkeypatch):
    def down(*a, **k):
        raise llm.LLMUnavailable("down")
    monkeypatch.setattr(vision.llm, "chat_json", down)
    f = vision.fix_for("smartphone", "", {"title": "Cracked screen"})            # matched to the catalog entry
    assert f["source"] == "catalog" and len(f["steps"]) >= 3 and f["safety"]
    assert vision.fix_for("banana", "", {"title": "Too ripe"})["source"] == "none"


def test_web_query_no_longer_assumes_screen_glass():
    assert scout.query_for(None, "TATA", "COPPER+", "Leaking cap or seal", "water bottle") == \
        "how to fix leaking cap or seal TATA COPPER+"
    assert "screen glass" not in scout.query_for(None, None, None, None, "water bottle")
    assert scout.query_for("replacement battery", None, "", "Battery", "smartphone") == "replacement battery for smartphone"


def test_pick_number():
    import james as JM
    assert [JM.pick_number(t) for t in ("problem two", "Number 3.", "the first one", "option 4", "2")] == [2, 3, 1, 4, 2]
    assert JM.pick_number("redmi note 10") is None and JM.pick_number("it is a two door fridge") is None


# ---------- the flow in james.py ----------

def _identified(j, monkeypatch, result=PHONE):
    import james as JM
    j.s.mode = "IDENTIFYING"
    j.s.t_identify = time.time()
    r = dict(result)
    r["problems"] = PR.merge(r["object"], r["common_problems"], r["problem"])
    j.q.put(("identified", r))
    j.drain()


def test_identify_shows_problems_then_fix_steps_then_search(monkeypatch):
    JM, j = _james(monkeypatch)
    _identified(j, monkeypatch)
    assert j.s.mode == "PROBLEMS" and len(j.s.problems) == 4
    assert [b.key for b in j.task_actions()] == ["talk", "cancel"]        # model unknown: Say model
    asked = []
    monkeypatch.setattr(JM.vision, "fix_for", lambda obj, name, p: asked.append((obj, p["title"])) or
                        {"steps": [{"title": "A", "detail": "aa"}, {"title": "B", "detail": "bb"}],
                         "safety": "Careful.", "pro_when": None, "part_needed": "replacement battery", "source": "model"})
    j.press("prob:1")
    assert j.s.mode == "FIXING" and j.s.choice == 1
    for _ in range(100):                                                   # the fix runs on a thread
        if not j.q.empty():
            break
        time.sleep(0.01)
    j.drain()
    assert asked == [("smartphone", "Battery drains fast")]
    assert j.s.mode == "FIXES" and j.s.expanded == 0
    keys = [b.key for b in j.task_actions()]
    assert keys[0] == "parts" and j.task_actions()[0].label == "Find part" and keys[-1] == "done"
    j.press("step:1"); assert j.s.expanded == 1
    j.press("step:1"); assert j.s.expanded is None
    searched = []
    monkeypatch.setattr(JM.James, "_search", lambda self, q: searched.append(q))
    j.press("parts")
    time.sleep(0.05)
    assert j.s.mode == "SEARCHING" and j.s.query == "replacement battery for smartphone"
    j.press("back")                                                         # from the search, back is not offered,
    assert j.s.mode == "PROBLEMS" and j.s.fix is None                       # but the key still returns to the list


def test_late_fix_for_another_problem_is_ignored(monkeypatch):
    JM, j = _james(monkeypatch)
    _identified(j, monkeypatch)
    j.s.mode, j.s.choice = "FIXING", 2
    j.q.put(("fix", 0, {"steps": [{"title": "x", "detail": ""}] * 3, "source": "model"}))
    j.drain()
    assert j.s.mode == "FIXING" and j.s.fix is None


def test_voice_picks_a_problem_by_number_or_notes_the_model(monkeypatch):
    JM, j = _james(monkeypatch)
    _identified(j, monkeypatch)
    chosen = []
    monkeypatch.setattr(JM.James, "choose", lambda self, i: chosen.append(i))
    j._before_listen = "PROBLEMS"
    j.on_heard("problem two", 0.8)
    assert chosen == [1]
    j.on_heard("it's a redmi note 10", 0.8)
    assert j.s.model_text == "redmi note 10" and j.s.mode == "PROBLEMS"


def test_bottle_is_not_a_phone_and_has_no_glass_buttons(monkeypatch):
    JM, j = _james(monkeypatch)
    bottle = dict(PHONE, object="plastic water bottle", brand="TATA", model="COPPER+", common_problems=[])
    _identified(j, monkeypatch, bottle)
    assert [p["title"] for p in j.s.problems][0] == "Dent in the body" and not j.is_phone()
    j.s.fix, j.s.mode = {"steps": [], "part_needed": None, "source": "none"}, "FIXES"
    assert "ud_glass" not in [b.key for b in j.task_actions()]


@pytest.mark.parametrize("W,H", [(1280, 720), (1920, 1080)])
def test_problems_and_fix_panels_render(monkeypatch, W, H):
    import os
    import cv2
    JM, j = _james(monkeypatch)
    _identified(j, monkeypatch, dict(PHONE, problem="Cracked screen glass", common_problems=[
        {"title": "Cracked screen glass", "sign": "spider lines top left", "seen": True}] + PHONE["common_problems"]))
    j.s.thumb = np.full((200, 200, 3), 90, np.uint8)
    qa = os.environ.get("JAMES_QA_DIR")
    frames = {}
    frames["problems"] = j.draw(np.full((H, W, 3), 40, np.uint8), None, "prob:0", 0.4)
    rows = [b for b in j._dock if b.key.startswith("prob:")]
    assert len(rows) == 4 and all(b.rect[2] > 200 for b in rows)
    j.s.mode, j.s.choice = "FIXING", 0
    frames["fixing"] = j.draw(np.full((H, W, 3), 40, np.uint8), None, None, 0.0)
    j.s.mode, j.s.expanded, j.s.fix_took_s = "FIXES", 1, 7.4
    j.s.fix = PR.catalog_fix("smartphone", "Cracked screen or back glass")
    j.s.fix["source"] = "model"
    frames["fixes"] = j.draw(np.full((H, W, 3), 40, np.uint8), None, "step:2", 0.0)
    steps = [b for b in j._dock if b.key.startswith("step:")]
    assert len(steps) >= 3 and steps[1].open
    for name, out in frames.items():
        assert out.shape == (H, W, 3)
        if qa:
            cv2.imwrite(os.path.join(qa, f"{name}_{W}.png"), out)


def test_live_duplicate_from_log_is_merged():
    """Log 00:53:09: the model said 'Screen cracks from drops' 4 times; the catalog's cracked-screen entry slipped in too."""
    out = PR.merge("smartphone", [{"title": "Screen cracks from drops", "sign": "", "seen": True}] * 4, "cracked screen")
    titles = [p["title"] for p in out]
    assert titles.count("Cracked screen or back glass") == 0 and len(out) == 4
    assert not PR.same_problem("Battery drains fast", "Not charging or charges slowly")
