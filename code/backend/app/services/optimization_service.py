"""Live BDG2 diagnosis, immutable public evidence, and isolated HVAC preview."""
from copy import deepcopy
import hashlib
import itertools
import json
from pathlib import Path
import threading

import numpy as np
import pandas as pd
from psycopg2.extras import Json

from app.db.session import get_conn
from app.algorithms import building_energy_diagnosis as building_core
from app.algorithms.diagnostic_engine import DiagnosticReplay
from app.algorithms.hvac_energy_optimizer import EnergyOptimizer
from app.services.query_service import NotFoundError
from app.services.hvac_application_service import cooling_plan

DATA = Path(__file__).resolve().parents[2]/'fixtures/optimization'
RUN_LOCK = threading.Lock()


def _load(name):
    return json.loads((DATA/name).read_text(encoding='utf-8'))


def _scope(cur, tenant_id, site_id):
    cur.execute('SELECT id FROM core.site WHERE id=%s AND tenant_id=%s', (site_id, tenant_id))
    if not cur.fetchone():
        raise NotFoundError('园区不存在或不属于当前租户')


def _records(tenant_id, site_id):
    with get_conn() as conn, conn.cursor() as cur:
        _scope(cur, tenant_id, site_id)
        cur.execute('SELECT id,building_code,display_name FROM core.building WHERE tenant_id=%s AND site_id=%s ORDER BY building_code', (tenant_id,site_id))
        buildings=[{'id':str(r[0]),'code':r[1],'name':r[2]} for r in cur.fetchall()]
        supported=[b for b in buildings if b['code'] in building_core.DEFAULT_CONFIG['buildings']]
        if not supported:
            return buildings, supported, [], []
        cur.execute("""SELECT b.building_code,pr.ts,SUM(pr.value_num)
            FROM fact.point_reading pr JOIN core.point p ON p.id=pr.point_id
            JOIN core.building b ON b.id=p.building_id
            WHERE pr.tenant_id=%s AND p.tenant_id=%s AND b.tenant_id=%s AND b.site_id=%s
              AND b.building_code=ANY(%s) AND p.energy_type='electricity' AND p.measure_kind='energy_kwh'
              AND pr.ts>=TIMESTAMPTZ '2017-01-01 00:00:00+00'
              AND pr.ts<TIMESTAMPTZ '2018-01-01 00:00:00+00'
            GROUP BY b.building_code,pr.ts ORDER BY b.building_code,pr.ts""",
            (tenant_id,tenant_id,tenant_id,site_id,[b['code'] for b in supported]))
        readings=[(r[0],r[1].isoformat(),float(r[2])) for r in cur.fetchall()]
        # The legacy BDG2 importer casts weather's naive source labels using the
        # database session timezone. Recover those labels from its provenance;
        # use the CURRENT fact value so edits are still detected by the gate.
        # Copied research electricity labels above explicitly use UTC markers.
        # Re-imports may replace source_batch_id after their staging rows were
        # cleaned. The original successful BDG2 batch still supplies the same
        # source-clock dictionary; never substitute its old temperature value.
        cur.execute("""WITH source_calendar AS (
                         SELECT DISTINCT sw.ts_text::timestamp AS source_clock,
                                         sw.ts_text::timestamptz AS fact_clock
                         FROM ingest.staging_weather sw
                         JOIN ingest.import_batch ib ON ib.id=sw.batch_id AND ib.tenant_id=sw.tenant_id
                         JOIN core.site st ON st.site_code=sw.site_code AND st.tenant_id=sw.tenant_id
                         WHERE sw.tenant_id=%s AND st.id=%s
                           AND ib.dataset_source='bdg2' AND ib.status='SUCCEEDED'
                       )
                       SELECT COALESCE(sc.source_clock,wr.ts AT TIME ZONE 'UTC') AS source_clock,
                              wr.air_temp_c
                       FROM fact.weather_reading wr
                       LEFT JOIN source_calendar sc ON sc.fact_clock=wr.ts
                       WHERE wr.tenant_id=%s AND wr.site_id=%s
                         AND COALESCE(sc.source_clock,wr.ts AT TIME ZONE 'UTC')>=TIMESTAMP '2017-01-01'
                         AND COALESCE(sc.source_clock,wr.ts AT TIME ZONE 'UTC')<TIMESTAMP '2018-01-01'
                       ORDER BY source_clock""", (tenant_id,site_id,tenant_id,site_id))
        weather=[(r[0].isoformat(),None if r[1] is None else float(r[1])) for r in cur.fetchall()]
        return buildings,supported,readings,weather


def _signature(readings, weather):
    return hashlib.sha256(json.dumps([readings,weather],separators=(',',':')).encode()).hexdigest()


def _clean(value):
    return json.loads(json.dumps(value,default=lambda x:x.item() if hasattr(x,'item') else str(x),allow_nan=False))


def run_diagnosis(tenant_id, site_id):
    if not RUN_LOCK.acquire(blocking=False):
        raise ValueError('已有诊断正在计算，请稍后刷新结果')
    try:
        buildings,supported,readings,weather=_records(tenant_id,site_id)
        if len(supported)!=3:
            raise ValueError('当前冻结版本仅适配Coleman、Alma、Melaine三栋；请补齐该组真实BDG2数据后运行')
        config=deepcopy(building_core.DEFAULT_CONFIG)
        reference=pd.read_csv(DATA/'bdg2_reference.csv',parse_dates=['timestamp']).set_index('timestamp')
        clock=pd.date_range('2016-01-01','2018-01-01',freq='h',inclusive='left')
        reference=reference.reindex(clock)
        observed=pd.DataFrame(readings,columns=['building','timestamp','y'])
        # Original BDG2 seed stores source wall-clock labels with a UTC marker.
        # Preserve those labels to match the frozen research calendar; do not shift twice.
        observed['timestamp']=pd.to_datetime(observed.timestamp,utc=True).dt.tz_localize(None)
        measured=observed.pivot(index='timestamp',columns='building',values='y')
        wx=pd.DataFrame(weather,columns=['timestamp','temperature'])
        wx['timestamp']=pd.to_datetime(wx.timestamp,utc=True).dt.tz_localize(None)
        temperature=reference.airTemperature.copy()
        observed_temperature=wx.set_index('timestamp').temperature.reindex(clock[clock.year==2017])
        weather_matches=bool(np.allclose(observed_temperature,reference.loc[clock.year==2017,'airTemperature'],equal_nan=True,rtol=1e-10,atol=1e-8))
        temperature.loc['2017-01-01':]=observed_temperature.to_numpy()
        history_temperature=temperature.rolling(24,min_periods=18).mean()
        bases={}; sequences={}; historic={}; initial={}; streams={}; gates={}
        source_matches={}
        for b in supported:
            code=b['code']; target=reference[code].copy()
            current=measured[code].reindex(clock[clock.year==2017])
            expected=reference.loc[clock.year==2017,code]
            source_matches[code]=weather_matches and bool(np.allclose(current,expected,equal_nan=True,rtol=1e-10,atol=1e-8))
            target.loc[clock.year==2017]=current.to_numpy()
            frame=pd.DataFrame({'y':target,'temperature':temperature,'temperature_24h':history_temperature},index=clock)
            frame['hour']=clock.hour; frame['dow']=clock.dayofweek; frame['weekend']=(clock.dayofweek>=5).astype(int)
            phase=(clock.month-1)/12+(clock.day-1)/372
            frame['year_sin']=np.sin(2*np.pi*phase);frame['year_cos']=np.cos(2*np.pi*phase)
            frame['hour_week']=clock.dayofweek*24+clock.hour;frame['long_zero']=False
            for split in ['train','validation','test']:
                mask=building_core.split_mask(frame,config[split])
                frame.loc[mask,'long_zero']=building_core.zero_run_mask(target.loc[mask],24)
            frame['good']=np.isfinite(target)&target.ge(0)&~frame.long_zero&frame[building_core.WEATHER].notna().all(axis=1)
            profile=config['profiles'][code]
            bases[code]=building_core.fit_base(frame,profile,config)
            seq=building_core.reference_sequence(frame,bases[code],profile['reference_method'],config)
            sequences[code]=seq
            blocks=building_core.blocks_from_hours(seq); start=pd.Timestamp(config['test'][0])
            historic[code]=building_core.historic_blocks(frame,config)
            initial[code]=[r for r in blocks if r['quality_complete'] and start-pd.Timedelta(days=29)<=pd.Timestamp(r['start'])<start]
            streams[code]=[r for r in blocks if pd.Timestamp(r['start'])>=start]
            # The published gate belongs to this frozen source cohort, not arbitrary uploads.
            gates[code]=source_matches[code] and profile['validation_reference_gate_passed']
        engine=DiagnosticReplay(config,historic,initial,gates)
        arrivals=sorted([(r['end'],b,r) for b,rows in streams.items() for r in rows],key=lambda r:r[:2])
        for end,group in itertools.groupby(arrivals,key=lambda r:r[0]):
            for _,b,block in group: engine.ingest(b,block)
            engine.publish_closed_weeks(end)
        hourly={}
        for b,seq in sequences.items():
            s=seq.loc['2017-07-01':,['y','q20','q50','q80']].replace({np.nan:None})
            hourly[b]=[{'time':t.isoformat(),**row.to_dict()} for t,row in s.iterrows()]
        payload=_clean({'buildings':supported,'source_matches_frozen_cohort':source_matches,
                       'cases':engine.records,'priorities':[r for r in engine.priorities if r['policy']=='guarded_28d'],
                       'hourly':hourly,'policy':'guarded_28d','version':config['version'],
                       'claim':'核查优先级，不是已经实现的节能量','period':['2017-07-01','2018-01-01'],
                       'source_calendar':'电量显式UTC来源标签；天气依据原导入批次恢复BDG2标签，使用当前数据库数值',
                       'models':{b:config['profiles'][b]['reference_method'] for b in config['buildings']}})
        signature=_signature(readings,weather)
        with get_conn() as conn,conn.cursor() as cur:
            cur.execute("""INSERT INTO optimization.diagnosis_run(tenant_id,site_id,algorithm_version,source_signature,payload)
                           VALUES(%s,%s,%s,%s,%s) RETURNING id""",(tenant_id,site_id,config['version'],signature,Json(payload)))
            run_id=str(cur.fetchone()[0]);conn.commit()
        return {'run_id':run_id,'cases':len(engine.records),'priorities':len(payload['priorities'])}
    finally:
        RUN_LOCK.release()


def diagnosis(tenant_id,site_id,building_id=None,start=None,end=None):
    buildings,supported,readings,weather=_records(tenant_id,site_id)
    if building_id and not any(b['id']==building_id for b in buildings):
        raise NotFoundError('建筑不存在或不属于当前园区')
    with get_conn() as conn,conn.cursor() as cur:
        cur.execute('SELECT id,created_at,source_signature,payload FROM optimization.diagnosis_run WHERE tenant_id=%s AND site_id=%s ORDER BY created_at DESC LIMIT 1',(tenant_id,site_id))
        row=cur.fetchone()
    base={'supported_buildings':supported,'unsupported_count':len(buildings)-len(supported),'claim':'偏高证据用于人工核查，不等同节能量'}
    if not row:return {**base,'status':'not_run','message':'运行已验证的三栋楼诊断，生成六小时证据与每周核查排序'}
    if row[2]!=_signature(readings,weather):
        return {**base,'status':'stale','message':'源数据已变化，请重新运行；旧结果未当作当前结论'}
    payload=row[3]; chosen=next((b for b in supported if b['id']==building_id),supported[0] if not building_id and supported else None)
    if chosen is None:return {**base,'status':'unsupported','message':'该建筑尚未完成模型适配，请选择已验证建筑'}
    code=chosen['code']; upper=min(pd.Timestamp(end or '2018-01-01'),pd.Timestamp('2018-01-01')).isoformat()
    lower=max(pd.Timestamp(start or '2017-07-01'),pd.Timestamp('2017-07-01')).isoformat()
    cases=[c for c in payload['cases'] if c['building']==code and c['start']>=lower and c['decision_available_at']<=upper]
    priorities=[c for c in payload['priorities'] if c['building']==code and c['start']>=lower and c['weekly_available_at']<=upper]
    history=[h for h in payload['hourly'][code] if lower<=h['time']<upper][-168:]
    return {**base,'status':'ready','run_id':str(row[0]),'created_at':row[1].isoformat(),'building':chosen,
            'cases':cases[-80:],'priorities':priorities,'hourly':history,'case_count':len(cases),
            'candidate_count':sum(c['status']=='review_candidate' for c in cases),
            'model':payload['models'][code],'source_match':payload['source_matches_frozen_cohort'][code],
            'version':payload['version'],'period':payload['period']}


def evidence_index():
    data=_load('simulation_index.json')
    real_path=DATA/'real_validation.json'
    data['real_validation']=_load('real_validation.json') if real_path.exists() else {'status':'pending','message':'真实公开数据正在验证，尚无现场执行节能结论'}
    return data


def evidence_case(case_id):
    if case_id not in {c['id'] for c in _load('simulation_index.json')['cases']}:
        raise NotFoundError('对照案例不存在')
    return _load(f'cases/{case_id}.json')


def preview(tenant_id,user_id,values):
    room=values['room_c'];outdoor=values['outdoor_c'];lower=values['lower_c'];upper=values['upper_c']
    if lower+.5>=upper-.5:raise ValueError('温度允许区间须宽于1℃，以容纳当前模型内部余量')
    control=EnergyOptimizer();control.initialize(room)
    forecast=np.tile([outdoor,0.,values['internal_kw']/2,values['internal_kw']/2,0.,values['solar_kw_m2']/2,values['solar_kw_m2']],(60,1))
    result=control.decide(room,forecast,np.full(61,lower),np.full(61,upper))
    result=_clean(result)
    result['interpretation']='交互试算：空气、内部物体、围护结构初始温度暂设相同；恒定外部条件。未执行设备，不产生已验证节能率。'
    result['scope']='bestest_air_case_only'
    with get_conn() as conn,conn.cursor() as cur:
        cur.execute('INSERT INTO optimization.hvac_trial(tenant_id,user_id,inputs,result) VALUES(%s,%s,%s,%s) RETURNING id',
                    (tenant_id,user_id,Json(values),Json(result)))
        result['trial_id']=str(cur.fetchone()[0]);conn.commit()
    return result


def plan(tenant_id, user_id, values):
    result = cooling_plan(values)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute('INSERT INTO optimization.hvac_trial(tenant_id,user_id,inputs,result) VALUES(%s,%s,%s,%s) RETURNING id',
                    (tenant_id, user_id, Json({**values, 'kind': 'cooling_plan_60m'}), Json(result)))
        result['trial_id'] = str(cur.fetchone()[0])
        conn.commit()
    return result
