North-Atlantic 10 m wind extracts (box 30-70°N, 65°W-10°E), built by this repository's workflows.
Variables `u10`/`v10` (m/s, wind towards), int16 × 0.01, NetCDF4/zlib; forecast files have dims
(init, [member,] lead [h], lat, lon). One `manifest_*.json` (sha256) per file.

Sources and licences — attribution required when using or redistributing:
* ERA5: Contains modified Copernicus Climate Change Service information [year]. Neither the
  European Commission nor ECMWF is responsible for any use that may be made of the Copernicus
  information or data it contains.
* ECMWF forecasts: Adapted from ECMWF forecast data by ECMWF, licensed under CC BY 4.0
  (https://creativecommons.org/licenses/by/4.0/); values spatially averaged and cropped.
* NOAA GEFS: data from NOAA/NCEP (NOAA Open Data Dissemination); values regridded and cropped —
  not original NOAA data and not endorsed by NOAA.
