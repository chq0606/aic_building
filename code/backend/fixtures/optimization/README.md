# 版本化公开研究参照

本目录用于可复现研究证据，与租户上传、真实设备接口及虚拟楼层数据分开。

- `bdg2_reference.csv`：原BDG2电量/天气中三栋已验证建筑的2016—2017子集。运行时2017电量和天气必须读取租户数据库；该子集只提供2016训练参照、官方数据补入及冻结队列一致性校验。来源：https://github.com/buds-lab/building-data-genome-project-2 。原6栋示例保留。
- `algorithm_manifest.json`：三份算法核心与原研究文件的来源及SHA256。代码生产副本位于`app/algorithms`，运行不导入研究目录。
- `simulation_index.json`、`cases/`：官方BOPTEST物理仿真中保存的56次冻结后运行，按5分钟抽样绘图，实际评分每3秒检查。属于物理仿真，不是现场节能。
- `real_validation.json`：对真实FLEXLAB2020记录的冻结模型迁移核验。两室均未通过，不得改写为本项目真实执行节能率。真实数据DOI：10.17632/xztfbtsgys.3；Sartori等，2023，Data in Brief 48:109149，https://doi.org/10.1016/j.dib.2023.109149 ，CC BY 4.0。数据原件、哈希、协议修订与逐窗误差保存在`research/system_integration_v1`。
- `migration.sql`：仅新增诊断结果和试算表；不删除原表或原读数。

导出脚本为`research/system_integration_v1/prepare_assets.py`；真实核验脚本为`validate_real.py`。重新导出不要修改已冻结的原研究核心。
