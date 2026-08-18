"""
合并 12 个 SQL 文件成单个 init.sql, 给 docker-entrypoint-initdb.d 用。

依赖顺序 (前面建的表后面才能 ALTER):
  1. postgresql_bdg2  - 建扩展 + 5 schema + 基础表 + 字典
  2. auth_bdg2        - DROP+CREATE core."user" (保留用户表重建以防 schema 变更)
  3. upload_bdg2      - 建 ingest.upload_session
  4. step05_import    - ALTER ingest.import_batch 加状态字段
  5. step08_anomaly   - mart.anomaly_event 索引
  6. step09_knowledge - ALTER knowledge.chunk 加 tokenized 列
  7. step10_retrieval - ALTER knowledge.chunk 加 section_path 列
  8. step11_visual    - ALTER core.building_visual_model 加 position 列
  9. step12_reconstruction - ALTER core.reconstruction_job 加 8 列
 10. prediction_bdg2  - DROP+CREATE mart.prediction_job
 11. step17_settings  - CREATE core.user_settings
 12. step18_assistant - ALTER knowledge.assistant_message 加 optimization_plan 列

用法:
    python tools/merge_sql.py
    # 输出 aic_building/init.sql
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

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
    "prediction_bdg2.sql",
    "step17_settings.sql",
    "step18_assistant.sql",
]

OUTPUT_FILE = PROJECT_ROOT / "init.sql"


def merge():
    parts = []
    parts.append("-- =============================================================")
    parts.append("-- init.sql - 自动生成, 不要手动改")
    parts.append("-- 由 tools/merge_sql.py 合并 12 个 SQL 文件得到, 给 docker postgres")
    parts.append("-- 容器的 /docker-entrypoint-initdb.d/ 用 (首次启动自动执行)")
    parts.append("-- =============================================================")
    parts.append("")

    for fname in SQL_FILES:
        fpath = PROJECT_ROOT / fname
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
