import tempfile
import unittest
from pathlib import Path
from unittest import mock

import easy_webui as ui


class EasyWebUITest(unittest.TestCase):
    def setUp(self):
        import numpy as np
        import soundfile as sf

        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name).resolve()
        self.original_paths = {
            "OUTPUT_DIR": ui.OUTPUT_DIR,
            "UPLOAD_DIR": ui.UPLOAD_DIR,
            "DEFAULT_VOICE": ui.DEFAULT_VOICE,
            "DEFAULT_EMOTION": ui.DEFAULT_EMOTION,
            "BASELINE_J": ui.BASELINE_J,
            "PREFERENCE_PATH": ui.PREFERENCE_PATH,
            "COMPARISON_PATH": ui.COMPARISON_PATH,
            "EMOTION_LIBRARY_RECORDINGS": ui.EMOTION_LIBRARY_RECORDINGS,
        }
        ui.OUTPUT_DIR = root / "outputs"
        ui.UPLOAD_DIR = ui.OUTPUT_DIR / "uploads"
        ui.DEFAULT_VOICE = root / "default_voice.wav"
        ui.DEFAULT_EMOTION = root / "default_emotion.wav"
        ui.BASELINE_J = root / "baseline.wav"
        ui.PREFERENCE_PATH = ui.OUTPUT_DIR / "preference_ratings.jsonl"
        ui.COMPARISON_PATH = ui.OUTPUT_DIR / "comparison_ratings.jsonl"
        ui.EMOTION_LIBRARY_RECORDINGS = root / "emotion_library"
        ui.EMOTION_LIBRARY_RECORDINGS.mkdir(parents=True)
        audio = np.zeros(2205, dtype=np.float32)
        sf.write(ui.DEFAULT_VOICE, audio, 22050)
        sf.write(ui.DEFAULT_EMOTION, audio, 22050)
        for number in range(1, 65):
            sf.write(
                ui.EMOTION_LIBRARY_RECORDINGS / f"{number:02d}_fixture.wav",
                audio,
                22050,
            )

    def tearDown(self):
        for name, value in self.original_paths.items():
            setattr(ui, name, value)
        self.temporary.cleanup()

    def base_payload(self, mode):
        return {
            "text": "你好，这是一段测试。",
            "voice_path": str(ui.DEFAULT_VOICE),
            "emotion_path": str(ui.DEFAULT_EMOTION),
            "mode": mode,
            "emo_alpha": 0.75,
            "duration_factor": 1.0,
            "lang": "ZH",
            "emo_vector": [0.0] * 8,
        }

    def test_voice_only_mapping(self):
        mode, kwargs = ui.build_infer_kwargs(
            self.base_payload("voice_only"), Path("/tmp/test.wav")
        )
        self.assertEqual(mode, "voice_only")
        self.assertIsNone(kwargs["emo_audio_prompt"])
        self.assertIsNone(kwargs["emo_vector"])
        self.assertFalse(kwargs["use_emo_text"])
        self.assertFalse(kwargs["use_random"])
        self.assertEqual(kwargs["duration_factor"], 1.0)

    def test_emotion_audio_mapping(self):
        mode, kwargs = ui.build_infer_kwargs(
            self.base_payload("emotion_audio"), Path("/tmp/test.wav")
        )
        self.assertEqual(mode, "emotion_audio")
        self.assertEqual(kwargs["emo_audio_prompt"], str(ui.DEFAULT_EMOTION))
        self.assertFalse(kwargs["use_emo_text"])

    def test_text_auto_mapping(self):
        payload = self.base_payload("text_auto")
        payload["emo_alpha"] = 0.6
        mode, kwargs = ui.build_infer_kwargs(payload, Path("/tmp/test.wav"))
        self.assertEqual(mode, "text_auto")
        self.assertIsNone(kwargs["emo_audio_prompt"])
        self.assertIsNone(kwargs["emo_vector"])
        self.assertTrue(kwargs["use_emo_text"])
        self.assertIsNone(kwargs["emo_text"])
        self.assertEqual(kwargs["emo_alpha"], 0.6)

    def test_manual_vector_mapping(self):
        payload = self.base_payload("manual_vector")
        payload["emo_vector"] = [0.1, 0.2, 0.3, 0.0, 0.0, 0.0, 0.0, 0.4]
        _, kwargs = ui.build_infer_kwargs(payload, Path("/tmp/test.wav"))
        self.assertEqual(kwargs["emo_vector"], payload["emo_vector"])
        self.assertFalse(kwargs["use_emo_text"])

    def test_smart_script_mapping_and_plan_validation(self):
        payload = self.base_payload("smart_script")
        mode, kwargs = ui.build_infer_kwargs(payload, Path("/tmp/test.wav"))
        self.assertEqual(mode, "smart_script")
        self.assertIsNone(kwargs["emo_audio_prompt"])
        self.assertIsNone(kwargs["emo_vector"])
        plan = ui.validate_emotion_plan(
            payload["text"],
            [{"text": payload["text"], "role": "陈述", "vector": [0.0] * 8}],
        )
        self.assertEqual(plan[0]["text"], payload["text"])

    def test_performance_sections_keep_text_and_stay_bounded(self):
        text = "标题。\n第一，甲。\n第二，乙。\n第三，丙。\n第四，丁。"
        sections = ui.prepare_performance_sections(text, target_chars=12)
        self.assertGreater(len(sections), 1)
        self.assertEqual(
            "".join(sections).replace("\n", ""), text.replace("\n", "")
        )

    def test_performance_plan_requires_matching_short_recordings(self):
        import numpy as np
        import soundfile as sf

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recording = root / "take.wav"
            sf.write(recording, np.zeros(22050), 22050)
            old_upload = ui.UPLOAD_DIR
            ui.UPLOAD_DIR = root
            try:
                plan = ui.validate_performance_plan(
                    "第一句。第二句。",
                    [
                        {"text": "第一句。", "emotion_path": str(recording)},
                        {"text": "第二句。", "emotion_path": str(recording)},
                    ],
                )
                self.assertEqual(len(plan), 2)
                self.assertEqual(plan[0]["source_duration_seconds"], 1.0)
                explicit = ui.validate_performance_plan(
                    "第一句。",
                    [{
                        "text": "第一句。",
                        "emotion_path": str(recording),
                        "duration_factor": 1.05,
                        "pause_after_ms": 440,
                    }],
                )
                self.assertEqual(explicit[0]["duration_factor"], 1.05)
                self.assertEqual(explicit[0]["pause_after_ms"], 440)
                with self.assertRaisesRegex(ValueError, "发生变化"):
                    ui.validate_performance_plan(
                        "另一句。",
                        [{"text": "第一句。", "emotion_path": str(recording)}],
                    )
            finally:
                ui.UPLOAD_DIR = old_upload

    def test_performance_generation_uses_each_recording(self):
        import numpy as np
        import soundfile as sf

        class FakeModel:
            def __init__(self):
                self.calls = []

            def infer(self, **kwargs):
                self.calls.append(kwargs)
                sf.write(kwargs["output_path"], np.full(2205, 0.1), 22050)

        plan = [
            {
                "text": "第一句。",
                "emotion_path": "/tmp/one.wav",
                "source_duration_seconds": 1.0,
                "duration_factor": 1.0,
                "pause_after_ms": 220,
            },
            {
                "text": "第二句。",
                "emotion_path": "/tmp/two.wav",
                "source_duration_seconds": 1.0,
                "duration_factor": 1.05,
                "pause_after_ms": 220,
            },
        ]
        kwargs = {
            "spk_audio_prompt": str(ui.DEFAULT_VOICE),
            "lang": "ZH",
            "emo_alpha": 0.75,
        }
        model = FakeModel()
        with tempfile.TemporaryDirectory() as directory:
            old_output_dir = ui.OUTPUT_DIR
            ui.OUTPUT_DIR = Path(directory)
            output = Path(directory) / "performance.wav"
            try:
                with mock.patch("torch.mps.synchronize"):
                    plan_path, _ = ui.generate_performance_script(
                        model, kwargs, plan, output
                    )
            finally:
                ui.OUTPUT_DIR = old_output_dir
            self.assertTrue(output.is_file())
            self.assertTrue(plan_path.is_file())
        self.assertEqual(
            [call["emo_audio_prompt"] for call in model.calls],
            ["/tmp/one.wav", "/tmp/two.wav"],
        )
        self.assertTrue(all(not call["use_random"] for call in model.calls))

    def test_emotion_library_mode_builds_semantic_matches(self):
        payload = self.base_payload("emotion_library")
        mode, kwargs = ui.build_infer_kwargs(payload, Path("/tmp/test.wav"))
        self.assertEqual(mode, "emotion_library")
        self.assertIsNone(kwargs["emo_audio_prompt"])
        plan = ui.build_emotion_library_plan(
            "如果一家公司只会追逐短期热点，结果往往是，看起来机会很多，却始终抓不住真正的主线。\n"
            "但现在，人工智能产业链正在出现一个值得重视的变化。\n"
            "订单、产能，还有技术升级，正在同时加速。\n"
            "所以注意，这个时候最重要的，不是盲目追高。\n"
            "真正值得我们持续跟踪的，是那些业绩能够不断兑现的公司。"
        )
        self.assertEqual(
            [item["role"] for item in plan],
            ["谨慎", "转折", "加速", "提醒", "收尾"],
        )
        self.assertTrue(all(Path(item["emotion_path"]).is_file() for item in plan))
        self.assertTrue(all(item["duration_factor"] == 1.0 for item in plan))
        self.assertEqual(
            [item["library_number"] for item in plan],
            [55, 17, 36, 18, 64],
        )

    def test_emotion_library_catalog_has_all_recording_units(self):
        items = ui.emotion_library_catalog()
        self.assertEqual(len(items), 64)
        self.assertEqual(items[0]["number"], 1)
        self.assertIn("平静", items[0]["cue"])
        self.assertEqual(items[-1]["number"], 64)
        self.assertIn("坚定结尾", items[-1]["cue"])

    def test_emotion_library_recording_is_saved_and_resumable(self):
        import base64
        import io
        import wave

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"\x00\x00" * 8000)
        with tempfile.TemporaryDirectory() as directory:
            old_recordings = ui.EMOTION_LIBRARY_RECORDINGS
            ui.EMOTION_LIBRARY_RECORDINGS = Path(directory)
            try:
                result = ui.save_emotion_library_recording(
                    {
                        "number": 1,
                        "data": base64.b64encode(buffer.getvalue()).decode("ascii"),
                    }
                )
                self.assertTrue(Path(result["path"]).is_file())
                self.assertEqual(result["duration_seconds"], 1.0)
                summary = ui.emotion_library_summary()
                self.assertEqual(summary["completed"], 1)
                self.assertTrue(summary["items"][0]["recorded"])
                self.assertEqual(summary["items"][0]["duration_seconds"], 1.0)
            finally:
                ui.EMOTION_LIBRARY_RECORDINGS = old_recordings

    def test_changed_smart_plan_text_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "发生变化"):
            ui.validate_emotion_plan(
                "原始文案。",
                [{"text": "被修改的文案。", "vector": [0.0] * 8}],
            )

    def test_semantic_split_and_roles(self):
        text = "但现在，情况变了。订单正在加速！所以注意，不要追高。"
        self.assertEqual(
            ui.split_semantic_segments(text),
            ["但现在，情况变了。", "订单正在加速！", "所以注意，不要追高。"],
        )
        self.assertEqual(ui.detect_speaking_role("但现在，情况变了。")[0], "转折")
        self.assertEqual(ui.detect_speaking_role("订单正在加速。")[0], "加速")
        self.assertEqual(ui.detect_speaking_role("所以注意，不要追高。")[0], "提醒")
        self.assertEqual(ui.detect_speaking_role("真正重要的是执行。")[0], "强调")
        self.assertEqual(
            ui.detect_speaking_role("真正值得长期坚持。", is_last=True)[0], "收尾"
        )

    def test_repeated_sentence_punctuation_stays_with_sentence(self):
        self.assertEqual(
            ui.split_semantic_segments("标题！！！下一句。"),
            ["标题！！！", "下一句。"],
        )

    def test_dialogue_aware_split_and_narrative_roles(self):
        text = (
            "我走过去问她：“这么晚了，你怎么一个人在这里？”\n"
            "她小声说：“我在等爸爸。”\n"
            "女孩开心地喊了一声：\n“爸爸！”\n"
            "那里，一个人都没有。"
        )
        segments = ui.split_semantic_segments(text)
        self.assertEqual(
            segments,
            [
                "我走过去问她：",
                "“这么晚了，你怎么一个人在这里？”",
                "她小声说：",
                "“我在等爸爸。”",
                "女孩开心地喊了一声：",
                "“爸爸！”",
                "那里，一个人都没有。",
            ],
        )
        self.assertEqual(ui.detect_speaking_role(segments[1])[0], "疑问")
        self.assertEqual(
            ui.detect_speaking_role(segments[3], previous_text=segments[2])[0],
            "低声",
        )
        self.assertEqual(
            ui.detect_speaking_role(segments[5], previous_text=segments[4])[0],
            "开心呼喊",
        )
        self.assertEqual(
            ui.detect_speaking_role(segments[-1], is_last=True)[0], "悬念"
        )

    def test_multisentence_quote_keeps_emotional_context(self):
        segments = ui.split_semantic_segments(
            "他压低声音说：\n“她爸爸三年前就去世了。每年她都会等他。”"
        )
        self.assertEqual(
            segments,
            [
                "他压低声音说：",
                "“她爸爸三年前就去世了。",
                "每年她都会等他。”",
            ],
        )
        self.assertEqual(
            ui.detect_speaking_role(segments[1], previous_text=segments[0])[0],
            "悲伤揭示",
        )
        self.assertEqual(
            ui.detect_speaking_role(segments[2], previous_text=segments[1])[0],
            "悲伤揭示",
        )

    def test_smart_plan_has_editable_prosody(self):
        result = ui.analyze_emotion_plan(
            {"text": "但现在，情况变了。", "emo_alpha": 0.75}
        )
        item = result["segments"][0]
        self.assertEqual(item["role"], "转折")
        self.assertEqual(item["duration_factor"], 1.03)
        self.assertEqual(item["pause_before_ms"], 180)
        self.assertEqual(item["pause_after_ms"], 240)
        validated = ui.validate_emotion_plan(result["text"], result["segments"])
        self.assertEqual(validated[0]["duration_factor"], 1.03)
        self.assertEqual(validated[0]["pause_after_ms"], 240)

    def test_segment_gap_uses_larger_neighbor_pause(self):
        previous = {"pause_after_ms": 180}
        current = {"pause_before_ms": 240}
        self.assertEqual(ui.segment_gap_ms(previous, current), 240)

    def test_smart_generation_applies_segment_speed_and_pauses(self):
        import numpy as np
        import soundfile as sf

        class FakeModel:
            def __init__(self):
                self.calls = []

            def infer(self, **kwargs):
                self.calls.append(kwargs)
                sf.write(kwargs["output_path"], np.full(2205, 0.1), 22050)

        plan = [
            {
                "text": "第一句。",
                "role": "陈述",
                "reason": "",
                "vector": [0.0] * 8,
                "duration_factor": 1.03,
                "pause_before_ms": 0,
                "pause_after_ms": 180,
                "energy": "中",
            },
            {
                "text": "第二句。",
                "role": "收尾",
                "reason": "",
                "vector": [0.0] * 8,
                "duration_factor": 1.08,
                "pause_before_ms": 240,
                "pause_after_ms": 400,
                "energy": "低",
            },
        ]
        kwargs = {
            "spk_audio_prompt": str(ui.DEFAULT_VOICE),
            "lang": "ZH",
            "emo_alpha": 0.75,
            "duration_factor": 1.0,
        }
        model = FakeModel()
        with tempfile.TemporaryDirectory() as directory:
            old_output_dir = ui.OUTPUT_DIR
            ui.OUTPUT_DIR = Path(directory)
            output = Path(directory) / "result.wav"
            try:
                with mock.patch("torch.mps.synchronize"):
                    ui.generate_smart_script(model, kwargs, plan, output)
            finally:
                ui.OUTPUT_DIR = old_output_dir
            info = sf.info(output)
            self.assertAlmostEqual(info.duration, 0.84, places=2)
        self.assertEqual([call["duration_factor"] for call in model.calls], [1.03, 1.08])

    def test_preference_rating_learns_only_from_better_output(self):
        import numpy as np
        import soundfile as sf

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wav = root / "candidate.wav"
            sf.write(wav, np.zeros(2205), 22050)
            wav.with_suffix(".json").write_text(
                '{"segments":[{"role":"提醒","duration_factor":1.09,'
                '"effective_duration_factor":0.93,'
                '"pause_before_ms":210,"pause_after_ms":330}]}',
                encoding="utf-8",
            )
            old_output = ui.OUTPUT_DIR
            old_preferences = ui.PREFERENCE_PATH
            ui.OUTPUT_DIR = root
            ui.PREFERENCE_PATH = root / "preference_ratings.jsonl"
            try:
                result = ui.rate_output(
                    {"filename": "candidate.wav", "rating": "better"}
                )
                self.assertEqual(result["counts"]["better"], 1)
                self.assertEqual(
                    result["learned_roles"]["提醒"]["duration_factor"], 0.93
                )
                ui.rate_output({"filename": "candidate.wav", "rating": "same"})
                self.assertNotIn("提醒", ui.learned_role_prosody())
            finally:
                ui.OUTPUT_DIR = old_output
                ui.PREFERENCE_PATH = old_preferences

    def test_preference_rating_rejects_unknown_file(self):
        with self.assertRaisesRegex(FileNotFoundError, "找不到"):
            ui.rate_output({"filename": "missing.wav", "rating": "better"})

    def test_ab_comparison_records_selected_pipeline(self):
        import numpy as np
        import soundfile as sf

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sf.write(root / "a.wav", np.zeros(2205), 22050)
            sf.write(root / "b.wav", np.zeros(2205), 22050)
            old_output = ui.OUTPUT_DIR
            old_comparisons = ui.COMPARISON_PATH
            ui.OUTPUT_DIR = root
            ui.COMPARISON_PATH = root / "comparison_ratings.jsonl"
            try:
                result = ui.rate_comparison(
                    {
                        "a_filename": "a.wav",
                        "a_mode": "continuous",
                        "b_filename": "b.wav",
                        "b_mode": "smart",
                        "choice": "a",
                        "dimension": "pipeline",
                    }
                )
                self.assertEqual(result["counts"]["continuous"], 1)
                self.assertEqual(result["recommended_mode"], "emotion_audio")
                self.assertEqual(result["next_dimension"], "pace")
            finally:
                ui.OUTPUT_DIR = old_output
                ui.COMPARISON_PATH = old_comparisons

    def test_ab_pace_comparison_records_selected_speed(self):
        import numpy as np
        import soundfile as sf

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sf.write(root / "a.wav", np.zeros(2205), 22050)
            sf.write(root / "b.wav", np.zeros(2205), 22050)
            old_output = ui.OUTPUT_DIR
            old_comparisons = ui.COMPARISON_PATH
            ui.OUTPUT_DIR = root
            ui.COMPARISON_PATH = root / "comparison_ratings.jsonl"
            try:
                result = ui.rate_comparison(
                    {
                        "a_filename": "a.wav",
                        "a_mode": "continuous",
                        "a_value": 0.95,
                        "b_filename": "b.wav",
                        "b_mode": "continuous",
                        "b_value": 1.05,
                        "choice": "b",
                        "dimension": "pace",
                    }
                )
                self.assertEqual(result["pace_counts"]["1.05"], 1)
                self.assertEqual(result["recommended_duration"], 1.05)
            finally:
                ui.OUTPUT_DIR = old_output
                ui.COMPARISON_PATH = old_comparisons

    def test_prepare_spoken_text_groups_numbered_list(self):
        text = "标题！！！\n第一，甲\n第二，乙\n第三，丙\n第四，丁\n第五，戊\n第六，己"
        prepared = ui.prepare_spoken_text(text)
        self.assertTrue(prepared.startswith("标题——\n第一，甲；"))
        self.assertIn("第五，戊。\n第六，己。", prepared)

    def test_ab_punctuation_comparison_records_selected_style(self):
        import numpy as np
        import soundfile as sf

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sf.write(root / "a.wav", np.zeros(2205), 22050)
            sf.write(root / "b.wav", np.zeros(2205), 22050)
            old_output = ui.OUTPUT_DIR
            old_comparisons = ui.COMPARISON_PATH
            ui.OUTPUT_DIR = root
            ui.COMPARISON_PATH = root / "comparison_ratings.jsonl"
            try:
                result = ui.rate_comparison(
                    {
                        "a_filename": "a.wav",
                        "a_mode": "continuous",
                        "a_value": "original",
                        "b_filename": "b.wav",
                        "b_mode": "continuous",
                        "b_value": "structured",
                        "choice": "b",
                        "dimension": "punctuation",
                    }
                )
                self.assertEqual(result["punctuation_counts"]["structured"], 1)
                self.assertEqual(result["recommended_text_style"], "structured")
            finally:
                ui.OUTPUT_DIR = old_output
                ui.COMPARISON_PATH = old_comparisons

    def test_vector_summary_reports_effective_total(self):
        effective, total, active = ui.vector_summary(
            [0.6, 0, 0, 0, 0, 0, 0.4, 0.7], 0.75
        )
        self.assertEqual(effective, [0.45, 0.0, 0.0, 0.0, 0.0, 0.0, 0.3, 0.525])
        self.assertEqual(total, 1.275)
        self.assertEqual(active[0]["label"], "平静")

    def test_boundary_pause_policy(self):
        self.assertEqual(ui.boundary_silence_seconds("一句话。"), 0.22)
        self.assertEqual(ui.boundary_silence_seconds("等等……"), 0.32)

    def test_invalid_input_is_rejected(self):
        payload = self.base_payload("emotion_audio")
        payload["text"] = ""
        with self.assertRaisesRegex(ValueError, "输入"):
            ui.build_infer_kwargs(payload, Path("/tmp/test.wav"))

    def test_missing_voice_has_beginner_friendly_error(self):
        payload = self.base_payload("voice_only")
        payload["voice_path"] = None
        with self.assertRaisesRegex(ValueError, "选择音色参考"):
            ui.build_infer_kwargs(payload, Path("/tmp/test.wav"))

    def test_page_contains_beginner_flow(self):
        html = ui.HTML_PATH.read_text(encoding="utf-8")
        self.assertIn("输入文案", html)
        self.assertIn("选择音色和情绪", html)
        self.assertIn("生成并试听", html)
        self.assertIn("推荐起点", html)
        self.assertIn("智能情绪编排", html)
        self.assertIn("分析文案情绪", html)
        self.assertIn("句前", html)
        self.assertIn("全局语速", html)
        self.assertIn("可选对比基线", html)
        self.assertIn("更好", html)
        self.assertIn("像真人", html)
        self.assertIn("自然度 A/B 校准", html)
        self.assertIn("两条都不像", html)
        self.assertIn("自动整理列表标点和停顿层级", html)
        self.assertIn("真人带读增强", html)
        self.assertIn("准备带读", html)
        self.assertIn("建立真人情绪库", html)
        self.assertIn('value="emotion_library"', html)
        self.assertIn("按文案语义自动匹配真人表达", html)
        self.assertIn("开始录音", html)
        self.assertIn("打开录音目录", html)
        self.assertIn('window.location.protocol === "file:"', html)
        self.assertIn('window.location.replace("http://127.0.0.1:7865/")', html)


if __name__ == "__main__":
    unittest.main()
