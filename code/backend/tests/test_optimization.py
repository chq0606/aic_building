"""Scope, evidence integrity and invalid controls at the real API boundary."""
def test_optimization_requires_login(client):
    assert client.get('/api/v1/optimization/hvac/evidence').status_code==401
    assert client.post('/api/v1/optimization/hvac/plan',json={}).status_code==401


def test_hour_plan_uses_authenticated_tenant_and_saves_snapshot(client,auth_demo):
    from app.db.session import get_conn
    response=client.post('/api/v1/optimization/hvac/plan',json={
        'room_c':25.5,'outdoor_c':32,'lower_c':23,'upper_c':26})
    assert response.status_code==200,response.text
    data=response.json()['data']
    assert data['scope']=='frozen_model_estimate_only'
    assert len(data['policies']['recommended']['series'])==61
    with get_conn() as conn,conn.cursor() as cur:
        cur.execute('SELECT tenant_id,user_id,inputs,result FROM optimization.hvac_trial WHERE id=%s',(data['trial_id'],))
        row=cur.fetchone()
    assert str(row[0])==auth_demo.tenant_id and str(row[1])==auth_demo.user_id
    assert row[2]['kind']=='cooling_plan_60m'
    assert row[3]['inputs']==data['inputs']


def test_hour_plan_rejects_invalid_inputs(client,auth_demo):
    assert client.post('/api/v1/optimization/hvac/plan',json={'lower_c':26,'upper_c':24}).status_code==422
    assert client.post('/api/v1/optimization/hvac/plan',content='{"room_c":NaN}',headers={'Content-Type':'application/json'}).status_code==422

def test_published_evidence_preserves_counterexamples(client,auth_demo):
    response=client.get('/api/v1/optimization/hvac/evidence')
    assert response.status_code==200
    evidence=response.json()['data']
    assert len(evidence['cases'])==18
    case=client.get('/api/v1/optimization/hvac/cases/acceptance_short01_212').json()['data']
    assert case['policies']['final']['metrics']['strict_comfort_gate_passed']
    assert case['policies']['v6']['metrics']['strict_comfort_gate_passed']
    assert case['policies']['v6']['metrics']['hvac_terminal_energy_kwh']<case['policies']['final']['metrics']['hvac_terminal_energy_kwh']

def test_no_arbitrary_evidence_file_reads(client,auth_demo):
    assert client.get('/api/v1/optimization/hvac/cases/not-a-published-case').status_code==404

def test_diagnosis_rejects_unowned_site(client,auth_demo):
    assert client.get('/api/v1/optimization/diagnosis',params={'site_id':'00000000-0000-0000-0000-000000000000'}).status_code==404

def test_invalid_temperature_interval_and_nan(client,auth_demo):
    response=client.post('/api/v1/optimization/hvac/preview',json={'lower_c':24,'upper_c':21})
    assert response.status_code==422
    assert client.post('/api/v1/optimization/hvac/preview',content='{"room_c":NaN}',headers={'Content-Type':'application/json'}).status_code==422

def test_preview_uses_core_without_claiming_savings(client,auth_demo):
    response=client.post('/api/v1/optimization/hvac/preview',json={'room_c':23.5,'outdoor_c':28})
    assert response.status_code==200,response.text
    output=response.json()['data']
    assert output['command_valid'] and 0<=output['fan_fraction']<=1
    assert output['scope']=='bestest_air_case_only'
    assert 'savings_percent' not in output

def test_real_transfer_failure_is_not_hidden(client,auth_demo):
    evidence=client.get('/api/v1/optimization/hvac/evidence').json()['data']['real_validation']
    assert evidence['status']=='transfer_not_passed'
    assert any('未通过' in test['interpretation'] for test in evidence['tests'])


def test_source_calendar_keeps_year_end_in_non_utc_database(auth_demo,demo_site_id):
    import numpy as np
    import pandas as pd
    from app.services.optimization_service import _records,DATA
    _,supported,readings,weather=_records(auth_demo.tenant_id,demo_site_id)
    assert len(supported)==3
    reference=pd.read_csv(DATA/'bdg2_reference.csv',parse_dates=['timestamp']).set_index('timestamp').loc['2017']
    observed=pd.DataFrame(readings,columns=['building','time','value'])
    observed['time']=pd.to_datetime(observed.time,utc=True).dt.tz_localize(None)
    for b in supported:
        current=observed[observed.building==b['code']].set_index('time').value.reindex(reference.index)
        np.testing.assert_allclose(current,reference[b['code']],equal_nan=True)
    current_weather=pd.Series({pd.Timestamp(t):v for t,v in weather}).reindex(reference.index)
    np.testing.assert_allclose(current_weather,reference.airTemperature,equal_nan=True)
