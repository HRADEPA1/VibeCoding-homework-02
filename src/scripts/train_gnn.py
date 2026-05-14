"""
GAT-based GNN ranker training script.

Reads JSONL training data from DATA_DIR/training/*/samples.jsonl,
trains a 2-layer Graph Attention Network, saves checkpoint to models/gat_ranker.pt.

Usage:
    python -m scripts.train_gnn
    python -m scripts.train_gnn --epochs 50 --lr 0.001 --hidden 64

Requirements (install separately):
    pip install torch torch_geometric
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
TRAINING_DIR = DATA_DIR / "training"
MODEL_DIR = Path(os.environ.get("MODEL_DIR", "./models"))
MODEL_PATH = MODEL_DIR / "gat_ranker.pt"

ROBOT_IDS = [1, 2, 3]
STEP_FEAT_DIM = 14    # one-hot op_ids
NODE_FEAT_DIM = 7     # from generate_training_data._build_node_features


def load_samples() -> list[dict]:
    samples = []
    for jsonl in TRAINING_DIR.rglob("samples.jsonl"):
        with open(jsonl) as f:
            for line in f:
                samples.append(json.loads(line))
    if not samples:
        print(f"no training data found in {TRAINING_DIR}", file=sys.stderr)
        print("run: python -m scripts.generate_training_data", file=sys.stderr)
        sys.exit(1)
    return samples


def build_graph(sample: dict, torch, Data):
    """Convert one JSONL sample to a PyG Data object."""
    step_feat = torch.tensor(sample["graph"]["step_features"], dtype=torch.float)

    nodes = sorted(sample["graph"]["nodes"], key=lambda n: n["robot_id"])
    x = torch.tensor([n["features"] for n in nodes], dtype=torch.float)

    # step node features (broadcast to robot node shape for concat)
    step_expanded = step_feat.unsqueeze(0).expand(len(nodes), -1)
    x = torch.cat([x, step_expanded], dim=1)   # [n_robots, node_feat + step_feat]

    # fully-connected edges (each robot connected to every other + self-loops removed)
    n = len(nodes)
    src, dst = [], []
    for i in range(n):
        for j in range(n):
            if i != j:
                src.append(i)
                dst.append(j)
    edge_index = torch.tensor([src, dst], dtype=torch.long)

    labels = sample["labels"]
    y = torch.tensor(
        [labels[str(nid)] for nid in ROBOT_IDS],
        dtype=torch.float,
    )
    return Data(x=x, edge_index=edge_index, y=y)


def build_model(in_channels: int, hidden: int, n_robots: int, torch, GATConv):
    class GATRanker(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.conv1 = GATConv(in_channels, hidden, heads=4, concat=True)
            self.conv2 = GATConv(hidden * 4, hidden, heads=1, concat=False)
            self.head = torch.nn.Linear(hidden, 1)

        def forward(self, x, edge_index):
            import torch.nn.functional as F
            x = F.elu(self.conv1(x, edge_index))
            x = F.elu(self.conv2(x, edge_index))
            return torch.sigmoid(self.head(x)).squeeze(-1)

    return GATRanker()


def train(epochs: int, lr: float, hidden: int, batch_size: int) -> None:
    try:
        import torch
        from torch_geometric.data import Data, DataLoader
        from torch_geometric.nn import GATConv
    except ImportError:
        print("Install: pip install torch torch_geometric", file=sys.stderr)
        sys.exit(1)

    samples = load_samples()
    print(f"loaded {len(samples)} training samples")

    in_channels = NODE_FEAT_DIM + STEP_FEAT_DIM
    model = build_model(in_channels, hidden, len(ROBOT_IDS), torch, GATConv)
    optimiser = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = torch.nn.MSELoss()

    dataset = [build_graph(s, torch, Data) for s in samples]
    split = int(0.8 * len(dataset))
    train_set, val_set = dataset[:split], dataset[split:]
    train_loader = DataLoader(train_set, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_set, batch_size=batch_size)

    best_val = float("inf")
    for epoch in range(1, epochs + 1):
        model.train()
        total_loss = 0.0
        for batch in train_loader:
            optimiser.zero_grad()
            pred = model(batch.x, batch.edge_index)
            loss = loss_fn(pred, batch.y)
            loss.backward()
            optimiser.step()
            total_loss += loss.item()

        if epoch % 10 == 0 or epoch == 1:
            model.eval()
            val_loss = 0.0
            with torch.no_grad():
                for batch in val_loader:
                    pred = model(batch.x, batch.edge_index)
                    val_loss += loss_fn(pred, batch.y).item()
            val_loss /= max(len(val_loader), 1)
            print(f"epoch {epoch:3d} | train {total_loss/len(train_loader):.4f} | val {val_loss:.4f}")
            if val_loss < best_val:
                best_val = val_loss
                MODEL_DIR.mkdir(parents=True, exist_ok=True)
                torch.save(model.state_dict(), MODEL_PATH)
                print(f"  checkpoint → {MODEL_PATH}")

    print(f"\ntraining complete. best val loss: {best_val:.4f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Train GNN ranker on generated data")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--lr", type=float, default=5e-4)
    parser.add_argument("--hidden", type=int, default=64)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    train(args.epochs, args.lr, args.hidden, args.batch_size)


if __name__ == "__main__":
    main()
