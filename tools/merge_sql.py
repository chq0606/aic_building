"""
把所有增量 SQL 合并成单个 init.sql, 给 docker-entrypoint-initdb.d 用。

依赖顺序 (前面建的表/列后面才能 ALTER / 引用):
   1. postgresql_bdg2.sql        建扩展 + 5 schema + 字典表 + 全部基础表 + 分区 + 索引
   2. auth_bdg2.sql              DROP+CREATE core."user"
   3. upload_bdg2.sql            ingest.upload_session
   4. step05_import.sql          import_batch.status 加 MERGING
   5. step08_anomaly.sql         anomaly_event 索引
   6. step09_knowledge.sql       knowledge.chunk 加 tokenized + 换 trigger
   7. step10_retrieval.sql       knowledge.chunk 加 section_path
   8. step11_visual.sql          building_visual_model 加 position_x/y
   9. step12_reconstruction.sql  reconstruction_job 加 8 列
  10. step12_reconstruction_tiles.sql  building_visual_model 加 tiles_path
  11. step13_building_yaw.sql    building_visual_model 加 yaw_deg
  12. prediction_bdg2.sql        DROP+CREATE mart.prediction_job
  13. step17_settings.sql        core.user_settings (依赖 core."user")
  14. step18_assistant.sql       assistant_message 加 optimization_plan
  15. step19_knowledge_embedding.sql  knowledge.document 加 embedding 状态
  16. step20_floor.sql           core.floor + floor_daily_energy + point_status_snapshot
                                 + point.floor_id + target_type 加 FLOOR
  17. step21_building_upload.sql target_type 加 BUILDING
  18. step22_gbm_prediction.sql  prediction_job 加 gbm
  19. step23_ml_outlier.sql      anomaly_event 加 ML_OUTLIER

注意: 所有 step*.sql 都是幂等写法 (IF NOT EXISTS / DROP CONSTRAINT IF EXISTS),
step20_floor.sql 内部还自带 step13/step12_tiles/step19 的补漏段, 重复执行无副作用。

用法:
    python tools/merge_sql.py
    # 输出 sql/init.sql
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = PROJECT_ROOT / "sql"

SQL_FILES = [
    "postgresql_bdg2.sql",
    "auth_bdg2.sql",
    "upload_bdg2.sql",
    "step05_import.sql",
    "step08_anomaly.sql",
    "step09_knowledge.sql",
    "step10_retrieval.sql",
    "step11_visual.sql",
    "step12_reconstruction.sql",
    "step12_reconstruction_tiles.sql",
    "step13_building_yaw.sql",
    "prediction_bdg2.sql",
    "step17_settings.sql",
    "step18_assistant.sql",
    "step19_knowledge_embedding.sql",
    "step20_floor.sql",
    "step21_building_upload.sql",
    "step22_gbm_prediction.sql",
    "step23_ml_outlier.sql",
]

OUTPUT_FILE = SQL_DIR / "init.sql"


def merge():
    parts = []
    parts.append("-- =============================================================")
    parts.append("-- init.sql - 自动生成, 不要手动改")
    parts.append(f"-- 由 tools/merge_sql.py 合并 {len(SQL_FILES)} 个 SQL 文件得到, 给 docker")
    parts.append("-- postgres 容器的 /docker-entrypoint-initdb.d/ 用 (首次启动自动执行)")
    parts.append("-- 改了某个 step SQL 后重跑: python tools/merge_sql.py")
    parts.append("-- =============================================================")
    parts.append("")

    for fname in SQL_FILES:
        fpath = SQL_DIR / fname
        if not fpath.exists():
            print(f"ERROR: {fpath} 不存在", file=sys.stderr)
            sys.exit(1)
        parts.append(f"-- >>>>>> 开始: {fname} <<<<<<")
        parts.append(fpath.read_text(encoding="utf-8"))
        parts.append("")
        parts.append(f"-- >>>>>>> 结束: {fname} <<<<<<<")
        parts.append("")

    OUTPUT_FILE.write_text("\n".join(parts), encoding="utf-8")
    size = OUTPUT_FILE.stat().st_size
    print(f"OK: 合并 {len(SQL_FILES)} 个 SQL 文件 -> {OUTPUT_FILE}")
    print(f"    文件大小: {size:,} bytes ({size/1024:.1f} KB)")


if __name__ == "__main__":
    merge()