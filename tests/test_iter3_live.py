"""Regression tests from the 03:25 to 03:28 IST live run on the Mac (build 8f62724d877d)."""
import commands as C
import talk as T


def test_open_then_full_stop_is_one_command():                  # 03:27:53 -> model opened Notes, not Keynote
    t = "Open. Key Note on my MacBook."
    st = C.plan(t)
    assert [s.kind for s in st] == ["open"] and st[0].arg.startswith("key note") and C.complexity(t, st) == ""


def test_open_then_full_stop_matches_keynote_not_notes():
    import launcher_apps as LA
    apps = {"Keynote": "/Applications/Keynote.app", "Notes": "/System/Applications/Notes.app"}
    assert LA.find_app(C.plan("Open. Key Note on my MacBook.")[0].arg, apps)[0] == "Keynote"


def test_github_repo_which_is_related_to():                     # 03:27:30
    st = C.plan("Open GitHub and search for the repo, which is related to weather.")
    assert [(s.kind, s.site, s.arg) for s in st] == [("search", "github", "weather")]


def test_plural_objects_read_naturally():                       # 03:26:20 "That appears to be a sunglasses"
    said = T.identified("sunglasses", "no visible damage", [{"title": "Lens scratches"}])
    assert said.startswith("That appears to be a pair of sunglasses. I can't see any damage.")
    assert ",:" not in said and ", :" not in said
    assert T.article("smartphone") == "a smartphone" and T.article("iPhone") == "an iPhone"


def test_no_caution_when_the_model_says_none():                 # 03:26:47 "A word of caution: None."
    fix = {"steps": [{"title": "Check the coating", "detail": "Look closely."}], "safety": "None"}
    assert "caution" not in T.fix_intro({"title": "x"}, fix)
    assert "There is one step." in T.fix_intro({"title": "x"}, fix)
    fix["safety"] = "Unplug it first"
    assert "A word of caution: Unplug it first." in T.fix_intro({"title": "x"}, fix)
