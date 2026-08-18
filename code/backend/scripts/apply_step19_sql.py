"""执行 step19_knowledge_embedding.sql 给 knowledge.document 加 embedding 状态 + embedded_chunk_count。"""
import sys
import psycopg2

sys.stdout.reconfigure(encoding="utf-8")

DSN = "postgresql://postgres:REDACTED@localhost:5432/aic_building"
SQL_PATH = r"E:\vscode_python\aic_building\step19_knowledge_embedding.sql"

with open(SQL_PATH, "r", encoding="utf-8") as f:
    sql = f.read()

conn = psycopg2.connect(DSN)
conn.autocommit = True
cur = conn.cursor()
try:
    cur.execute(sql)
    print("OK: step19_knowledge_embedding.sql 执行成功")
    # pg_constraint.consrc 在 PG 12+ 已废弃, 用 pg_get_constraintdef() 函数取定义
    cur.execute("""
        SELECT conname, pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conrelid = 'knowledge.document'::regclass AND contype = 'c'
    """)
    print("\nknowledge.document CHECK 约束:")
    for r in cur.fetchall():
        print(f"  {r[0]:30s} {r[1]}")

    cur.execute("""
        SELECT column_name, data_type, column_default
        FROM information_schema.columns
        WHERE table_schema='knowledge' AND table_name='document'
        ORDER BY ordinal_position
    """)
    print("\nknowledge.document 列:")
    for r in cur.fetchall():
        print(f"  {r[0]:30s} {r[1]:20s} default={r[2]}")
finally:
    cur.close()
    conn.close()
