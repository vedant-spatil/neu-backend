"""Chunked CPU scoring of an IBM Multi-GNN checkpoint (plan.md section 22).

Needs the optional `ml` extra (torch, torch-geometric) and the Multi-GNN clone at settings.mgnn_dir.
The loader does not write model_settings.json or touch wandb: the architecture comes in as arguments.

Seed edges of each split are cut into fixed-size chunks. Each chunk is scored and written to
<chunk_dir>/<split>_<idx>.csv (EdgeID, y, score). Existing chunk files are skipped, so an interrupted
run resumes. Chunks can be split across processes with a chunk range.
"""

from __future__ import annotations

import logging
import math
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import torch

log = logging.getLogger(__name__)


def load_model(repo: Path, data_root: Path, hidden: int, layers: int, neighs: list[int],
               batch_size: int, ckpt: Path, device: torch.device):
    """Builds the IBM model for the given architecture, loads the checkpoint strictly, returns (model, packs).

    packs[split] = (inds, data): inds are the seed edge indices into data, like IBM's val/test inds.
    """
    repo, data_root = Path(repo).resolve(), Path(data_root).resolve()
    if not (repo / "training.py").exists():
        raise FileNotFoundError(f"Multi-GNN clone not found at {repo}. Run scripts/setup_mgnn.sh.")

    previous_cwd = os.getcwd()
    os.chdir(repo)  # IBM code uses relative imports and paths
    sys.path.insert(0, str(repo))
    try:
        import train_util
        train_util.AddEgoIds.__abstractmethods__ = frozenset()  # same fix as the original eval script
        from data_loading import get_data
        from torch_geometric.nn import to_hetero
        from train_util import AddEgoIds, add_arange_ids
        from training import get_model
        from util import create_parser, set_seed

        args = create_parser().parse_args(["--data", "Small_HI", "--model", "gin", "--emlps", "--reverse_mp",
                                           "--ego", "--ports", "--testing", "--n_epochs", "1",
                                           "--batch_size", str(batch_size)])
        args.num_neighs = neighs
        args.unique_name = "eval"
        set_seed(args.seed)
        data_config = {"paths": {"aml_data": str(data_root), "model_to_load": str(repo),
                                 "model_to_save": str(repo)}}
        tr, val, te, _tr_inds, val_inds, te_inds = get_data(args, data_config)
        add_arange_ids([tr, val, te])

        # Eval mode ignores dropout, so only the sizes that shape the weights are needed.
        config = SimpleNamespace(n_hidden=hidden, n_gnn_layers=layers, dropout=0.0, final_dropout=0.0,
                                 n_heads=None, model="gin")
        sample = next(iter(_sample_loader(te, te_inds, neighs, batch_size, AddEgoIds())))
        model = to_hetero(get_model(sample, config, args), te.metadata(), aggr="mean")
        state = torch.load(Path(ckpt).resolve(), map_location="cpu", weights_only=False)
        model.load_state_dict(state["model_state_dict"], strict=True)
        model.to(device).eval()
        log.info("model loaded strictly; eval mode")
    finally:
        os.chdir(previous_cwd)

    return model, {"val": (val_inds, val), "test": (te_inds, te)}


def _sample_loader(data, inds, neighs, batch_size, transform):
    from torch_geometric.loader import LinkNeighborLoader

    eli = data["node", "to", "node"].edge_index[:, inds]
    el = data["node", "to", "node"].y[inds]
    return LinkNeighborLoader(data, num_neighbors=neighs,
                              edge_label_index=(("node", "to", "node"), eli), edge_label=el,
                              batch_size=batch_size, shuffle=False, transform=transform)


def score_chunk(model, data, inds, neighs: list[int], batch_size: int, device: torch.device) -> pd.DataFrame:
    """Scores one chunk of seed edges. inds are global edge indices into data."""
    from train_util import AddEgoIds

    loader = _sample_loader(data, inds, neighs, batch_size, AddEgoIds())
    return export_scores(loader, inds, model, data, device)


def export_scores(loader, inds, model, data, device) -> pd.DataFrame:
    """Per-batch scoring: softmax probability of class 1 for each seed edge, keyed by EdgeID.

    Copied from the original eval_checkpoint.export_scores, including the fix for seed edges the sampler
    leaves out (IBM skips this for Small_HI).
    """
    rows = []
    for batch in loader:
        inds_c = inds.detach().cpu()
        batch_edge_inds = inds_c[batch['node', 'to', 'node'].input_id.detach().cpu()]
        batch_edge_ids = loader.data['node', 'to', 'node'].edge_attr.detach().cpu()[batch_edge_inds, 0]
        mask = torch.isin(batch['node', 'to', 'node'].edge_attr[:, 0].detach().cpu(), batch_edge_ids)
        missing = ~torch.isin(batch_edge_ids, batch['node', 'to', 'node'].edge_attr[:, 0].detach().cpu())
        if missing.sum() != 0:
            missing_ids = batch_edge_ids[missing].int()
            node_mapping = {v.item(): i for i, v in enumerate(batch['node'].n_id)}
            add_edge_index = data['node', 'to', 'node'].edge_index[:, missing_ids].detach().clone()
            add_edge_index = torch.tensor([[node_mapping[v.item()] for v in row] for row in add_edge_index])
            add_edge_attr = data['node', 'to', 'node'].edge_attr[missing_ids, :].detach().clone()
            add_y = data['node', 'to', 'node'].y[missing_ids].detach().clone()
            batch['node', 'to', 'node'].edge_index = torch.cat((batch['node', 'to', 'node'].edge_index, add_edge_index), 1)
            batch['node', 'to', 'node'].edge_attr = torch.cat((batch['node', 'to', 'node'].edge_attr, add_edge_attr), 0)
            batch['node', 'to', 'node'].y = torch.cat((batch['node', 'to', 'node'].y, add_y), 0)
            mask = torch.cat((mask, torch.ones(add_y.shape[0], dtype=torch.bool)))
        eids = batch['node', 'to', 'node'].edge_attr[:, 0][mask].detach().cpu().numpy()
        ys = batch['node', 'to', 'node'].y[mask].detach().cpu().numpy()
        batch['node', 'to', 'node'].edge_attr = batch['node', 'to', 'node'].edge_attr[:, 1:]
        batch['node', 'rev_to', 'node'].edge_attr = batch['node', 'rev_to', 'node'].edge_attr[:, 1:]
        with torch.no_grad():
            batch.to(device)
            out = model(batch.x_dict, batch.edge_index_dict, batch.edge_attr_dict)[('node', 'to', 'node')]
            prob = torch.softmax(out[mask.to(device)], dim=-1)[:, 1].cpu().numpy()
        rows.append(pd.DataFrame({"EdgeID": eids, "score": prob, "y": ys}))
    return pd.concat(rows, ignore_index=True).drop_duplicates("EdgeID")


def score_split(model, split: str, inds, data, neighs: list[int], batch_size: int, device: torch.device,
                chunk_size: int, chunk_dir: Path, chunk_range: tuple[int, float] = (0, math.inf)) -> int:
    """Scores the chunks of one split that are in chunk_range and not yet on disk. Returns the chunk count."""
    chunk_dir.mkdir(parents=True, exist_ok=True)
    lo, hi = chunk_range
    n_chunks = math.ceil(len(inds) / chunk_size)
    log.info(f"{split}: {len(inds)} seed edges in {n_chunks} chunks of {chunk_size}")
    torch.manual_seed(1)
    for idx in range(n_chunks):
        if not (lo <= idx < hi):
            continue
        path = chunk_dir / f"{split}_{idx:04d}.csv"
        if path.exists():
            log.info(f"{split} chunk {idx}: exists, skipping")
            continue
        t0 = time.time()
        chunk_inds = inds[idx * chunk_size:(idx + 1) * chunk_size]
        df = score_chunk(model, data, chunk_inds, neighs, batch_size, device)
        tmp = path.with_suffix(".tmp")
        df.to_csv(tmp, index=False)
        tmp.replace(path)  # atomic: a half-written chunk never looks finished
        log.info(f"{split} chunk {idx}: {len(df)} edges in {time.time() - t0:.0f}s")
    return n_chunks


def merge_split(chunk_dir: Path, scores_dir: Path, split: str, n_chunks: int) -> pd.DataFrame:
    """Joins every chunk of a split into one EdgeID/y/score table and writes <scores_dir>/<split>_scores.csv."""
    parts = [pd.read_csv(chunk_dir / f"{split}_{i:04d}.csv") for i in range(n_chunks)]
    df = pd.concat(parts, ignore_index=True).drop_duplicates("EdgeID")
    scores_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(scores_dir / f"{split}_scores.csv", index=False)
    return df


def chunks_complete(chunk_dir: Path, split: str, n_chunks: int) -> bool:
    return all((chunk_dir / f"{split}_{i:04d}.csv").exists() for i in range(n_chunks))
