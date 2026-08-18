"""执行 step09_knowledge.sql 给 knowledge.chunk 加 chunk_text_tokenized 列并替换 trigger。"""
import sys
import psycopg2

sys.stdout.reconfigure(encoding="utf-8")

DSN = "postgresql://postgres:REDACTED@localhost:5432/aic_building"
SQL_PATH = r"E:\vscode_python\aic_building\step09_knowledge.sql"

with open(SQL_PATH, "r", encoding="utf-8") as f:
    sql = f.read()

conn = psycopg2.connect(DSN)
conn.autocommit = True
cur = conn.cursor()
try:
    cur.execute(sql)
    print("OK: step09_knowledge.sql 执行成功")
    # 验证字段
    cur.execute("""
        SELECT column_name, data_type FROM information_schema.columns
        WHERE table_schema='knowledge' AND table_name='chunk'
        ORDER BY ordinal_position
    """)
    print("\nknowledge.chunk 列:")
    for r in cur.fetchall():
        print(f"  {r[0]:30s} {r[1]}")

    # 验证 trigger
    cur.execute("""
        SELECT tgname FROM pg_trigger
        WHERE tgrelid = 'knowledge.chunk'::regclass AND NOT tgisinternal
    """)
    print("\ntriggers on knowledge.chunk:")
    for r in cur.fetchall():
        print(f"  {r[0]}")
finally:
    cur.close()
    conn.close()
