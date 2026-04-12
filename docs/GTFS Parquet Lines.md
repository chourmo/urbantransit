## General Format
- one row per line arc + continuous sequence number (instead of a stop-based format)
    - advantages:
        - richer concept of inter-stop, especially for on demande routes (multiple stops or zone)
        - geometry between stops (shapes)
        - easier to integrate TB route planning algo
    - disadvantages:
        - repetition of trip-specific attributes, but compressed by parquet as continuous values 
- **no need for calendar / calendar_dates**
    - timetable fully materialized (no missing departure / arrival), extended but limited to a week
- **no trip concept**  : no trip_id, add some attrbutes to lines and merge with routes
- **no shape concept** : geometries are included in lines, for each arc

### Lines
- set of trips of same route with same stop sequence, same service and never overtaking
    - calculation on stop_id
    - no first departure at same minute
    - can cross multiple days

__Attributes__
- addition of route_type, route_name and agency_name
- addition of trip_short_name, trip_headsign, wheelchair_accessible, bikes_allowed, cars_allowed from trips, merge with equivalent routes attributes, drop trip_ prefix
- replace arrival by travel to have better compression ratio (travel time on an arc are often the same)
- TODO : remove block_id, integration in transfer description - in_seat column    
- TODO : add boolean attribute "from_frequencies" true if stop_times estimated from frequencies file

### Stop_times
- conversion of departure_time to seconds since trip start, over a full week: int32
- homogenized stop_sequence: starts at 0, increment of 1, last stop 65355 - remains compatible with GTFS standard
    - allows easy identification of first (value 0) and last stops (value 65355) 
- timepoint converted to boolean
- grouping of trip_headsign and stop_headsign into headsign
- shape_id and shape_dist_traveled: removed due to geometry integration in lines

### Transfers

- **Transfers** column in lines, strictly mapping content in transfers.txt
- algorythm for transfer creation in Transitgraph
- transfer from arc exit to entry of next arc
- list of structs:
    - line_gid: 32-bit integer  
    - position: arc number: 16-bit integer
    - TODO : min_transfer_time: walking time in seconds between two stops - 16-bit integer
    - TODO : transfer_type: preserved values:
        - default NA
        - 1: timed transfer
        - 2: transfer requires a minimum amount of time
        - 4: in-seat transfer
        - 5: in-seat transfers are not allowed between sequential trips

__non-duplicated attributes on origin and destination__

| Attribute                    | Origin        | Destination     |
|-----------------------------|---------------|------------------|
| stop_id                     |       X       |                  |
| departure_time              |       X       |                  |
| arrival_time                |   replaced travel                |
| stop_headsign               |       X       |                  |
| start_pickup_drop_off_window|       X       |                  |
| end_pickup_drop_off_window  |       X       |                  |
| pickup_type                 |       X       |                  |
| dropoff_type                |               |        X         |
| continuous_pickup           |       X       |                  |
| continuous_drop_off         |               |        X         |
| timepoint                   |       X       |                  |

### Geometry generation

- three representations in three columns:
    - __geometry__: linestring, first and last points are stops geometries projected on shape
    - TODO : __geom_area__: locations.geojson -> polygon / multipolygon
    - TODO : __geom_multistops__: source location_groups / locations_groups_stops -> multipoints

- geometry is extracted from shapes and shape_dist_traveled :
    1. if no shapes.txt, then the geometry is a line between point geometries
    2. calculate optional shape_dist_traveled in shapes : projected distance between points  
    3. calculate optional shape_dist_traveled in stop_times :  
        3.1 create a linestring geometry from shapes  
        3.2 find closest point  
        3.3 if shape_dist_traveled is not increasing, then replace by na  
            - __cause__ : transit line is coming back on same geometry, which may lead to having a closest point further on geometry  
    4. add stop_times point inside shapes to be sure that all points exist
    5. on arcs, geometry between shape_dist_traveled are the subset of points from shapes