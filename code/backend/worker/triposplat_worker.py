"""
TripoSplat 单图重建 worker (Step 12)。

跑法:
    cd backend
    python -m worker.triposplat_worker

工作流:
1. 启动时加载 TripoSplatPipeline (10s, 5 个权重共 3.6GB), 全局单例复用,
   后续 job 不重新加载。pip install 时如果没装 torch / safetensors / tqdm
   等依赖, 启动会失败 -- 见 requirements.txt (Step 12 加了 matplotlib + plyfile
   两个新依赖, 其他都是 TripoSplat 自带的)。

2. 进入主循环, 每 reconstruction_poll_interval (5s) 轮询 DB:
   - SELECT FOR UPDATE SKIP LOCKED 拿一个 PENDING 且 retry 间隔已过的 job
   - UPDATE status='RUNNING', started_at=now(), commit 释放锁
   - 调 _process_job(job):
     a. 从 photo_upload_id 拿 input_photo 路径 (磁盘文件)
     b. pipe.run(photo, steps, num_gaussians, ...) -> (gaussian, prepared)
     c. gaussian.save_ply(output_raw.ply)  # TripoSplat 原始输出, 归一化
     d. prepared.save(preprocessed.webp)   # TripoSplat 预处理后的图 (抠背景后)
     e. calibrate_ply_scale(output_raw.ply, output.ply, length, width, height)
     f. render_preview(output.ply, preview.png)
     g. 复制 input_photo 到 job 目录
     h. _convert_ply_to_3dtiles(output.ply -> tiles/tileset.json)
        用 3dgs-ply-3dtiles-converter (Node) 把 .ply 转成 Cesium 原生支持
        的 3D Tiles (KHR_gaussian_splatting GLB)。转换失败不阻断 job,
        tiles_path 留 NULL, 前端降级走 .ply 静态点云渲染。
     i. 写 building_visual_model (PHOTO_SINGLE / PLY / is_active=true,
        旧 PHOTO_SINGLE 置 is_active=false), 同时写 tiles_path
     j. UPDATE job: status='SUCCEEDED', output_model_id=..., finished_at=now()

3. 异常处理 (捕获所有 Exception, 包括 TripoSplat 内部异常):
   - retry_count < max_retries (3): 置 PENDING, retry_count+=1, last_retry_at=now(),
     error_message=异常信息, started_at=NULL
   - retry_count >= max_retries: 置 FAILED, error_message=异常信息, finished_at=now()
   - last_retry_at 让下一个轮询周期跳过该 job, 等 60s 后再 pick (自然实现重试间隔)

4. SIGINT/SIGTERM 优雅退出: 处理完当前 job 后退出, 不中断进行中的重建。
   Windows 下 SIGTERM 不存在, 主靠 SIGINT (Ctrl+C)。
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

# 让 from app import ... 和 from worker import ... 能跑
BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))
# 让 from triposplat import TripoSplatPipeline 能跑 (vendored 在 third_party/)
sys.path.insert(0, str(BACKEND_ROOT / "third_party" / "triposplat"))

# Windows 控制台 GBK 编码会让 logger 输出中文乱码 (UnicodeEncodeError), 切 UTF-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    # CUDA_HOME 给 PyTorch cpp_extension 用 (虽然 worker 不需要 JIT 编译, 但
    # TripoSplat 内部某些 op 可能用到)。设为 conda env 的 Library 目录。
    os.environ.setdefault("CUDA_HOME", str(BACKEND_ROOT.parent.parent / "anaconda" / "envs" / "building_aic" / "Library"))

from loguru import logger

from app.core.config import settings
from app.core.logging import setup_logging
from app.db.session import close_pool, get_conn, init_pool
from app.services.reconstruction_service import resolve_photo_path
from worker.ply_calibration import calibrate_ply_scale
from worker.preview import render_preview


_pipeline_singleton = None  # 全局缓存 pipeline, 避免每次 job 重新加载 3.6GB 权重


# 3dgs-ply-3dtiles-converter CLI 入口 (npm 包装的 Node 脚本)
# 用 subprocess 调, 不走 Node API 是因为 Python <-> Node 没有低成本 IPC 方式,
# subprocess + JSON 输出够用, 转换 30s 内完成 (1M splats 实测 7s)。
_CONVERTER_BIN = BACKEND_ROOT / "node_modules" / "3dgs-ply-3dtiles-converter" / "bin" / "3dgs-ply-3dtiles-converter.js"


# worker PID 文件锁: 防止两个 pipeline 进程同时活着爆 RAM (2026-07-29 黑屏根因)。
# worker 启动写 PID, 退出删; 任何脚本调 get_pipeline() 前检查 lock, 有 worker 在跑就拒绝。
_WORKER_PID_FILE = settings.reconstruction_storage_path / ".triposplat_worker.pid"


# TripoSplat 推理预设 (Step 13 加)。通过 settings.triposplat_preset 切换,
# 避免每次改单个字段。预设值跟 config.py 注释保持同步。
#
# 默认走 standard (131072 gaussians + 20 steps): 本机 RAM 16GB, worker 单进程
# 常驻 11.5GB 虚拟内存, 多一个 pipeline 进程就会爆 RAM 触发系统重启
# (2026-07-29 14:28 黑屏重启就是这个, 不是 GPU 显存爆)。high_quality 峰值更高
# 不作为默认, 需要时显式 triposplat_preset=high_quality。
_TRIPO_PRESETS = {
    # standard: 旧默认, 显存安全, 细节一般
    "standard":    {"steps": 20, "num_gaussians": 131072},
    # high_quality: 1.5x 密度, RTX 5060 8GB 显存可跑但峰值更高, 不作为默认
    "high_quality": {"steps": 25, "num_gaussians": 196608},
}


def _resolve_tripo_preset() -> tuple[int, int]:
    """按 settings.triposplat_preset 返回 (steps, num_gaussians)。

    preset='none' 时直接用 settings.triposplat_steps / triposplat_num_gaussians
    (老路径, 兼容现有 .env 覆盖)。预设名不存在时落到 standard (默认, 显存安全)
    并 log warning。
    """
    preset = settings.triposplat_preset.strip().lower()
    if preset == "none":
        return settings.triposplat_steps, settings.triposplat_num_gaussians
    if preset in _TRIPO_PRESETS:
        p = _TRIPO_PRESETS[preset]
        return p["steps"], p["num_gaussians"]
    logger.warning(
        "未知 triposplat_preset='{}', 落到 standard. 可选: {} / none",
        settings.triposplat_preset, " / ".join(_TRIPO_PRESETS.keys()),
    )
    p = _TRIPO_PRESETS["standard"]
    return p["steps"], p["num_gaussians"]


def _is_pid_alive(pid: int) -> bool:
    """检查 PID 对应进程是否还活着。Windows 走 ctypes OpenProcess, 不引 psutil 依赖。

    OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, ...) 返 0 表示进程不存在或
    没权限 (系统进程), 两种都视为"不活着" -- 我们的 worker 是用户态进程, 不会
    没权限。返非 0 是活着, 要 CloseHandle 还句柄。
    """
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False
    import ctypes
    kernel32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return False


def _claim_worker_lock():
    """worker 启动时 claim PID 文件锁。

    PID 文件存在且进程活着 -> 拒绝启动 (已有 worker 在跑, 第二个会爆 RAM)。
    PID 文件存在但进程死了 -> 覆盖 (上次 worker 异常退出留的僵尸 lock)。
    PID 文件不存在 -> 写当前 PID。
    """
    pid_file = _WORKER_PID_FILE
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            if old_pid != os.getpid() and _is_pid_alive(old_pid):
                raise RuntimeError(
                    f"TripoSplat worker 已在运行 (pid={old_pid}), 拒绝启动第二个。"
                    f"如确认没在跑, 删 {pid_file} 后重试。"
                )
        except ValueError:
            pass  # 文件内容不是数字, 覆盖
    pid_file.write_text(str(os.getpid()))
    logger.info("worker lock claimed: pid={} -> {}", os.getpid(), pid_file)


def _release_worker_lock():
    """worker 退出时释放 PID 文件锁。只有当前 PID 匹配才删, 避免误删别人的。"""
    pid_file = _WORKER_PID_FILE
    if not pid_file.exists():
        return
    try:
        stored_pid = int(pid_file.read_text().strip())
        if stored_pid == os.getpid():
            pid_file.unlink(missing_ok=True)
            logger.info("worker lock released: {}", pid_file)
    except (ValueError, OSError):
        pass


def _check_no_other_worker():
    """独立脚本调 get_pipeline() 前检查: 是否有 worker 在跑。

    有 worker (PID 文件存在且进程活着且 PID != 当前) -> 抛 RuntimeError。
    防止独立 pipeline 进程跟 worker 共存爆 RAM (2026-07-29 黑屏根因:
    worker 11.5GB + 验证脚本 15.3GB 叠加打满 30GB 虚拟内存触发系统重启)。

    worker 自己调 get_pipeline() 时 PID 文件里是自己, 放行。
    僵尸 lock (进程已死) 清理掉, 放行。
    """
    pid_file = _WORKER_PID_FILE
    if not pid_file.exists():
        return
    try:
        worker_pid = int(pid_file.read_text().strip())
    except (ValueError, OSError):
        return
    if worker_pid == os.getpid():
        return
    if _is_pid_alive(worker_pid):
        raise RuntimeError(
            f"TripoSplat worker 正在运行 (pid={worker_pid}), 独立加载 pipeline 会爆 RAM。"
            f"要么停 worker (taskkill /PID {worker_pid} /F), 要么走 verify_step12.py 复用 worker 单例。"
        )
    try:
        pid_file.unlink(missing_ok=True)
    except OSError:
        pass


def _convert_ply_to_3dtiles(ply_path: Path, output_dir: Path) -> Path | None:
    """调 3dgs-ply-3dtiles-converter 把 .ply 转成 3D Tiles。

    输入: ply_path (calibrate_ply_scale 后的真实米单位 .ply)
    输出: output_dir/tileset.json + output_dir/tiles/{level}/{x}.glb

    返 tileset.json 路径, 失败返 None (job 仍判 SUCCEEDED, 前端降级走 .ply)。

    CLI 参数:
      --no-open-inspector     批处理模式, 不开浏览器 dev tools
      --max-depth 8           LOD 层数, BDG2 单楼 32K-262K gaussians 用 8 层够
      --max-leaf-limit 50000  叶节点最大 splat 数, 控制单 tile GLB 大小
      --lod-multiplier medium     LOD 步长 medium (默认 high), 远处也加载较细 LOD
      --sampling-rate-per-level 0.7  父 LOD 保留 70% splat (默认 0.5), 平滑过渡
      --opacity-filter 0.1     丢弃 opacity<0.1 的 splat (默认 0.05), 减噪声
      --coverage-boost-scale 1.0  简化 splat 覆盖补偿 1.0 (默认 0.8), 不缩小
      (不传 --coordinate: tileset 用单位矩阵, 前端 Cesium3DTileset.modelMatrix
       再施加局部 ENU 变换把 splat 摆到 building.position)

    为什么不传 --coordinate: 我们园区用本地 ENU 坐标系 (CesiumViewer 的
    SCENE_ORIGIN = (0,0) 经纬度), 每栋楼的位置由 building.position_x/y 决定。
    converter 的 --coordinate 是把 tileset 整体放到指定经纬度, 一次只能放一个
    位置, 不适合"一个 tileset 对应一栋楼, 多栋楼各有位置"的场景。改成前端
    Cesium3DTileset.modelMatrix = localToEcef(x, y, 0) 的 4x4 矩阵, 让 splat
    跟 BuildingBlock 共用同一套 localToEcef 坐标变换。

    转换失败处理: 只 log warning 不抛异常。.ply 是主产物 (TripoSplat 直接输出),
    3D Tiles 是渲染优化 (Cesium 原生 3DGS 加载)。tiles 没了前端还能走 .ply
    接口降级渲染 (BuildingSplat fallback 逻辑)。
    """
    if not _CONVERTER_BIN.exists():
        logger.warning(
            "3dgs-ply-3dtiles-converter 未安装: {} 不存在, 跳过 3D Tiles 转换。"
            "前端将走 .ply 静态点云降级渲染。",
            _CONVERTER_BIN,
        )
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    # 已存在 tileset.json 时跳过 (重试 job 不重复转换)
    tileset_path = output_dir / "tileset.json"
    if tileset_path.exists():
        logger.info("3D Tiles 已存在, 跳过转换: {}", tileset_path)
        return tileset_path

    cmd = [
        "node",
        str(_CONVERTER_BIN),
        "--no-open-inspector",
        "--max-depth", "8",
        "--max-leaf-limit", "50000",
        # LOD 参数 (Step 13 调优):
        #   --lod-multiplier medium: LOD 层级间几何误差步长用 medium (默认 high),
        #     让远处也加载较细的 LOD, 减少"近看清晰远看糊"的跳变
        #   --sampling-rate-per-level 0.7: 父 LOD 保留 70% splat (默认 0.5),
        #     让粗 LOD 也有足够细节, 视觉过渡更平滑
        #   --opacity-filter 0.1: 丢弃 opacity < 0.1 的 splat (默认 0.05),
        #     过滤更多半透明噪声高斯, 减少边缘"果冻感"扩散
        #   --coverage-boost-scale 1.0: 简化 splat 覆盖率补偿 1.0 (默认 0.8),
        #     让简化后的 splat 不缩小, 保持视觉覆盖
        "--lod-multiplier", "medium",
        "--sampling-rate-per-level", "0.7",
        "--opacity-filter", "0.1",
        "--coverage-boost-scale", "1.0",
        str(ply_path),
        str(output_dir),
    ]
    t0 = time.time()
    try:
        # capture_output=True 拿 stdout/stderr, text=True 自动 decode
        # timeout=120s: 1M splats 实测 7s, 给 17x 余量防慢机
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,  # 不抛 CalledProcessError, 自己看 returncode
        )
    except subprocess.TimeoutExpired:
        logger.error("3D Tiles 转换超时 (120s): ply={}", ply_path)
        return None
    except FileNotFoundError:
        logger.error("Node.js 未安装或不在 PATH, 跳过 3D Tiles 转换")
        return None

    if result.returncode != 0:
        logger.error(
            "3D Tiles 转换失败 (code={}): ply={} stderr={}",
            result.returncode, ply_path, result.stderr[:500],
        )
        return None

    if not tileset_path.exists():
        logger.error(
            "3D Tiles 转换成功但 tileset.json 未生成: {} stdout={}",
            output_dir, result.stdout[:500],
        )
        return None

    # 读 build_summary.json 拿 splat 数 + 耗时, 给日志用
    summary_path = output_dir / "build_summary.json"
    splat_count = "?"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            splat_count = summary.get("totalSplats", "?")
        except Exception:
            pass

    logger.info(
        "3D Tiles 转换完成: splats={} 耗时={:.1f}s tileset={}",
        splat_count, time.time() - t0, tileset_path,
    )
    return tileset_path


def get_pipeline():
    """延迟加载 TripoSplatPipeline, 全局单例。

    首次调用会花 10s 加载 5 个权重文件到 GPU (3.6GB)。后续 job 复用同一
    实例。worker 启动时主动调一次预热 (避免第一个 job 等额外 10s)。

    独立脚本 (非 worker) 调本函数前会先检查是否有 worker 在跑, 有就拒绝
    (防止两个 pipeline 进程共存爆 RAM, 见 _check_no_other_worker)。
    """
    global _pipeline_singleton
    if _pipeline_singleton is None:
        _check_no_other_worker()
        # 延迟 import: triposplat 模块在 third_party/ 下, sys.path 已加
        from triposplat import TripoSplatPipeline

        ckpt = settings.triposplat_ckpts_path
        # 校验 5 个权重文件都存在, 缺一个直接报错
        required = [
            ckpt / "diffusion_models" / "triposplat_fp16.safetensors",
            ckpt / "vae" / "triposplat_vae_decoder_fp16.safetensors",
            ckpt / "clip_vision" / "dino_v3_vit_h.safetensors",
            ckpt / "vae" / "flux2-vae.safetensors",
            ckpt / "background_removal" / "birefnet.safetensors",
        ]
        missing = [str(p) for p in required if not p.exists()]
        if missing:
            raise FileNotFoundError(
                f"TripoSplat 权重文件缺失, 检查 {ckpt}: 缺 {missing}"
            )

        logger.info("加载 TripoSplatPipeline (5 个权重共 3.6GB)...")
        _pipeline_singleton = TripoSplatPipeline(
            ckpt_path=str(required[0]),
            decoder_path=str(required[1]),
            dinov3_path=str(required[2]),
            flux2_vae_encoder_path=str(required[3]),
            rmbg_path=str(required[4]),
            device="cuda",
        )
        logger.info("TripoSplatPipeline 就绪")
    return _pipeline_singleton


def _claim_next_job():
    """SELECT FOR UPDATE SKIP LOCKED 拿一个 PENDING job 并置 RUNNING。

    事务内: SELECT 锁行 -> UPDATE status='RUNNING' -> commit 释放锁。
    返回 job dict 或 None (无 PENDING 或都在 retry 冷却期)。

    SQL 注意点:
      - FOR UPDATE SKIP LOCKED 跳过被其他 worker 锁住的行, 多 worker 并发安全
      - last_retry_at + interval '60s' < now() 让重试 job 等 60s 再被 pick
        (PENDING 状态没动, 只是查询时跳过, 自然实现重试间隔)
      - ORDER BY created_at 保证最早的 job 先处理 (FIFO)
      - last_retry_at IS NULL 处理首次 PENDING (没重试过)
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, tenant_id, building_id, input_upload_file_id,
                       retry_count,
                       input_length_m, input_width_m, input_height_m,
                       input_floors_count, input_position_x, input_position_y
                FROM core.reconstruction_job
                WHERE status = 'PENDING'
                  AND (last_retry_at IS NULL
                       OR last_retry_at + make_interval(secs => %s) < now())
                ORDER BY created_at
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            """, (settings.reconstruction_retry_interval_seconds,))
            row = cur.fetchone()
            if row is None:
                return None

            (job_id, tenant_id, building_id, photo_id, retry_count,
             length_m, width_m, height_m, floors_count, pos_x, pos_y) = row

            cur.execute("""
                UPDATE core.reconstruction_job
                SET status = 'RUNNING', started_at = now()
                WHERE id = %s::uuid
            """, (str(job_id),))
        conn.commit()

    return {
        "id": str(job_id),
        "tenant_id": str(tenant_id),
        "building_id": str(building_id),
        "photo_upload_id": str(photo_id) if photo_id else None,
        "retry_count": retry_count,
        "length_m": float(length_m) if length_m is not None else None,
        "width_m": float(width_m) if width_m is not None else None,
        "height_m": float(height_m) if height_m is not None else None,
        "floors_count": floors_count,
        "position_x": float(pos_x) if pos_x is not None else None,
        "position_y": float(pos_y) if pos_y is not None else None,
    }


def _insert_visual_model(
    tenant_id: str,
    building_id: str,
    ply_path: Path,
    preview_path: Path,
    length_m: float,
    width_m: float,
    height_m: float,
    floors_count: int,
    position_x: float | None,
    position_y: float | None,
    tiles_path: Path | None = None,
) -> str:
    """写 building_visual_model 记录, 返新记录 id。

    同 building 旧 PHOTO_SINGLE 记录 is_active=false (保留历史), 新记录
    is_active=true。沿用 Step 11 upsert_block_model 的模式。

    tiles_path 可空: 3D Tiles 转换失败时 None, 前端走 .ply 降级渲染。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE core.building_visual_model
                SET is_active = false
                WHERE building_id = %s::uuid
                  AND tenant_id = %s::uuid
                  AND model_mode = 'PHOTO_SINGLE'
                  AND is_active = true
            """, (building_id, tenant_id))
            deactivated = cur.rowcount

            cur.execute("""
                INSERT INTO core.building_visual_model
                    (tenant_id, building_id, model_mode, render_format,
                     storage_path, preview_image_path, tiles_path,
                     length_m, width_m, height_m, floors_count,
                     position_x, position_y, is_active, created_at)
                VALUES (%s::uuid, %s::uuid, 'PHOTO_SINGLE', 'PLY',
                        %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, true, now())
                RETURNING id
            """, (
                tenant_id, building_id,
                str(ply_path), str(preview_path),
                str(tiles_path) if tiles_path else None,
                length_m, width_m, height_m, floors_count,
                position_x, position_y,
            ))
            new_id = str(cur.fetchone()[0])
        conn.commit()
    logger.info(
        "visual_model 写入: tenant={} building={} new={} deactivated_old={} tiles={}",
        tenant_id[:8], building_id[:8], new_id[:8], deactivated,
        bool(tiles_path),
    )
    return new_id


def _process_job(job: dict):
    """处理单个 job。任何异常上抛由主循环捕获走重试逻辑。

    步骤:
      a. 准备 job 目录
      b. 拿 input_photo 路径
      c. TripoSplat 推理 (~97s, 4.6GB 显存)
      d. 保存 output_raw.ply + preprocessed.webp
      e. 尺度校准 -> output.ply
      f. 渲染 preview.png
      g. 复制 input_photo 到 job 目录
      h. 转 3D Tiles (output.ply -> tiles/tileset.json), 失败不阻断
      i. 写 building_visual_model, 拿 model_id
      j. UPDATE reconstruction_job: status='SUCCEEDED', output_model_id=...
    """
    job_id = job["id"]
    tenant_id = job["tenant_id"]
    building_id = job["building_id"]

    if not job["photo_upload_id"]:
        raise FileNotFoundError("job 没有 photo_upload_id, 无法重建")
    if not (job["length_m"] and job["width_m"] and job["height_m"]):
        raise ValueError(f"job 缺尺寸参数: length={job['length_m']}, width={job['width_m']}, height={job['height_m']}")

    job_dir = settings.reconstruction_storage_path / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # b. input_photo 路径 (不存在会抛 FileNotFoundError)
    input_photo = resolve_photo_path(tenant_id, job["photo_upload_id"])

    # c. TripoSplat 推理 (按 preset 选参数, 见 config.py 注释)
    pipe = get_pipeline()
    t0 = time.time()
    steps, num_gaussians = _resolve_tripo_preset()
    gaussian, prepared = pipe.run(
        str(input_photo),
        seed=42,
        steps=steps,
        num_gaussians=num_gaussians,
        guidance_scale=settings.triposplat_guidance_scale,
        shift=settings.triposplat_shift,
        show_progress=True,
    )
    logger.info(
        "job {} TripoSplat 推理耗时 {:.1f}s (preset={} steps={} gaussians={})",
        job_id[:8], time.time() - t0, settings.triposplat_preset, steps, num_gaussians,
    )

    # d. 保存原始输出
    output_raw = job_dir / "output_raw.ply"
    gaussian.save_ply(str(output_raw))
    prepared_path = job_dir / "preprocessed.webp"
    # webp 质量 95 (默认 80) 减少高频损失, 见 config.py 注释
    prepared.save(str(prepared_path), quality=settings.triposplat_webp_quality)

    # e. 尺度校准 (用原始 .ply)
    output_ply = job_dir / "output.ply"
    calibrate_ply_scale(
        output_raw, output_ply,
        job["length_m"], job["width_m"], job["height_m"],
    )

    # f. 渲染 preview
    preview_png = job_dir / "preview.png"
    render_preview(output_ply, preview_png)

    # g. 复制 input_photo 到 job 目录 (产物完整性, 前端能直接拿同一目录的文件)
    input_copy = job_dir / f"input_photo{input_photo.suffix}"
    shutil.copy2(input_photo, input_copy)

    # h. 转 3D Tiles (失败不阻断, tiles_path 留 NULL)
    tiles_dir = job_dir / "tiles"
    tileset_path = _convert_ply_to_3dtiles(output_ply, tiles_dir)

    # i. 写 building_visual_model
    model_id = _insert_visual_model(
        tenant_id=tenant_id,
        building_id=building_id,
        ply_path=output_ply,
        preview_path=preview_png,
        length_m=job["length_m"],
        width_m=job["width_m"],
        height_m=job["height_m"],
        floors_count=job["floors_count"] or 1,
        position_x=job["position_x"],
        position_y=job["position_y"],
        tiles_path=tileset_path,
    )

    # j. UPDATE job: SUCCEEDED
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE core.reconstruction_job
                SET status = 'SUCCEEDED',
                    output_model_id = %s::uuid,
                    finished_at = now(),
                    error_message = NULL
                WHERE id = %s::uuid
            """, (model_id, job_id))
        conn.commit()


def _handle_job_failure(job: dict, exc: Exception):
    """job 失败处理: 自动重试或置 FAILED。

    retry_count < max_retries: 置 PENDING, retry_count+=1, last_retry_at=now()。
    worker 下一轮会跳过 (last_retry_at + 60s > now()), 等 60s 后再 pick。

    retry_count >= max_retries: 置 FAILED, finished_at=now()。
    用户可调 POST /reconstruction/jobs/{id}/retry 手动重试 (retry_count 重置为 0)。
    """
    job_id = job["id"]
    err_msg = str(exc)[:500]

    with get_conn() as conn:
        with conn.cursor() as cur:
            if job["retry_count"] < settings.reconstruction_max_retries:
                cur.execute("""
                    UPDATE core.reconstruction_job
                    SET status = 'PENDING',
                        retry_count = retry_count + 1,
                        last_retry_at = now(),
                        error_message = %s,
                        started_at = NULL
                    WHERE id = %s::uuid
                """, (err_msg, job_id))
                logger.warning(
                    "job {} 失败, 自动重试 ({}/{}): {}",
                    job_id[:8], job["retry_count"] + 1,
                    settings.reconstruction_max_retries, err_msg[:200],
                )
            else:
                cur.execute("""
                    UPDATE core.reconstruction_job
                    SET status = 'FAILED',
                        error_message = %s,
                        finished_at = now()
                    WHERE id = %s::uuid
                """, (err_msg, job_id))
                logger.error(
                    "job {} 超过重试上限 ({}), 置 FAILED: {}",
                    job_id[:8], settings.reconstruction_max_retries, err_msg[:200],
                )
        conn.commit()


_running = True


def _shutdown(signum, frame):
    """SIGINT/SIGTERM 优雅退出: 标记 _running=False, 主循环处理完当前 job 后退出。"""
    global _running
    logger.info("收到信号 {}, 处理完当前 job 后退出", signum)
    _running = False


def main():
    signal.signal(signal.SIGINT, _shutdown)
    # Windows 没 SIGTERM, try 一下避免 AttributeError
    try:
        signal.signal(signal.SIGTERM, _shutdown)
    except (AttributeError, ValueError):
        pass

    setup_logging()
    _claim_worker_lock()
    try:
        logger.info("TripoSplat worker 启动 (pid={})", os.getpid())
        init_pool()

        # 预热 pipeline (加载 3.6GB 权重, 10s)
        get_pipeline()
        logger.info(
            "pipeline 就绪, 进入主循环 (poll_interval={}s, max_retries={}, retry_interval={}s)",
            settings.reconstruction_poll_interval,
            settings.reconstruction_max_retries,
            settings.reconstruction_retry_interval_seconds,
        )

        while _running:
            try:
                job = _claim_next_job()
                if job is None:
                    time.sleep(settings.reconstruction_poll_interval)
                    continue

                logger.info(
                    "picked job {} (building={}, retry={})",
                    job["id"][:8], job["building_id"][:8], job["retry_count"],
                )
                try:
                    _process_job(job)
                    logger.info("job {} SUCCEEDED", job["id"][:8])
                except Exception as e:
                    logger.exception("job {} 失败", job["id"][:8])
                    _handle_job_failure(job, e)
            except Exception as e:
                # 主循环异常 (DB 断连 / 网络抖动等), 不要让 worker 死
                logger.exception("worker 主循环异常: {}", e)
                time.sleep(5)

        logger.info("worker 退出, 关闭连接池")
        close_pool()
    finally:
        _release_worker_lock()


if __name__ == "__main__":
    main()
