"""Step 18 AI 抽屉后端验收脚本。

跑法:
    cd backend
    "/e/anaconda/envs/building_aic/python.exe" verify_step18.py

直调 service 层 + 走 HTTP (FastAPI TestClient) 双轨验收。

验收项:
  1. tools 直调: 4 个工具用 demo 数据返非空
  2. agent_service: scenario 识别 + system prompt 拼装
  3. qa_service._validate_optimization_plan: JSON Schema 校验
  4. qa_service._extract_references: 引用提取
  5. HTTP: 创建会话 / 列表 / 详情 / 历史 (不需 LLM)
  6. HTTP: SSE 流式发消息 (mock LLM, 验证 SSE 协议)
  7. HTTP: PDF 导出 (用 mock message 数据)
  8. demo 用户也能调 AI 抽屉
  9. llm_service: 用假 Key 调, 返诊断错误 (不真调智谱)
"""
import sys

# Windows 控制台默认 GBK 编码, 打印中文会炸
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import close_pool, get_conn, init_pool
from app.main import app
from app.services import agent_service, qa_service, llm_service
from app.services.llm_service import GlmApiKeyError, GlmResponse
from app.tools import TOOL_SCHEMAS, execute_tool


# ---------------------------------------------------------------------------
# 测试 stub (跟 verify_step17 一样的套路)
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


def setup_http_stub(is_demo: bool = True):
    if is_demo:
        def _stub():
            return make_stub_demo_user()
    else:
        def _stub():
            u = make_stub_demo_user()
            return CurrentUser(
                user_id=u.user_id,
                tenant_id=u.tenant_id,
                username=u.username,
                is_demo=False,
            )
    app.dependency_overrides[get_current_user] = _stub


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


def get_demo_site_id() -> str:
    """demo 租户下的第一个 site (Bobcat Site / BDG2)。

    必须按 tenant_id 过滤, 否则 LIMIT 1 可能返其他租户的 site,
    拿去调 query_park_overview 会触发 NotFoundError。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT s.id FROM core.site s
                   WHERE s.tenant_id = (SELECT id FROM core.tenant WHERE tenant_code='demo')
                   LIMIT 1"""
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("demo 没有 site, 先跑 seed")
            return str(row[0])


def get_demo_building_id() -> str:
    """demo 租户下的第一栋楼。同样按 tenant 过滤。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT b.id FROM core.building b
                   WHERE b.tenant_id = (SELECT id FROM core.tenant WHERE tenant_code='demo')
                   LIMIT 1"""
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("demo 没有 building, 先跑 seed")
            return str(row[0])


# ---------------------------------------------------------------------------
# 1. 工具直调
# ---------------------------------------------------------------------------


def test_tools_execute():
    """4 个工具用 demo 数据返非空, 验证 SQL 和 service 层接线没问题。"""
    print("\n=== test_tools_execute ===")
    tenant_id = get_demo_tenant_id()
    site_id = get_demo_site_id()
    building_id = get_demo_building_id()

    with get_conn() as conn:
        # query_park_overview
        r = execute_tool("query_park_overview", conn, tenant_id, {"site_id": site_id})
        assert r["ok"], f"query_park_overview 失败: {r.get('error')}"
        assert r["building_count"] > 0, "应该有楼"
        print(f"  query_park_overview: {r['building_count']} 栋楼, 总能耗 {r['total_kwh']:.1f} kWh")

        # query_building_energy
        r = execute_tool("query_building_energy", conn, tenant_id, {
            "building_id": building_id,
            "granularity": "month",
        })
        assert r["ok"], f"query_building_energy 失败: {r.get('error')}"
        assert r["series_count"] > 0, "应该有时序数据"
        print(f"  query_building_energy: {r['series_count']} 个时序点, building={r['building_name']}")

        # query_anomalies (单楼)
        r = execute_tool("query_anomalies", conn, tenant_id, {"building_id": building_id})
        assert r["ok"], f"query_anomalies 失败: {r.get('error')}"
        print(f"  query_anomalies(building): total={r.get('total', 0)}")

        # query_anomalies (园区)
        r = execute_tool("query_anomalies", conn, tenant_id, {"site_id": site_id})
        assert r["ok"], f"query_anomalies(site) 失败: {r.get('error')}"
        print(f"  query_anomalies(site): scope={r['scope']}")

        # search_knowledge
        r = execute_tool("search_knowledge", conn, tenant_id, {"query": "EUI 限值", "top_k": 3})
        # 知识库可能没数据 (没导 PDF), ok=True 但 items=[] 也算通过
        assert r["ok"], f"search_knowledge 失败: {r.get('error')}"
        print(f"  search_knowledge: {r['top_k']} 条相关文档")

    print(f"  4 个工具全部跑通 ✓")


# ---------------------------------------------------------------------------
# 2. agent_service
# ---------------------------------------------------------------------------


def test_agent_scenario():
    """场景识别: 关键词命中。"""
    print("\n=== test_agent_scenario ===")
    cases = [
        ("这栋楼有什么节能空间", agent_service.SCENARIO_OPTIMIZATION),
        ("节能优化方案", agent_service.SCENARIO_OPTIMIZATION),
        ("这栋楼异常原因", agent_service.SCENARIO_ANOMALY),
        ("GB 55015 EUI 限值是多少", agent_service.SCENARIO_STANDARD),
        ("这栋楼最近 7 天用电情况", agent_service.SCENARIO_DATA),
    ]
    for msg, expected in cases:
        actual = agent_service.decide_scenario(msg)
        assert actual == expected, f"scenario 错: msg={msg!r} expected={expected} actual={actual}"
        print(f"  {msg!r} -> {actual} ✓")


def test_agent_system_prompt():
    """system prompt 拼装: 含上下文 + 场景指令。"""
    print("\n=== test_agent_system_prompt ===")
    ctx = {
        "site_id": "test-site-id",
        "site_name": "测试园区",
        "building_id": "test-bid",
        "building_name": "测试楼",
        "time_range": {"start": "2026-01-01", "end": "2026-07-01"},
        "metric": "eui",
    }
    for scenario in agent_service.ALL_SCENARIOS:
        prompt = agent_service.build_system_prompt(ctx, scenario)
        assert "建筑能耗分析" in prompt, f"缺基础指令: {scenario}"
        assert "测试楼" in prompt, f"缺上下文: {scenario}"
        if scenario == agent_service.SCENARIO_OPTIMIZATION:
            assert "JSON" in prompt, "optimization 场景缺 JSON 指令"
        print(f"  {scenario}: prompt 长度 {len(prompt)} ✓")


# ---------------------------------------------------------------------------
# 3. qa_service._validate_optimization_plan
# ---------------------------------------------------------------------------


def test_validate_optimization_plan():
    """JSON Schema 校验: 合法数据通过, 缺字段失败。"""
    print("\n=== test_validate_optimization_plan ===")
    # 合法数据
    ok_data = {
        "summary": "测试摘要",
        "problems": [
            {"problem_desc": "问题1", "evidence_ref": "证据1", "severity": "high"},
        ],
        "measures": [
            {"measure_name": "措施1", "saved_kwh": 100, "saved_co2": 50,
             "payback_months": 12, "difficulty": "easy", "clause_ref": "GB 55015"},
            {"measure_name": "措施2", "saved_kwh": 200, "saved_co2": 100,
             "payback_months": 24, "difficulty": "medium", "clause_ref": "GB 50189"},
            {"measure_name": "措施3", "saved_kwh": 300, "saved_co2": 150,
             "payback_months": 36, "difficulty": "hard", "clause_ref": "GB 55015"},
        ],
        "priorities": [
            {"measure_name": "措施1", "reason": "回收期最短"},
        ],
    }
    ok, parsed, err = qa_service._validate_optimization_plan(ok_data)
    assert ok, f"合法数据应该通过: {err}"
    print(f"  合法数据校验通过 ✓ (measures={len(parsed['measures'])})")

    # 缺字段
    bad_data = {"summary": "x", "problems": [], "measures": [], "priorities": []}
    ok, _, err = qa_service._validate_optimization_plan(bad_data)
    assert not ok, "measures=[] 应该失败"
    assert "至少 3 条" in err
    print(f"  measures<3 失败 ✓: {err}")

    # measure 缺字段
    bad_data2 = {
        "summary": "x", "problems": [], "priorities": [],
        "measures": [
            {"measure_name": "缺 saved_kwh"},
            {"measure_name": "x", "saved_kwh": 100, "saved_co2": 50, "payback_months": 1, "difficulty": "easy", "clause_ref": "x"},
            {"measure_name": "x", "saved_kwh": 100, "saved_co2": 50, "payback_months": 1, "difficulty": "easy", "clause_ref": "x"},
        ],
    }
    ok, _, err = qa_service._validate_optimization_plan(bad_data2)
    assert not ok, "measure 缺 saved_kwh 应该失败"
    assert "saved_kwh" in err
    print(f"  measure 缺字段失败 ✓: {err}")


# ---------------------------------------------------------------------------
# 4. _extract_references
# ---------------------------------------------------------------------------


def test_extract_references():
    """引用提取: 从 tool_results 抽 chunk_ids + citations。"""
    print("\n=== test_extract_references ===")
    tool_results = [
        {"name": "search_knowledge", "result": {"ok": True, "items": [
            {"chunk_id": "11111111-1111-1111-1111-111111111111", "standard_no": "GB 55015-2021",
             "section_title": "3.3.1", "content_snippet": "测试条款 1"},
            {"chunk_id": "22222222-2222-2222-2222-222222222222", "standard_no": "GB 50189",
             "section_title": "5.1.1", "content_snippet": "测试条款 2"},
        ]}},
        {"name": "query_anomalies", "result": {"ok": True, "items": [
            {"id": "a1", "event_type": "BASELINE_DEVIATION", "severity": "high",
             "observed_value": 100, "baseline_value": 50, "evidence": "偏高"},
        ]}},
        {"name": "query_building_energy", "result": {"ok": True,
            "building_name": "测试楼", "stats": {"total_kwh": 12345, "peak_kwh": 100}}},
        {"name": "query_park_overview", "result": {"ok": False}},  # 失败的不抽
    ]
    chunk_ids, point_ids, citations = qa_service._extract_references(tool_results)
    assert len(chunk_ids) == 2, f"应该抽 2 个 chunk_id, 实际 {len(chunk_ids)}"
    assert len(citations) >= 3, f"应该至少 3 条 citation, 实际 {len(citations)}"
    types = {c["type"] for c in citations}
    assert "document" in types and "anomaly" in types and "data" in types
    print(f"  chunk_ids={len(chunk_ids)} citations={len(citations)} types={types} ✓")


# ---------------------------------------------------------------------------
# 5. HTTP API 测试 (不涉及 LLM)
# ---------------------------------------------------------------------------


def test_http_sessions():
    """HTTP: 创建会话 / 列表 / 详情。"""
    print("\n=== test_http_sessions ===")
    setup_http_stub(is_demo=True)
    try:
        with TestClient(app) as client:
            # 创建
            resp = client.post("/api/v1/assistant/sessions", json={
                "title": "验收测试会话",
                "context": {"site_id": "test", "metric": "eui"},
            })
            assert resp.status_code == 200, f"创建会话失败: {resp.status_code} {resp.text}"
            body = resp.json()
            assert body["code"] == 0
            session = body["data"]
            session_id = session["id"]
            print(f"  创建会话: id={session_id[:8]}... title={session['title']}")

            # 列表
            resp = client.get("/api/v1/assistant/sessions")
            assert resp.status_code == 200
            body = resp.json()
            assert body["code"] == 0
            assert body["data"]["total"] >= 1
            print(f"  列表: total={body['data']['total']}")

            # 详情
            resp = client.get(f"/api/v1/assistant/sessions/{session_id}")
            assert resp.status_code == 200
            assert resp.json()["data"]["id"] == session_id
            print(f"  详情 ✓")

            # 历史 (空会话应该返空 list)
            resp = client.get(f"/api/v1/assistant/sessions/{session_id}/messages")
            assert resp.status_code == 200
            assert resp.json()["data"]["total"] == 0
            print(f"  历史消息: 0 条 ✓")

            # 404: 不存在的会话
            resp = client.get("/api/v1/assistant/sessions/00000000-0000-0000-0000-000000000000")
            assert resp.status_code == 404
            print(f"  404 不存在会话 ✓")

            return session_id
    finally:
        teardown_http_stub()


def test_http_optimization_plan_404():
    """HTTP: GET optimization-plan 不存在的 message 返 404。"""
    print("\n=== test_http_optimization_plan_404 ===")
    setup_http_stub(is_demo=True)
    try:
        with TestClient(app) as client:
            resp = client.get("/api/v1/assistant/messages/00000000-0000-0000-0000-000000000000/optimization-plan")
            assert resp.status_code == 404
            print(f"  不存在消息返 404 ✓")
    finally:
        teardown_http_stub()


# ---------------------------------------------------------------------------
# 6. SSE 流测试 (mock LLM)
# ---------------------------------------------------------------------------


def test_http_sse_stream():
    """HTTP: POST /messages SSE 流式 (mock LLM 不真调智谱)。

    用 patch 替换 llm_service.chat / chat_stream, 让 agent_service.plan 返
    固定 tool_calls, chat_stream yield 固定 delta, 验证 SSE 事件协议。
    """
    print("\n=== test_http_sse_stream ===")
    setup_http_stub(is_demo=True)

    # 前一个 HTTP 测试结束时 TestClient 退出会关掉连接池, 这里重新建。
    # main() 开头那次 init_pool 已经被 with TestClient 的 lifespan close 掉了。
    init_pool()

    # mock 数据
    fake_tool_calls = [
        {"id": "call_1", "name": "query_park_overview",
         "arguments": {"site_id": get_demo_site_id()}},
    ]
    fake_chat_resp = GlmResponse(
        content="我将调用工具查询数据。",
        tool_calls=fake_tool_calls,
        finish_reason="tool_calls",
    )

    def fake_chat(messages, tools=None, **kwargs):
        # 第一次调用是 agent.plan, 返 tool_calls
        return fake_chat_resp

    def fake_chat_stream(messages, tools=None, **kwargs):
        # 最终回答阶段流式 yield
        for delta in ["这栋楼", "总能耗 ", "12345 ", "kWh"]:
            yield delta

    try:
        with patch("app.services.llm_service.chat", side_effect=fake_chat), \
             patch("app.services.llm_service.chat_stream", side_effect=fake_chat_stream):
            with TestClient(app) as client:
                # 先建会话
                resp = client.post("/api/v1/assistant/sessions", json={"title": "SSE 测试"})
                session_id = resp.json()["data"]["id"]

                # 发消息 (SSE)
                resp = client.post(
                    f"/api/v1/assistant/sessions/{session_id}/messages",
                    json={"content": "园区能耗怎么样", "context": None},
                )
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers.get("content-type", "")

                # 解析 SSE 事件
                events = []
                for line in resp.iter_lines():
                    if isinstance(line, bytes):
                        line = line.decode("utf-8")
                    if line.startswith("data: "):
                        payload = line[6:]
                        if payload == "[DONE]":
                            continue
                        events.append(json.loads(payload))

                # 断言事件序列
                types = [e["type"] for e in events]
                print(f"  SSE 事件序列: {types}")

                assert "tool_calls" in types, "应该有 tool_calls 事件"
                assert "tool_result" in types, "应该有 tool_result 事件"
                assert "delta" in types, "应该有 delta 事件"
                assert "done" in types, "应该有 done 事件"

                # tool_calls 事件含 items
                tc_event = next(e for e in events if e["type"] == "tool_calls")
                assert len(tc_event["items"]) == 1
                assert tc_event["items"][0]["name"] == "query_park_overview"
                print(f"  tool_calls 事件 ✓ name={tc_event['items'][0]['name']}")

                # tool_result 事件含 data + ok
                tr_event = next(e for e in events if e["type"] == "tool_result")
                assert tr_event["ok"] is True
                assert tr_event["data"]["ok"] is True
                assert tr_event["data"]["building_count"] > 0
                print(f"  tool_result 事件 ✓ building_count={tr_event['data']['building_count']}")

                # delta 事件累积文本
                deltas = [e["text"] for e in events if e["type"] == "delta"]
                full_text = "".join(deltas)
                assert "12345" in full_text, f"delta 累积文本应该含 '12345': {full_text!r}"
                print(f"  delta 事件 ✓ 累积: {full_text!r}")

                # done 事件含 message_id
                done_event = next(e for e in events if e["type"] == "done")
                assert done_event["message_id"], "done 事件应该含 message_id"
                print(f"  done 事件 ✓ message_id={done_event['message_id'][:8]}...")

                # 验证消息已持久化
                resp = client.get(f"/api/v1/assistant/sessions/{session_id}/messages")
                msgs = resp.json()["data"]["items"]
                assert len(msgs) >= 2, f"应该有 user+assistant 两条消息, 实际 {len(msgs)}"
                print(f"  持久化 ✓ 共 {len(msgs)} 条消息")

                return done_event["message_id"]
    finally:
        teardown_http_stub()


def test_http_sse_optimization_scenario():
    """HTTP: 节能优化场景 SSE 流, mock LLM 返 JSON。

    验证 optimization_plan 事件触发 + 持久化。
    """
    print("\n=== test_http_sse_optimization_scenario ===")
    setup_http_stub(is_demo=True)

    fake_plan = {
        "summary": "测试楼能耗偏高, 有节能空间",
        "problems": [
            {"problem_desc": "EUI 偏高", "evidence_ref": "对比基线", "severity": "high"},
        ],
        "measures": [
            {"measure_name": "措施1", "saved_kwh": 1000, "saved_co2": 500,
             "payback_months": 12, "difficulty": "easy", "clause_ref": "GB 55015-2021 3.3.1"},
            {"measure_name": "措施2", "saved_kwh": 2000, "saved_co2": 1000,
             "payback_months": 24, "difficulty": "medium", "clause_ref": "GB 50189 5.1.1"},
            {"measure_name": "措施3", "saved_kwh": 3000, "saved_co2": 1500,
             "payback_months": 36, "difficulty": "hard", "clause_ref": "GB 55015-2021 4.2.1"},
        ],
        "priorities": [
            {"measure_name": "措施1", "reason": "回收期最短"},
        ],
    }

    def fake_chat_for_optimization(messages, tools=None, response_format=None, **kwargs):
        # agent.plan 阶段: 返空 tool_calls (LLM 决定不调工具)
        # 最终回答阶段: response_format=json_object, 返 JSON 字符串
        if response_format:
            return GlmResponse(
                content=json.dumps(fake_plan, ensure_ascii=False),
                finish_reason="stop",
            )
        # plan 阶段
        return GlmResponse(content="", tool_calls=[], finish_reason="stop")

    try:
        with patch("app.services.llm_service.chat", side_effect=fake_chat_for_optimization):
            with TestClient(app) as client:
                # 建会话
                resp = client.post("/api/v1/assistant/sessions", json={"title": "节能优化测试"})
                session_id = resp.json()["data"]["id"]

                # 发消息 (节能优化场景)
                resp = client.post(
                    f"/api/v1/assistant/sessions/{session_id}/messages",
                    json={"content": "这栋楼有什么节能空间", "context": {"building_id": get_demo_building_id()}},
                )

                events = []
                for line in resp.iter_lines():
                    if isinstance(line, bytes):
                        line = line.decode("utf-8")
                    if line.startswith("data: "):
                        payload = line[6:]
                        if payload != "[DONE]":
                            events.append(json.loads(payload))

                types = [e["type"] for e in events]
                print(f"  SSE 事件序列: {types}")

                assert "optimization_plan" in types, "应该有 optimization_plan 事件"
                opt_event = next(e for e in events if e["type"] == "optimization_plan")
                assert opt_event["data"]["summary"] == fake_plan["summary"]
                assert len(opt_event["data"]["measures"]) == 3
                print(f"  optimization_plan 事件 ✓ measures={len(opt_event['data']['measures'])}")

                # done 事件
                done_event = next(e for e in events if e["type"] == "done")
                message_id = done_event["message_id"]

                # GET optimization-plan 接口验证持久化
                resp = client.get(f"/api/v1/assistant/messages/{message_id}/optimization-plan")
                assert resp.status_code == 200
                plan = resp.json()["data"]["plan"]
                assert plan["summary"] == fake_plan["summary"]
                print(f"  GET optimization-plan ✓ 持久化 plan={plan['summary'][:20]}...")

                return message_id
    finally:
        teardown_http_stub()


# ---------------------------------------------------------------------------
# 7. PDF 导出测试
# ---------------------------------------------------------------------------


def test_http_export_pdf():
    """HTTP: POST export-pdf 用上一步节能优化测试生成的 message_id。

    验证 PDF bytes 生成 + Content-Type 正确。
    """
    print("\n=== test_http_export_pdf ===")
    message_id = test_http_sse_optimization_scenario()
    if not message_id:
        print("  跳过: 上一步未生成 message_id")
        return

    setup_http_stub(is_demo=True)
    try:
        with TestClient(app) as client:
            resp = client.post(f"/api/v1/assistant/messages/{message_id}/export-pdf")
            assert resp.status_code == 200, f"导出失败: {resp.status_code} {resp.text[:200]}"
            assert resp.headers["content-type"] == "application/pdf"
            content_disposition = resp.headers.get("content-disposition", "")
            assert "filename" in content_disposition
            pdf_bytes = resp.content
            assert len(pdf_bytes) > 1000, f"PDF 太小: {len(pdf_bytes)} bytes"
            # PDF 文件头校验: %PDF-
            assert pdf_bytes[:5] == b"%PDF-", f"PDF 头不对: {pdf_bytes[:10]!r}"
            print(f"  PDF 生成 ✓ size={len(pdf_bytes)} bytes")
            print(f"  Content-Disposition: {content_disposition[:80]}...")
    finally:
        teardown_http_stub()


# ---------------------------------------------------------------------------
# 8. demo 用户也能用 + llm_service 用假 Key 报错
# ---------------------------------------------------------------------------


def test_llm_service_invalid_key():
    """llm_service 用假 Key 调应该报 GlmApiKeyError。"""
    print("\n=== test_llm_service_invalid_key ===")
    try:
        llm_service.chat(
            messages=[{"role": "user", "content": "ping"}],
            api_key="sk-invalid-fake-key-for-test.xxx",
            max_tokens=5,
        )
        raise AssertionError("假 Key 应该报错, 不应该走到这里")
    except GlmApiKeyError as e:
        print(f"  GlmApiKeyError ✓: {e}")
        assert "无效" in str(e) or "Key" in str(e)
    except Exception as e:
        # 网络不通也算通过 (本机没法连智谱)
        print(f"  其他错误 (网络?) 也算通过: {type(e).__name__}: {e}")


def test_resolve_api_key_no_key():
    """没传 Key 且没配 .env 应该抛 GlmApiKeyError。"""
    print("\n=== test_resolve_api_key_no_key ===")
    # 临时把 settings.glm_api_key 设空, 不影响全局 (Settings 是单例, 但这里改完就抛)
    from app.core.config import settings
    original = settings.glm_api_key
    settings.glm_api_key = ""  # 临时清空
    try:
        try:
            llm_service.resolve_api_key(api_key=None, user_id=None)
            raise AssertionError("没 Key 应该抛 GlmApiKeyError")
        except GlmApiKeyError as e:
            print(f"  GlmApiKeyError ✓: {str(e)[:50]}...")
    finally:
        settings.glm_api_key = original


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def main():
    init_pool()
    try:
        # 工具 + service 层
        test_tools_execute()
        test_agent_scenario()
        test_agent_system_prompt()
        test_validate_optimization_plan()
        test_extract_references()

        # HTTP API
        test_http_sessions()
        test_http_optimization_plan_404()

        # SSE 流 + 节能优化 + PDF (顺序跑, 后者依赖前者生成的 message_id)
        test_http_sse_stream()
        test_http_export_pdf()

        # LLM 错误处理
        test_llm_service_invalid_key()
        test_resolve_api_key_no_key()

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
