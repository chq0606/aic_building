"""Missing-migration regression at the HTTP boundary, without an external database."""
import pytest
import json
from uuid import UUID
from psycopg2.errors import UndefinedTable

from app.api.optimization import diagnosis, preview, plan, PreviewInput
from app.core.deps import CurrentUser
from app.services import optimization_service


@pytest.mark.parametrize('service_name',['diagnosis','preview','plan'])
def test_missing_optimization_tables_returns_actionable_error(monkeypatch,service_name):
    def missing(*args,**kwargs):
        raise UndefinedTable('private SQL and schema details')
    monkeypatch.setattr(optimization_service,service_name,missing)
    user=CurrentUser('user','tenant','tester',False)
    response=(diagnosis(UUID('00000000-0000-0000-0000-000000000001'),user=user)
              if service_name=='diagnosis' else (plan if service_name=='plan' else preview)(PreviewInput(),user=user))
    assert response.status_code==503
    body=json.loads(response.body)
    assert body['code']==503
    assert '初始化' in body['message']
    assert b'private SQL' not in response.body
