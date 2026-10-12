#!/usr/bin/env python3
"""Train a promotional-overlay classifier (logistic probe) on the features from
_badge_dataset.py. Pure-numpy (no sklearn dependency).

Usage: python3 _train_badge.py <data.npz> [<out.npz>]
Saves coef/intercept/mean/std + the decision threshold to <out.npz>.
"""
import sys

import numpy as np


def _standardize(X, mean, std):
    return (X - mean) / np.maximum(std, 1e-6)


def main():
    data = np.load(sys.argv[1], allow_pickle=True)
    out = sys.argv[2] if len(sys.argv) > 2 else "badge_model.npz"
    X, y = data["feats"].astype(np.float64), data["labels"].astype(np.float64)
    print(f"[TRAIN] {X.shape[0]} samples, {int(y.sum())} overlay / {int((1-y).sum())} clean, {X.shape[1]}-d")

    # deterministic shuffle + split
    rng = np.random.default_rng(0)
    idx = rng.permutation(len(X))
    X, y = X[idx], y[idx]
    n_tr = int(len(X) * 0.8)
    Xtr, ytr, Xte, yte = X[:n_tr], y[:n_tr], X[n_tr:], y[n_tr:]

    mean = Xtr.mean(axis=0)
    std = Xtr.std(axis=0)
    Xtr_s, Xte_s = _standardize(Xtr, mean, std), _standardize(Xte, mean, std)

    # logistic regression via gradient descent
    w = np.zeros(X.shape[1])
    b = 0.0
    lr, epochs, l2 = 0.05, 400, 1e-3
    for ep in range(epochs):
        z = Xtr_s @ w + b
        p = 1.0 / (1.0 + np.exp(-z))
        g = p - ytr
        w -= lr * (Xtr_s.T @ g / len(Xtr_s) + l2 * w)
        b -= lr * g.mean()

    def acc(Xs, ys, thr=0.5):
        p = 1.0 / (1.0 + np.exp(-(Xs @ w + b)))
        return float(((p >= thr) == (ys >= 0.5)).mean())

    # pick the threshold with the best train accuracy (guards against imbalance)
    best_thr, best = 0.5, 0.0
    for thr in np.arange(0.2, 0.8, 0.02):
        a = acc(Xtr_s, ytr, thr)
        if a > best:
            best, best_thr = a, float(thr)

    print(f"[TRAIN] train acc={best:.3f}  test acc={acc(Xte_s, yte, best_thr):.3f}  thr={best_thr:.2f}")
    np.savez(out, w=w.astype(np.float32), b=np.float32(b), mean=mean.astype(np.float32),
             std=std.astype(np.float32), thr=np.float32(best_thr))
    print(f"[TRAIN] saved {out}")


if __name__ == "__main__":
    main()
