"""
CSV 模板生成 (Step 16)。

三个模板对齐后端 ingest.upload_session.target_type:
  buildings -> target_type=BUILDING  建筑基础信息
  readings  -> target_type=POINT     能耗读数长表
  weather   -> target_type=WEATHER   气象读数

模板格式: 前 N 行用 # 开头的注释说明字段含义, 接着是 header + 1 行示例数据,
最后留几行空数据让用户填。前端下载后用 Excel 打开能看到注释行(Excel 不会
因为 # 跳过), pandas read_csv 时建议加 comment='#' 跳过注释行 (mapping-suggest
默认就这么读)。

字段命名跟 BDG2 demo seed 对齐 (参 code/seed_bdg2.py), 客户换了数据集后
可以在 MappingWizard 里改列映射, 不必死抠模板字段名。
"""
from __future__ import annotations

from fastapi import HTTPException, status

from app.core.response import error


# 允许的模板类型。不在白名单里的返 400。
TEMPLATE_TYPES: dict[str, str] = {
    "buildings": "建筑基础信息模板",
    "floors": "楼层信息模板",
    "readings": "能耗读数模板 (长表)",
    "weather": "气象读数模板",
}


# buildings.csv 模板。字段跟 core.building 表对齐 (除了 site_code 是冗余的
# 园区编码, 后端 commit 时按 site_code 找 site_id)。
BUILDINGS_CSV = """# 建筑基础信息模板
# 用途: 录入园区内每栋楼的基础信息。每行一栋楼, building_id 必填且租户内唯一。
# 字段说明:
#   building_id    建筑唯一编码 (必填, 字母数字下划线, 租户内唯一)
#   building_name  建筑名称 (必填, 给前端展示用)
#   site_code      所属园区编码 (必填, 必须是当前租户已存在的园区)
#   primary_use    主要用途 (必填, 可选值: education/office/public/assembly/science/residential)
#   sqm            建筑面积, 平方米 (必填, > 0)
#   floors_count   楼层数 (必填, 整数 > 0)
#   year_built     建造年份 (可选, 4 位整数)
#   latitude       纬度 (可选, WGS84, 保留 6 位小数)
#   longitude      经度 (可选, WGS84, 保留 6 位小数)
building_id,building_name,site_code,primary_use,sqm,floors_count,year_built,latitude,longitude
B1,示例办公楼,Bobcat,office,5400.0,4,2018,30.123456,114.234567
B2,示例教学楼,Bobcat,education,10150.0,6,2015,,
"""


# readings.csv 模板。长表格式 (一行一个读数), 跟后端 canonical long format 对齐。
# 宽表 (每列一个测点) 不在本模板支持范围, 用户传宽表时走 MappingWizard 的
# wide_melt 配置。
#
# floor_number 列可选 (Step 11a-6):
#   不填 / 留空 = 楼栋级 METER (整栋楼总表)
#   填数字       = 楼层级 SENSOR (该层分表), 必须先上传 floors.csv 创建楼层
# 楼层级 point_code 自动生成为 {building_id}__F{floor_number}__{energy_type}
READINGS_CSV = """# 能耗读数模板 (长表)
# 用途: 录入每栋楼每个测点的能耗读数。每行一个时间点的读数。
# 字段说明:
#   timestamp    时间戳 (必填, 格式 yyyy-MM-dd HH:mm:ss 或 ISO8601)
#   building_id  建筑编码 (必填, 必须在 buildings.csv 里已存在或同批上传)
#   energy_type  能源类型 (必填, 可选: electricity/gas/hotwater/chilledwater/water/solar)
#   value        读数 (必填, 数值)
#   unit         单位 (必填, 能耗类 kWh, 水量 m3 或 L, 体积流量 L/min 等)
#   floor_number 楼层编号 (可选, 不填=楼栋总表, 填数字=楼层分表, 需先传 floors.csv)
timestamp,building_id,energy_type,value,unit,floor_number
2017-01-01 00:00:00,B1,electricity,12.50,kWh,
2017-01-01 00:15:00,B1,electricity,12.80,kWh,
2017-01-01 00:00:00,B1,electricity,8.20,kWh,1
2017-01-01 00:00:00,B1,electricity,4.30,kWh,2
2017-01-01 00:00:00,B1,hotwater,3.20,m3,
"""


# weather.csv 模板。气象读数长表, 跟 readings.csv 类似但绑 site 不是 building。
WEATHER_CSV = """# 气象读数模板
# 用途: 录入园区的气象读数, 用于能耗-气温关联分析。每行一个时间点的气象数据。
# 字段说明:
#   timestamp          时间戳 (必填, 格式 yyyy-MM-dd HH:mm:ss 或 ISO8601)
#   site_code          园区编码 (必填, 必须是当前租户已存在的园区)
#   air_temp_c         气温, 摄氏度 (可选)
#   cloud_cover_pct     云量, 百分比 0-100 (可选)
#   dew_temp_c         露点温度, 摄氏度 (可选)
#   precip_mm          降水量, 毫米 (可选)
#   sea_level_pressure_hpa  海平面气压, hPa (可选)
#   wind_speed_mps     风速, m/s (可选)
#   wind_direction_deg 风向, 度 0-360 (可选)
timestamp,site_code,air_temp_c,cloud_cover_pct,dew_temp_c,precip_mm,sea_level_pressure_hpa,wind_speed_mps,wind_direction_deg
2017-01-01 00:00:00,Bobcat,5.2,75,2.1,0.0,1018.5,3.2,180
2017-01-01 01:00:00,Bobcat,4.8,78,1.8,0.0,1018.7,3.0,175
"""


# floors.csv 模板。楼层实体信息, target_type=FLOOR。
# 跟 core.floor 表对齐 (除了 building_id 是 building_code, 后端 commit 时按 code 找 id)。
# is_rooftop=true 的楼层会落 solar (如果楼栋有 solar 能源); floor_type 在
# LAB/MECHANICAL/SPORTS 的楼层会落 gas (如果楼栋有 gas 能源) - 这两个匹配
# 在 auto-split 算法里做, 用户手动上传 floor 后跑 readings 上传时按 point_code
# 关联, 不需要用户操心能源落位。
FLOORS_CSV = """# 楼层信息模板
# 用途: 录入每栋楼每层的基础信息。每行一层, building_id + floor_number 组合必填且楼栋内唯一。
# 字段说明:
#   building_id    所属建筑编码 (必填, 必须在 buildings.csv 里已存在或同批上传)
#   floor_number   楼层编号 (必填, 整数, 1=底层, N=顶层, 楼栋内唯一)
#   floor_name     楼层名称 (必填, 给前端展示用, 例如 "3F 教学层")
#   floor_type     楼层用途 (必填, 可选: LOBBY/CLASSROOM/OFFICE/LAB/MECHANICAL/LIBRARY/SPORTS/STUDENT_CENTER/OTHER)
#   area_sqm       楼层面积, 平方米 (必填, > 0)
#   is_rooftop     是否顶层 (必填, true/false, 顶层=true 才能落 solar)
building_id,floor_number,floor_name,floor_type,area_sqm,is_rooftop
B1,1,1F 大堂,LOBBY,1350.0,false
B1,2,2F 办公,OFFICE,1350.0,false
B1,3,3F 机房,MECHANICAL,1350.0,true
B2,1,1F 教室,CLASSROOM,1690.0,false
B2,2,2F 实验室,LAB,1690.0,true
"""


def get_template_csv(template_type: str) -> tuple[str, str]:
    """按类型返 (filename, csv_content)。type 不在白名单抛 HTTPException 400。

    返 tuple 给路由层, 路由层用 StreamingResponse / Response 返 text/csv。
    """
    if template_type not in TEMPLATE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error(
                message=f"不支持的模板类型: {template_type}, 仅支持 {list(TEMPLATE_TYPES.keys())}",
                code=400,
            ),
        )

    templates: dict[str, str] = {
        "buildings": BUILDINGS_CSV,
        "floors": FLOORS_CSV,
        "readings": READINGS_CSV,
        "weather": WEATHER_CSV,
    }
    return f"{template_type}.csv", templates[template_type]
