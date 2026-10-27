"""Checks that need no network and no keys: segment planning, reference order, the Chinese prompt, the rewrite checks,
the workflow graph."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from h3ref import config, film, rewrite, rh  # noqa: E402

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/film.example.json"


def shots(lengths):
    out, t = [], 0.0
    for i, n in enumerate(lengths):
        out.append({"start": t, "end": t + n, "camera": "近景", "action": f"小林做第{i + 1}件事"})
        t += n
    return out


def a_film(n_cast=2, board=True):
    return {"aspect": "9:16", "look": "真人实拍短片。", "setting": "一间小厨房。", "speech": "zh",
            "board": "/b.jpg" if board else None,
            "cast": [{"name": n, "picture": f"/{n}.jpg", "look": "短发"} for n in ["小林", "阿婆", "小周"][:n_cast]]}


class Plan(unittest.TestCase):
    def test_segments_cut_at_shot_boundaries_and_stay_within_15_s(self):
        sh = shots([2, 2.5, 3, 2, 2, 2.5, 3, 2, 2, 2.5, 1.5])
        segs = film.plan(sh)
        self.assertEqual(sum(len(s["shots"]) for s in segs), len(sh))
        for s in segs:
            self.assertLessEqual(s["end"] - s["start"], 15)
            self.assertTrue(config.MIN_SECONDS <= s["seconds"] <= config.MAX_SECONDS)
        self.assertEqual(len(segs), 2)

    def test_short_film_is_one_segment_of_at_least_5_s(self):
        segs = film.plan(shots([1.5, 2]))
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["seconds"], 5)

    def test_example_film_loads(self):
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.assertIn(data["aspect"], config.SIZES)
        self.assertTrue(all(s.get("action") for s in data["shots"]))


class Refs(unittest.TestCase):
    def test_shot_mode_fits_nine_pictures_dropping_the_board_first(self):
        f = a_film(3)
        seg = film.plan(shots([2] * 7))[0]
        seg["shots"] = [{**s, "action": "大家一起" + s["action"]} for s in seg["shots"]]
        pics = film.refs(f, seg, "shot")
        self.assertLessEqual(len(pics), config.MAX_IMAGES)
        self.assertNotIn("board", [p["kind"] for p in pics])          # 3 people + 7 frames = 10: the board went
        self.assertEqual(sum(1 for p in pics if p["kind"] == "frame"), 6)

    def test_only_the_characters_in_the_segment(self):
        f = a_film(3)
        seg = film.plan(shots([2, 2]))[0]
        seg["shots"][0]["action"] = "阿婆端出一碗面"
        names = [p["cast"]["name"] for p in film.refs(f, seg, "shot") if p["kind"] == "cast"]
        self.assertEqual(names, ["小林", "阿婆"])

    def test_chained_modes_add_the_previous_last_frame(self):
        f = a_film(1, board=False)
        segs = film.plan(shots([3] * 9))
        self.assertEqual([p["kind"] for p in film.refs(f, segs[1], "none")], ["cast", "prev"])
        self.assertEqual([p["kind"] for p in film.refs(f, segs[1], "first")], ["cast", "frame", "prev"])
        self.assertEqual([p["kind"] for p in film.refs(f, segs[0], "first")], ["cast", "frame"])


class Prompt(unittest.TestCase):
    def test_pictures_are_named_in_order_and_times_are_segment_relative(self):
        f = a_film(1)
        segs = film.plan(shots([3] * 9))
        seg = segs[1]
        seg["shots"][0]["line"] = "(小林) 好香！"
        text = film.compile_prompt(f, seg, film.refs(f, seg, "shot"), len(segs))
        self.assertIn("这是一条连续短片的第2段", text)
        self.assertIn("参考图片1是本片的角色、道具和场景参考总表", text)
        self.assertIn("参考图片2只约束小林身份：短发。", text)
        self.assertIn("参考图片3是第1个镜头（0–3秒）开始那一瞬的画面，也就是 [Shot 1] 的首帧", text)
        self.assertIn("0–3秒：【近景】", text)
        self.assertIn("台词：(小林) 好香！", text)
        self.assertIn("中文台词", text)


GOOD = """subject_definitions:
<Subject 1> is the woman in <Picture 1>.

summary:
[reference generation] A woman cooks.

retention_analysis:
<Subject 1>: fully_preserved.

detailed_description:
A live-action vertical 9:16 short.
[Shot 1] A close-up of <Subject 1> stirring a pot.

overall_soundscape:
A pot bubbles.

non_diegetic_music:
gentle piano"""


class Rewrite(unittest.TestCase):
    def test_a_clean_rewrite_passes_and_music_is_forced_off(self):
        self.assertEqual(rewrite.problems(GOOD, 1), [])
        out = rewrite.assemble(rewrite.sections(GOOD))
        self.assertTrue(out.endswith("non_diegetic_music:\nN/A"))
        self.assertTrue(rewrite.is_rewritten(GOOD))
        self.assertFalse(rewrite.is_rewritten("根据下面文字分镜生成一段12秒竖屏视频"))

    def test_negative_phrasing_and_missing_pictures_are_caught(self):
        bad = GOOD.replace("A pot bubbles.", "A pot bubbles, with no background music.").replace("<Picture 1>", "<Picture 3>")
        issues = rewrite.problems(bad, 1)
        self.assertTrue(any("negative instruction" in x for x in issues))
        self.assertTrue(any("<Picture 3>" in x for x in issues))

    def test_sections_out_of_order(self):
        swapped = GOOD.replace("summary:", "TMP:").replace("retention_analysis:", "summary:").replace("TMP:", "retention_analysis:")
        self.assertTrue(rewrite.problems(swapped, 1))

    def test_guide_from_a_local_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write("guide text")
        old = config.GUIDE_FILE
        try:
            config.GUIDE_FILE = fh.name
            self.assertIn("guide text", rewrite.request("提示词", 2, 12, "9:16"))
        finally:
            config.GUIDE_FILE = old


class Graph(unittest.TestCase):
    def test_graph_takes_our_inputs(self):
        g = rh.graph("hello", ["a.png", "b.png"], 13, "9:16", 7)
        self.assertEqual(g[rh.PROMPT]["inputs"]["prompt"], "hello")
        self.assertEqual(g[rh.LENGTH]["inputs"]["select"], 9)                 # input9 = 13 s
        self.assertEqual((g[rh.SIZE]["inputs"]["width"], g[rh.SIZE]["inputs"]["height"]), (544, 960))
        self.assertEqual(g[rh.SEED]["inputs"]["noise_seed"], 7)
        self.assertEqual([g[n]["inputs"]["image"] for n in rh.IMAGES[:3]], ["a.png", "b.png", "None"])
        self.assertEqual(g[rh.STEPS]["inputs"]["steps"], config.H3_STEPS)

    def test_length_limits_and_picture_limit(self):
        self.assertEqual(rh.graph("x", ["a"], 5, "16:9", 1)[rh.LENGTH]["inputs"]["select"], 1)
        self.assertEqual(rh.graph("x", ["a"], 15, "1:1", 1)[rh.LENGTH]["inputs"]["select"], 11)
        with self.assertRaises(ValueError):
            rh.graph("x", ["a"] * 10, 10, "9:16", 1)

    def test_bundled_graph_has_no_leftover_uploads(self):
        g = json.loads(config.RH_GRAPH.read_text(encoding="utf-8"))
        loads = [n["inputs"].get("image") for n in g.values() if n.get("class_type") == "LoadImage"]
        self.assertTrue(all(x == "None" for x in loads))


if __name__ == "__main__":
    unittest.main()
