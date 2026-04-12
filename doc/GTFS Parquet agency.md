## Agency
- All GTFS variables are kept into columns
- 64-bit integer agency_gid from hash of "agency_name" + "_" + "timezone",
- fill missing agency_id with agency_name
- Add a bounding box based on routes bounding boxes

## Routes

- Store into a struct in Agency
- 64-bit integer route_gid from hash of "agency_id" + "_" + "route_id"
- Use extended route types
- Replacement of continuous_pickup and continuous_drop_off with a boolean, values are in trips
- Add a bounding box based on stops

### Semantic difference with GTFS
- name attribute = route_short_name except if empty, then route_long_name. Route_long_name is set only if both route_short_name and route_long_name are set, drop route_short_name
- Removal of route_sort_order: the sort order can be temporal, spatial, etc. Sorting in the struct by route_sort_order if present, otherwise by route_name, removal of route_sort_order

## Feed_info

- All GTFS variables are kept into Agencies

