"""Tests that all external dependencies are importable."""


def test_all_deps_importable():
    import numpy
    import scipy
    import rasterio
    import shapely
    import yaml


def test_nightskyquality_importable():
    from nightskyquality import ALRResult, ALRConfig, radiance_to_alr, write_geotiff
