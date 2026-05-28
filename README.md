# Kenneth GUI

Tkinter GUI for capturing IMEC UWB serial output, storing sessions in SQLite,
and exporting measurement workbooks.

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

## Firmware Serial Protocol

The GUI parses these CSV-style firmware rows:

```text
S,node_id,sample_index,distance_m[,source]
F,node_id,sample_index[,error_code]
M,node_id,mean_m,good_count[,fail_count,total_count,last_distance_m]
Q,node_id,rx_power_dbm,first_path_power_dbm,rx_pacc,std_noise,first_path_index,first_path_sample,fp_ampl1,fp_ampl2,fp_ampl3,cir_power,cir_max_growth,lde_threshold,peak_path_index,peak_path_amplitude
NLOS,node_id,first_path_fraction_1_64,peak_path_delta_samples,rx_minus_first_path_power_db,peak_to_first_path_ampl1_ratio,fp_ampl2_to_fp_ampl1_ratio,fp_ampl3_to_fp_ampl1_ratio,cir_power_to_noise_ratio,lde_threshold_to_noise_ratio
CIR,node_id,start_sample,count,real0,imag0,real1,imag1,...
PHY,channel,prf_mhz,preamble_code,preamble_symbols,data_rate_kbps,pac_size,ntm_1,ntm_2,smart_tx_power_enabled,tx_power
```

Start Logging sends `START` by default. Stop Logging sends `STOP` by default.
Both commands can be edited or disabled in the Firmware serial control section.

## Project Layout

```text
uwb_capture/common.py       shared helpers and ParsedRecord model
uwb_capture/parser.py       serial line parser
uwb_capture/serial_io.py    pyserial background reader and writer
uwb_capture/store.py        SQLite persistence and base workbook export
uwb_capture/base_gui.py     base capture GUI
uwb_capture/extended_gui.py extended workflow, per-anchor truth, and exports
tests/test_parser.py        parser regression tests
```

Keep serial I/O, parsing, persistence, export, and GUI widgets separate when
editing. Commit after each coherent change.

## Validate

```bash
python3 -m compileall -q .
python3 -m unittest discover -s tests -v
```

## Outputs

Default capture output goes under `Measurements/GUI_Captures/`. SQLite files,
Excel files, virtual environments, bytecode caches, and Windows zone sidecars
are ignored by Git.
