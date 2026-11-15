"""Model bake-off: can ANY modern model predict the next candle on unseen data?  Run with the separate ML environment (LightGBM, scikit-learn, PyTorch):

  .venv-ml\\Scripts\\python tools\\ml_bakeoff.py [--rows 60000] [--folds 4] [--models minigbm,logreg,lightgbm,cnn,gru,transformer]

Same data, same walk-forward protocol for every model (each time block is predicted by a model trained only on EARLIER candles, with a gap), plus a
shuffled-label control. Reports AUC with its 95% range and the win rate of each model's most confident calls against break-even. Read-only on the database.
Models: minigbm (the app's own), logreg, lightgbm (tuned, early stopping on a time-ordered slice), cnn / gru / transformer on the raw last-32-candle window
(ATR-normalised open/high/low/close moves + tick count) pooled across all pairs.
"""
import argparse
import json
import math
import sqlite3
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ai, evaluator, minigbm, research, vec  # noqa: E402

DB = Path(__file__).resolve().parent.parent / "data" / "signals.db"
WIN = 32


def low_priority() -> None:
    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    except Exception:  # noqa: BLE001
        pass


# ----------------------------------------------------------------------------------------------- data: tabular features + raw windows
def window_array(d: pd.DataFrame) -> np.ndarray:
    """(n, WIN, 5) ATR-normalised raw candle window ending at each candle (NaN where there is not enough history)."""
    o, h, l, c = (d[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    n_ticks = d["n"].to_numpy(float)
    atr = vec.featurize(d)["atr14"].to_numpy(float)
    atr = np.where(atr > 0, atr, np.nan)
    pc = np.r_[np.nan, c[:-1]]
    ch = np.stack([(o - pc) / atr, (h - o) / atr, (l - o) / atr, (c - o) / atr, (n_ticks / np.nanmedian(n_ticks) - 1.0)], axis=1)      # per-candle channels, each normalised by its OWN atr
    out = np.full((len(d), WIN, 5), np.nan, dtype=np.float32)
    for i in range(WIN - 1, len(d)):
        out[i] = ch[i - WIN + 1:i + 1]
    return out


def build(conn, min_payout=80.0, max_rows=60000):
    payouts = {s: p for s, p in conn.execute("SELECT symbol, payout FROM instruments WHERE payout IS NOT NULL")}
    pairs = [s for (s,) in conn.execute("SELECT DISTINCT symbol FROM candles WHERE tf=60").fetchall() if float(payouts.get(s) or 0) >= min_payout]
    xs, ws, ys, ts = [], [], [], []
    for sym in sorted(pairs):
        q = pd.DataFrame(conn.execute("SELECT ts, o, h, l, c, ticks FROM candles WHERE symbol=? AND tf=60 ORDER BY ts", (sym,)).fetchall(),
                         columns=["t", "open", "high", "low", "close", "n"]).astype(float)
        for a, b in vec.contiguous_runs(q["t"].to_numpy().astype(np.int64), 60):
            if b - a < 200:
                continue
            d = q.iloc[a:b].reset_index(drop=True)
            f = ai.candle_features(d)
            move = (d["close"].shift(-1) - d["open"].shift(-1)).to_numpy()
            M, W = f.to_numpy(float), window_array(d)
            ok = np.isfinite(M).all(axis=1) & np.isfinite(W).all(axis=(1, 2)) & np.isfinite(move) & (move != 0)
            ok[:60], ok[-1] = False, False
            if ok.any():
                xs.append(M[ok]); ws.append(W[ok]); ys.append((move[ok] > 0).astype(np.float32)); ts.append(d["t"].to_numpy()[ok])
    X, W, y, t = np.vstack(xs), np.concatenate(ws), np.concatenate(ys), np.concatenate(ts)
    o = np.argsort(t, kind="stable")
    X, W, y, t = X[o][-max_rows:], W[o][-max_rows:], y[o][-max_rows:], t[o][-max_rows:]
    return X, np.clip(W, -8, 8), y, t


# ----------------------------------------------------------------------------------------------- fitters: fit(Xtr, Wtr, ytr, seed) -> predict(X, W) (higher = more likely UP)
def fit_minigbm(Xtr, Wtr, ytr, seed):
    m = minigbm.gbm_fit(Xtr, ytr, rng=np.random.default_rng(seed), rounds=120, depth=3)
    return lambda X, W: m(X)


def fit_logreg(Xtr, Wtr, ytr, seed):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    sc = StandardScaler().fit(Xtr)
    m = LogisticRegression(C=0.05, max_iter=300).fit(np.clip(sc.transform(Xtr), -6, 6), ytr)
    return lambda X, W: m.decision_function(np.clip(sc.transform(X), -6, 6))


def fit_lightgbm(Xtr, Wtr, ytr, seed):
    import lightgbm as lgb
    cut = int(len(ytr) * 0.85)                                         # time-ordered: early stopping on the LATEST part of the training data
    m = lgb.LGBMClassifier(n_estimators=600, learning_rate=0.03, num_leaves=15, min_child_samples=200, subsample=0.7, subsample_freq=1, colsample_bytree=0.7,
                           reg_lambda=5.0, n_jobs=4, random_state=seed, verbose=-1)
    m.fit(Xtr[:cut], ytr[:cut], eval_set=[(Xtr[cut:], ytr[cut:])], eval_metric="auc", callbacks=[lgb.early_stopping(40, verbose=False)])
    return lambda X, W: m.predict_proba(X)[:, 1]


def _torch_fit(kind):
    def fit(Xtr, Wtr, ytr, seed):
        import torch
        from torch import nn
        torch.manual_seed(seed)
        torch.set_num_threads(4)
        F = Wtr.shape[2]

        class Net(nn.Module):
            def __init__(self):
                super().__init__()
                if kind == "cnn":
                    self.body = nn.Sequential(nn.Conv1d(F, 32, 3, padding=1), nn.ReLU(), nn.Conv1d(32, 64, 3, padding=1), nn.ReLU())
                    self.head = nn.Sequential(nn.Linear(128, 32), nn.ReLU(), nn.Dropout(0.3), nn.Linear(32, 1))
                elif kind == "gru":
                    self.rnn = nn.GRU(F, 32, batch_first=True)
                    self.head = nn.Sequential(nn.Dropout(0.3), nn.Linear(32, 1))
                else:
                    self.inp = nn.Linear(F, 32)
                    self.pos = nn.Parameter(torch.zeros(1, WIN, 32))
                    self.enc = nn.TransformerEncoder(nn.TransformerEncoderLayer(32, 4, 64, dropout=0.2, batch_first=True), 2)
                    self.head = nn.Sequential(nn.Dropout(0.3), nn.Linear(32, 1))

            def forward(self, x):                                      # x: (B, WIN, F)
                if kind == "cnn":
                    z = self.body(x.transpose(1, 2))
                    return self.head(torch.cat([z.mean(2), z.amax(2)], 1)).squeeze(1)
                if kind == "gru":
                    return self.head(self.rnn(x)[0][:, -1]).squeeze(1)
                z = self.enc(self.inp(x) + self.pos)
                return self.head(z.mean(1)).squeeze(1)

        net = Net()
        opt = torch.optim.AdamW(net.parameters(), lr=2e-3, weight_decay=1e-3)
        cut = int(len(ytr) * 0.85)
        Xt, yt = torch.from_numpy(Wtr[:cut]), torch.from_numpy(ytr[:cut])
        Xv, yv = torch.from_numpy(Wtr[cut:]), ytr[cut:]
        best, best_state, bad = 0.0, None, 0
        lossf = nn.BCEWithLogitsLoss()
        for epoch in range(12):
            net.train()
            perm = torch.randperm(len(yt))
            for i in range(0, len(perm), 512):
                b = perm[i:i + 512]
                opt.zero_grad()
                lossf(net(Xt[b]), yt[b]).backward()
                opt.step()
            net.eval()
            with torch.no_grad():
                a = minigbm.auc(net(Xv).numpy(), yv)
            if a > best:
                best, bad = a, 0
                best_state = {k: v.clone() for k, v in net.state_dict().items()}
            else:
                bad += 1
                if bad >= 3:
                    break
        net.load_state_dict(best_state)
        net.eval()

        def predict(X, W):
            with torch.no_grad():
                return np.concatenate([net(torch.from_numpy(W[i:i + 4096])).numpy() for i in range(0, len(W), 4096)])
        return predict
    return fit


FITTERS = {"minigbm": fit_minigbm, "logreg": fit_logreg, "lightgbm": fit_lightgbm, "cnn": _torch_fit("cnn"), "gru": _torch_fit("gru"), "transformer": _torch_fit("transformer")}


def walk(name, X, W, y, folds, embargo, seed=7):
    n, cuts = len(y), [int(len(y) * f) for f in np.linspace(0.4, 1.0, folds + 1)]
    rng = np.random.default_rng(seed)
    idx, score, ctrl, per = [], [], [], []
    for k in range(folds):
        lo, hi = cuts[k], cuts[k + 1]
        tr, te = np.arange(0, lo - embargo), np.arange(lo, hi)
        pred = FITTERS[name](X[tr], W[tr], y[tr], seed + k)
        s = np.asarray(pred(X[te], W[te]), float)
        c = np.asarray(FITTERS[name](X[tr], W[tr], rng.permutation(y[tr]), seed + 50 + k)(X[te], W[te]), float)
        per.append(round(float(minigbm.auc(s, y[te])), 4))
        idx.append(te); score.append(s); ctrl.append(c)
    idx, score, ctrl = np.concatenate(idx), np.concatenate(score), np.concatenate(ctrl)
    yt = y[idx]
    a, ac = float(minigbm.auc(score, yt)), float(minigbm.auc(ctrl, yt))
    st = research.auc_stats(a, int(yt.sum()), int((1 - yt).sum()))
    top = research.top_calls(score, yt, 0.1, 0.54)
    return {"model": name, "auc": round(a, 4), "lo": round(st["lo"], 4), "hi": round(st["hi"], 4), "p": round(st["p"], 4), "control": round(ac, 4), "folds": per,
            "top10_win": round(top["win"], 4), "top10_n": top["n"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=60000)
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--models", default="minigbm,logreg,lightgbm,cnn,gru,transformer")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    low_priority()
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=60)
    t0 = time.time()
    X, W, y, t = build(conn, max_rows=a.rows)
    print(f"dataset: {len(y):,} candles, {X.shape[1]} tabular features, window {W.shape[1]}x{W.shape[2]}, base rate up {y.mean():.4f}, built in {time.time() - t0:.0f}s", flush=True)
    res = []
    for name in a.models.split(","):
        t1 = time.time()
        r = walk(name, X, W, y, a.folds, 300)
        r["seconds"] = round(time.time() - t1)
        res.append(r)
        print(f"{name:12s} AUC {r['auc']:.4f} (95% {r['lo']:.4f}-{r['hi']:.4f}, p={r['p']:.3f})  control {r['control']:.4f}  folds {r['folds']}  "
              f"top-10% win {r['top10_win']:.3f} of {r['top10_n']:,}  [{r['seconds']}s]", flush=True)
    if a.out:
        Path(a.out).write_text(json.dumps({"rows": int(len(y)), "results": res}, indent=1))


if __name__ == "__main__":
    main()
