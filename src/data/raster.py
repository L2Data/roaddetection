import logging
from shapely.ops import transform
from shapely.geometry import mapping

import rasterio as rio

from rasterio import windows
from rasterio import features
from rasterio.warp import transform_bounds

import numpy as np
from itertools import product
from utils import output_sat_path, output_sat_rgb_path, output_map_path
from bounding_box import inner_bbox, window_trueBoundingBox, cut_linestrings_at_bounds
import pyproj
from functools import partial
from operator import is_not


class Raster(object):

    def __init__(self, analyticFile, rgbFile, meta):
        self.logger = logging.getLogger(__name__)

        self.analyticFile = analyticFile
        self.rgbFile = rgbFile
        self.meta = meta
        self.DST_CRS = "EPSG:4326"

    def get_windows(self, raster, width, height, overlap=None):
        """
        Produces regularly spaced tiles (instances of rasterio.window.Window) 
        of size [width, height] pixels from rasterio DataSetReader (image file opened 
        with rasterio.open) with overlap 
        width: width of tiles (pixels)
        height: height of tiles (pixels)
        overlap: overlap of tiles in each dimension (relative units, range [0 1[)
        """    
        # check overlap
        if overlap is None:
            overlap = 0.0
        else:
            assert (overlap >= 0.0) and (overlap < 1.0), "overlap must be in [0 1["
        
        width_masterImg, height_masterImg = raster.meta['width'], raster.meta['height']
        
        # check that tiles are within master image
        assert (width <= width_masterImg) and (height <= height_masterImg), "tiles are too large for image"
        
        # produce a list of regularly spaced horizontal offsets such that all tiles produced 
        # from it fit into the master image, plus one offset which ensures that the right edge 
        # of the last tile is identical to the right edge of the master window (the implication
        # is that the last tile has a different degree of overlap with its neighbor than the other
        # images)
        offsets_horiz = list(range(0, width_masterImg, round(width *(1.0 - overlap))))
        offsets_horiz[-1] = width_masterImg - width
        # same for vertical offsets
        offsets_vert = list(range(0, height_masterImg, round(height * (1.0 - overlap))))
        offsets_vert[-1] = height_masterImg - height
        # construct iterator
        offsets = product(offsets_horiz, offsets_vert)
        
        for col_off, row_off in offsets:
            window = windows.Window(col_off=col_off, row_off=row_off, width=width, height=height)
            transform = windows.transform(window, raster.transform)
            yield window, transform


    def to_tiles(self, output_path, window_size, idx, overlap):
        logging.info("Generating tiles for image : {}".format(self.analyticFile.name) + \
                     " with edge overlap {}".format(overlap))
        i = 0
        with rio.open(self.analyticFile) as raster:
            innerBBox = inner_bbox(self.meta)
            meta = raster.meta.copy()
            for window, t in self.get_windows(raster, window_size, window_size, overlap):
                w_img = raster.read(window=window)
                if not self.is_window_empty(w_img):
                    meta['transform'] = t
                    meta['width'], meta['height'] = window.width, window.height
                    self.write_analytic_tile(w_img, meta, output_path, i)
                    self.write_rgb_tile(window, meta, output_path, i)
                    self.write_map(raster, window, output_path, idx, i, meta, innerBBox, window_size)
                    i += 1

    def write_map(self, raster, window, output_path, spatial_idx, i, meta, box, window_size):
        windowBounds = self.transform_bnds(raster.crs, self.DST_CRS, raster.window_bounds(window))

        sec_WindowImageBBox = window_trueBoundingBox(windowBounds, box)

        dst_bounds = mapping(sec_WindowImageBBox.geometry)['bbox']

        intersecting_road_items = spatial_idx.intersection(dst_bounds, objects=True)

        linesDf = cut_linestrings_at_bounds(sec_WindowImageBBox, intersecting_road_items)

        m2 = meta.copy()
        m2['count'] = 1
        m2['dtype'] = 'uint8'
        nodata = 0

        with rio.open(output_map_path(self.analyticFile, i, output_path), 'w', **m2) as outds:
            if len(linesDf) > 0:
                g2 = linesDf.to_crs(m2['crs'].data)
                burned = features.rasterize(shapes=[(x.geometry, self.get_pixel_value(int(x.label))) for i, x in g2.iterrows()],
                                            fill=nodata,
                                            out_shape=(window_size, window_size),
                                            all_touched=True,
                                            transform=meta['transform'])
                outds.write(burned, indexes=1)

    def get_pixel_value(self,label):
        if label == 1:
            return 127
        elif label == 2:
            return 255
        return 0

    def write_analytic_tile(self, window, meta, output_path, i):
        outpath = output_sat_path(self.analyticFile, i, output_path)
        with rio.open(outpath, 'w', **meta) as outds:
            outds.write(window)

    def write_rgb_tile(self, window, meta, output_path, i):
        outpath = output_sat_rgb_path(self.analyticFile, i, output_path)
        m2 = meta.copy()
        m2['dtype'] = 'uint8'
        with rio.open(self.rgbFile.as_posix()) as raster_rgb:
            w = raster_rgb.read(window=window)
            with rio.open(outpath, 'w', **m2) as outds:
                outds.write(w)

    def is_window_empty(self, w):
        return not np.any(w)

    def transform_bnds(self, src_crs, dst_crs, src_bounds):
        return transform_bounds(src_crs, dst_crs, src_bounds[0], src_bounds[1], src_bounds[2], src_bounds[3])
