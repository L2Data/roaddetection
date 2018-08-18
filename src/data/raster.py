import logging
from shapely.ops import transform
from shapely.geometry import mapping

import rasterio as rio

from rasterio import windows
from rasterio import features
from rasterio.warp import transform_bounds

import numpy as np
from itertools import product
from utils import get_tile_prefix
from bounding_box import inner_bbox, window_trueBoundingBox, cut_linestrings_at_bounds
import pyproj
from functools import partial


class Raster(object):

    def __init__(self, imageFile, meta):
        self.logger = logging.getLogger(__name__)

        self.imageFile = imageFile
        self.meta = meta
        self.DST_CRS = "EPSG:4326"

    def get_windows(self, raster, width, height):
        nols, nrows = raster.meta['width'], raster.meta['height']
        offsets = product(range(0, nols, width), range(0, nrows, height))
        big_window = windows.Window(col_off=0, row_off=0, width=nols, height=nrows)
        for col_off, row_off in offsets:
            window = windows.Window(col_off=col_off, row_off=row_off, width=width, height=height).intersection(
                big_window)
            transform = windows.transform(window, raster.transform)
            yield window, transform

    def to_tiles(self, output_path, window_size, idx):
        logging.info("Generating tiles for image : {}".format(self.imageFile.name))

        i = 0
        with rio.open(self.imageFile) as raster:
            innerBBox = inner_bbox(self.meta)
            meta = raster.meta.copy()
            for window, t in self.get_windows(raster, window_size, window_size):
                w = raster.read(window=window)
                if not self.is_window_empty(w):
                    meta['transform'] = t
                    meta['width'], meta['height'] = window.width, window.height
                    self.write_tile(w, meta, output_path, i)
                    self.write_map(raster, window, output_path, idx, i, meta, innerBBox, window_size)
                    i += 1

    def write_map(self, raster, window, output_path, spatial_idx, i, meta, box, window_size):
        windowBounds = self.transform_bnds(raster.crs, self.DST_CRS, raster.window_bounds(window))

        sec_WindowImageBBox = window_trueBoundingBox(windowBounds, box)

        dst_bounds = mapping(sec_WindowImageBBox.geometry)['bbox']

        intersecting_road_items = spatial_idx.intersection(windowBounds, objects=True)

        lines = [r.object for r in intersecting_road_items]

        m2 = meta.copy()
        m2['count'] = 1
        m2['dtype'] = 'uint8'
        nodata = 255

        with rio.open(self.output_map_path(i, output_path), 'w', **m2) as outds:
            if len(lines) > 0:
                g2 = [transform(self.project(), line) for line in lines]
                burned = features.rasterize(g2,
                                            fill=nodata,
                                            out_shape=(window_size, window_size),
                                            all_touched=True,
                                            transform=meta['transform'])
                outds.write(burned, indexes=1)

    def project(self):
        p1 = pyproj.Proj(init=self.DST_CRS)
        p2 = pyproj.Proj(init='EPSG:32750')  # the is the crs of the source raster file
        project = partial(pyproj.transform, p1, p2)
        return project

    def write_tile(self, window, meta, output_path, i):
        outpath = self.output_sat_path(i, output_path)
        with rio.open(outpath, 'w', **meta) as outds:
            outds.write(window)

    def output_sat_path(self, i, output_path):
        TRAINING_SAT_DIR = '{}/sat'.format(output_path)
        output_tile_filename = '{}/{}_{}.tif'
        outpath = output_tile_filename.format(TRAINING_SAT_DIR, get_tile_prefix(self.imageFile.name), i)
        return outpath

    def output_map_path(self, i, output_path):
        TRAINING_MAP_DIR = '{}/map'.format(output_path)
        output_tile_filename = '{}/{}_{}.tif'
        outpath = output_tile_filename.format(TRAINING_MAP_DIR, get_tile_prefix(self.imageFile.name), i)
        return outpath

    def is_window_empty(self, w):
        return not np.any(w)

    def transform_bnds(self, src_crs, dst_crs, src_bounds):
        return transform_bounds(src_crs, dst_crs, src_bounds[0], src_bounds[1], src_bounds[2], src_bounds[3])
