"""Explicit real-data acceptance driver. Never part of unittest discovery.

GIS Python: prepare / run / audit. GUI Python: configure.
All outputs go to a newly selected acceptance directory; sources are read-only.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'code')]


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def prepare(args):
    import geopandas as gpd
    from shapely.geometry import box
    from app.input_packages import discover, materialize
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    package = discover(args.source)
    variant = next(v for v in package['variants'] if v['label'] == args.variant)
    rows = [r for r in variant['drafts'] if not args.periods or r['name'] in args.periods]
    common = materialize(args.output, rows)
    area = gpd.read_file(common['boundary'])
    if args.size:
        point = area.geometry.union_all().representative_point()
        roi = box(point.x-args.size/2, point.y-args.size/2, point.x+args.size/2, point.y+args.size/2)
        area.geometry = area.geometry.intersection(roi)
    boundary = args.output / 'operation_area.shp'
    area.to_file(boundary, engine='pyogrio')
    raw = dict(rows=rows, boundary=str(boundary.resolve()), variant=variant['label'],
               area_m2=float(area.area.sum()), prepare_seconds=time.monotonic()-started,
               notices=variant['notices'], source=str(args.source.resolve()))
    write(args.output / 'input.json', raw)
    subprocess.run([str(ROOT/'gui_env/python.exe'), __file__, 'configure', str(args.output)], check=True)
    print(json.dumps({k:v for k,v in raw.items() if k!='rows'},ensure_ascii=False), flush=True)


def configure(args):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from plugin.controller import Controller
    from plugin.ui.project_browser import create_project, scan_project, save_configuration
    app = QApplication.instance() or QApplication([])
    controller = Controller()
    spec = json.loads((args.output/'input.json').read_text(encoding='utf-8'))
    for entry in ('package', 'txt'):
        project = args.output/entry
        if not project.exists():
            project = create_project(args.output, entry, dict(areas=[],periods=[],truths=[],area_irmad_references={}))
        if (project/'_work/current').exists():
            raise ValueError('Do not reconfigure an already executed acceptance project')
        model = scan_project(project)
        model['areas'] = [['acceptance',spec['boundary']]]
        model['periods'] = []
        for row in spec['rows']:
            source = row['source']
            if entry == 'txt':
                path = project/'02_影像'/f"{row['name']}.txt"
                path.write_text(source+'\n',encoding='utf-8')
                source = str(path)
            model['periods'].append(['acceptance',row['name'],source])
        model['area_irmad_references'] = {'acceptance':spec['rows'][0]['name']}
        if entry == 'package':
            model['input_packages'] = [dict(area='acceptance',version=spec['variant'],periods=[r['name'] for r in spec['rows']],generated_boundary=False)]
        save_configuration(model,{})
        reopened = scan_project(project)
        task = dict(project_root=str(project),areas=reopened['areas'],periods=reopened['periods'],
                    truths=[],output=str(project/'成果输出'),evaluate=False,device='auto',
                    area_irmad_references=model['area_irmad_references'])
        write(project/'task.json',task)
        started = time.monotonic()
        report = controller.inspect_data(task)
        write(project/'data_check.json',dict(seconds=time.monotonic()-started,report=report))
    controller.shutdown()


def tree_rss(root_pid):
    """Windows process-tree working set; no extra package in production environments."""
    import ctypes as c
    from ctypes import wintypes as w
    class Entry(c.Structure):
        _fields_=[('size',w.DWORD),('usage',w.DWORD),('pid',w.DWORD),('heap',c.c_size_t),
                  ('module',w.DWORD),('threads',w.DWORD),('parent',w.DWORD),('priority',w.LONG),
                  ('flags',w.DWORD),('exe',w.WCHAR*260)]
    class Memory(c.Structure):
        _fields_=[('size',w.DWORD),('faults',w.DWORD)]+[(n,c.c_size_t) for n in
            ('peak','working','paged_peak','paged','nonpaged_peak','nonpaged','pagefile','pagefile_peak')]
    kernel=c.windll.kernel32
    kernel.CreateToolhelp32Snapshot.restype=w.HANDLE
    kernel.OpenProcess.restype=w.HANDLE
    kernel.CloseHandle.argtypes=[w.HANDLE]
    kernel.Process32FirstW.argtypes=[w.HANDLE,c.POINTER(Entry)]
    kernel.Process32NextW.argtypes=[w.HANDLE,c.POINTER(Entry)]
    read=c.windll.psapi.GetProcessMemoryInfo
    read.argtypes=[w.HANDLE,c.POINTER(Memory),w.DWORD]
    snapshot=kernel.CreateToolhelp32Snapshot(2,0)
    parents={}
    entry=Entry();entry.size=c.sizeof(entry)
    try:
        ok=kernel.Process32FirstW(snapshot,c.byref(entry))
        while ok:
            parents[entry.pid]=entry.parent
            ok=kernel.Process32NextW(snapshot,c.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    ids={root_pid}
    while True:
        expanded=ids|{pid for pid,parent in parents.items() if parent in ids}
        if expanded==ids:break
        ids=expanded
    total=0
    for pid in ids:
        handle=kernel.OpenProcess(0x410,False,pid)
        if not handle:continue
        try:
            memory=Memory();memory.size=c.sizeof(memory)
            if read(handle,c.byref(memory),memory.size):total+=memory.working
        finally:
            kernel.CloseHandle(handle)
    return total


def run(args):
    import shutil
    from app.project_relocation import _io_path
    previous = [args.output/name for name in ('resume.log','resume_result.json','driver_resume.log','resources_resume.json')
                if (args.output/name).is_file()]
    if args.resume and previous:
        history=args.output/'acceptance_history'/str(time.time_ns())
        history.mkdir(parents=True)
        for path in previous:shutil.copy2(path,history/path.name)
    log = args.output / ('driver_resume.log' if args.resume else 'driver.log')
    command = [str(ROOT/'gui_env/python.exe'),str(ROOT/'tests/run_real_pipeline.py'),str(args.output/'task.json')]
    if args.resume:
        command.append('--resume')
    started = time.monotonic()
    peak, samples = 0, 0
    with log.open('w',encoding='utf-8') as stream:
        process = subprocess.Popen(command,stdout=stream,stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        while process.poll() is None:
            peak = max(peak,tree_rss(process.pid))
            samples += 1
            time.sleep(.5)
    result = dict(returncode=process.returncode,seconds=time.monotonic()-started,
                  peak_tree_rss_bytes=peak,samples=samples,
                  disk_bytes=sum(p.stat().st_size for p in _io_path(args.output).rglob('*') if p.is_file()))
    write(args.output/('resources_resume.json' if args.resume else 'resources.json'),result)
    print(json.dumps(result),flush=True)
    return process.returncode


def audit(args):
    import geopandas as gpd
    import rasterio
    results = {}
    for entry in ('package','txt'):
        project = args.output/entry
        completed = [p for p in (project/'run_result.json',project/'resume_result.json') if p.is_file()]
        if not any(json.loads(p.read_text(encoding='utf-8')).get('status')=='completed' for p in completed):
            raise AssertionError(f'No completed real pipeline: {project}')
        vectors = {}
        for path in (project/'成果输出').rglob('*.shp'):
            frame = gpd.read_file(path)
            if not all(path.with_suffix(ext).exists() for ext in ('.shp','.shx','.dbf','.prj')):
                raise AssertionError(f'Missing SHP components: {path}')
            if frame.crs is None or not frame.geometry.is_valid.all() or frame.geometry.isna().any() or frame.geometry.is_empty.any():
                raise AssertionError(f'Invalid formal vector geometry or CRS: {path}')
            # Exclude no attributes: compare canonical complete records, including IDs.
            records = sorted(json.dumps({**{k:str(v) for k,v in row.items() if k!='geometry'},
                                         'geometry':row.geometry.normalize().wkb_hex if row.geometry else ''},sort_keys=True)
                             for _,row in frame.iterrows())
            vectors[str(path.relative_to(project/'成果输出'))] = dict(count=len(frame),crs=str(frame.crs),
                fields=list(frame.columns),valid=bool(frame.geometry.is_valid.all()),
                nulls=int(frame.geometry.isna().sum()),empty=int(frame.geometry.is_empty.sum()),
                digest=hashlib.sha256('\n'.join(records).encode()).hexdigest())
        rasters = {}
        for path in (project/'_work/cache/normalized').rglob('*.tif'):
            with rasterio.open(path) as ds:
                digest = hashlib.sha256()
                for _,window in ds.block_windows(1):
                    digest.update(ds.read(window=window).tobytes())
                    digest.update(ds.dataset_mask(window=window).tobytes())
                value=dict(crs=str(ds.crs),transform=list(ds.transform),shape=[ds.count,ds.height,ds.width],digest=digest.hexdigest())
                # cache generation and root paths are intentionally absent
                rasters[str(path.relative_to(project/'_work/cache/normalized')).split(os.sep)[0]+'/'+path.name]=value
        results[entry]=dict(vectors=vectors,normalized=rasters)
        if not vectors or not rasters:
            raise AssertionError(f'Missing formal vectors or normalized imagery: {project}')
    results['comparison']=dict(vectors_equal=results['package']['vectors']==results['txt']['vectors'],
                               normalized_equal=results['package']['normalized']==results['txt']['normalized'],
                               vector_count=len(results['package']['vectors']),normalized_count=len(results['package']['normalized']))
    write(args.output/'comparison.json',results)
    print(json.dumps(results['comparison']))
    if not all(results['comparison'][key] for key in ('vectors_equal','normalized_equal')):
        raise AssertionError('Package and TXT outputs differ; inspect comparison.json')


def coverage_benchmark(args):
    import types
    import geopandas as gpd
    from app import input_packages
    args.output.mkdir(parents=True,exist_ok=False)
    variant=next(v for v in input_packages.discover(args.source)['variants'] if v['label']==args.variant)
    code=subprocess.check_output(['git','show','d307a9f:road_change_plugin/code/app/input_packages.py'],cwd=ROOT,text=True,encoding='utf-8')
    baseline=types.ModuleType('input_packages_baseline')
    exec(compile(code,'input_packages_baseline','exec'),baseline.__dict__)
    results={};geometries={}
    for label,module in [('baseline',baseline),('balanced',input_packages)]:
        start=time.monotonic()
        result=module.materialize(args.output/label,variant['drafts'])
        geometries[label]=gpd.read_file(result['boundary']).geometry.union_all()
        results[label]=dict(seconds=time.monotonic()-start,boundary=result['boundary'])
    difference=geometries['baseline'].symmetric_difference(geometries['balanced']).area
    results['symmetric_difference_m2']=difference
    results['relative_difference']=difference/geometries['baseline'].area
    results['periods']=[(r['name'],r['count']) for r in variant['drafts']]
    write(args.output/'benchmark.json',results)
    print(json.dumps(results,ensure_ascii=False),flush=True)
    if results['relative_difference']>1e-10:
        raise AssertionError('Balanced union changed spatial coverage')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['prepare','configure','run','audit','coverage_benchmark'])
    parser.add_argument('output',type=Path)
    parser.add_argument('--source',type=Path)
    parser.add_argument('--variant',default='切图 · 15 级')
    parser.add_argument('--size',type=float,default=1024)
    parser.add_argument('--periods',nargs='*')
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    args.output=args.output.resolve()
    return globals()[args.action](args)


if __name__=='__main__':
    raise SystemExit(main())
