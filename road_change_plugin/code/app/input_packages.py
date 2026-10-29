"""Data-package discovery and spatial preparation, run in the GIS interpreter.

No model imports. The output feeds the existing area / period / TXT contract.
"""
from pathlib import Path
import hashlib
import json
import re
import sys
from datetime import date


def period_name(text):
    dates = set()
    invalid = False
    matches = list(re.finditer(r'(?<!\d)(\d{4})[-_]?(\d{2})[-_]?(\d{2})(?!\d)', text))
    for match in matches:
        try:
            dates.add(date(*map(int, match.groups())).strftime('%Y%m%d'))
        except ValueError:
            invalid = True
    if matches:
        return next(iter(dates)) if len(dates) == 1 and not invalid else ''
    # Years must be a leading calendar label, never a satellite product version.
    match = re.match(r'^(19\d{2}|20\d{2})(?:年|[-_]|$)', text)
    return match[1] if match else ''


def analysis_crs_for(source_crs, area):
    """Keep metre-based projected inputs unchanged; geographic inputs use local UTM."""
    from pyproj import CRS
    from rasterio.crs import CRS as RasterCRS
    crs = CRS.from_user_input(source_crs)
    if crs.is_projected and crs.axis_info and all(abs(a.unit_conversion_factor - 1) < 1e-9 for a in crs.axis_info[:2]):
        return RasterCRS.from_user_input(source_crs)
    bounds = area.to_crs(4326).total_bounds
    lon, lat = (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2
    if not -80 <= lat <= 84 or bounds[2] - bounds[0] > 6:
        raise ValueError('区域超出本地投影自动选择范围，请提供适合该区域的米制投影影像')
    zone = min(60, max(1, int((lon + 180) // 6) + 1))
    return RasterCRS.from_epsg((32600 if lat >= 0 else 32700) + zone)


def _files(directory):
    return sorted((p for p in directory.iterdir() if not p.is_symlink()), key=lambda p: p.name)


def discover(directory):
    """Recognize only explicit package layouts, with bounded depth and no symlinks."""
    root = Path(directory).resolve()
    if not root.is_dir():
        raise ValueError('数据包目录不存在')
    # Unwrap archive-style repeated names; never merge sibling packages/regions.
    for _ in range(3):
        children = _files(root)
        dirs = [p for p in children if p.is_dir()]
        if len(dirs) == 1 and dirs[0].name == root.name:
            root = dirs[0]
        else:
            break
    variants = []
    originals = [p for p in _files(root) if p.is_file() and p.suffix.lower() in {'.tif', '.tiff'}]
    if originals:
        groups = {}
        for p in originals:
            name = period_name(p.stem)
            if not name:
                raise ValueError(f'无法确定期次：{p.name}；请使用原有“添加文件”入口手动命名')
            groups.setdefault(name, []).append(p)
        variants.append(_variant('整幅影像', groups))
    tile_root = root / '切图' if (root / '切图').is_dir() else root
    periods = [p for p in _files(tile_root) if p.is_dir() and period_name(p.name)]
    levels = {}
    for folder in periods:
        period = period_name(folder.name)
        for level in _files(folder):
            if not level.is_dir() or not level.name.isdigit():
                continue
            images = [p for p in _files(level) if p.is_file() and p.suffix.lower() in {'.tif', '.tiff'}]
            if images:
                group = levels.setdefault(level.name, {})
                if period in group:
                    raise ValueError(f'{period} 存在多个影像产品，请分别导入或先整理为明确的期次')
                group[period] = images
    expected = {period_name(p.name) for p in periods}
    for level, groups in sorted(levels.items(), key=lambda pair: int(pair[0])):
        variant = _variant(f'切图 · {level} 级', groups)
        missing = sorted(expected - groups.keys())
        if missing:
            variant['notices'].append('该层级没有期次：' + '、'.join(missing))
        variants.append(variant)
    if not variants:
        raise ValueError('未识别到年度整幅影像或“期次/层级/TIF”数据包。请选择单个数据包根目录；普通目录可使用“添加文件夹”。')
    metadata, notes = _metadata(root / '影像信息.xlsx')
    for variant in variants:
        variant['notices'].extend(notes)
        for draft in variant['drafts']:
            if draft['name'] in metadata:
                draft['origin'] += ' · ' + metadata[draft['name']]
    return dict(root=str(root), variants=variants)


def _metadata(path):
    """Read optional descriptive metadata. Never substitute it for raster georeferencing."""
    if not path.is_file():
        return {}, []
    import zipfile
    import xml.etree.ElementTree as ET
    ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    try:
        with zipfile.ZipFile(path) as archive:
            if sum(i.file_size for i in archive.infolist()) > 16 * 1024 * 1024:
                raise ValueError('说明表超过读取上限')
            strings = []
            if 'xl/sharedStrings.xml' in archive.namelist():
                strings = [''.join(t.itertext()) for t in ET.fromstring(archive.read('xl/sharedStrings.xml')).findall('s:si', ns)]
            table = []
            for row in ET.fromstring(archive.read('xl/worksheets/sheet1.xml')).findall('.//s:row', ns):
                values = {}
                for cell in row.findall('s:c', ns):
                    value = cell.find('s:v', ns)
                    text = value.text or '' if value is not None else ''.join(cell.itertext())
                    values[re.sub(r'\d', '', cell.get('r', ''))] = strings[int(text)] if cell.get('t') == 's' else text
                table.append(values)
        if not table or table[0].get('B') != '时间':
            raise ValueError('未识别到时间列')
        result, notices = {}, []
        for row in table[1:]:
            period = period_name(row.get('B', ''))
            if not period:
                continue
            named_period = period_name(row.get('A', ''))
            if named_period and named_period != period:
                notices.append(f'说明表日期与影像名称不一致：{period} / {named_period}')
            result[period] = ' / '.join(row.get(k, '') for k in ('F', 'D', 'C', 'E'))
        return result, notices + ['说明表中的分辨率仅作说明，坐标与像元大小以实际影像为准。']
    except (OSError, ValueError, KeyError, IndexError, ET.ParseError, zipfile.BadZipFile) as exc:
        return {}, [f'影像说明表未读取：{exc}；已按目录和影像识别']


def _variant(label, groups):
    import rasterio
    from rasterio.warp import transform_bounds
    from shapely.geometry import box
    from shapely.ops import unary_union
    drafts, notices, tile_ids, footprints = [], [], {}, []
    for period, files in sorted(groups.items()):
        boxes, identities = [], set()
        products = {re.sub(r'_\d+_\d+$', '', p.stem) for p in files
                    if re.search(r'_\d+_\d+$', p.stem)}
        if len(products) > 1:
            raise ValueError(f'{period} 包含多个切图产品：' + '、'.join(sorted(products)) + '；请分别导入')
        for path in files:
            file_period = period_name(path.stem)
            if not file_period and re.search(r'(?<!\d)\d{4}[-_]?\d{2}[-_]?\d{2}(?!\d)', path.stem):
                raise ValueError(f'影像日期无效或不唯一：{path.name}')
            if file_period and file_period != period:
                raise ValueError(f'目录期次 {period} 与文件日期 {file_period} 不一致：{path.name}')
            with rasterio.open(path) as ds:
                if ds.crs is None or ds.count < 3 or any(t != 'uint8' for t in ds.dtypes[:3]):
                    raise ValueError(f'影像须有坐标信息和前三波段 uint8 RGB：{path}')
                boxes.append(box(*transform_bounds(ds.crs, 4326, *ds.bounds, densify_pts=21)))
            match = re.search(r'_(\d+_\d+)$', path.stem)
            if match:
                if match[1] in identities:
                    raise ValueError(f'{period} 存在重复瓦片编号：{match[1]}')
                identities.add(match[1])
        tile_ids[period] = identities
        footprints.append(unary_union(boxes))
        drafts.append(dict(name=period, source='\n'.join(map(str, files)), count=len(files),
                           origin=f'{label} / {period}', reason='', error=''))
        # GeoTools/SensGlobe SHP is an index, never a road truth or area input.
        for parent in {p.parent for p in files}:
            for index in parent.glob('*.shp'):
                import pyogrio
                try:
                    info = pyogrio.read_info(index)
                    if 'location' in info['fields']:
                        table = pyogrio.read_dataframe(index, read_geometry=False, columns=['location'])
                        broken = sum(not (parent / str(value)).is_file() for value in table['location'])
                        if broken:
                            notices.append(f'{period} 索引中 {broken} 个路径无效；已按实际 TIF 建立清单')
                except (OSError, ValueError, pyogrio.errors.DataSourceError) as exc:
                    notices.append(f'{period} 索引不可读取：{index.name}（{exc}）；已按实际 TIF 建立清单')
    all_ids = set().union(*tile_ids.values())
    for period, ids in tile_ids.items():
        missing = sorted(all_ids - ids) if ids else []
        if missing:
            notices.append(f'{period} 相对其他期次缺少 {len(missing)} 张瓦片：' + '、'.join(missing[:8]))
    common = footprints[0]
    for footprint in footprints[1:]:
        common = common.intersection(footprint)
    if common.is_empty:
        raise ValueError(f'{label} 各期没有共同覆盖，请分别建立区域')
    notices.append('每期只导入当前版本；索引 SHP 不作为真值。缺少范围时可生成各期共同有效覆盖范围。')
    return dict(label=label, drafts=drafts, notices=notices)


def _source_files(source):
    paths = [Path(line.strip()).resolve() for line in source.splitlines() if line.strip()]
    if len(paths) == 1 and paths[0].suffix.lower() == '.txt':
        from input_catalog import read_path_list
        paths = [entry.path for entry in read_path_list(paths[0]).entries]
    return paths


class _CoverageUnion:
    """Balanced unions: do not repeatedly union each tile into an ever larger polygon."""
    def __init__(self):
        self.levels = []

    def add(self, geometry):
        level = 0
        while level < len(self.levels) and self.levels[level] is not None:
            geometry = self.levels[level].union(geometry)
            self.levels[level] = None
            level += 1
        if level == len(self.levels):
            self.levels.append(geometry)
        else:
            self.levels[level] = geometry

    def geometry(self):
        from shapely.ops import unary_union
        return unary_union([g for g in self.levels if g is not None])


def materialize(root, rows):
    """Persist an immutable common valid-data polygon, using bounded mask reads."""
    import geopandas as gpd
    import rasterio
    from rasterio.features import shapes
    from rasterio.windows import Window
    from shapely.geometry import shape
    from shapely.ops import unary_union, transform
    from pyproj import Transformer
    from engine.raster_memory import check_native_blocks
    import tempfile
    import os
    if not rows:
        raise ValueError('请至少选择一个期次')
    common, identities = None, []
    for row in rows:
        accumulator = _CoverageUnion()
        transforms = {}
        for path in _source_files(row['source']):
            stat = path.stat()
            identities.append([str(path), stat.st_size, stat.st_mtime_ns])
            with rasterio.open(path) as ds:
                check_native_blocks(ds)
                if ds.crs is None:
                    raise ValueError(f'影像缺少坐标信息：{path}')
                key = ds.crs.to_wkt()
                if key not in transforms:
                    transforms[key] = Transformer.from_crs(ds.crs, 4326, always_xy=True).transform
                project = transforms[key]
                for y in range(0, ds.height, 512):
                    for x in range(0, ds.width, 512):
                        window = Window(x, y, min(512, ds.width-x), min(512, ds.height-y))
                        valid = ds.dataset_mask(window=window) > 0
                        pieces = [shape(g) for g, _ in shapes(valid.astype('uint8'), mask=valid, transform=ds.window_transform(window))]
                        if pieces:
                            polygon = transform(project, unary_union(pieces))
                            accumulator.add(polygon)
        coverage = accumulator.geometry()
        if coverage.is_empty:
            raise ValueError(f"{row['name']} 没有有效影像像元")
        common = coverage if common is None else common.intersection(coverage)
    if common is None or common.is_empty or common.area == 0:
        raise ValueError('所选期次没有共同有效覆盖范围，请提供独立范围并检查影像')
    if common.geom_type == 'GeometryCollection':
        common = unary_union([g for g in common.geoms if g.geom_type in {'Polygon', 'MultiPolygon'}])
    if common.is_empty:
        raise ValueError('共同覆盖没有有效面范围')
    digest = hashlib.sha256(b'coverage_v2' + common.wkb + json.dumps(identities, sort_keys=True).encode()).hexdigest()[:24]
    directory = Path(root).resolve() / '01_验证区' / '_imported' / digest
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / 'common.shp'
    marker = directory / 'complete.json'
    intact = False
    if marker.is_file():
        try:
            expected = json.loads(marker.read_text(encoding='utf-8'))
            intact = isinstance(expected, dict) and all(target.with_suffix(ext).is_file() and
                         hashlib.sha256(target.with_suffix(ext).read_bytes()).hexdigest() == digest
                         for ext, digest in expected.items()) and all(ext in expected for ext in ('.shp','.shx','.dbf','.prj'))
        except (OSError, ValueError):
            pass
    if not intact:
        frame = gpd.GeoDataFrame({'name': ['common']}, geometry=[common], crs=4326)
        with tempfile.TemporaryDirectory(prefix='.coverage-', dir=directory.parent) as temporary:
            staged = Path(temporary) / 'common.shp'
            frame.to_crs(analysis_crs_for(4326, frame)).to_file(staged, engine='pyogrio', encoding='UTF-8')
            checksums = {}
            for ext in ('.shp','.shx','.dbf','.prj','.cpg'):
                file = staged.with_suffix(ext)
                checksums[ext] = hashlib.sha256(file.read_bytes()).hexdigest()
                os.replace(file, target.with_suffix(ext))
            pending = Path(temporary) / 'complete.json'
            pending.write_text(json.dumps(checksums), encoding='utf-8')
            os.replace(pending, marker)
    return dict(boundary=str(target), notice='已按各期共同有效覆盖生成范围；缺失瓦片和 NoData 区域不纳入该范围。')


def main():
    request = json.load(sys.stdin)
    if request['action'] == 'scan':
        result = discover(request['directory'])
    elif request['action'] == 'materialize':
        result = materialize(request['root'], request['rows'])
    else:
        raise ValueError('未知数据包操作')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
