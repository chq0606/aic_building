"""查看 knowledge schema 字段类型。"""
import sys
import psycopg2

sys.stdout.reconfigure(encoding="utf-8")

DSN = "postgresql://postgres:REDACTED@localhost:5432/aic_building"
conn = psycopg2.connect(DSN)
cur = conn.cursor()
cur.execute("""
    SELECT column_name, data_type, is_nullable, column_default
    FROM information_schema.columns
    WHERE table_schema='knowledge' AND table_name='document'
    ORDER BY ordinal_position
""")
print("knowledge.document 列:")
for r in cur.fetchall():
    print(f"  {r[0]:25s} {r[1]:30s} null={r[2]} default={r[3]}")

cur.execute("""
    SELECT column_name, data_type, is_nullable, column_default
    FROM information_schema.columns
    WHERE table_schema='knowledge' AND table_name='chunk'
    ORDER BY ordinal_position
""")
print()
print("knowledge.chunk 列:")
for r in cur.fetchall():
    print(f"  {r[0]:25s} {r[1]:30s} null={r[2]} default={r[3]}")
conn.close()
