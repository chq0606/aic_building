from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import JSONResponse
from psycopg2.errors import UndefinedTable
from uuid import UUID
from pydantic import BaseModel,Field,ConfigDict,field_validator
import math
from app.core.deps import CurrentUser,get_current_user
from app.core.response import success,error
from app.services import optimization_service as service
from app.services.query_service import NotFoundError

router=APIRouter(prefix='/optimization',tags=['optimization'])

def call(func,*args,**kwargs):
    try:return success(data=func(*args,**kwargs))
    except NotFoundError as ex:raise HTTPException(404,detail=str(ex))
    except ValueError as ex:raise HTTPException(422,detail=str(ex))
    except UndefinedTable:
        return JSONResponse(status_code=503,content=error(
            '节能功能尚未完成初始化，请完成初始化后重试。',code=503))

@router.get('/diagnosis')
def diagnosis(site_id:UUID,building_id:UUID|None=None,start:str|None=None,end:str|None=None,user:CurrentUser=Depends(get_current_user)):
    return call(service.diagnosis,user.tenant_id,str(site_id),str(building_id) if building_id else None,start,end)

@router.post('/diagnosis/run')
def run(site_id:UUID,user:CurrentUser=Depends(get_current_user)):
    return call(service.run_diagnosis,user.tenant_id,str(site_id))

@router.get('/hvac/evidence')
def evidence(user:CurrentUser=Depends(get_current_user)):
    return call(service.evidence_index)

@router.get('/hvac/cases/{case_id}')
def case(case_id:str,user:CurrentUser=Depends(get_current_user)):
    return call(service.evidence_case,case_id)

class PreviewInput(BaseModel):
    model_config=ConfigDict(allow_inf_nan=False,extra='forbid')
    room_c:float=Field(23.5,ge=15,le=30)
    outdoor_c:float=Field(28,ge=-20,le=45)
    lower_c:float=Field(21,ge=15,le=26)
    upper_c:float=Field(24,ge=18,le=30)
    internal_kw:float=Field(.3,ge=0,le=2)
    solar_kw_m2:float=Field(.3,ge=0,le=1.2)

    @field_validator('*',mode='before')
    @classmethod
    def finite_error_input(cls,value):
        # Preserve a serializable input in FastAPI's validation error for JSON NaN.
        return repr(value) if isinstance(value,float) and not math.isfinite(value) else value

@router.post('/hvac/preview')
def preview(body:PreviewInput,user:CurrentUser=Depends(get_current_user)):
    return call(service.preview,user.tenant_id,user.user_id,body.model_dump())


@router.post('/hvac/plan')
def plan(body:PreviewInput,user:CurrentUser=Depends(get_current_user)):
    return call(service.plan,user.tenant_id,user.user_id,body.model_dump())
