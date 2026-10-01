"""XGBoost models for Hobby Hero.

- moneyline: binary classifier for home-team win probability
- puck line: regression for home win margin (to derive puck-line probability)
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.calibration import CalibratedClassifierCV

FEATURE_PREFIXES = [
    "win_rate_w", "gf_per_game_w", "ga_per_game_w",
    "reg_win_rate_w", "ot_loss_rate_w", "so_win_rate_w",
]


def select_features(df: pd.DataFrame) -> List[str]:
    """Return the matchup delta feature columns present in the frame."""
    feats = [c for c in df.columns if c.endswith("_delta")]
    # optionally include rest-day deltas and home-ice indicator
    ordered = sorted(feats)
    return ordered


def train_moneyline(df: pd.DataFrame, feature_cols: List[str]) -> xgb.XGBClassifier:
    X = df[feature_cols].astype(float)
    y = df["home_win"]
    model = xgb.XGBClassifier(
        n_estimators=300,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        objective="binary:logistic",
        eval_metric="logloss",
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X, y)
    return model


def train_moneyline_calibrated(df: pd.DataFrame, feature_cols: List[str],
                               method: str = "isotonic", cv: int = 5):
    """Train XGBoost + probability calibration (Platt or isotonic).

    Calibration makes the raw model outputs reliable probabilities, which
    is required for edge/EV calculations against market odds.
    """
    X = df[feature_cols].astype(float)
    y = df["home_win"]
    base = xgb.XGBClassifier(
        n_estimators=300,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        objective="binary:logistic",
        eval_metric="logloss",
        random_state=42,
        n_jobs=-1,
    )
    calib = CalibratedClassifierCV(base, method=method, cv=cv)
    calib.fit(X, y)
    return calib


def predict_moneyline(model, df: pd.DataFrame,
                      feature_cols: List[str]) -> np.ndarray:
    """Return home-team win probability. Works for both raw and calibrated."""
    return model.predict_proba(df[feature_cols].astype(float))[:, 1]


def train_margin(df: pd.DataFrame, feature_cols: List[str]) -> xgb.XGBRegressor:
    X = df[feature_cols].astype(float)
    y = df["margin"].astype(float)
    model = xgb.XGBRegressor(
        n_estimators=300,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        objective="reg:squarederror",
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X, y)
    return model


def predict_margin(model: xgb.XGBRegressor, df: pd.DataFrame,
                   feature_cols: List[str]) -> np.ndarray:
    return model.predict(df[feature_cols].astype(float))


def save_model(model, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    model.save_model(str(path))


def load_model(path: str | Path):
    p = str(path)
    # infer classifier vs regressor from filename
    if "margin" in p:
        m = xgb.XGBRegressor()
    else:
        m = xgb.XGBClassifier()
    m.load_model(p)
    return m
