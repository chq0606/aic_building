import csv
import os

# 路径配置：dataset 是原始数据，extracted_data 是输出目录
DATASET_DIR = os.path.join(os.path.dirname(__file__), '..', 'dataset')
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'extracted_data')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 选定的 6 栋 Bobcat 建筑，同一 site，统一天气
BUILDINGS = [
    'Bobcat_education_Alissa',
    'Bobcat_education_Dylan',
    'Bobcat_education_Seth',
    'Bobcat_assembly_Franklin',
    'Bobcat_science_Tammy',
    'Bobcat_public_Angie',
]

# BDG2 中 Bobcat site 实际存在的 6 种能源类型及其单位
# steam 和 irrigation 在 Bobcat 不存在，solar 只有 2 栋有，gas 只有 3 栋有
ENERGY_FILES = {
    'electricity':   ('raw/electricity.csv',   'kWh'),
    'hotwater':      ('raw/hotwater.csv',      'kWh_thermal'),
    'chilledwater':  ('raw/chilledwater.csv',  'kWh_thermal'),
    'water':         ('raw/water.csv',         'L'),       # 用水量，单位升
    'solar':         ('raw/solar.csv',         'kWh'),
    'gas':           ('raw/gas.csv',           'kWh'),
}


def extract_metadata():
    """从 metadata.csv 中筛出 6 栋目标建筑，写入 extracted_data"""
    src = os.path.join(DATASET_DIR, 'metadata.csv')
    dst = os.path.join(OUTPUT_DIR, 'metadata.csv')
    with open(src, newline='') as fin, open(dst, 'w', newline='') as fout:
        reader = csv.DictReader(fin)
        writer = csv.DictWriter(fout, fieldnames=reader.fieldnames)
        writer.writeheader()
        n = 0
        for row in reader:
            if row['building_id'] in BUILDINGS:
                writer.writerow(row)
                n += 1
    print(f'metadata: {n} buildings')


def extract_readings():
    """逐个能源文件读取 2017 年数据，宽表转长表，流式写入避免内存溢出"""
    dst = os.path.join(OUTPUT_DIR, 'readings_2017.csv')
    total = 0
    with open(dst, 'w', newline='') as fout:
        writer = csv.writer(fout)
        writer.writerow(['energy_type', 'building_id', 'timestamp', 'value', 'unit'])
        for etype, (rel_path, unit) in ENERGY_FILES.items():
            src = os.path.join(DATASET_DIR, rel_path)
            if not os.path.exists(src):
                print(f'  {etype}: file missing, skip')
                continue

            with open(src, newline='') as f:
                header = next(csv.reader(f))
                # 找到 6 栋建筑在该能源文件中的列位置
                col_map = {b: i for i, b in enumerate(header) if b in BUILDINGS}
                if not col_map:
                    print(f'  {etype}: 0 buildings, skip')
                    continue

                rows = 0
                buf = []
                for row in csv.reader(f):
                    ts = row[0]
                    if not ts.startswith('2017'):
                        continue
                    for bldg, ci in col_map.items():
                        val = row[ci].strip()
                        if val:
                            buf.append([etype, bldg, ts, val, unit])
                            rows += 1
                    # 每 5 万行刷一次磁盘，控制内存
                    if len(buf) >= 50000:
                        writer.writerows(buf)
                        buf.clear()
                if buf:
                    writer.writerows(buf)
                print(f'  {etype}: {rows:,} rows ({len(col_map)} buildings)')
                total += rows

    print(f'readings_2017.csv: {total:,} rows total')


def extract_weather():
    """从 weather.csv 中提取 Bobcat site 的 2017 年天气数据"""
    src = os.path.join(DATASET_DIR, 'weather.csv')
    dst = os.path.join(OUTPUT_DIR, 'weather_2017.csv')
    with open(src, newline='') as fin, open(dst, 'w', newline='') as fout:
        reader = csv.DictReader(fin)
        writer = csv.DictWriter(fout, fieldnames=reader.fieldnames)
        writer.writeheader()
        n = 0
        for row in reader:
            if row['site_id'] == 'Bobcat' and row['timestamp'].startswith('2017'):
                writer.writerow(row)
                n += 1
    print(f'weather_2017.csv: {n} rows')


if __name__ == '__main__':
    extract_metadata()
    extract_readings()
    extract_weather()
    print('done')
