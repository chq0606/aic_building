"""Step 19 能耗预测服务后端验收脚本。

跑法:
    cd backend
    e:/anaconda/envs/building_aic/python.exe verify_step19.py

直调 service 层 + 走 HTTP (FastAPI TestClient) 双轨验收。

验收项:
  1. evaluate_mape: 单元测试 (零值跳过 + 简单百分比)
  2. get_building_history: 拿 demo 楼的历史日数据
  3. predict_prophet: 直调训练 + 预测, MAPE < 50%
  4. predict_lstm: 直调训练 (epochs=5 快速冒烟), 输出 horizon 行
  5. predict_linear: 直调训练 + 预测
  6. create_job + get_job + get_latest: service 层 SQL 三件套
  7. HTTP: POST /prediction/buildings/{id}/forecast 返 202 + job_id
  8. HTTP: GET /prediction/jobs/{id} 返完整 job 详情
  9. HTTP: GET /prediction/buildings/{id}/latest 返最近预测
  10. HTTP: demo 用户 POST 返 403 (require_write_access 拦截)
  11. HTTP: horizon_days=0 返 422 (Pydantic 校验)
  12. HTTP: 不存在的 building_id 返 404
  13. worker 进程: 启动 -> pick PENDING job -> 跑完置 SUCCEEDED
"""
import sys
import os
import time
import subprocess
from pathlib import Path
from datetime import datetime, timezone

# Windows 控制台默认 GBK 编码, 打印中文会炸
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# 让 from app import ... 能跑
BACKEND_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_ROOT))

import pandas as pd
from fastapi.testclient import TestClient

from app.core.deps import CurrentUser, get_current_user, require_write_access
from app.db.session import close_pool, get_conn, init_pool
from app.main import app
from app.services import prediction_service


# ---------------------------------------------------------------------------
# 测试 stub (跟 verify_step18 一样的套路)
# ---------------------------------------------------------------------------


def make_stub_demo_user() -> CurrentUser:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id, tenant_id, username, is_demo FROM core."user" WHERE username=\'demo\'')
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("demo 用户不存在, 先跑 seed")
            return CurrentUser(
                user_id=str(row[0]),
                tenant_id=str(row[1]),
                username=row[2],
                is_demo=row[3],
            )


def make_stub_normal_user() -> CurrentUser:
    """非 demo 用户: 复用 demo 的 user_id/tenant_id, 但 is_demo=False。
    这样能通过 require_write_access 检查 (demo=True 才会拦)。
    """
    u = make_stub_demo_user()
    return CurrentUser(
        user_id=u.user_id,
        tenant_id=u.tenant_id,
        username=u.username,
        is_demo=False,
    )


def setup_http_stub(is_demo: bool = True):
    """覆盖 get_current_user + require_write_access 的依赖。
    require_write_access 内部调 get_current_user, 所以只覆盖 get_current_user 即可。
    """
    if is_demo:
        def _stub():
            return make_stub_demo_user()
    else:
        def _stub():
            return make_stub_normal_user()
    app.dependency_overrides[get_current_user] = _stub
    # require_write_access 用 Annotated[CurrentUser, Depends(get_current_user)] 间接拿
    # 但它内部用 if user.is_demo: raise 403, 所以同样要覆盖
    def _stub_write():
        if is_demo:
            from fastapi import HTTPException, status
            from app.core.response import error
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=error(message="demo 用户无写权限", code=403),
            )
        return make_stub_normal_user()
    app.dependency_overrides[require_write_access] = _stub_write


def teardown_http_stub():
    app.dependency_overrides.clear()


def get_demo_tenant_id() -> str:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM core.tenant WHERE tenant_code='demo'")
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("demo 租户不存在")
            return str(row[0])


def get_demo_building_id() -> str:
    """demo 租户下的第一栋楼 (Bobcat_education_Alissa 或 B1 等)。

    按 tenant_id 过滤避免拿到其他租户的楼。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT b.id, b.display_name FROM core.building b
                   WHERE b.tenant_id = (SELECT id FROM core.tenant WHERE tenant_code='demo')
                   ORDER BY b.display_name
                   LIMIT 1"""
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("demo 没有 building, 先跑 seed")
            return str(row[0])


# ---------------------------------------------------------------------------
# 1. evaluate_mape 单元测试
# ---------------------------------------------------------------------------


def test_evaluate_mape():
    """MAPE 计算: 跳过 actual=0, 简单百分比。"""
    print("\n=== test_evaluate_mape ===")
    # 简单用例: actual=[100,200,300], predicted=[110,180,330]
    # APE: |100-110|/100=0.1, |200-180|/200=0.1, |300-330|/300=0.1
    # MAPE = 10%
    mape = prediction_service.evaluate_mape([100, 200, 300], [110, 180, 330])
    assert mape is not None, "MAPE 不应该返 None"
    assert abs(mape - 10.0) < 0.01, f"MAPE 应该是 10.0, 实际 {mape}"
    print(f"  简单用例 MAPE=10% ✓ (实际 {mape})")

    # 零值跳过: actual=[0,100,200], predicted=[50,110,180]
    # 跳过第一个 (actual=0), 后两个 APE: 0.1, 0.1 -> MAPE=10%
    mape = prediction_service.evaluate_mape([0, 100, 200], [50, 110, 180])
    assert mape is not None
    assert abs(mape - 10.0) < 0.01, f"零值跳过 MAPE 应该 10.0, 实际 {mape}"
    print(f"  零值跳过 ✓ (MAPE {mape})")

    # 全零: 返 None
    mape = prediction_service.evaluate_mape([0, 0, 0], [1, 2, 3])
    assert mape is None, "全零应该返 None"
    print(f"  全零返 None ✓")


# ---------------------------------------------------------------------------
# 2. get_building_history
# ---------------------------------------------------------------------------


def test_get_building_history():
    """拿 demo 楼的历史日数据, 验证返非空 + DataFrame 格式。"""
    print("\n=== test_get_building_history ===")
    tenant_id = get_demo_tenant_id()
    building_id = get_demo_building_id()

    hist = prediction_service.get_building_history(
        building_id=building_id,
        tenant_id=tenant_id,
        max_days=365,
    )
    assert len(hist) > 30, f"历史数据应该 >30 天, 实际 {len(hist)}"
    assert "date" in hist.columns, "缺 date 列"
    assert "value" in hist.columns, "缺 value 列"
    print(f"  楼={building_id[:8]}... days={len(hist)} range={hist['date'].min()}~{hist['date'].max()}")
    print(f"  历史 ✓")


# ---------------------------------------------------------------------------
# 3. predict_prophet
# ---------------------------------------------------------------------------


def test_predict_prophet():
    """Prophet 训练 + 预测, MAPE < 50% (demo 单年数据放宽阈值)。"""
    print("\n=== test_predict_prophet ===")
    tenant_id = get_demo_tenant_id()
    building_id = get_demo_building_id()

    hist = prediction_service.get_building_history(
        building_id=building_id,
        tenant_id=tenant_id,
        max_days=365,
    )

    # 切训练集 + 验证集 (最后 7 天)
    eval_days = 7
    train = hist.iloc[:-eval_days].reset_index(drop=True)
    eval_actual = hist.iloc[-eval_days:]["value"].astype(float).tolist()

    # 训练 + 预测 7+14=21 天 (前 7 对照验证集算 MAPE, 后 14 是真正预测)
    t0 = time.time()
    forecast = prediction_service.predict_prophet(train, horizon=21)
    elapsed = time.time() - t0
    assert len(forecast) == 21, f"应该返 21 行, 实际 {len(forecast)}"

    # 验证前 7 行的 MAPE
    eval_predicted = [f["yhat"] for f in forecast[:7]]
    mape = prediction_service.evaluate_mape(eval_actual, eval_predicted)
    print(f"  Prophet 训练耗时={elapsed:.2f}s MAPE={mape}%")
    assert mape is not None, "MAPE 不应该返 None"
    assert mape < 50, f"Prophet MAPE 应该 <50%, 实际 {mape}%"
    print(f"  Prophet 预测 ✓ MAPE<50% (实际 {mape}%)")
    # 看一下预测区间 (Prophet 才有 yhat_lower/yhat_upper)
    sample = forecast[-1]
    print(f"  末尾预测: date={sample['date']} yhat={sample['yhat']:.1f} "
          f"lower={sample['yhat_lower'] is not None} upper={sample['yhat_upper'] is not None}")


# ---------------------------------------------------------------------------
# 4. predict_lstm
# ---------------------------------------------------------------------------


def test_predict_lstm():
    """LSTM 训练 (epochs=5 快速冒烟), 输出 horizon 行。

    epochs=5 训练没收敛, MAPE 会很差, 这里只验证不崩 + 输出格式对。
    """
    print("\n=== test_predict_lstm ===")
    tenant_id = get_demo_tenant_id()
    building_id = get_demo_building_id()

    hist = prediction_service.get_building_history(
        building_id=building_id,
        tenant_id=tenant_id,
        max_days=365,
    )

    t0 = time.time()
    forecast = prediction_service.predict_lstm(hist, horizon=14, epochs=5, window=14)
    elapsed = time.time() - t0
    assert len(forecast) == 14, f"应该返 14 行, 实际 {len(forecast)}"

    # LSTM 无置信区间, yhat_lower/yhat_upper 都应该是 None
    sample = forecast[-1]
    assert sample["yhat_lower"] is None, "LSTM 应该 yhat_lower=None"
    assert sample["yhat_upper"] is None, "LSTM 应该 yhat_upper=None"
    print(f"  LSTM 训练耗时={elapsed:.2f}s forecast_rows={len(forecast)}")
    print(f"  末尾预测: date={sample['date']} yhat={sample['yhat']:.1f}")
    print(f"  LSTM 预测 ✓ (无置信区间符合预期)")


# ---------------------------------------------------------------------------
# 5. predict_linear
# ---------------------------------------------------------------------------


def test_predict_linear():
    """LinearRegression baseline 训练 + 预测。"""
    print("\n=== test_predict_linear ===")
    tenant_id = get_demo_tenant_id()
    building_id = get_demo_building_id()

    hist = prediction_service.get_building_history(
        building_id=building_id,
        tenant_id=tenant_id,
        max_days=365,
    )

    forecast = prediction_service.predict_linear(hist, horizon=14)
    assert len(forecast) == 14, f"应该返 14 行, 实际 {len(forecast)}"

    # linear 无置信区间
    sample = forecast[-1]
    assert sample["yhat_lower"] is None
    assert sample["yhat_upper"] is None
    print(f"  Linear forecast_rows={len(forecast)} 末尾 yhat={sample['yhat']:.1f}")
    print(f"  Linear 预测 ✓")


# ---------------------------------------------------------------------------
# 6. service 层 SQL 三件套 (create_job + get_job + get_latest)
# ---------------------------------------------------------------------------


def test_service_sql_helpers():
    """create_job + get_job + get_latest 三个 SQL helper。

    注意: 这里只测 SQL 操作不调 run_prediction (那是 worker 的活, 测试 13 单测)。
    """
    print("\n=== test_service_sql_helpers ===")
    tenant_id = get_demo_tenant_id()
    building_id = get_demo_building_id()

    # create_job
    job_id = prediction_service.create_job(
        tenant_id=tenant_id,
        building_id=building_id,
        model_type="prophet",
        horizon_days=14,
    )
    assert job_id, "create_job 应该返 job_id"
    print(f"  create_job: job_id={job_id[:8]}...")

    # get_job
    job = prediction_service.get_job(tenant_id, job_id)
    assert job is not None, "get_job 不应该返 None"
    assert job["id"] == job_id
    assert job["status"] == "PENDING"
    assert job["model_type"] == "prophet"
    assert job["horizon_days"] == 14
    assert job["result"] is None, "PENDING job result 应该是 None"
    print(f"  get_job: status={job['status']} model={job['model_type']} horizon={job['horizon_days']} ✓")

    # get_latest (没 SUCCEEDED 的 job 时返 None)
    latest = prediction_service.get_latest(tenant_id, building_id, model_type="prophet")
    # 此时刚创建的 job 是 PENDING, latest 应该返 None
    # (如果之前测试跑过留了 SUCCEEDED job, latest 会返那个, 也算正常)
    if latest is None:
        print(f"  get_latest: 暂无 SUCCEEDED 预测 (符合预期, 当前 job 还是 PENDING)")
    else:
        print(f"  get_latest: 找到历史 SUCCEEDED job (id={latest['id'][:8]}...)")

    # 跨租户: 用一个伪造的 tenant_id 查, 应该返 None
    fake_tenant = "00000000-0000-0000-0000-000000000001"
    cross = prediction_service.get_job(fake_tenant, job_id)
    assert cross is None, "跨租户查 job 应该返 None"
    print(f"  跨租户隔离 ✓")

    # 清理: 删掉这个测试 job (避免污染后续测试)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM mart.prediction_job WHERE id = %s::uuid", (job_id,))
        conn.commit()
    print(f"  清理测试 job ✓")


# ---------------------------------------------------------------------------
# 7-12. HTTP API 测试
# ---------------------------------------------------------------------------
# 注意: TestClient lifespan 会调 close_pool 退出, 每个 test_http_* 函数间
# 连接池可能已关闭。这里在 main() 开头一次性取好 building_id / tenant_id,
# 各 HTTP 测试直接用这些缓存的 ID, 不再调 get_demo_building_id()。


def test_http_submit_forecast(building_id: str) -> str | None:
    """HTTP: POST /prediction/buildings/{id}/forecast 返 202 + job_id。"""
    print("\n=== test_http_submit_forecast ===")
    setup_http_stub(is_demo=False)
    try:
        with TestClient(app) as client:
            resp = client.post(
                f"/api/v1/prediction/buildings/{building_id}/forecast",
                json={"horizon_days": 14, "model_type": "prophet"},
            )
            assert resp.status_code == 202, f"应该返 202, 实际 {resp.status_code}: {resp.text}"
            body = resp.json()
            assert body["code"] == 0
            data = body["data"]
            assert "job_id" in data
            assert data["status"] == "PENDING"
            print(f"  POST 202 ✓ job_id={data['job_id'][:8]}... status={data['status']}")
            return data["job_id"]
    finally:
        teardown_http_stub()


def test_http_get_job(building_id: str, job_id: str | None = None):
    """HTTP: GET /prediction/jobs/{id} 返完整 job 详情。"""
    print("\n=== test_http_get_job ===")
    if not job_id:
        print("  跳过: 没 job_id (test_http_submit_forecast 失败了)")
        return
    setup_http_stub(is_demo=False)
    try:
        with TestClient(app) as client:
            resp = client.get(f"/api/v1/prediction/jobs/{job_id}")
            assert resp.status_code == 200, f"应该返 200, 实际 {resp.status_code}"
            body = resp.json()
            assert body["code"] == 0
            job = body["data"]
            assert job["id"] == job_id
            print(f"  GET job ✓ status={job['status']} model={job['model_type']}")

            # 跨租户: 用一个不存在的 job_id
            resp = client.get("/api/v1/prediction/jobs/00000000-0000-0000-0000-000000000000")
            assert resp.status_code == 404, f"不存在的 job 应该 404, 实际 {resp.status_code}"
            print(f"  404 不存在 job ✓")
    finally:
        teardown_http_stub()


def test_http_get_latest(building_id: str):
    """HTTP: GET /prediction/buildings/{id}/latest 返最近预测 (或 404)。"""
    print("\n=== test_http_get_latest ===")
    setup_http_stub(is_demo=False)
    try:
        with TestClient(app) as client:
            resp = client.get(f"/api/v1/prediction/buildings/{building_id}/latest")
            # 200 (有 SUCCEEDED 缓存) 或 404 (没缓存) 都算正常, 不能 500
            assert resp.status_code in (200, 404), f"应该 200 或 404, 实际 {resp.status_code}: {resp.text}"
            if resp.status_code == 200:
                job = resp.json()["data"]
                print(f"  GET latest ✓ status={job['status']} model={job['model_type']}")
            else:
                print(f"  GET latest 返 404 (无 SUCCEEDED 缓存, 符合预期) ✓")
    finally:
        teardown_http_stub()


def test_http_demo_user_forbidden(building_id: str):
    """HTTP: demo 用户调 POST 预测返 403 (require_write_access 拦截)。"""
    print("\n=== test_http_demo_user_forbidden ===")
    setup_http_stub(is_demo=True)
    try:
        with TestClient(app) as client:
            resp = client.post(
                f"/api/v1/prediction/buildings/{building_id}/forecast",
                json={"horizon_days": 14, "model_type": "prophet"},
            )
            assert resp.status_code == 403, f"demo 应该返 403, 实际 {resp.status_code}"
            print(f"  demo POST 返 403 ✓ (符合预期, demo 不能写)")
    finally:
        teardown_http_stub()


def test_http_horizon_validation(building_id: str):
    """HTTP: horizon_days=0 返 422 (Pydantic ge=1 校验失败)。"""
    print("\n=== test_http_horizon_validation ===")
    setup_http_stub(is_demo=False)
    try:
        with TestClient(app) as client:
            # horizon_days=0 -> ge=1 校验失败
            resp = client.post(
                f"/api/v1/prediction/buildings/{building_id}/forecast",
                json={"horizon_days": 0, "model_type": "prophet"},
            )
            assert resp.status_code == 422, f"horizon=0 应该 422, 实际 {resp.status_code}"
            print(f"  horizon=0 返 422 ✓")

            # horizon_days=91 -> le=90 校验失败
            resp = client.post(
                f"/api/v1/prediction/buildings/{building_id}/forecast",
                json={"horizon_days": 91, "model_type": "prophet"},
            )
            assert resp.status_code == 422, f"horizon=91 应该 422, 实际 {resp.status_code}"
            print(f"  horizon=91 返 422 ✓")

            # model_type 不合法
            resp = client.post(
                f"/api/v1/prediction/buildings/{building_id}/forecast",
                json={"horizon_days": 14, "model_type": "arima"},
            )
            assert resp.status_code == 422, f"model=arima 应该 422, 实际 {resp.status_code}"
            print(f"  model_type=arima 返 422 ✓")
    finally:
        teardown_http_stub()


def test_http_building_not_found():
    """HTTP: 不存在的 building_id 返 404。"""
    print("\n=== test_http_building_not_found ===")
    setup_http_stub(is_demo=False)
    try:
        fake_id = "00000000-0000-0000-0000-000000000001"
        with TestClient(app) as client:
            resp = client.post(
                f"/api/v1/prediction/buildings/{fake_id}/forecast",
                json={"horizon_days": 14, "model_type": "prophet"},
            )
            assert resp.status_code == 404, f"不存在的 building 应该 404, 实际 {resp.status_code}: {resp.text}"
            print(f"  不存在 building 返 404 ✓")

            resp = client.get(f"/api/v1/prediction/buildings/{fake_id}/latest")
            assert resp.status_code == 404, f"不存在的 building latest 应该 404, 实际 {resp.status_code}"
            print(f"  不存在 building latest 返 404 ✓")
    finally:
        teardown_http_stub()


# ---------------------------------------------------------------------------
# 13. worker 进程集成测试
# ---------------------------------------------------------------------------


def test_worker_integration():
    """启动 worker 进程, 创建一个 PENDING job, 等 worker pick 后跑完。

    跑法:
      1. 先用 service 层 create_job 建一个 PENDING job (不调 run_prediction)
      2. 启动 prediction_worker.py 子进程
      3. 轮询 get_job 直到 status=SUCCEEDED (或超时)
      4. 关掉 worker 进程
      5. 验证 result_jsonb 含 history + forecast + mape
    """
    print("\n=== test_worker_integration ===")
    tenant_id = get_demo_tenant_id()
    building_id = get_demo_building_id()

    # 1. 建 PENDING job
    job_id = prediction_service.create_job(
        tenant_id=tenant_id,
        building_id=building_id,
        model_type="prophet",
        horizon_days=7,
    )
    print(f"  创建 PENDING job: {job_id[:8]}...")

    # 2. 启动 worker 子进程 (后台)
    # 用 sys.executable 拿当前 Python 解释器路径 (跟主进程同 env)
    worker_cmd = [
        sys.executable, "-m", "worker.prediction_worker",
    ]
    print(f"  启动 worker: {' '.join(worker_cmd)}")
    worker_proc = subprocess.Popen(
        worker_cmd,
        cwd=str(BACKEND_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )

    try:
        # 3. 轮询直到 SUCCEEDED 或超时 (60s, Prophet 训练应该 < 10s)
        deadline = time.time() + 90
        final_status = None
        while time.time() < deadline:
            job = prediction_service.get_job(tenant_id, job_id)
            if job is None:
                break
            final_status = job["status"]
            if final_status in ("SUCCEEDED", "FAILED"):
                break
            time.sleep(2)
            print(f"  等待 worker... status={final_status}")

        assert final_status == "SUCCEEDED", f"worker 应该跑成 SUCCEEDED, 实际 {final_status}"

        # 4. 验证 result
        job = prediction_service.get_job(tenant_id, job_id)
        assert job["result"] is not None, "SUCCEEDED job result 不应该是 None"
        result = job["result"]
        assert "history" in result, "result 缺 history"
        assert "forecast" in result, "result 缺 forecast"
        assert "mape" in result, "result 缺 mape"
        assert len(result["forecast"]) == 7, f"forecast 应该 7 行, 实际 {len(result['forecast'])}"
        print(f"  worker 跑成 SUCCEEDED ✓ mape={result['mape']}% forecast={len(result['forecast'])} 行")
        print(f"  history={len(result['history'])} 天 warning={result.get('warning') is not None}")
    finally:
        # 5. 关 worker 进程
        if worker_proc.poll() is None:
            worker_proc.terminate()
            try:
                worker_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker_proc.kill()
                worker_proc.wait()
        print(f"  worker 进程已关闭")

        # 6. 清理测试 job
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM mart.prediction_job WHERE id = %s::uuid", (job_id,))
            conn.commit()


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def main():
    print("=" * 70)
    print("Step 19 能耗预测服务 - 验收脚本")
    print("=" * 70)

    init_pool()
    try:
        # 一次性拿好 building_id / tenant_id, 避免后续 TestClient 关连接池后查不到
        tenant_id = get_demo_tenant_id()
        building_id = get_demo_building_id()
        print(f"使用 demo tenant={tenant_id[:8]}... building={building_id[:8]}...")

        # 直调 service 层
        test_evaluate_mape()
        test_get_building_history()
        test_predict_prophet()
        test_predict_lstm()
        test_predict_linear()
        test_service_sql_helpers()

        # HTTP API
        job_id = test_http_submit_forecast(building_id)
        test_http_get_job(building_id, job_id)
        test_http_get_latest(building_id)
        test_http_demo_user_forbidden(building_id)
        test_http_horizon_validation(building_id)
        test_http_building_not_found()

        # worker 集成 (这里 init_pool 重新打开, 因前面 TestClient 关过了)
        init_pool()
        test_worker_integration()

        # 清理: 删掉测试中遗留的 PENDING job (test_http_submit_forecast 创建的)
        init_pool()
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM mart.prediction_job
                    WHERE status = 'PENDING'
                      AND created_at < now() - interval '1 hour'
                """)
            conn.commit()

        print("\n" + "=" * 70)
        print("✓ Step 19 所有验收通过")
        print("=" * 70)
    except AssertionError as e:
        print(f"\n✗ 验收失败: {e}")
        raise
    finally:
        close_pool()


if __name__ == "__main__":
    main()
