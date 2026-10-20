"""Add three genuine BDG2 research buildings, never reset the demo tenant."""
import json
from pathlib import Path
import pandas as pd
from psycopg2.extras import execute_values
from app.db.session import init_pool,get_conn

DATA=Path(__file__).resolve().parents[2]/'fixtures/optimization'

def setup():
    init_pool()
    frame=pd.read_csv(DATA/'bdg2_reference.csv')
    metadata=json.loads((DATA/'bdg2_buildings.json').read_text())
    with get_conn() as conn,conn.cursor() as cur:
        cur.execute((DATA/'migration.sql').read_text(encoding='utf-8'))
        cur.execute("SELECT s.id,s.tenant_id FROM core.site s JOIN core.tenant t ON t.id=s.tenant_id WHERE t.tenant_code='demo' AND s.site_code='Bobcat'")
        row=cur.fetchone()
        if not row:raise ValueError('请先使用现有流程导入BDG2 demo园区')
        site,tenant=row
        for m in metadata:
            code=m['building_id']
            cur.execute("""INSERT INTO core.building(tenant_id,site_id,building_code,display_name,primary_use,sub_use,sqm,source_dataset,source_ref)
                VALUES(%s,%s,%s,%s,%s,%s,%s,'bdg2','https://github.com/buds-lab/building-data-genome-project-2')
                ON CONFLICT(tenant_id,building_code) DO NOTHING""",(tenant,site,code,code,m.get('primaryspaceusage'),m.get('sub_primaryspaceusage'),m.get('sqm')))
            cur.execute('SELECT id FROM core.building WHERE tenant_id=%s AND site_id=%s AND building_code=%s',(tenant,site,code));building=cur.fetchone()[0]
            cur.execute("""INSERT INTO core.point(tenant_id,site_id,building_id,point_code,point_name,energy_type,measure_kind,unit_code,source_dataset)
                VALUES(%s,%s,%s,%s,%s,'electricity','energy_kwh','kWh','bdg2') ON CONFLICT(tenant_id,point_code) DO NOTHING""",
                (tenant,site,building,code+'__electricity',code+' Electricity'))
            cur.execute('SELECT id FROM core.point WHERE tenant_id=%s AND building_id=%s AND point_code=%s',(tenant,building,code+'__electricity'));point=cur.fetchone()[0]
            current=frame[frame.timestamp.str.startswith('2017')][['timestamp',code]].dropna()
            values=[(tenant,site,building,point,str(t)+'+00',float(y)) for t,y in current.itertuples(index=False,name=None)]
            execute_values(cur,"""INSERT INTO fact.point_reading(tenant_id,site_id,building_id,point_id,ts,value_num) VALUES %s
                ON CONFLICT(point_id,ts) DO NOTHING""",values,page_size=1500)
            cur.execute("""INSERT INTO mart.building_daily_energy(tenant_id,building_id,energy_type,date,total_kwh,max_kwh,min_kwh,avg_kwh,hour_count,eui_kwh_per_m2)
                SELECT %s,%s,'electricity',(ts AT TIME ZONE 'UTC')::date,SUM(value_num),MAX(value_num),MIN(value_num),AVG(value_num),COUNT(*),SUM(value_num)/NULLIF(%s,0)
                FROM fact.point_reading WHERE tenant_id=%s AND point_id=%s GROUP BY (ts AT TIME ZONE 'UTC')::date
                ON CONFLICT(building_id,energy_type,date) DO NOTHING""",(tenant,building,m.get('sqm'),tenant,point))
        conn.commit()
        return {'site_id':str(site),'tenant_id':str(tenant),'added_or_preserved_buildings':[m['building_id'] for m in metadata]}

if __name__=='__main__':print(json.dumps(setup()))
