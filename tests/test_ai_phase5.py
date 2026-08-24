import json
import tempfile
import unittest
from pathlib import Path

from ai.evaluation import (
    FEATURE_NAMES,
    evaluate_candidate_ranker,
    normalize_candidate_dataset,
    score_candidate,
    train_and_evaluate,
    train_candidate_ranker,
    load_saved_model,
)


def write_jsonl(path: Path, records: list[dict]):
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )


class MLPhaseFiveTests(unittest.TestCase):
    def test_normalizes_activity_log_candidates_into_features_and_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "activities.jsonl"
            write_jsonl(log_path, [{
                "id": "a1",
                "category": "calculation",
                "action": "constellation-candidate",
                "status": "success",
                "values": {
                    "geometricScore": 42,
                    "quality": 900,
                    "feasible": True,
                    "targetAlignmentDeg": 1.5,
                },
                "details": {"searchRunId": "search-1"},
            }])

            examples = normalize_candidate_dataset([log_path])

        self.assertEqual(len(examples), 1)
        self.assertEqual(set(examples[0].features), set(FEATURE_NAMES))
        self.assertTrue(examples[0].success)
        self.assertEqual(examples[0].target_score, 900)

    def test_trains_ranker_and_beats_bad_geometric_baseline(self):
        examples = []
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "activities.jsonl"
            records = []
            for group in range(4):
                records.extend([
                    {
                        "id": f"bad-{group}",
                        "category": "calculation",
                        "action": "constellation-candidate",
                        "status": "rejected",
                        "values": {
                            "geometricScore": 100,
                            "quality": 10,
                            "feasible": False,
                            "collisionFree": False,
                            "corridorSatisfied": False,
                            "deltaVDeficitKmS": 7,
                            "targetAlignmentDeg": 30,
                        },
                        "details": {"searchRunId": f"search-{group}"},
                    },
                    {
                        "id": f"good-{group}",
                        "category": "calculation",
                        "action": "constellation-candidate",
                        "status": "success",
                        "values": {
                            "geometricScore": 10,
                            "quality": 1200,
                            "feasible": True,
                            "collisionFree": True,
                            "corridorSatisfied": True,
                            "deltaVDeficitKmS": 0,
                            "targetAlignmentDeg": 1,
                        },
                        "details": {"searchRunId": f"search-{group}"},
                    },
                ])
            write_jsonl(log_path, records)
            examples = normalize_candidate_dataset([log_path])

        model = train_candidate_ranker(examples)
        evaluation = evaluate_candidate_ranker(examples, model)

        self.assertTrue(model["useOnlyForPrioritization"])
        self.assertGreater(score_candidate(model, examples[1].features), score_candidate(model, examples[0].features))
        self.assertGreater(evaluation["top1SuccessRateModel"], evaluation["top1SuccessRateBaseline"])
        self.assertEqual(evaluation["pairwiseAccuracy"], 1.0)

    def test_corrections_transfer_between_projects_with_active_project_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "activities.jsonl"
            write_jsonl(log_path, [
                {
                    "id": "candidate-selected",
                    "projectId": "project-a",
                    "category": "calculation",
                    "action": "constellation-candidate",
                    "status": "success",
                    "values": {"quality": 100, "feasible": True},
                    "details": {"searchRunId": "run-a", "variantId": "variant-a"},
                },
                {
                    "id": "candidate-other-project",
                    "projectId": "project-b",
                    "category": "calculation",
                    "action": "constellation-candidate",
                    "status": "success",
                    "values": {"quality": 900, "feasible": True},
                    "details": {"searchRunId": "run-b", "variantId": "variant-b"},
                },
                {
                    "id": "user-feedback",
                    "projectId": "project-a",
                    "category": "calculation",
                    "action": "constellation-result-selected",
                    "status": "success",
                    "details": {"searchRunId": "run-a", "resultId": "variant-a"},
                },
                {
                    "id": "other-project-feedback",
                    "projectId": "project-b",
                    "category": "calculation",
                    "action": "constellation-result-selected",
                    "status": "success",
                    "details": {"searchRunId": "run-b", "resultId": "variant-b"},
                },
            ])

            examples = normalize_candidate_dataset([log_path], project_id="project-a")

        self.assertEqual(len(examples), 2)
        active_project = next(item for item in examples if item.source_id == "candidate-selected")
        transferred = next(item for item in examples if item.source_id == "candidate-other-project")
        self.assertTrue(active_project.user_corrected)
        self.assertEqual(active_project.sample_weight, 4.0)
        self.assertEqual(active_project.target_score, 2_100)
        self.assertTrue(transferred.user_corrected)
        self.assertEqual(transferred.sample_weight, 2.0)
        self.assertEqual(transferred.target_score, 2_900)

    def test_train_and_evaluate_reports_more_data_needed_for_empty_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            report = train_and_evaluate([Path(directory) / "missing.jsonl"])

        self.assertEqual(report["dataset"]["rows"], 0)
        self.assertEqual(report["verdict"], "needs-more-data")
        self.assertTrue(report["model"]["useOnlyForPrioritization"])

    def test_train_and_evaluate_requires_positive_and_negative_examples(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "activities.jsonl"
            write_jsonl(log_path, [{
                "id": "bad-only",
                "category": "calculation",
                "action": "constellation-candidate",
                "status": "rejected",
                "values": {"geometricScore": 100, "feasible": False},
                "details": {"searchRunId": "search-1"},
            } for _ in range(10)])

            report = train_and_evaluate([log_path])

        self.assertEqual(report["dataset"]["positiveRows"], 0)
        self.assertEqual(report["verdict"], "needs-more-data")

    def test_ranker_learns_quality_order_within_rejected_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "activities.jsonl"
            write_jsonl(log_path, [{
                "id": f"candidate-{index}",
                "category": "calculation",
                "action": "constellation-candidate",
                "status": "rejected",
                "values": {
                    "quality": quality,
                    "deltaVDeficitKmS": deficit,
                    "feasible": False,
                },
                "details": {"searchRunId": "quality-window"},
            } for index, (quality, deficit) in enumerate([(400, 0.4), (100, 6.0)])])
            examples = normalize_candidate_dataset([log_path])

        model = train_candidate_ranker(examples)
        self.assertGreater(
            score_candidate(model, examples[0].features),
            score_candidate(model, examples[1].features),
        )

    def test_train_and_evaluate_can_persist_and_reload_ranker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log_path = root / "activities.jsonl"
            model_path = root / "ranker.json"
            write_jsonl(log_path, [
                {
                    "id": "good",
                    "category": "calculation",
                    "action": "constellation-candidate",
                    "status": "success",
                    "values": {"feasible": True, "collisionFree": True},
                    "details": {"searchRunId": "run-1"},
                },
                {
                    "id": "bad",
                    "category": "calculation",
                    "action": "constellation-candidate",
                    "status": "rejected",
                    "values": {"feasible": False, "collisionFree": False},
                    "details": {"searchRunId": "run-1"},
                },
            ])

            report = train_and_evaluate(
                [log_path], persist_model=True, model_path=model_path,
            )
            restored = load_saved_model(model_path)

        self.assertIsNotNone(restored)
        self.assertEqual(restored["trainingRows"], 2)
        self.assertIn("trainedAtUtc", restored)
        self.assertEqual(report["model"], restored)


if __name__ == "__main__":
    unittest.main()
