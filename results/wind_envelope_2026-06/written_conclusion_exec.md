# CTE Wind-Envelope Conclusion - Executive

The production-like CTE campaign establishes a measured operating envelope, not
just a tracking-error table. In the corrected 020 analysis of campaign `017`,
13 of 16 East/North wind cells produced accepted square/loiter data and 3
high-wind cells produced no accepted run. The calm square RMS true-path
deviation is 7.15 m. The worst
accepted cell, `wind_x_12_y_04`, is
17.99 m RMS, about
2.52x calm.

The degradation is explained primarily by the wind vector. A magnitude-only
model explains 0.673 of combo-level
RMS variation, East/North components explain 0.733,
and adding the component interaction explains
0.751. That still leaves real
residual behavior, especially near the envelope edge, so this should be framed
as a measured envelope and not a perfect one-variable law.

The edge is physical. The no-accepted cells sit where the resultant wind is at
or above the production-like `AIRSPEED_CRUISE = 14 m/s`. At `wind_x_12_y_12`,
the resultant wind is 16.97 m/s, the aircraft holds roughly 14 m/s airspeed,
median groundspeed is about 2.8 m/s, and mission progress stalls at waypoint 2.
The internal EKF wind audit accepted all 38 named BIN files, so the aircraft
was seeing the advertised wind; these cells are not harness gaps.

Use this as the deck line: the production-like aircraft completes low and
moderate wind cells with quantified, predictable degradation, and then reaches
a cruise-airspeed-limited envelope edge at the high-wind corner.
