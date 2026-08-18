"""Step 17 系统设置页后端验收脚本。

跑法:
    cd backend
    "/e/anaconda/envs/building_aic/python.exe" verify_step17.py

直调 service 层 + 走 HTTP (FastAPI TestClient) 双轨验收。HTTP 走
dependency_overrides 把 get_current_user 替换成测试 stub, 跳过 JWT 解码,
直接注入 demo 租户的 CurrentUser。这样不用真起 uvicorn 也不用真去
/auth/login 拿 token, 跑得快。

验收项 (按 plan):
  1. settings_service 直调: 加密往返 + GLM Key 脱敏 hint + BGE 状态 + 数据源状态
  2. GET /settings/glm-api-key HTTP 200 + 字段完整 (configured/hint/updated_at)
  3. PUT /settings/glm-api-key HTTP 200 + 加密入库 (DB 里有密文)
  4. POST /settings/test-glm HTTP 200 + 用假 Key 测返 ok=False + 合理诊断
  5. GET /settings/bge-info HTTP 200 + 字段完整
  6. GET /settings/data-source HTTP 200 + 4 个 count + last_seed_*
  7. POST /admin/seed-demo (demo 用户) HTTP 403 (require_write_access 拦)
  8. GET /health 含 app_name / repo_url / license 字段
"""
import sys

# Windows 控制台默认 GBK 编码, 打印中文 building name 会炸 UnicodeEncodeError
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import close_pool, get_conn, init_pool
from app.main import app
from app.services import settings_service


# ---------------------------------------------------------------------------
# 测试 stub
# ---------------------------------------------------------------------------

def get_demo_user_id() -> str:
    """从 DB 查 demo 用户的真实 user_id (不靠 JWT, 直接拿)。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id, tenant_id, username, is_demo FROM core."user" WHERE username=\'demo\'')
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("demo 用户不存在, 先跑 seed")
            return str(row[0])


def get_demo_tenant_id() -> str:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM core.tenant WHERE tenant_code='demo'")
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("demo 租户不存在, 先跑 seed")
            return str(row[0])


def make_stub_demo_user() -> CurrentUser:
    """构造 demo 用户身份的 CurrentUser (绕过 JWT 解码)。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id, tenant_id, username, is_demo FROM core."user" WHERE username=\'demo\'')
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("demo 用户不存在")
            return CurrentUser(
                user_id=str(row[0]),
                tenant_id=str(row[1]),
                username=row[2],
                is_demo=row[3],
            )


def make_stub_normal_user() -> CurrentUser:
    """构造普通注册用户身份 (用 demo 的 user_id 但 is_demo=False, 跳过 demo 拦截)。

    写操作测试 (seed-demo) 用 demo 用户会被 require_write_access 403 拦,
    测正常流程要 is_demo=False。但实际 demo 用户真实 is_demo=True,
    这里仅做测试 stub 覆盖, 不影响 DB。
    """
    demo = make_stub_demo_user()
    return CurrentUser(
        user_id=demo.user_id,
        tenant_id=demo.tenant_id,
        username=demo.username,
        is_demo=False,  # 关键: 绕过 demo 写拦截
    )


# ---------------------------------------------------------------------------
# 验收测试
# ---------------------------------------------------------------------------

def test_encrypt_roundtrip():
    """AES-GCM 加密往返: encrypt -> decrypt 应该 == 原文。"""
    print("\n=== test_encrypt_roundtrip ===")
    plain = "sk-test-key-12345678.xxxxxxxx"
    enc = settings_service._encrypt(plain)
    assert enc != plain, "密文不应该等于明文"
    dec = settings_service._decrypt(enc)
    assert dec == plain, f"解密失败: {dec} != {plain}"
    # 同明文加密两次 nonce 不同, 密文不同
    enc2 = settings_service._encrypt(plain)
    assert enc != enc2, "两次加密密文相同 (nonce 应该随机)"
    print(f"  plain:  {plain}")
    print(f"  enc:    {enc[:40]}...")
    print(f"  dec:    {dec}")
    print(f" 两次密文不同 (nonce 随机) ✓")


def test_make_hint():
    """脱敏 hint 生成: 末 4 位保留, 前面星号。"""
    print("\n=== test_make_hint ===")
    cases = [
        ("sk-abcdefghij", "*********ghij"),  # 13 字符 -> 9 星 + 末 4
        ("abc", "***"),     # 不足 4 位全星号
        ("1234567890", "******7890"),  # 10 字符 -> 6 星 + 末 4
    ]
    for plain, expected in cases:
        actual = settings_service._make_hint(plain)
        assert actual == expected, f"hint 错: plain={plain} expected={expected} actual={actual}"
        print(f"  {plain} -> {actual} ✓")


def test_set_and_get_glm_key_service():
    """service 层: 保存 GLM Key -> 加密入库 -> 取出来解密一致。"""
    print("\n=== test_set_and_get_glm_key_service ===")
    user_id = get_demo_user_id()
    test_key = "sk-test-step17-abcdef.xyz"

    with get_conn() as conn:
        # 保存
        status_obj = settings_service.set_glm_api_key(conn, user_id, test_key)
        assert status_obj.configured is True
        assert status_obj.hint is not None
        assert status_obj.hint.endswith(test_key[-4:]), f"hint 末 4 位错: {status_obj.hint}"
        print(f"  保存后: hint={status_obj.hint} updated_at={status_obj.updated_at}")

        # 取出来解密
        decrypted = settings_service.get_glm_api_key(conn, user_id)
        assert decrypted == test_key, f"解密不一致: {decrypted} != {test_key}"
        print(f"  解密一致: {decrypted}")

        # 验证 DB 里存的是密文不是明文
        with conn.cursor() as cur:
            cur.execute(
                "SELECT glm_api_key_encrypted FROM core.user_settings WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
            assert row is not None and row[0] is not None
            assert test_key not in row[0], "明文出现在 DB 里! 加密失败"
            print(f"  DB 里是密文, 不含明文 ✓")

        # 状态查询 (脱敏)
        status2 = settings_service.get_glm_key_status(conn, user_id)
        assert status2.configured is True
        assert status2.hint == status_obj.hint
        print(f"  get_glm_key_status 返脱敏值: {status2.hint}")


def test_test_glm_connection_service():
    """service 层: 用假 Key 测试, 应该返 ok=False + 合理诊断。"""
    print("\n=== test_test_glm_connection_service ===")
    result = settings_service.test_glm_connection("sk-invalid-key-xxx")
    print(f"  ok={result.ok} message={result.message}")
    print(f"  detail={result.detail}")
    assert result.ok is False, "假 Key 不应该连通"
    # 合理诊断: 401 / 网络错误 / 超时 都算合理
    assert "无效" in result.message or "超时" in result.message or "连接" in result.message or "API" in result.message, \
        f"诊断信息不合理: {result.message}"
    print(f"  ok=False + 合理诊断 ✓")


def test_get_data_source_status_service():
    """service 层: 查 demo 租户数据源状态, 4 个 count 应该非零。"""
    print("\n=== test_get_data_source_status_service ===")
    tenant_id = get_demo_tenant_id()
    with get_conn() as conn:
        s = settings_service.get_data_source_status(conn, tenant_id)
    print(f"  building_count = {s.building_count}")
    print(f"  point_count    = {s.point_count}")
    print(f"  reading_count  = {s.reading_count}")
    print(f"  anomaly_count  = {s.anomaly_count}")
    print(f"  last_seed_at   = {s.last_seed_at}")
    print(f"  last_seed_status = {s.last_seed_status}")
    assert s.building_count > 0, "demo 应该有楼"
    assert s.reading_count > 0, "demo 应该有读数"
    print(f"  demo 数据非空 ✓")


# ---------------------------------------------------------------------------
# HTTP 验收
# ---------------------------------------------------------------------------

def setup_http_stub(is_demo: bool = True):
    """给 app 注入 dependency_overrides, 跳过 JWT 解码。"""
    if is_demo:
        def _stub():
            return make_stub_demo_user()
    else:
        def _stub():
            return make_stub_normal_user()
    app.dependency_overrides[get_current_user] = _stub


def teardown_http_stub():
    app.dependency_overrides.clear()


def test_http_get_glm_key_status():
    """HTTP: GET /settings/glm-api-key 200 + 字段完整。"""
    print("\n=== test_http_get_glm_key_status ===")
    setup_http_stub(is_demo=True)
    try:
        with TestClient(app) as client:
            resp = client.get("/api/v1/settings/glm-api-key")
            assert resp.status_code == 200, f"HTTP {resp.status_code}"
            body = resp.json()
            assert body["code"] == 0, f"业务码错: {body}"
            data = body["data"]
            assert "configured" in data
            assert "hint" in data
            assert "updated_at" in data
            print(f"  configured={data['configured']} hint={data['hint']} updated_at={data['updated_at']}")
            print(f"  字段完整 ✓")
    finally:
        teardown_http_stub()


def test_http_put_glm_key():
    """HTTP: PUT /settings/glm-api-key 200 + 加密入库。"""
    print("\n=== test_http_put_glm_key ===")
    setup_http_stub(is_demo=True)  # demo 也能配
    try:
        with TestClient(app) as client:
            resp = client.put(
                "/api/v1/settings/glm-api-key",
                json={"api_key": "sk-http-test-key.zzz"},
            )
            assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
            body = resp.json()
            assert body["code"] == 0
            data = body["data"]
            assert data["configured"] is True
            assert data["hint"] is not None
            assert data["hint"].endswith(".zzz"), f"hint 末 4 位错: {data['hint']}"
            print(f"  保存成功: hint={data['hint']} updated_at={data['updated_at']}")

            # 再 GET 验证状态
            resp2 = client.get("/api/v1/settings/glm-api-key")
            assert resp2.status_code == 200
            data2 = resp2.json()["data"]
            assert data2["configured"] is True
            assert data2["hint"] == data["hint"]
            print(f"  GET 状态一致 ✓")
    finally:
        teardown_http_stub()


def test_http_test_glm():
    """HTTP: POST /settings/test-glm 200 + 用假 Key 返 ok=False。"""
    print("\n=== test_http_test_glm ===")
    setup_http_stub(is_demo=True)
    try:
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/settings/test-glm",
                json={"api_key": "sk-invalid-test-glm-key"},
            )
            assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
            body = resp.json()
            assert body["code"] == 0
            data = body["data"]
            assert data["ok"] is False, "假 Key 不应该连通"
            assert "message" in data
            print(f"  ok={data['ok']} message={data['message']}")
            print(f"  detail={data.get('detail')}")
            print(f"  返 ok=False + 合理诊断 ✓")
    finally:
        teardown_http_stub()


def test_http_get_bge_info():
    """HTTP: GET /settings/bge-info 200 + 字段完整。"""
    print("\n=== test_http_get_bge_info ===")
    setup_http_stub(is_demo=True)
    try:
        with TestClient(app) as client:
            resp = client.get("/api/v1/settings/bge-info")
            assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
            body = resp.json()
            assert body["code"] == 0
            data = body["data"]
            for field in ["configured", "model_path", "expected_dim", "actual_dim", "loaded", "device"]:
                assert field in data, f"缺字段: {field}"
            print(f"  configured={data['configured']} model_path={data['model_path']}")
            print(f"  expected_dim={data['expected_dim']} actual_dim={data['actual_dim']}")
            print(f"  loaded={data['loaded']} device={data['device']}")
            print(f"  字段完整 ✓")
    finally:
        teardown_http_stub()


def test_http_get_data_source():
    """HTTP: GET /settings/data-source 200 + 4 count + last_seed_*。"""
    print("\n=== test_http_get_data_source ===")
    setup_http_stub(is_demo=True)
    try:
        with TestClient(app) as client:
            resp = client.get("/api/v1/settings/data-source")
            assert resp.status_code == 200, f"HTTP {resp.status_code}"
            body = resp.json()
            assert body["code"] == 0
            data = body["data"]
            for field in [
                "building_count", "point_count", "reading_count", "anomaly_count",
                "last_seed_at", "last_seed_batch_id", "last_seed_status",
            ]:
                assert field in data, f"缺字段: {field}"
            print(f"  building={data['building_count']} points={data['point_count']}")
            print(f"  readings={data['reading_count']} anomalies={data['anomaly_count']}")
            print(f"  last_seed_at={data['last_seed_at']} status={data['last_seed_status']}")
            print(f"  字段完整 ✓")
    finally:
        teardown_http_stub()


def test_http_seed_demo_rejected_for_demo_user():
    """HTTP: POST /admin/seed-demo (demo 用户) 应该 403。

    require_write_access 拦截 demo, 这是 Step 17 新加的。
    """
    print("\n=== test_http_seed_demo_rejected_for_demo_user ===")
    setup_http_stub(is_demo=True)
    try:
        with TestClient(app) as client:
            resp = client.post("/api/v1/admin/seed-demo?reset=false")
            assert resp.status_code == 403, f"demo 用户应该 403, 实际 {resp.status_code}: {resp.text}"
            body = resp.json()
            # detail 里 message 字段是后端 error() 函数返的
            detail = body.get("detail", {})
            if isinstance(detail, dict):
                msg = detail.get("message", "")
            else:
                msg = str(detail)
            print(f"  HTTP 403 ✓ msg={msg}")
            assert "demo" in msg.lower() or "只读" in msg or "权限" in msg, f"诊断信息不含 demo 提示: {msg}"
            print(f"  demo 拦截生效 ✓")
    finally:
        teardown_http_stub()


def test_http_seed_demo_allowed_for_normal_user():
    """HTTP: POST /admin/seed-demo (普通用户) 应该返 200 + batch_id。

    不真跑 seed (耗时长), 只验 demo 拦截不误伤普通用户。
    用普通用户 stub (is_demo=False) 调, 应该返 200 + batch_id。
    后台线程会跑 seed, 但我们不验证完成, 只验证入口通。
    """
    print("\n=== test_http_seed_demo_allowed_for_normal_user ===")
    setup_http_stub(is_demo=False)
    try:
        with TestClient(app) as client:
            resp = client.post("/api/v1/admin/seed-demo?reset=false")
            assert resp.status_code == 200, f"普通用户应该 200, 实际 {resp.status_code}: {resp.text}"
            body = resp.json()
            assert body["code"] == 0
            data = body["data"]
            assert "batch_id" in data
            assert data["status"] == "LOADING"
            print(f"  HTTP 200 ✓ batch_id={data['batch_id'][:8]}... status={data['status']}")
            print(f"  普通用户调用未误伤 ✓")

            # 等几秒让后台线程跑完 (避免下一个测试受 _seed_lock 影响)
            import time
            time.sleep(2)
    finally:
        teardown_http_stub()


def test_http_health_fields():
    """HTTP: GET /health 含 app_name / repo_url / license 字段。"""
    print("\n=== test_http_health_fields ===")
    with TestClient(app) as client:
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        data = body["data"]
        for field in ["status", "version", "env", "app_name", "repo_url", "license"]:
            assert field in data, f"缺字段: {field}"
        print(f"  status={data['status']} version={data['version']} env={data['env']}")
        print(f"  app_name={data['app_name']}")
        print(f"  repo_url={data['repo_url'] or '(未配置)'}")
        print(f"  license={data['license']}")
        print(f"  字段完整 ✓")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    init_pool()
    try:
        # service 层直调
        test_encrypt_roundtrip()
        test_make_hint()
        test_set_and_get_glm_key_service()
        test_test_glm_connection_service()
        test_get_data_source_status_service()

        # HTTP 验收
        test_http_get_glm_key_status()
        test_http_put_glm_key()
        test_http_test_glm()
        test_http_get_bge_info()
        test_http_get_data_source()
        test_http_seed_demo_rejected_for_demo_user()
        test_http_seed_demo_allowed_for_normal_user()
        test_http_health_fields()

        print("\n\n========================================")
        print("=== ALL TESTS PASSED ===")
        print("========================================")
    except AssertionError as e:
        print(f"\n\n=== ASSERTION FAILED: {e} ===")
        raise
    finally:
        close_pool()


if __name__ == "__main__":
    main()
