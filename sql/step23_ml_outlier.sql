-- ============================================================================
-- Step 19 补充: Isolation Forest 异常检测 (event_type=ML_OUTLIER)
-- ----------------------------------------------------------------------------
-- mart.anomaly_event.event_type 原 CHECK 只允许 6 类规则式异常, 加 ML_OUTLIER
-- (sklearn IsolationForest 无监督检测楼栋异常能耗日)。
-- ============================================================================

ALTER TABLE mart.anomaly_event DROP CONSTRAINT IF EXISTS anomaly_event_event_type_check;
ALTER TABLE mart.anomaly_event
    ADD CONSTRAINT anomaly_event_event_type_check
    CHECK (event_type IN (
        'SPIKE','DRIFT','PROLONGED_ZERO','MISSING_GAP',
        'SCHEDULE_VIOLATION','BASELINE_DEVIATION','ML_OUTLIER'
    ));
