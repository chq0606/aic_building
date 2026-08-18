"""Step 12 单图重建 worker 验收脚本。

跑法:
    cd backend
    "/e/anaconda/envs/building_aic/python.exe" verify_step12.py

直调 service 层 + worker._process_job, 不走 HTTP 和 worker 主循环。这样
不需要起 uvicorn, 也不需要等 worker 轮询 PENDING job, 跑得快 (单 job
~100s)。HTTP 层 (api/reconstruction.py) 只是薄壳, service + worker 跑通
HTTP 层大概率没问题。

验收项 (按 plan):
  1. 上传 photo -> 拿 photo_id
  2. create_job -> PENDING job
  3. _process_job -> 端到端跑通:
     - TripoSplat 推理 ~97s 完成
     - storage/reconstruction/{job_id}/ 下 4 个文件齐全 (output.ply / output_raw.ply / preview.png / input_photo.*)
     - 用 PIL.Image.open 看 preview.png 尺寸 (不 Read)
     - DB: reconstruction_job.status=SUCCEEDED, output_model_id 非空
     - DB: building_visual_model 有 PHOTO_SINGLE + is_active=true 记录
  4. calibrate_ply_scale 数学: bbox 校准后等于用户输入的 length/width/height
  5. retry 机制: 不存在的 photo -> 3 次自动重试后 FAILED
  6. retry_job 手动重试: FAILED job reset 回 PENDING, retry_count=0
  7. list_jobs + get_job 基础查询
  8. delete_job + 磁盘目录也删

GLM 多模态输入规避: 不能 Read 照片或 preview.png。验收时用 PIL.Image.open
看尺寸 + os.path.getsize 看文件大小, 看视觉内容用图片查看器。
"""
import os
import shutil
import sys
import time
from pathlib import Path

# Windows 控制台默认 GBK, loguru 输出中文 + tqdm 进度条会乱码
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# 让 worker 模块能 import (worker 模块会插入 third_party/triposplat 到 sys.path)
BACKEND_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(BACKEND_ROOT / "third_party" / "triposplat"))

from loguru import logger
from PIL import Image
from plyfile import PlyData

from app.db.session import close_pool, get_conn, init_pool
from app.models.reconstruction import SinglePhotoRequest
from app.services.reconstruction_service import (
    create_job,
    delete_job,
    get_job,
    list_jobs,
    retry_job,
)
from app.services.upload_service import save_photo_file
from worker.ply_calibration import calibrate_ply_scale


TEST_BUILDING_CODE = "Bobcat_education_Alissa"  # demo 楼 (sqm=11254.80, floors=2)
TEST_PHOTO = BACKEND_ROOT / "third_party" / "triposplat" / "static" / "example_inputs" / "building_stone_house.webp"


def get_demo_tenant_id() -> str:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM core.tenant WHERE tenant_code='demo'")
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("demo 租户不存在, 先跑 seed")
            return str(row[0])


def get_building_id_by_code(building_code: str, tenant_id: str) -> str:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM core.building
                WHERE building_code = %s AND tenant_id = %s::uuid
            """, (building_code, tenant_id))
            row = cur.fetchone()
            if row is None:
                raise RuntimeError(f"building 不存在: {building_code}")
            return str(row[0])


def cleanup_visual_models(tenant_id: str, building_id: str) -> None:
    """跑前清场: 删该 building 所有 PHOTO_SINGLE visual_model, 避免上次跑残留。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM core.building_visual_model
                WHERE tenant_id = %s::uuid AND building_id = %s::uuid
                  AND model_mode = 'PHOTO_SINGLE'
            """, (tenant_id, building_id))
            deleted = cur.rowcount
        conn.commit()
    if deleted:
        print(f"  清场: 删除 {deleted} 个旧 PHOTO_SINGLE visual_model")


def cleanup_jobs(tenant_id: str, building_id: str) -> None:
    """跑前清场: 删该 building 所有 reconstruction_job + 磁盘文件。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM core.reconstruction_job
                WHERE tenant_id = %s::uuid AND building_id = %s::uuid
            """, (tenant_id, building_id))
            job_ids = [str(r[0]) for r in cur.fetchall()]
            cur.execute("""
                DELETE FROM core.reconstruction_job
                WHERE tenant_id = %s::uuid AND building_id = %s::uuid
            """, (tenant_id, building_id))
        conn.commit()
    for jid in job_ids:
        job_dir = Path("storage/reconstruction") / jid
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)
    if job_ids:
        print(f"  清场: 删除 {len(job_ids)} 个旧 job + 磁盘文件")


def test_upload_photo(tenant_id: str) -> str:
    """测试 save_photo_file。返 photo_id。"""
    print("\n=== test_upload_photo ===")
    assert TEST_PHOTO.exists(), f"测试照片不存在: {TEST_PHOTO}"
    content = TEST_PHOTO.read_bytes()
    print(f"  测试照片: {TEST_PHOTO.name} ({len(content)/1024:.1f} KB)")

    photo_id, path = save_photo_file(
        tenant_id=tenant_id,
        original_filename=TEST_PHOTO.name,
        file_content=content,
        mime_type="image/webp",
    )
    assert path.exists(), f"照片应存在: {path}"
    assert len(photo_id) == 36, f"photo_id 应 uuid4 (36 字符), 实际 {len(photo_id)}"
    print(f"  photo_id={photo_id[:8]}...")
    print(f"  path={path}")
    return photo_id


def test_create_job(tenant_id: str, building_id: str, photo_id: str) -> str:
    """测试 create_job。返 job_id。"""
    print("\n=== test_create_job ===")
    req = SinglePhotoRequest(
        photo_upload_id=photo_id,
        length_m=30.0, width_m=20.0, height_m=12.0,
        floors_count=3,
        position_x=15.0, position_y=25.0,
    )
    result = create_job(tenant_id, building_id, req)
    assert result["status"] == "PENDING", f"应 PENDING, 实际 {result['status']}"
    assert result["engine"] == "TRIPOSPLAT"
    assert result["model_mode"] == "PHOTO_SINGLE"
    assert result["retry_count"] == 0
    job_id = result["id"]
    print(f"  job_id={job_id[:8]}...")
    print(f"  status=PENDING, dimensions=30x20x12 floors=3 pos=(15,25)")

    # DB 落库校验
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT status, engine, model_mode, retry_count,
                       input_length_m, input_width_m, input_height_m,
                       input_floors_count, input_position_x, input_position_y
                FROM core.reconstruction_job WHERE id = %s::uuid
            """, (job_id,))
            row = cur.fetchone()
    assert row[0] == "PENDING"
    assert row[1] == "TRIPOSPLAT"
    assert row[2] == "PHOTO_SINGLE"
    assert row[3] == 0
    assert float(row[4]) == 30.0
    assert float(row[5]) == 20.0
    assert float(row[6]) == 12.0
    assert row[7] == 3
    assert float(row[8]) == 15.0
    assert float(row[9]) == 25.0
    print(f"  DB 落库校验通过")
    return job_id


def test_process_job(job_id: str) -> None:
    """端到端跑通 _process_job。约 100s (TripoSplat 推理 97s + 校准 + 渲染)。"""
    print("\n=== test_process_job (约 100s) ===")
    from worker.triposplat_worker import _process_job

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT tenant_id, building_id, input_upload_file_id, retry_count,
                       input_length_m, input_width_m, input_height_m,
                       input_floors_count, input_position_x, input_position_y
                FROM core.reconstruction_job WHERE id = %s::uuid
            """, (job_id,))
            row = cur.fetchone()
    job = {
        "id": job_id,
        "tenant_id": str(row[0]),
        "building_id": str(row[1]),
        "photo_upload_id": str(row[2]) if row[2] else None,
        "retry_count": row[3],
        "length_m": float(row[4]) if row[4] is not None else None,
        "width_m": float(row[5]) if row[5] is not None else None,
        "height_m": float(row[6]) if row[6] is not None else None,
        "floors_count": row[7],
        "position_x": float(row[8]) if row[8] is not None else None,
        "position_y": float(row[9]) if row[9] is not None else None,
    }

    t0 = time.time()
    _process_job(job)
    print(f"  _process_job 总耗时: {time.time() - t0:.1f}s")

    # 验证产物文件
    job_dir = Path("storage/reconstruction") / job_id
    expected_files = ["output.ply", "output_raw.ply", "preview.png", "preprocessed.webp"]
    for fname in expected_files:
        p = job_dir / fname
        assert p.exists(), f"产物文件不存在: {p}"
        print(f"  {fname}: {p.stat().st_size / 1024:.1f} KB")

    # input_photo 拷贝 (后缀跟原始照片一致)
    input_copies = list(job_dir.glob("input_photo.*"))
    assert len(input_copies) == 1, f"input_photo 应有 1 个拷贝, 实际 {len(input_copies)}"
    print(f"  input_photo: {input_copies[0].name} ({input_copies[0].stat().st_size / 1024:.1f} KB)")

    # 验证 preview.png 是合法图片 (用 PIL, 不 Read)
    preview = Image.open(job_dir / "preview.png")
    assert preview.size[0] > 100 and preview.size[1] > 100, \
        f"preview 太小: {preview.size}"
    print(f"  preview.png: {preview.size}, mode={preview.mode}")

    # 验证 DB
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT status, output_model_id, error_message, started_at, finished_at
                FROM core.reconstruction_job WHERE id = %s::uuid
            """, (job_id,))
            row = cur.fetchone()
    assert row[0] == "SUCCEEDED", f"应 SUCCEEDED, 实际 {row[0]}, err={row[2]}"
    assert row[1] is not None, "output_model_id 应非空"
    assert row[4] is not None, "finished_at 应非空"
    print(f"  job status=SUCCEEDED, output_model_id={str(row[1])[:8]}...")

    # 验证 building_visual_model 写入
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT model_mode, render_format, storage_path, preview_image_path,
                       length_m, width_m, height_m, is_active
                FROM core.building_visual_model WHERE id = %s::uuid
            """, (str(row[1]),))
            vm = cur.fetchone()
    assert vm[0] == "PHOTO_SINGLE"
    assert vm[1] == "PLY"
    assert "output.ply" in vm[2]
    assert "preview.png" in vm[3]
    assert float(vm[4]) == 30.0
    assert float(vm[5]) == 20.0
    assert float(vm[6]) == 12.0
    assert vm[7] is True
    print(f"  building_visual_model: mode=PHOTO_SINGLE format=PLY is_active=True")
    return row[1]  # 返 model_id


def test_scale_calibration() -> None:
    """验证 calibrate_ply_scale 数学正确: 缩放后 bbox 应等于用户输入。"""
    print("\n=== test_scale_calibration ===")
    # 用 Step 11 测试时 TripoSplat 跑出的 raw .ply (bbox ≈ 1x0.7x0.9)
    raw_ply = Path("storage/tripoosplat/test_out/output.ply")
    if not raw_ply.exists():
        print(f"  跳过: 测试 .ply 不存在 {raw_ply} (先跑 _test_triposplat_inference.py 生成)")
        return

    out_ply = Path("storage/reconstruction/_test_calibrate.ply")
    result = calibrate_ply_scale(raw_ply, out_ply, length_m=120.0, width_m=80.0, height_m=7.0)
    print(f"  sx={result['sx']:.3f} sy={result['sy']:.3f} sz={result['sz']:.3f}")

    # 读校准后 .ply 验证 bbox (mmap=False 避免 Windows 文件占用, 删不了)
    ply = PlyData.read(str(out_ply), mmap=False)
    v = ply["vertex"].data
    bbox_x = float(v["x"].max() - v["x"].min())
    bbox_y = float(v["y"].max() - v["y"].min())
    bbox_z = float(v["z"].max() - v["z"].min())
    print(f"  校准后 bbox: x={bbox_x:.3f} y={bbox_y:.3f} z={bbox_z:.3f}")
    assert abs(bbox_x - 120.0) < 0.5, f"x bbox 应≈120, 实际 {bbox_x}"
    assert abs(bbox_y - 80.0) < 0.5, f"y bbox 应≈80, 实际 {bbox_y}"
    assert abs(bbox_z - 7.0) < 0.5, f"z bbox 应≈7, 实际 {bbox_z}"
    print(f"  校准数学验证通过 (target 120x80x7)")

    out_ply.unlink(missing_ok=True)  # 清理


def test_retry_mechanism(tenant_id: str, building_id: str) -> str:
    """验证重试机制: 故意传不存在的 photo -> 3 次自动重试后 FAILED。"""
    print("\n=== test_retry_mechanism ===")
    from worker.triposplat_worker import _handle_job_failure

    # 用一个不存在磁盘上的 photo_id (uuid4 格式但文件找不到)
    fake_photo_id = "00000000-0000-0000-0000-000000000000"
    req = SinglePhotoRequest(
        photo_upload_id=fake_photo_id,
        length_m=10.0, width_m=10.0, height_m=5.0, floors_count=1,
    )

    # create_job 内部校验 photo 文件存在, 所以这里直接构造一个 PENDING job
    # 跳过校验, 让 _process_job 时才触发 FileNotFoundError
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO core.reconstruction_job
                    (tenant_id, building_id, model_mode, engine, status,
                     input_upload_file_id, input_photo_count,
                     input_length_m, input_width_m, input_height_m,
                     input_floors_count, retry_count, created_at)
                VALUES (%s::uuid, %s::uuid, 'PHOTO_SINGLE', 'TRIPOSPLAT', 'PENDING',
                        %s::uuid, 1,
                        10, 10, 5, 1, 0, now())
                RETURNING id
            """, (tenant_id, building_id, fake_photo_id))
            job_id = str(cur.fetchone()[0])
        conn.commit()

    print(f"  job_id={job_id[:8]} (fake photo)")

    # 模拟 worker 拿到 job 后 _process_job 失败 4 次 (1 次原始 + 3 次重试)
    from app.core.config import settings
    max_retries = settings.reconstruction_max_retries

    for attempt in range(max_retries + 1):
        # 拿当前 job 状态
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT retry_count, status FROM core.reconstruction_job
                    WHERE id = %s::uuid
                """, (job_id,))
                row = cur.fetchone()
        retry_count = row[0]
        status = row[1]
        print(f"  attempt {attempt + 1}: retry_count={retry_count}, status={status}")

        # 模拟 _process_job 失败 (传不存在的 photo 会抛 FileNotFoundError)
        job = {
            "id": job_id,
            "tenant_id": tenant_id,
            "building_id": building_id,
            "photo_upload_id": fake_photo_id,
            "retry_count": retry_count,
            "length_m": 10.0, "width_m": 10.0, "height_m": 5.0,
            "floors_count": 1, "position_x": None, "position_y": None,
        }
        _handle_job_failure(job, FileNotFoundError(f"photo 不存在: {fake_photo_id}"))

    # 应该 FAILED
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT status, retry_count, error_message
                FROM core.reconstruction_job WHERE id = %s::uuid
            """, (job_id,))
            row = cur.fetchone()
    assert row[0] == "FAILED", f"应 FAILED, 实际 {row[0]}"
    assert row[1] == max_retries, f"retry_count 应 {max_retries}, 实际 {row[1]}"
    assert row[2] is not None and "photo 不存在" in row[2]
    print(f"  最终状态 FAILED, retry_count={row[1]} (符合预期)")
    return job_id


def test_manual_retry(tenant_id: str, job_id: str) -> None:
    """验证手动 retry: FAILED job reset 回 PENDING, retry_count=0。"""
    print("\n=== test_manual_retry ===")
    result = retry_job(tenant_id, job_id)
    assert result is not None
    assert "error" not in result
    assert result["status"] == "PENDING"
    assert result["retry_count"] == 0
    print(f"  手动 retry 成功: status=PENDING, retry_count=0 (重置)")

    # DB 验证
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT status, retry_count, error_message, started_at, finished_at
                FROM core.reconstruction_job WHERE id = %s::uuid
            """, (job_id,))
            row = cur.fetchone()
    assert row[0] == "PENDING"
    assert row[1] == 0
    assert row[2] is None
    assert row[3] is None
    assert row[4] is None
    print(f"  DB 验证: PENDING, retry_count=0, 所有时间戳清空")


def test_list_and_get_jobs(tenant_id: str, building_id: str) -> None:
    """list_jobs + get_job 基础查询。"""
    print("\n=== test_list_and_get_jobs ===")
    # list 全租户
    all_jobs = list_jobs(tenant_id)
    assert len(all_jobs) > 0, "应至少 1 个 job"
    print(f"  list_jobs(全租户): {len(all_jobs)} 个")

    # 按 building 过滤
    b_jobs = list_jobs(tenant_id, building_id=building_id)
    assert len(b_jobs) > 0
    assert all(j["building_id"] == building_id for j in b_jobs)
    print(f"  list_jobs(building): {len(b_jobs)} 个")

    # 按 status 过滤
    succeeded = list_jobs(tenant_id, status="SUCCEEDED")
    print(f"  list_jobs(status=SUCCEEDED): {len(succeeded)} 个")

    # get_job 详情
    job_id = all_jobs[0]["id"]
    detail = get_job(tenant_id, job_id)
    assert detail is not None
    assert detail["id"] == job_id
    assert "dimensions" in detail
    assert "position" in detail
    print(f"  get_job({job_id[:8]}...): status={detail['status']}, dimensions={detail['dimensions']}")


def test_delete_job(tenant_id: str, job_id: str) -> None:
    """删 job + 验证磁盘文件也删了。"""
    print("\n=== test_delete_job ===")
    job_dir = Path("storage/reconstruction") / job_id
    assert job_dir.exists(), f"删前 job_dir 应存在: {job_dir}"

    result = delete_job(tenant_id, job_id)
    assert result is not None
    assert result["deleted"] is True
    print(f"  delete_job({job_id[:8]}...) 返回 deleted=True")

    # DB 验证: get_job 返 None
    assert get_job(tenant_id, job_id) is None, "删后 get_job 应返 None"
    print(f"  get_job 返 None (符合预期)")

    # 磁盘验证: 目录已删
    assert not job_dir.exists(), f"删后目录不应存在: {job_dir}"
    print(f"  磁盘目录已删: {job_dir}")


def main():
    print("=" * 60)
    print("Step 12 单图重建 worker 验收")
    print("=" * 60)

    init_pool()
    tenant_id = get_demo_tenant_id()
    building_id = get_building_id_by_code(TEST_BUILDING_CODE, tenant_id)
    print(f"tenant_id={tenant_id[:8]}...")
    print(f"building_id={building_id[:8]}... ({TEST_BUILDING_CODE})")

    # 清场
    cleanup_visual_models(tenant_id, building_id)
    cleanup_jobs(tenant_id, building_id)

    # 1. 上传照片
    photo_id = test_upload_photo(tenant_id)

    # 2. 创建 job
    job_id = test_create_job(tenant_id, building_id, photo_id)

    # 3. 端到端跑通 (核心: TripoSplat 推理 + 校准 + 渲染 + DB 写入)
    test_process_job(job_id)

    # 4. 尺度校准数学验证 (用之前测试时生成的 .ply, 不重新跑 TripoSplat)
    test_scale_calibration()

    # 5. 重试机制 (用一个 fake photo_id)
    failed_job_id = test_retry_mechanism(tenant_id, building_id)

    # 6. 手动 retry
    test_manual_retry(tenant_id, failed_job_id)

    # 7. list_jobs + get_job
    test_list_and_get_jobs(tenant_id, building_id)

    # 8. delete_job + 磁盘也删 (删成功跑通的那个 job)
    test_delete_job(tenant_id, job_id)

    # 清理 retry 测试创建的 failed job (它没有产物文件, 直接 DB 删)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM core.reconstruction_job
                WHERE id = %s::uuid
            """, (failed_job_id,))
        conn.commit()

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)

    close_pool()


if __name__ == "__main__":
    main()
