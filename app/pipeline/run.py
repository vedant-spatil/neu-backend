"""Single ETL path for the model (plan.md section 22). Replaces the Airflow DAG.

    mainvenv/bin/python -m app.pipeline.run --steps features,score,alerts,evaluate
    mainvenv/bin/python -m app.pipeline.run --steps score --chunk-range 0:4     # one of several parallel runs

Steps (run in order, each idempotent):
  features   raw CSV -> formatted_transactions.csv, edge_map.csv, split_manifest.json (app/ml/features.py)
  score      chunked checkpoint scoring of val and test; merges when all chunks exist (app/ml/scoring.py)
  alerts     flagged edges (score >= threshold) -> alerts table, source='model' (app/ml/alerts.py)
  evaluate   precision, recall, F1, PR-AUC per split -> <eval_dir>/<run>/metrics.json

XGBoost triage (plan.md section 24), run after the CSV is in place:
  triage_features  account-day features and forward labels -> <eval_dir>/<run>/triage/features.parquet
  triage_train     time split, XGBoost grid, budget metrics -> <eval_dir>/<run>/triage/metrics.json
  triage_score     scores every account-day; --write-db fills triage_scores (app/ml/triage_score.py)

    mainvenv/bin/python -m app.pipeline.run --steps triage_features,triage_train --run triage_small \\
        --csv docs/HI-Small_Trans.csv --horizon 2 --train-end 4 --val-end 6 --budget 50

Outputs for a run live in <eval_dir>/<run_id>/. Settings come from app/config.py (env vars override).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from pathlib import Path

import pandas as pd

from app.config import get_settings
from app.ml import features
from app.ml.metrics import threshold_metrics

log = logging.getLogger("pipeline")
GIN_STEPS = ("features", "score", "alerts", "evaluate")
TRIAGE_STEPS = ("triage_features", "triage_train", "triage_score")
STEPS = GIN_STEPS + TRIAGE_STEPS
SPLITS = ("val", "test")


def _features_dir(settings) -> Path:
    # IBM data_loading reads <mgnn_data_dir>/Small_HI/formatted_transactions.csv
    return settings.mgnn_data_dir / "Small_HI"


def _run_dir(settings, run_id: str) -> Path:
    return settings.eval_dir / run_id


def step_features(settings) -> None:
    feat_dir = _features_dir(settings)
    raw_stats = features.check_inputs(settings.raw_csv)
    formatted = features.build_formatted(settings.raw_csv, feat_dir)
    stats = features.validate(formatted, raw_stats)
    manifest = features.write_manifest(formatted, feat_dir / "split_manifest.json")
    log.info(f"features ok: {stats}; splits={ {k: v['edges'] for k, v in manifest['splits'].items()} }")


def step_score(settings, run_id: str, chunk_range: tuple[int, float]) -> None:
    import torch

    from app.ml import scoring

    if not settings.model_ckpt.exists():
        raise FileNotFoundError(f"checkpoint not found: {settings.model_ckpt}")
    neighs = [int(x) for x in settings.model_neighbors.split(",")]
    if len(neighs) != settings.model_layers:
        raise ValueError("model_neighbors needs one value per layer")
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    run_dir = _run_dir(settings, run_id)
    chunk_dir = run_dir / "chunks"

    batch = settings.model_batch_size
    model, packs = scoring.load_model(settings.mgnn_dir, settings.mgnn_data_dir, settings.model_hidden,
                                      settings.model_layers, neighs, batch, settings.model_ckpt, device)
    for split in SPLITS:
        inds, data = packs[split]
        n_chunks = scoring.score_split(model, split, inds, data, neighs, batch, device,
                                       settings.model_chunk_size, chunk_dir, chunk_range)
        if scoring.chunks_complete(chunk_dir, split, n_chunks):
            scoring.merge_split(chunk_dir, run_dir, split, n_chunks)
            log.info(f"merged {split} -> {run_dir / (split + '_scores.csv')}")
        else:
            log.info(f"{split}: chunks still missing, merge skipped (run the remaining ranges)")


def _load_scores(run_dir: Path, split: str) -> pd.DataFrame:
    path = run_dir / f"{split}_scores.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing. Run the score step for all chunks first.")
    return pd.read_csv(path)


def step_alerts(settings, run_id: str, threshold: float) -> None:
    from app.db.session import engine
    from app.ml import alerts

    run_dir = _run_dir(settings, run_id)
    edge_map = pd.read_csv(_features_dir(settings) / features.EDGE_MAP)
    rows: list[dict] = []
    for split in SPLITS:
        scores = _load_scores(run_dir, split)
        rows += alerts.build_alert_rows(scores, edge_map, run_id, threshold, split)
    written = alerts.write_model_alerts(engine, rows, run_id)
    log.info(f"model alerts written: {written} (threshold {threshold}, run {run_id})")


def step_evaluate(settings, run_id: str, threshold: float) -> dict:
    run_dir = _run_dir(settings, run_id)
    results = {}
    for split in SPLITS:
        results[split] = threshold_metrics(_load_scores(run_dir, split), threshold)
    (run_dir / "metrics.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    log.info(f"evaluate: {json.dumps(results)}")
    return results


def _triage_dir(settings, run_id: str) -> Path:
    return _run_dir(settings, run_id) / "triage"


def step_triage_features(settings, run_id: str, csv_path: Path, horizon: int) -> None:
    from app.ml import triage_features

    manifest = triage_features.run_features(csv_path, _triage_dir(settings, run_id), horizon)
    log.info(f"triage features ok: rows={manifest['rows']} positives={manifest['positives']} "
             f"days={manifest['max_day']} horizon={horizon}")


def step_triage_train(settings, run_id: str, budget: int, train_end: int | None, val_end: int | None,
                      patterns: Path | None) -> dict:
    from app.ml import triage_train

    metrics = triage_train.run_train(_triage_dir(settings, run_id), _triage_dir(settings, run_id), budget,
                                     train_end, val_end, patterns)
    log.info(f"triage train: best_iteration={metrics['best_iteration']} test={json.dumps(metrics['test'])}")
    return metrics


def step_triage_score(settings, run_id: str, write_db: bool) -> dict:
    from app.ml import triage_score

    engine = None
    if write_db:
        from app.db.session import engine as db_engine
        engine = db_engine
    summary = triage_score.run_score(_triage_dir(settings, run_id), _triage_dir(settings, run_id), run_id, engine)
    log.info(f"triage scores: {json.dumps(summary)}")
    return summary


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--steps", default=",".join(GIN_STEPS), help=f"comma list of {','.join(STEPS)}")
    ap.add_argument("--run", default=settings.model_run_id, help="run id, names the output folder")
    ap.add_argument("--threshold", type=float, default=settings.alert_threshold)
    ap.add_argument("--chunk-range", default=None, help="lo:hi chunk indices for the score step")
    ap.add_argument("--csv", type=Path, default=settings.raw_csv, help="transactions CSV for triage_features")
    ap.add_argument("--horizon", type=int, default=7, help="label horizon in days (triage_features)")
    ap.add_argument("--budget", type=int, default=50, help="flagged account-days per day (triage_train)")
    ap.add_argument("--train-end", type=int, default=None, help="last window day in train (default: 60%%)")
    ap.add_argument("--val-end", type=int, default=None, help="last window day in val (default: 80%%)")
    ap.add_argument("--patterns", type=Path, default=None, help="HI-*_Patterns.txt for typology recall")
    ap.add_argument("--write-db", action="store_true", help="triage_score: write rows to triage_scores")
    cli = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)-5.5s] %(message)s")

    steps = [s.strip() for s in cli.steps.split(",") if s.strip()]
    unknown = set(steps) - set(STEPS)
    if unknown:
        ap.error(f"unknown steps: {sorted(unknown)}")

    chunk_range: tuple[int, float] = (0, math.inf)
    if cli.chunk_range:
        lo, hi = cli.chunk_range.split(":")
        chunk_range = (int(lo), int(hi))

    for step in STEPS:  # fixed order regardless of how --steps is written
        if step not in steps:
            continue
        log.info(f"== step {step}")
        if step == "features":
            step_features(settings)
        elif step == "score":
            step_score(settings, cli.run, chunk_range)
        elif step == "alerts":
            step_alerts(settings, cli.run, cli.threshold)
        elif step == "evaluate":
            step_evaluate(settings, cli.run, cli.threshold)
        elif step == "triage_features":
            step_triage_features(settings, cli.run, cli.csv, cli.horizon)
        elif step == "triage_train":
            step_triage_train(settings, cli.run, cli.budget, cli.train_end, cli.val_end, cli.patterns)
        elif step == "triage_score":
            step_triage_score(settings, cli.run, cli.write_db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
