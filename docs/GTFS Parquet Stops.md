stop_gid are garanteed to be unique for an agency, original stop_id may therefore be duplicated

## Stops
- location_type = 0
- simple geometry (point) - WGS84
- integration of levels values:
    - level_index = NA by default
    - level_name = NA by default

## Stations (TODO)
- location_type = 1
- attributes (in struct):
    * name: station's stop_name
    * geometry: station's point
    * entrances (location_type 2): list of dicts: name, geometry, wheelchair_boarding, level_index, level_name
    * nodes (location_type 3): list of dicts: name, geometry, wheelchair_boarding, level_index, level_name
    * boarding area (location_type 4): list of dicts: name, geometry, wheelchair_boarding, level_index, 
    * pathways (from pathways.txt)
    * levels (from levels.txt)