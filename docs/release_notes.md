North-Atlantic extracts (box 20-75°N, 80°W-15°E) of wind, waves, sea-surface temperature and
surface currents, built by this repository's workflows. NetCDF4, int16-packed (see the variable
attributes); winds and currents *towards*, wave directions *coming from*; forecast files have dims
(init, [member,] lead [h], lat, lon). One `manifest_*.json` (sha256) per file.

Sources and licences — attribution required when using or redistributing:
* ERA5: Contains modified Copernicus Climate Change Service information [year]. Neither the
  European Commission nor ECMWF is responsible for any use that may be made of the Copernicus
  information or data it contains.
* ECMWF forecasts: Adapted from ECMWF forecast data by ECMWF, licensed under CC BY 4.0
  (https://creativecommons.org/licenses/by/4.0/); values spatially averaged and cropped.
* NOAA GEFS: data from NOAA/NCEP (NOAA Open Data Dissemination); values regridded and cropped —
  not original NOAA data and not endorsed by NOAA.
* Copernicus Marine: Generated using E.U. Copernicus Marine Service Information.
