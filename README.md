# Kenneth GUI

Tkinter GUI for capturing IMEC UWB/BLE measurement reports, storing sessions in
SQLite, and exporting measurement workbooks.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python serial_to_excel_extended_gui.py
```

The extended launcher is the normal entry point. The base GUI is also available
if you do not need the per-anchor measurement workflow:

```bash
python serial_to_excel.py
```

In the extended GUI, detected responder IDs appear in the per-responder table.
After a capture, select each responder ID and save its own LOS/NLOS label before
exporting; the per-anchor workbooks and measurement-list workbook use those
independent labels.

## Localization And Simulation

The extended GUI includes a `Localization` tab for anchor coordinates and tag
position solving. Enter each anchor's X/Y coordinates in meters, load the latest
captured ranges, then solve. The solver subtracts squared anchor-range equations
to form radical-axis lines, then performs least squares on those lines. Any
common height term cancels during the subtraction. Results report the estimated
X/Y point, radical-axis RMSE in meters, confidence, and line residuals.

Use `Run Square Simulation` to fill a four-anchor square/floor-plan test with
fake ranges, solve it, and plot the estimated clicker point. The anchor X/Y
fields are the known anchor placements; the clicker X/Y is not entered by the
operator and is always estimated from anchor ranges. The visible simulation
controls only set the floor-plan width and height. The plot can be opened in
fullscreen from the `Fullscreen` button beside the layout preview.

Use `Start Live Tracking` to send range-data-only
`CMD_ML_START_FAST_RANGING` requests to the clicker every configured number of
seconds. Returned anchors are added to the localization table as their ranges
arrive. Enter each returned anchor's real X/Y coordinate; after at least three
anchors have coordinates and ranges, the GUI updates the estimated clicker
position with the same radical-axis solver.

## Anchor Geometry Survey

The extended GUI also includes an `Anchor Geometry` tab for anchor-to-anchor
survey data. `Gather Anchor Distances` sends `CMD_SURVEY_START_PAIR` to the
clicker, then waits for the configured collection window while incoming
`SURVEY_PAIR_RESULT` packets populate the pair table. The final firmware TLV
shape is still expected to settle; the parser accepts repeated `ANCHOR_ID` TLVs
and provisional pair-specific TLVs for anchor A, anchor B, pair distance, and
pair status.

Pair distances can also be added manually. `Solve Layout` treats each pair
distance as a spring and optimizes the lowest-energy 2D anchor layout with a
dependency-free multi-seed basin-hopping solver. The layout plot shows anchor
positions and pair distances, supports mirror and rotation controls, and can be
drag-rotated with the mouse. Select two anchors and press `Straighten` to rotate
the layout so that pair lies on the same Y coordinate for a straight box view.

## Bluetooth Protocol Workflow

The GUI now treats Connect and Disconnect as BLE transport actions. Enter or
scan for the BLE device, provide the notify and write characteristic UUIDs from
the firmware, then connect. Start Logging and Stop Logging only control the
local capture session; they do not send text commands such as `START` or `STOP`.

The measurement/export workflow expects these incoming IMEC binary messages:

```text
0x20 CLICK_REPORT           normal click distance measurements
0x22 ANCHOR_HEARTBEAT       device health/status telemetry
0x41 COMMAND_RESULT         result for a GUI command
0x51 SURVEY_REACH_REPORT    anchor reachability survey output
0x53 SURVEY_PAIR_RESULT     anchor-to-anchor distance measurements
0x7F MSG_ERROR              protocol or firmware error
```

The GUI exposes these outgoing command buttons:

```text
CMD_GET_STATUS          0x0002
CMD_START_HEARTBEAT     0x0009
CMD_STOP_HEARTBEAT      0x000A
CMD_SURVEY_REACHABILITY 0x0100
CMD_SURVEY_PREPARE_PAIR 0x0101
CMD_SURVEY_START_PAIR   0x0102
CMD_SURVEY_ABORT        0x0103
CMD_ML_START_COLLECTION 0x8000
CMD_ML_START_FAST_RANGING 0x8001
```

The command packets use the shared IMEC binary envelope and TLV payloads from
`UWB+BLE Protocols and Strategies 0.2.48.md`. Device IDs may be entered as
decimal, `0x` hex, or colon-separated hex. `Target ID` can be `0` for broadcast
where the firmware allows broadcast handling.

## Legacy Text Parser

For compatibility, BLE notifications that contain text are still parsed with the
older CSV-style firmware parser:

```text
S,node_id,sample_index,distance_m[,source]
F,node_id,sample_index[,error_code]
M,node_id,mean_m,good_count[,fail_count,total_count,last_distance_m]
Q,node_id,rx_power_dbm,first_path_power_dbm,rx_pacc,std_noise,first_path_index,first_path_sample,fp_ampl1,fp_ampl2,fp_ampl3,cir_power,cir_max_growth,lde_threshold,peak_path_index,peak_path_amplitude
NLOS,node_id,first_path_fraction_1_64,peak_path_delta_samples,rx_minus_first_path_power_db,peak_to_first_path_ampl1_ratio,fp_ampl2_to_fp_ampl1_ratio,fp_ampl3_to_fp_ampl1_ratio,cir_power_to_noise_ratio,lde_threshold_to_noise_ratio
CIR,node_id,start_sample,count,real0,imag0,real1,imag1,...
PHY,channel,prf_mhz,preamble_code,preamble_symbols,data_rate_kbps,pac_size,ntm_1,ntm_2,smart_tx_power_enabled,tx_power
```

## Project Layout

```text
uwb_capture/common.py       shared helpers and ParsedRecord model
uwb_capture/protocol.py     IMEC binary packet/TLV codec
uwb_capture/bluetooth_io.py BLE scanner, connector, and packet writer
uwb_capture/localization.py 2D radical-axis line least-squares solver
uwb_capture/anchor_geometry.py anchor-to-anchor spring layout solver
uwb_capture/parser.py       legacy text line parser
uwb_capture/store.py        SQLite persistence and base workbook export
uwb_capture/base_gui.py     base capture GUI
uwb_capture/extended_gui.py extended workflow, per-anchor truth, and exports
tests/test_parser.py        parser regression tests
```

Keep Bluetooth I/O, protocol parsing, persistence, export, and GUI widgets
separate when editing. Commit after each coherent change.

## Validate

```bash
python3 -m compileall -q .
python3 -m unittest discover -s tests -v
```

## Outputs

Default capture output goes under `Measurements/GUI_Captures/`. SQLite files,
Excel files, virtual environments, bytecode caches, and Windows zone sidecars
are ignored by Git.
