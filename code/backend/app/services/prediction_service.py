"""
能耗预测服务 (Step 19)。

三个模型:
  predict_prophet   Prophet 1.3.0 主力, 自带预编译 CmdStan, 开箱即用
  predict_lstm      PyTorch LSTM demo (input=1, hidden=32, layers=2, output=1), 纯 CPU
  predict_linear    sklearn LinearRegression baseline, 给 Prophet/LSTM 一个统计基线参考

数据流:
  mart.building_daily_energy -> 取日粒度总能耗 -> 切训练集/验证集
  -> 调对应模型 predict horizon + eval_days 天 -> 算 MAPE -> 写 result_jsonb

MAPE 评估:
  训练集 = history[:-eval_days]
  验证集 = history[-eval_days:]
  模型在训练集上训练后预测 horizon + eval_days 天
  前 eval_days 行对照验证集算 MAPE
  后 horizon 行是真正未来预测

run_prediction 是 worker 调的主入口, 任何异常上抛由 worker 主循环捕获置 FAILED。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

from app.core.config import settings
from app.db.session import get_conn


# 模型类型白名单 (跟 prediction_bdg2.sql CHECK 约束对齐)
MODEL_TYPES = ("prophet", "lstm", "linear", "gbm")

# 最少训练数据天数。Prophet/LSTM/linear 训练样本太少没意义, 直接报错让用户接更多数据。
MIN_TRAIN_DAYS = 30


# ── 1. 数据获取 ────────────────────────────────────────────────────────

def get_building_history(
    building_id: str,
    tenant_id: str,
    max_days: int | None = None,
) -> pd.DataFrame:
    """查 mart.building_daily_energy 拿日粒度总能耗。

    返 DataFrame[date, value], date 是 date 类型, value 是该楼当天所有 measure_kind 的
    总能耗 kWh。date 升序, 缺失日期不补 (Prophet/LSTM 都能处理不连续日期, 一期不强行插值)。

    max_days=None 用 settings.prediction_train_max_days。取最近 max_days 天。
    数据少于 MIN_TRAIN_DAYS 抛 ValueError, 让调用方知道训练样本不够。

    tenant_id 隔离: SQL 强制带 WHERE tenant_id = %s, 不依赖连接层做隔离。
    """
    if max_days is None:
        max_days = settings.prediction_train_max_days

    with get_conn() as conn:
        with conn.cursor() as cur:
            # 校验 building 属于本租户 (跟 query_service._verify_building 一致)
            cur.execute("""
                SELECT building_code, display_name
                FROM core.building
                WHERE id = %s::uuid AND tenant_id = %s::uuid
            """, (building_id, tenant_id))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"building 不存在或不属于当前租户: {building_id}")
            building_code, display_name = row

            # 取最近 max_days 天日能耗。date 是 DATE 类型, value 求和。
            # energy_type 为 electricity/gas/hotwater/chilledwater/solar/steam 的都算,
            # 不挑 measure_kind 因为 mart 表已经在 Step 05 聚合时过滤过 water (volume_liter)。
            cur.execute("""
                SELECT date, SUM(total_kwh) AS value
                FROM mart.building_daily_energy
                WHERE building_id = %s::uuid AND tenant_id = %s::uuid
                GROUP BY date
                ORDER BY date
            """, (building_id, tenant_id))
            rows = cur.fetchall()

    if not rows:
        raise ValueError(f"building {building_code} 无任何日能耗数据, 无法训练")

    df = pd.DataFrame(rows, columns=["date", "value"])
    df["date"] = pd.to_datetime(df["date"])
    # 取最近 max_days 天 (df 已按 date 升序, 取末尾 max_days 行)
    if len(df) > max_days:
        df = df.iloc[-max_days:].reset_index(drop=True)

    if len(df) < MIN_TRAIN_DAYS:
        raise ValueError(
            f"building {building_code} 训练数据仅 {len(df)} 天, 少于 {MIN_TRAIN_DAYS} 天最低要求"
        )

    logger.info(
        "get_building_history tenant={} building={} days={} range={}~{}",
        tenant_id[:8], building_code, len(df),
        df["date"].iloc[0].date(), df["date"].iloc[-1].date(),
    )
    return df


# ── 1.5 天气数据获取 (gbm 模型用) ──────────────────────────────────────

def get_building_weather_daily(
    building_id: str,
    tenant_id: str,
    max_days: int | None = None,
) -> pd.DataFrame:
    """查 fact.weather_reading 拿站点日粒度天气 (按天聚合)。

    天气是 site 级 (不是 building 级), 先解析 building -> site_id, 再聚合到
    日粒度: air_temp_c / dew_temp_c / wind_speed_mps / cloud_cover_pct 取日均值。

    返 DataFrame[date, air_temp_c, dew_temp_c, wind_speed_mps, cloud_cover_pct],
    date 升序, 取最近 max_days 天。gbm 模型用天气特征训练 + 未来用 day-of-year
    气候平均兜底。
    """
    if max_days is None:
        max_days = settings.prediction_train_max_days

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT site_id FROM core.building
                WHERE id = %s::uuid AND tenant_id = %s::uuid
            """, (building_id, tenant_id))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"building 不存在或不属于当前租户: {building_id}")
            site_id = str(row[0])

            cur.execute("""
                SELECT date_trunc('day', ts)::date AS date,
                       AVG(air_temp_c)          AS air_temp_c,
                       AVG(dew_temp_c)          AS dew_temp_c,
                       AVG(wind_speed_mps)      AS wind_speed_mps,
                       AVG(cloud_cover_pct)     AS cloud_cover_pct
                FROM fact.weather_reading
                WHERE site_id = %s::uuid AND tenant_id = %s::uuid
                GROUP BY date_trunc('day', ts)::date
                ORDER BY date
            """, (site_id, tenant_id))
            rows = cur.fetchall()

    df = pd.DataFrame(
        rows,
        columns=["date", "air_temp_c", "dew_temp_c", "wind_speed_mps", "cloud_cover_pct"],
    )
    if len(df) == 0:
        raise ValueError(f"building {building_id} 所在站点无天气数据, 无法训练 gbm 模型")
    df["date"] = pd.to_datetime(df["date"])
    if len(df) > max_days:
        df = df.iloc[-max_days:].reset_index(drop=True)

    logger.info(
        "get_building_weather_daily site={} days={} range={}~{}",
        site_id[:8], len(df), df["date"].iloc[0].date(), df["date"].iloc[-1].date(),
    )
    return df


# ── 2. MAPE 评估 ───────────────────────────────────────────────────────

def evaluate_mape(actual: list[float], predicted: list[float]) -> float | None:
    """平均绝对百分比误差。actual=0 的点跳过避免除零。

    返 float 百分比 (<20% 算可用, BDG2 demo 单年可能 20-50%)。
    全 0 或空集返 None。
    """
    valid = [(a, p) for a, p in zip(actual, predicted) if a != 0]
    if not valid:
        return None
    mape = sum(abs((a - p) / a) for a, p in valid) / len(valid) * 100
    return round(mape, 2)


# ── 3. Prophet 实现 ────────────────────────────────────────────────────

def predict_prophet(train_df: pd.DataFrame, horizon: int) -> list[dict]:
    """Prophet 1.3.0 训练 + 预测。

    train_df: DataFrame[date, value]
    horizon: 预测天数

    BDG2 demo 单年数据:
      - yearly_seasonality='auto' Prophet 会自动关闭 (<2 年学不到)
      - weekly_seasonality='auto' 会开 (周内作息模式)
      - daily_seasonality=False (日数据没日内粒度)
      - interval_width=0.95 置信区间 95%

    返 forecast: [{date: "YYYY-MM-DD", yhat, yhat_lower, yhat_upper}]
    """
    from prophet import Prophet

    # Prophet 输入格式: ds (datetime), y (numeric)
    fit_df = pd.DataFrame({
        "ds": train_df["date"],
        "y": train_df["value"].astype(float),
    })

    # 抑制 cmdstanpy 的 INFO 级日志 (每个 chain start/end 都打, 太吵)
    import logging
    logging.getLogger("cmdstanpy").setLevel(logging.WARNING)

    m = Prophet(
        yearly_seasonality="auto",
        weekly_seasonality="auto",
        daily_seasonality=False,
        interval_width=0.95,
        # changepoint 灵敏度调低, 避免短数据过拟合
        changepoint_prior_scale=0.05,
    )
    m.fit(fit_df)

    future = m.make_future_dataframe(periods=horizon, freq="D")
    fcst = m.predict(future)

    # 取末尾 horizon 行 (预测部分)
    tail = fcst.tail(horizon)
    result = [
        {
            "date": row["ds"].strftime("%Y-%m-%d"),
            "yhat": round(float(row["yhat"]), 3),
            "yhat_lower": round(float(row["yhat_lower"]), 3),
            "yhat_upper": round(float(row["yhat_upper"]), 3),
        }
        for _, row in tail.iterrows()
    ]
    return result


# ── 4. LSTM 实现 ──────────────────────────────────────────────────────

class _LSTMModel:
    """LSTM(input=1, hidden=32, layers=2, output=1) - 提示词指定架构。

    用纯 PyTorch 实现 (不引 pytorch-lightning, 减少依赖)。
    内部用 torch.nn.LSTM + torch.nn.Linear, CPU 跑 50 epoch 单楼 30-60s。
    """

    def __init__(self, hidden: int, layers: int):
        import torch
        import torch.nn as nn
        self.torch = torch
        self.hidden = hidden
        self.layers = layers
        self.device = torch.device("cpu")  # demo 级, 强制 CPU, 避免 GPU 占用

        self.lstm = nn.LSTM(
            input_size=1,
            hidden_size=hidden,
            num_layers=layers,
            batch_first=True,
        ).to(self.device)
        self.fc = nn.Linear(hidden, 1).to(self.device)

    def forward(self, x):
        """x shape: (batch, seq_len, 1), 返 (batch, 1)。"""
        out, _ = self.lstm(x)
        # 取序列最后一个时间步的输出 -> fc
        out = self.fc(out[:, -1, :])
        return out

    def parameters(self):
        return list(self.lstm.parameters()) + list(self.fc.parameters())


def predict_lstm(
    train_df: pd.DataFrame,
    horizon: int,
    epochs: int | None = None,
    window: int | None = None,
) -> list[dict]:
    """LSTM demo 训练 + 自回归预测。

    步骤:
    1. MinMaxScaler 归一化到 [0, 1]
    2. 滑动窗口 window=14 构造样本: (X=[v_{t-13}..v_{t-1}], Y=v_t)
       用前 14 天预测第 15 天。14 天能覆盖两周的作息周期。
    3. LSTM(1,32,2,1) + MSELoss + Adam(lr=0.01)
    4. 50 epoch CPU, batch_size=8 (提示词指定参数)
    5. 用最后 14 天作 seed, 自回归 predict horizon 天
       (每次 predict 一天后, 把预测值塞回窗口再 predict 下一天)
    6. 反归一化回原值

    无置信区间 (dropout MC 估计一期不做, demo 不强求)。
    返 forecast: [{date, yhat, yhat_lower=None, yhat_upper=None}]
    """
    import torch
    import torch.nn as nn
    from sklearn.preprocessing import MinMaxScaler

    if epochs is None:
        epochs = settings.prediction_lstm_epochs
    if window is None:
        window = settings.prediction_lstm_window

    # 固定随机种子保证可复现 (demo 一致性, 真实场景可去掉)
    torch.manual_seed(42)
    np.random.seed(42)

    # 归一化
    values = train_df["value"].astype(float).values.reshape(-1, 1)
    scaler = MinMaxScaler(feature_range=(0, 1))
    scaled = scaler.fit_transform(values).flatten()

    if len(scaled) <= window:
        raise ValueError(
            f"LSTM 训练数据 {len(scaled)} 天不足 window={window}+1, 至少需要 {window+1} 天"
        )

    # 滑动窗口构造样本: X[i] = scaled[i:i+window], Y[i] = scaled[i+window]
    X, Y = [], []
    for i in range(len(scaled) - window):
        X.append(scaled[i:i + window])
        Y.append(scaled[i + window])
    X = np.array(X).reshape(-1, window, 1)
    Y = np.array(Y)
    logger.info(
        "predict_lstm 训练样本: X.shape={}, Y.shape={}, epochs={}, window={}",
        X.shape, Y.shape, epochs, window,
    )

    model = _LSTMModel(
        hidden=settings.prediction_lstm_hidden,
        layers=settings.prediction_lstm_layers,
    )
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=settings.prediction_lstm_learning_rate)

    # 训练循环 (全 batch, 数据量小不用 mini-batch 也行, 但 batch_size=8 提示词指定)
    batch_size = min(settings.prediction_lstm_batch_size, len(X))
    model.lstm.train()
    model.fc.train()
    for epoch in range(epochs):
        # 打乱顺序
        perm = torch.randperm(len(X))
        epoch_loss = 0.0
        for i in range(0, len(X), batch_size):
            idx = perm[i:i + batch_size]
            batch_x = torch.tensor(X[idx], dtype=torch.float32).to(model.device)
            batch_y = torch.tensor(Y[idx], dtype=torch.float32).reshape(-1, 1).to(model.device)

            pred = model.forward(batch_x)
            loss = criterion(pred, batch_y)

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            epoch_loss += loss.item() * len(idx)
        epoch_loss /= len(X)
        if (epoch + 1) % 10 == 0 or epoch == 0:
            logger.info("LSTM epoch {}/{} loss={:.6f}", epoch + 1, epochs, epoch_loss)

    # 自回归预测 horizon 天
    # 用最后 window 天作 seed, 每次预测下一天后塞回窗口
    model.lstm.eval()
    model.fc.eval()
    with torch.no_grad():
        seed = scaled[-window:].tolist()
        preds = []
        for _ in range(horizon):
            x = torch.tensor([seed], dtype=torch.float32).reshape(1, window, 1).to(model.device)
            y = model.forward(x).item()
            preds.append(y)
            seed = seed[1:] + [y]  # 滑窗口

    # 反归一化
    preds_arr = np.array(preds).reshape(-1, 1)
    preds_original = scaler.inverse_transform(preds_arr).flatten()

    # 推断预测日期 (从训练集最后一天 +1 开始)
    last_date = train_df["date"].iloc[-1]
    dates = [last_date + timedelta(days=i + 1) for i in range(horizon)]

    # LSTM 无置信区间, yhat_lower/yhat_upper 都 None
    result = [
        {
            "date": d.strftime("%Y-%m-%d"),
            "yhat": round(float(v), 3),
            "yhat_lower": None,
            "yhat_upper": None,
        }
        for d, v in zip(dates, preds_original)
    ]
    return result


# ── 5. linear 实现 ────────────────────────────────────────────────────

def predict_linear(train_df: pd.DataFrame, horizon: int) -> list[dict]:
    """scikit-learn LinearRegression baseline.

    X = day_index (0, 1, 2, ..., n-1), y = value
    拟合后 predict horizon 天。给 Prophet/LSTM 一个统计基线参考,
    MAPE 对比能看出深度模型有没有学到东西。

    无置信区间 (线性回归解析解不给置信区间, 要 t-分布算, 一期不做)。
    """
    from sklearn.linear_model import LinearRegression

    X = np.arange(len(train_df)).reshape(-1, 1)
    y = train_df["value"].astype(float).values
    model = LinearRegression()
    model.fit(X, y)

    # 预测 horizon 天 (day_index 从 len(train_df) 开始)
    future_X = np.arange(len(train_df), len(train_df) + horizon).reshape(-1, 1)
    preds = model.predict(future_X)

    last_date = train_df["date"].iloc[-1]
    dates = [last_date + timedelta(days=i + 1) for i in range(horizon)]

    result = [
        {
            "date": d.strftime("%Y-%m-%d"),
            "yhat": round(float(v), 3),
            "yhat_lower": None,
            "yhat_upper": None,
        }
        for d, v in zip(dates, preds)
    ]
    return result


# ── 5.5 GBM (天气驱动梯度提升) 实现 ─────────────────────────────────────

# gbm 特征列 (顺序跟 build_gbm_features 的列一致, 特征重要性按这个顺序对齐)
# 注意不包含 day_of_year: 它跟气温高度共线 (都编码季节), 梯度提升会优先抢 day_of_year
# 把气温的独立贡献压到接近 0, "天气驱动"的故事就立不住。用粗粒度的 month 保留季节
# 基线, 让气温/露点承担更细的天气变化。
GBM_FEATURE_COLS = (
    "day_of_week", "is_weekend", "month",
    "air_temp_c", "dew_temp_c", "wind_speed_mps", "cloud_cover_pct",
)


def _build_gbm_features(d: pd.DataFrame) -> pd.DataFrame:
    """从 DataFrame[date, weather...] 构造 gbm 特征矩阵。"""
    out = pd.DataFrame(index=d.index)
    out["day_of_week"] = d["date"].dt.dayofweek
    out["is_weekend"] = (d["date"].dt.dayofweek >= 5).astype(float)
    out["month"] = d["date"].dt.month
    for c in ("air_temp_c", "dew_temp_c", "wind_speed_mps", "cloud_cover_pct"):
        out[c] = d[c].astype(float)
    return out


def predict_gbm(
    history_df: pd.DataFrame,
    weather_df: pd.DataFrame,
    eval_days: int,
    horizon: int,
) -> tuple[list[dict], list[dict]]:
    """天气驱动的 GBM (scikit-learn GradientBoostingRegressor, XGBoost 等价) 预测。

    history_df: DataFrame[date, value] 完整历史 (N 天)
    weather_df: DataFrame[date, air_temp_c, dew_temp_c, wind_speed_mps, cloud_cover_pct]
    eval_days: 回测窗口, 用真实天气算 MAPE
    horizon: 未来预测天数, 天气用 day-of-year 气候平均兜底 (没有未来天气)

    特征: 星期 / 周末 / 月份 / 年内第几天 / 气温 / 露点 / 风速 / 云量。
    气温对空调+热水驱动型楼影响最大, 是特征重要性的主看点。

    返 (all_forecast, feature_importances):
      all_forecast: eval_days + horizon 天 [{date, yhat, yhat_lower=None, yhat_upper=None}]
      feature_importances: [{feature, importance}] 按 importance 降序
    """
    from sklearn.ensemble import GradientBoostingRegressor

    # 对齐 energy + weather (left merge, 天气缺失的天留 NaN)
    df = history_df.merge(weather_df, on="date", how="left")

    train_full = df.iloc[:-eval_days].reset_index(drop=True)
    # 天气列 NaN 用列均值填充 (简单 imputation, 不丢训练天; GradientBoosting 不接受 NaN)
    weather_cols = ["air_temp_c", "dew_temp_c", "wind_speed_mps", "cloud_cover_pct"]
    train_full = train_full.dropna(subset=["value"])
    for c in weather_cols:
        if train_full[c].notna().sum() == 0:
            raise ValueError(f"天气列 {c} 全为空, 无法训练 gbm")
        train_full[c] = train_full[c].fillna(train_full[c].mean())
    if len(train_full) < MIN_TRAIN_DAYS:
        raise ValueError(
            f"GBM 训练天仅 {len(train_full)} 天, 少于 {MIN_TRAIN_DAYS}"
        )

    X = _build_gbm_features(train_full)
    y = train_full["value"].astype(float).values

    model = GradientBoostingRegressor(
        n_estimators=200,
        max_depth=3,
        learning_rate=0.08,
        random_state=42,
    )
    model.fit(X, y)

    feature_importances = sorted(
        [
            {"feature": GBM_FEATURE_COLS[i], "importance": round(float(v), 4)}
            for i, v in enumerate(model.feature_importances_)
        ],
        key=lambda x: x["importance"],
        reverse=True,
    )

    # day-of-year 气候平均天气 (未来预测的天气兜底): 每个年内第几天的训练期均值
    clim = train_full.groupby(train_full["date"].dt.dayofyear).agg({
        "air_temp_c": "mean", "dew_temp_c": "mean",
        "wind_speed_mps": "mean", "cloud_cover_pct": "mean",
    }).to_dict("index")

    last_date = history_df["date"].iloc[-1]
    all_forecast: list[dict] = []
    for i in range(eval_days + horizon):
        d = last_date + timedelta(days=i + 1)
        doy = d.timetuple().tm_yday
        row = {"date": d}
        if i < eval_days:
            # 回测期用真实天气 (算 MAPE)
            w = weather_df[weather_df["date"] == d]
            for c in ("air_temp_c", "dew_temp_c", "wind_speed_mps", "cloud_cover_pct"):
                row[c] = float(w.iloc[0][c]) if len(w) > 0 and pd.notna(w.iloc[0][c]) else float(clim.get(doy, {}).get(c, 0.0))
        else:
            # 未来用气候平均
            for c in ("air_temp_c", "dew_temp_c", "wind_speed_mps", "cloud_cover_pct"):
                row[c] = float(clim.get(doy, {}).get(c, 0.0))

        f = _build_gbm_features(pd.DataFrame([row]))
        yhat = float(model.predict(f)[0])
        all_forecast.append({
            "date": d.strftime("%Y-%m-%d"),
            "yhat": round(max(0.0, yhat), 3),
            "yhat_lower": None,
            "yhat_upper": None,
        })

    return all_forecast, feature_importances


# ── 6. 编排 ──────────────────────────────────────────────────────────

def _build_result(
    history_df: pd.DataFrame,
    forecast: list[dict],
    eval_actual: list[float],
    eval_predicted: list[float],
    model_type: str,
    warning: str | None,
    feature_importances: list[dict] | None = None,
) -> dict:
    """构造 result_jsonb 结构。

    history: [{date, value}] - 训练用过的历史数据 (前端画实线)
    forecast: [{date, yhat, yhat_lower, yhat_upper}] - 未来 horizon 天预测 (前端画虚线)
    mape: 最后 eval_days 天实际 vs 预测算的 MAPE
    model_type: prophet / lstm / linear / gbm
    warning: 训练数据不足提示 (BDG2 demo 单年 Prophet 学不到年季节性)
    feature_importances: gbm 特征重要性 [{feature, importance}], 其他模型 None
    """
    result = {
        "history": [
            {"date": d.strftime("%Y-%m-%d"), "value": round(float(v), 3)}
            for d, v in zip(history_df["date"], history_df["value"])
        ],
        "forecast": forecast,
        "mape": evaluate_mape(eval_actual, eval_predicted),
        "model_type": model_type,
        "warning": warning,
    }
    if feature_importances:
        result["feature_importances"] = feature_importances
    return result


def run_prediction(
    job_id: str,
    building_id: str,
    tenant_id: str,
    model_type: str,
    horizon: int,
) -> dict:
    """worker 调的主入口。

    流程:
    1. 拿历史日数据 (最多 365 天)
    2. 切训练集 (history[:-eval_days]) + 验证集 (history[-eval_days:])
    3. 调对应模型预测 horizon + eval_days 天 (前 eval_days 天对照验证集算 MAPE,
       后 horizon 天是真正未来预测)
    4. 算 MAPE
    5. 构造 result_jsonb
    6. UPDATE mart.prediction_job: status=SUCCEEDED, mape, result_jsonb, finished_at

    异常上抛由 worker 主循环捕获置 FAILED。
    """
    if model_type not in MODEL_TYPES:
        raise ValueError(f"不支持的 model_type: {model_type}, 可选 {MODEL_TYPES}")
    if not (1 <= horizon <= settings.prediction_max_horizon_days):
        raise ValueError(
            f"horizon_days 必须在 1-{settings.prediction_max_horizon_days}, 收到 {horizon}"
        )

    t0 = datetime.now(timezone.utc)
    eval_days = settings.prediction_mape_eval_days

    # 1. 拿历史数据
    history = get_building_history(
        building_id=building_id,
        tenant_id=tenant_id,
        max_days=settings.prediction_train_max_days,
    )

    # 数据不足评估验证集时降级 (例如总共只有 35 天, eval=7, 训练集只剩 28 天)
    if len(history) <= eval_days:
        raise ValueError(
            f"训练数据 {len(history)} 天, 无法留 {eval_days} 天作验证集, 至少需要 {eval_days + 1} 天"
        )

    train = history.iloc[:-eval_days].reset_index(drop=True)
    eval_actual = history.iloc[-eval_days:]["value"].astype(float).tolist()

    # 训练数据不足 2 年 (<730 天) 加 warning (Prophet 年季节性学不到)
    warning = None
    if len(history) < 730:
        warning = (
            f"训练数据仅 {len(history)} 天 (<2 年), Prophet 年季节性未学到, MAPE 可能偏高"
        )

    logger.info(
        "run_prediction job={} building_id={} model={} train_days={} eval_days={} horizon={}",
        job_id[:8], building_id[:8], model_type, len(train), eval_days, horizon,
    )

    # 2. 调对应模型预测 horizon + eval_days 天
    # 前 eval_days 行对照验证集算 MAPE, 后 horizon 行是真正未来预测
    total_predict = horizon + eval_days
    feature_importances: list[dict] | None = None
    if model_type == "prophet":
        all_forecast = predict_prophet(train, total_predict)
    elif model_type == "lstm":
        all_forecast = predict_lstm(train, total_predict)
    elif model_type == "gbm":
        weather = get_building_weather_daily(
            building_id=building_id,
            tenant_id=tenant_id,
            max_days=settings.prediction_train_max_days,
        )
        all_forecast, feature_importances = predict_gbm(
            history_df=history,
            weather_df=weather,
            eval_days=eval_days,
            horizon=horizon,
        )
    else:  # linear
        all_forecast = predict_linear(train, total_predict)

    # 3. 切分: 前 eval_days 行对照验证集算 MAPE, 后 horizon 行是未来预测
    eval_predicted = [f["yhat"] for f in all_forecast[:eval_days]]
    future_forecast = all_forecast[eval_days:]

    # 4. 构造 result_jsonb
    result = _build_result(
        history_df=history,
        forecast=future_forecast,
        eval_actual=eval_actual,
        eval_predicted=eval_predicted,
        model_type=model_type,
        warning=warning,
        feature_importances=feature_importances,
    )

    # 5. UPDATE job: SUCCEEDED
    mape_val = result["mape"]
    elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE mart.prediction_job
                SET status = 'SUCCEEDED',
                    mape = %s,
                    result_jsonb = %s::jsonb,
                    finished_at = now(),
                    error_message = NULL
                WHERE id = %s::uuid
            """, (
                mape_val,
                json.dumps(result, ensure_ascii=False),
                job_id,
            ))
        conn.commit()

    logger.info(
        "run_prediction job={} SUCCEEDED mape={} elapsed={:.1f}s",
        job_id[:8], mape_val, elapsed,
    )
    return result


# ── 7. SQL 操作 (给 API 层用) ──────────────────────────────────────────

def create_job(
    tenant_id: str,
    building_id: str,
    model_type: str,
    horizon_days: int,
) -> str:
    """API 层调: 创建 PENDING job, 返 job_id。

    building 存在性 + tenant 归属校验放在 API 层做 (NotFoundError 转 404)。
    这里只负责插表。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO mart.prediction_job
                    (tenant_id, building_id, model_type, horizon_days, status, created_at)
                VALUES (%s::uuid, %s::uuid, %s, %s, 'PENDING', now())
                RETURNING id
            """, (tenant_id, building_id, model_type, horizon_days))
            job_id = str(cur.fetchone()[0])
        conn.commit()
    logger.info(
        "create_job tenant={} building={} model={} horizon={} job={}",
        tenant_id[:8], building_id[:8], model_type, horizon_days, job_id[:8],
    )
    return job_id


def get_job(tenant_id: str, job_id: str) -> dict[str, Any] | None:
    """API 层调: 查单个 job 详情。不存在或跨租户返 None。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, building_id, model_type, horizon_days, status,
                       mape, result_jsonb, error_message,
                       started_at, finished_at, created_at
                FROM mart.prediction_job
                WHERE id = %s::uuid AND tenant_id = %s::uuid
            """, (job_id, tenant_id))
            row = cur.fetchone()
    if row is None:
        return None
    return _row_to_job_out(row)


def get_latest(
    tenant_id: str,
    building_id: str,
    model_type: str | None = None,
) -> dict[str, Any] | None:
    """API 层调: 取最近一次 SUCCEEDED 的预测结果。

    model_type 可选过滤。前端进 PredictionPanel 时调这个, 有最近预测直接显示避免重新跑。
    """
    where_clauses = [
        "tenant_id = %s::uuid",
        "building_id = %s::uuid",
        "status = 'SUCCEEDED'",
    ]
    params: list[Any] = [tenant_id, building_id]
    if model_type:
        where_clauses.append("model_type = %s")
        params.append(model_type)
    where_sql = " AND ".join(where_clauses)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT id, building_id, model_type, horizon_days, status,
                       mape, result_jsonb, error_message,
                       started_at, finished_at, created_at
                FROM mart.prediction_job
                WHERE {where_sql}
                ORDER BY created_at DESC
                LIMIT 1
            """, params)
            row = cur.fetchone()
    if row is None:
        return None
    return _row_to_job_out(row)


def _row_to_job_out(row: tuple) -> dict[str, Any]:
    """DB row -> JobOut dict。row 字段顺序见 SELECT 列。"""
    result_jsonb = row[6]
    return {
        "id": str(row[0]),
        "building_id": str(row[1]),
        "model_type": row[2],
        "horizon_days": row[3],
        "status": row[4],
        "mape": float(row[5]) if row[5] is not None else None,
        "result": result_jsonb if result_jsonb else None,
        "error_message": row[7],
        "started_at": row[8].isoformat() if row[8] else None,
        "finished_at": row[9].isoformat() if row[9] else None,
        "created_at": row[10].isoformat() if row[10] else "",
    }
