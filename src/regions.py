"""regions.yaml loader and region resolver."""
import yaml

REQUIRED_FIELDS = {"bbox", "equal_area_epsg", "admin_level", "osm_country_code"}


def load_regions(path: str = "regions.yaml") -> dict:
    """Load regions.yaml and return {name: region_dict}."""
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"regions.yaml must be a dict, got {type(data).__name__}")
    for name, region in data.items():
        _validate_region(name, region)
    return data


def _validate_region(name: str, region: dict) -> None:
    missing = REQUIRED_FIELDS - set(region.keys())
    if missing:
        raise ValueError(f"Region '{name}' missing fields: {missing}")
    if len(region["bbox"]) != 4:
        raise ValueError(f"Region '{name}': bbox must have 4 elements")


def get_region(name: str, regions_path: str = "regions.yaml") -> dict:
    """Resolve a region name; raises KeyError with a helpful message if not found."""
    regions = load_regions(regions_path)
    if name not in regions:
        known = ", ".join(sorted(regions.keys()))
        raise KeyError(f"Unknown region '{name}'. Known: {known}")
    return regions[name]
