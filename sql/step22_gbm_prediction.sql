-- ============================================================================
-- Step 19 补充: 天气驱动的 GBM 能耗预测 (model_type=gbm)
-- ----------------------------------------------------------------------------
-- mart.prediction_job.model_type 原 CHECK 只允许 prophet/lstm/linear。
-- 加 gbm (scikit-learn GradientBoostingRegressor, XGBoost 等价实现), 用天气
-- 特征 (气温/露点/风速/云量) + 时间特征 (星期/月份) 预测能耗, 并输出特征重要性。
-- ============================================================================

ALTER TABLE mart.prediction_job DROP CONSTRAINT IF EXISTS prediction_job_model_type_check;
ALTER TABLE mart.prediction_job
    ADD CONSTRAINT prediction_job_model_type_check
    CHECK (model_type IN ('prophet', 'lstm', 'linear', 'gbm'));

COMMENT ON COLUMN mart.prediction_job.model_type IS
  'prophet=Facebook Prophet, lstm=PyTorch LSTM demo, linear=线性回归基线, gbm=天气驱动梯度提升回归';
