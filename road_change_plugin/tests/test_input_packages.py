"""GIS input acceptance, synthetic rasters only; no models or inference."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code'))
import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box
from app.input_packages import discover, materialize, analysis_crs_for, period_name


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def raster(self, name, x=112, blank=False, crs=4490):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = np.full((3, 16, 16), 100, dtype='uint8')
        if blank:
            data[:, :4, :4] = 0
        with rasterio.open(path, 'w', driver='GTiff', width=16, height=16, count=3,
                           crs=crs, dtype='uint8', nodata=0,
                           transform=from_origin(x, 22, .00002, .00002)) as ds:
            ds.write(data)
        return path

    def test_annual_original_and_levels_are_separate_and_missing_period_is_reported(self):
        for year in (2021, 2022):
            self.raster(f'包/包/{year}年-1.tif')
            self.raster(f'包/包/切图/{year}/15/{year}_1_1.tif')
        self.raster('包/包/切图/2022/16/2022_2_2.tif')
        variants = discover(self.root / '包')['variants']
        self.assertEqual([v['label'] for v in variants], ['整幅影像', '切图 · 15 级', '切图 · 16 级'])
        self.assertEqual([len(v['drafts']) for v in variants], [2, 2, 1])
        self.assertIn('2021', ' '.join(variants[2]['notices']))
        self.assertEqual(variants[0]['drafts'][0]['name'], '2021')

    def test_missing_interior_tile_detected_even_when_outer_bounds_match(self):
        for period in ('20260214', '20260604'):
            for tile in (0, 1, 2):
                if period == '20260604' and tile == 1:
                    continue
                self.raster(f'包/{period}-GF6-PMS/15/sensor_{period}_{tile}_0.tif', x=112+tile*.00032)
        result = discover(self.root / '包')['variants'][0]
        self.assertEqual([r['count'] for r in result['drafts']], [3, 2])
        self.assertIn('1_0', ' '.join(result['notices']))
        boundary = materialize(self.root / '项目', result['drafts'])['boundary']
        area = gpd.read_file(boundary).to_crs(4490).geometry.union_all()
        self.assertFalse(area.contains(box(112.0004,21.9998,112.0005,21.9999).centroid))

    def test_valid_coverage_excludes_nodata_and_accepts_existing_txt(self):
        a = self.raster('2021.tif')
        b = self.raster('2022.tif', blank=True)
        listing = self.root / '2021.txt'
        listing.write_text(a.name, encoding='utf-8')
        rows = [dict(name='2021', source=str(listing)), dict(name='2022', source=str(b))]
        result = materialize(self.root / '项目', rows)
        frame = gpd.read_file(result['boundary'])
        self.assertTrue(frame.crs.is_projected)
        area = frame.to_crs(4490).geometry.union_all()
        self.assertFalse(area.contains(box(112.00001,21.99995,112.00004,21.99998).centroid))
        self.assertAlmostEqual(area.area, .00032**2 - .00008**2, delta=1e-13)
        self.assertEqual(materialize(self.root / '项目', rows)['boundary'], result['boundary'])

    def test_disjoint_regions_rejected_and_projected_crs_preserved(self):
        self.raster('包/2021.tif', x=112)
        self.raster('包/2022.tif', x=114)
        with self.assertRaisesRegex(ValueError, '共同覆盖'):
            discover(self.root / '包')
        area = gpd.GeoDataFrame(geometry=[box(112,22,112.1,22.1)], crs=4490)
        self.assertEqual(analysis_crs_for(4490, area).to_epsg(), 32649)
        self.assertEqual(analysis_crs_for(4526, area).to_epsg(), 4526)
        self.assertEqual(period_name('GF6_V2014_20489_53183'), '')

    def test_geographic_normalization_produces_shared_metre_grid_and_reuses_cache(self):
        import user_pipeline
        rows = [dict(name=str(y), source=str(self.raster(f'{y}.tif'))) for y in (2021, 2022)]
        area = materialize(self.root / '项目', rows)['boundary']
        periods = {}
        for row in rows:
            path = self.root / (row['name'] + '.txt')
            path.write_text(row['source'], encoding='utf-8')
            periods[row['name']] = path
        output = self.root / 'normalized'
        results = user_pipeline.normalize_validation_sources(periods, area, output, tile_size=64)
        grids = []
        for directory in results.values():
            with rasterio.open(user_pipeline.listed_rasters(directory)[0]) as ds:
                self.assertTrue(ds.crs.is_projected)
                self.assertGreater(ds.res[0], 1)
                grids.append((ds.crs, ds.transform, ds.width, ds.height))
        self.assertEqual(grids[0], grids[1])
        self.assertEqual(user_pipeline.normalize_validation_sources(periods, area, output, tile_size=64), results)

    def test_existing_projected_grid_is_unchanged(self):
        import user_pipeline
        path = self.root / 'original.tif'
        transform = from_origin(38413000, 2703000, 1, 1)
        with rasterio.open(path, 'w', driver='GTiff', width=16, height=16, count=3,
                           crs=4526, dtype='uint8', transform=transform) as ds:
            ds.write(np.full((3,16,16), 100, dtype='uint8'))
        boundary = self.root / 'area.shp'
        gpd.GeoDataFrame(geometry=[box(38413000,2702984,38413016,2703000)], crs=4526).to_file(boundary)
        source = self.root / 'list.txt'
        source.write_text(str(path), encoding='utf-8')
        result = user_pipeline.normalize_validation_sources({'2021': source}, boundary, self.root / 'normalized', tile_size=64)
        with rasterio.open(user_pipeline.listed_rasters(result['2021'])[0]) as ds:
            self.assertEqual(ds.crs.to_epsg(), 4526)
            self.assertEqual(ds.transform, transform)
            self.assertEqual((ds.width, ds.height), (16,16))
            self.assertTrue((ds.read() == 100).all())

    def test_folder_file_date_conflict_is_not_silently_grouped(self):
        self.raster('包/20260214-GF6/15/image_20260604_1_1.tif')
        with self.assertRaisesRegex(ValueError, '不一致'):
            discover(self.root / '包')


if __name__ == '__main__':
    unittest.main()
