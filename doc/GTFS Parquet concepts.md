## High Level concepts

- Grouping into three files instead of 9 main and 25 secondary files
- Weekly data as it is the recommended minimum valid period for data
    - no more need for calendar, calendar_dates and trips
- Almost same semantics as GTFS,
    - except for a major reorganization of stop_times
- No optional columns or data, as empty data is well supported ; enforce default values
- Hierarchical Arrow data types instead of simple csv format: boolean, geometry, timezone-aware datetimes, lists and structs, minimize data size if possible, integer ids
- Geometries for stops (points), agencies (bounding box) and lines (segments):
    - requires a projected CRS for each file, as long as geopandas cannot natively use geographic coordinates
- basic shortest times and transfers creation code


* __Three Files__:
     - __stops__  and __stations__
        - Overture places subcategories 
        - global GID identifier among Places, unique per agency
        - partitioned by spatial index for easier searching
        - one format for stops (location_type=0) and one for stations (location_type=1) 
            - stations contain other location_types and pathways
    - __agencies__
        - unique file as it should be relatively lightweight, if not, partition by continent or country
        - grouping of all attributes in structs : routes, fares...  
        - add a bounding box based on stops in agency
    - __lines__
        - stop_times attributes + shapes + trips + some routes and stops attributes
        - line_gid: All trips with same line_gid have the same route and direction, same stop list and that don't overtake each other
        - weekly data : timepoints are an integer in seconds from monday at noon up to sunday at midnight + 4 hours
        - one row per arc (line between two stops), departure and travel times are attributes
        - partitioned by agency or agency group (no agency in two files, but possibility of multiple agencies in one file)

* __Semantics differences / precisions compared to GTFS__ :
    - stop_sequence are continuous integer values, starting at 0 and ending at 65355
    - trip_id are replaced by line_gid (see corresponding docs)

* __Unique IDs__:
    - unique random uint64 for agency, route, stops and lines IDs (gid instead of id)
    - keep original IDs for stops, agency and routes
    - __TO BE IMPLEMENTED__ : agency, route, stops persistence: comparison with agency_id, route_id, stop_id, otherwise add new value
    - __TO BE IMPLEMENTED__ : lines persistence: if same stop sequence and same route, otherwise add new value

## GTFS files dispatch

| GTFS          	    | Transit                               	| STATUS [X] DONE |
|------------   	    |----------------                          	|:---------------:|
| Agency       	        | Agencies                                 	|  X              |
| Attributions  	    | Agencies/attribtions + lines and routes  	|                 |
| Feed_info      	    | Agencies / Feed_info column           	|  X              |
| Routes       	        | Agencies / Routes column               	|  X              |
| Stop_area     	    | Agencies / Fares column                  	|                 |
| Area           	    | Agencies / Fares column                  	|                 |
| Fare_rules     	    | Agencies / Fares column               	|                 |
| Fare_attributes       | Agencies / Fares column                 	|                 |
| Stops (type = 0)      | Places (Stops)  	                        |  X              |
| Stops (type > 0)      | Places (Station)                      	|                 |
| Pathways            	| Places (Station)                       	|                 |
| Levels            	| Places (Stops & Station)                 	|                 |
| Stop_times          	| Lines                                    	|  X              |
| Trips             	| Lines                                    	|  X              |
| Calendar           	| Lines / Week days structs               	|  X              |
| Calendar_dates     	| Lines / Week days structs                	|  X              |
| Shapes             	| Lines / Geometry column                 	|  X              |
| Frequencies       	| Lines (convert to normal stop_times)     	|                 |
| Transfers             | Lines / Transfers columns                	|                 |
| Location_groups       | Lines / Location column                 	|                 |
| Location_groups_stops | Lines / Location column                   |                 |
| Location.geojson      | Lines / Location column                	|                 |
| Translations          | ?                       	                |                 |