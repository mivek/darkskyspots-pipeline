"""Shared E3 confidence rules; no country or spot-specific exceptions."""

LEVEL = {"A": 0, "B1": 1, "B2": 2, "C": 3}
LAND_CODES = {10, 20, 30, 40, 50, 60, 70, 100}
RISK_CODES = {80, 90, 95}
MODERATE_WETLANDS = {"wetland_bog", "wetland_wet_meadow", "wetland_fen"}


def confidence(ev, wc):
    """No coordinate, country, control ID or national-source rule."""
    if ev["wbm_class"] not in {-1, 0, 1, 2, 3}:
        raise ValueError("Unexpected WBM evidence class")
    if wc is None or not set(map(int, wc["support_classes"])) <= {
        0,
        *LAND_CODES,
        *RISK_CODES,
    }:
        raise ValueError("Missing or invalid WorldCover evidence")
    if wc["available"] and (
        not wc["support_classes"] or 0 in set(map(int, wc["support_classes"]))
    ):
        raise ValueError("Inconsistent WorldCover availability")
    reasons = []
    moderate = []
    if ev["wbm_class"] != 0 or ev.get("reviewed_water"):
        return dict(
            level="C",
            reasons=[
                r
                for r in ev["reasons"]
                if r in {"WBM_NoData", "WBM_water", "reviewed_high_confidence_water"}
            ],
            review="blocked",
        )
    if not ev["marine_support_land"]:
        reasons.append("coastal_support_disagreement")
    if ev["water_features"]:
        reasons.append("mapped_surface_water_disagreement")
    if "explicit_review_hold" in ev["reasons"]:
        reasons.append("explicit_review_hold")
    wetlands = {f.get("class", "wetland") for f in ev["wetland_features"]}
    if any(
        str(f.get("tidal", "")).lower() in {"yes", "true", "1"}
        for f in ev["water_features"] + ev["wetland_features"]
    ):
        reasons.append("explicit_tidal_influence")
    if wetlands - MODERATE_WETLANDS:
        reasons.append("higher_risk_or_untyped_wetland")
    elif wetlands:
        moderate.append("peatland_meadow_or_fen_uncertainty")
    wc_risk = sorted(set(map(int, wc["support_classes"])) & RISK_CODES)
    if wc_risk:
        reasons.append("WorldCover_aquatic_or_inundation_disagreement")
    if not wc["available"]:
        moderate.append("WorldCover_unavailable_or_partial")
    if not ev["inland_support_coverage"]:
        moderate.append("incomplete_hydrographic_collection")
    level = "B2" if reasons else "B1" if moderate else "A"
    return dict(
        level=level,
        reasons=reasons + moderate,
        wetland_types=sorted(wetlands),
        WorldCover_risk_classes=wc_risk,
        review=(
            "blocked_explicit_hold"
            if "explicit_review_hold" in reasons
            else "required" if level != "A" else "no_known_objection"
        ),
    )


def priority(candidate):
    """Production callers must supply an explicit, qualified internal tier."""
    return LEVEL[candidate["_geographic_level"]]
