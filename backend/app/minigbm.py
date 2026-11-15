"""Small, dependency-free learners (numpy only) for the research check and the AI advisor: logistic regression and histogram gradient boosting.

They return scoring objects (call them on a feature matrix; higher score = more likely 'up'). The objects are plain data + numpy, so they can be pickled and
saved, which is how the AI advisor keeps a trained model across restarts. AUC is the rank-based area under the ROC curve. These are deliberately plain: the
point is to test for information in the data, not to chase a leaderboard.
"""
from __future__ import annotations

import numpy as np

BINS = 24


def auc(score: np.ndarray, y: np.ndarray) -> float:
    """Probability that a random 'up' candle scores higher than a random 'down' one (0.5 = no information)."""
    n1 = float(y.sum())
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return 0.5
    order = np.argsort(score, kind="stable")
    ranks = np.empty(len(score))
    ranks[order] = np.arange(1, len(score) + 1)
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


class LogisticModel:
    def __init__(self, mu, sd, w, b):
        self.mu, self.sd, self.w, self.b = mu, sd, w, b

    def __call__(self, A: np.ndarray) -> np.ndarray:
        return ((A - self.mu) / self.sd) @ self.w + self.b


def logistic_fit(X: np.ndarray, y: np.ndarray, rng: np.random.Generator | None = None, l2: float = 1e-2, iters: int = 300, lr: float = 0.2):
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = (X - mu) / sd
    w, b = np.zeros(Z.shape[1]), 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(Z @ w + b)))
        w -= lr * (Z.T @ (p - y) / len(y) + l2 * w)
        b -= lr * float((p - y).mean())
    return LogisticModel(mu, sd, w, b)


class GBMModel:
    def __init__(self, edges, trees, lr):
        self.edges, self.trees, self.lr = edges, trees, lr

    def _binned(self, A: np.ndarray) -> np.ndarray:
        return np.column_stack([np.searchsorted(self.edges[j], A[:, j]) for j in range(A.shape[1])]).astype(np.uint8)

    @staticmethod
    def _walk(node, Ab: np.ndarray) -> np.ndarray:
        if node[0] == "L":
            return np.full(len(Ab), node[1])
        out = np.empty(len(Ab))
        left = Ab[:, node[1]] <= node[2]
        out[left], out[~left] = GBMModel._walk(node[3], Ab[left]), GBMModel._walk(node[4], Ab[~left])
        return out

    def __call__(self, A: np.ndarray) -> np.ndarray:
        Ab = self._binned(A)
        return sum(self.lr * self._walk(t, Ab) for t in self.trees)


def gbm_fit(X: np.ndarray, y: np.ndarray, rng: np.random.Generator | None = None, rounds: int = 100, lr: float = 0.05, depth: int = 3,
            lam: float = 30.0, min_leaf: int = 150):
    rng = rng or np.random.default_rng(0)
    edges = [np.unique(np.quantile(X[:, j], np.linspace(0, 1, BINS + 1)[1:-1])) for j in range(X.shape[1])]
    model = GBMModel(edges, [], lr)
    Xb = model._binned(X)

    def grow(idx, g, h, dep):
        if dep == 0 or len(idx) < 2 * min_leaf:
            return ("L", -g[idx].sum() / (h[idx].sum() + lam))
        G, H = g[idx].sum(), h[idx].sum()
        best_gain, best = 0.0, None
        for j in range(Xb.shape[1]):
            col = Xb[idx, j]
            hg = np.bincount(col, weights=g[idx], minlength=BINS)
            hh = np.bincount(col, weights=h[idx], minlength=BINS)
            cg, ch = np.cumsum(hg)[:-1], np.cumsum(hh)[:-1]
            cnt = np.cumsum(np.bincount(col, minlength=BINS))[:-1]
            gain = cg ** 2 / (ch + lam) + (G - cg) ** 2 / (H - ch + lam) - G ** 2 / (H + lam)
            gain[(cnt < min_leaf) | (len(idx) - cnt < min_leaf)] = -1
            k = int(np.argmax(gain))
            if gain[k] > best_gain:
                best_gain, best = gain[k], (j, k)
        if best is None:
            return ("L", -G / (H + lam))
        j, k = best
        left = Xb[idx, j] <= k
        return ("S", j, k, grow(idx[left], g, h, dep - 1), grow(idx[~left], g, h, dep - 1))

    F = np.zeros(len(y))
    for _ in range(rounds):
        p = 1 / (1 + np.exp(-F))
        g, h = p - y, p * (1 - p)
        sub = np.flatnonzero(rng.random(len(y)) < 0.7)              # row subsampling keeps trees from memorising the same rows
        tree = grow(sub, g, h, depth)
        model.trees.append(tree)
        F += lr * GBMModel._walk(tree, Xb)
    return model
