"""临时脚本: 查 building_visual_model 表实际 schema 位置 + 字段。跑完删。"""
from app.db.session import close_pool, get_conn, init_pool


def main() -> None:
    init_pool()
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # 哪个 schema 下有 building_visual_model
                cur.execute("""
                    SELECT table_schema, table_name
                    FROM information_schema.tables
                    WHERE table_name = 'building_visual_model'
                """)
                rows = cur.fetchall()
        print(f"building_visual_model schemas: {rows}")

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT table_schema, table_name
                    FROM information_schema.tables
                    WHERE table_name = 'reconstruction_job'
                """)
                rows = cur.fetchall()
        print(f"reconstruction_job schemas: {rows}")

        # 列出所有 schema
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT schema_name FROM information_schema.schemata
                    WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
                """)
                schemas = [r[0] for r in cur.fetchall()]
        print(f"\nexisting schemas: {schemas}")

        # building_visual_model 的字段
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT column_name, data_type, is_nullable, column_default
                    FROM information_schema.columns
                    WHERE table_name = 'building_visual_model'
                    ORDER BY ordinal_position
                """)
                rows = cur.fetchall()
        print(f"\nbuilding_visual_model columns ({len(rows)}):")
        for r in rows:
            print(f"  {r[0]:25} {r[1]:20} nullable={r[2]:3} default={r[3]}")

        # 看看 demo 6 栋楼的 building 表数据 (sqm / floors_count)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT building_code, display_name, sqm, floors_count, building_type, primary_use
                    FROM core.building
                    WHERE tenant_id = (SELECT id FROM core.tenant WHERE tenant_code='demo')
                    ORDER BY building_code
                """)
                rows = cur.fetchall()
        print(f"\ndemo buildings ({len(rows)}):")
        for r in rows:
            print(f"  code={r[0]:10} name={r[1]!r:30} sqm={r[2]} floors={r[3]} type={r[4]} use={r[5]}")

        # 当前 building_visual_model 行数
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM core.building_visual_model")
                n = cur.fetchone()[0]
        print(f"\nexisting building_visual_model rows: {n}")
    finally:
        close_pool()


if __name__ == "__main__":
    main()
