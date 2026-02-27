# BeamMP Servers on Google Sheets

> This app is for **BeamMP players and server analysts who want to import live server data into Google Sheets, track changes over time, and break modlists into a clean, deduplicated dataset**.

---

## Table of Contents

- [Overview](#overview)
- [Problem Statement](#problem-statement)
- [Brief MNP Description](#brief-mnp-description)
- [Architecture Overview](#architecture-overview)
  - [Import → Diff → Snapshot Lifecycle](#import--diff--snapshot-lifecycle)
  - [Data Model: Composite Key + Signature](#data-model-composite-key--signature)
- [Core Systems](#core-systems)
  - [1. Import Engine](#1-import-engine)
  - [2. Normalization & Field Transforms](#2-normalization--field-transforms)
  - [3. Diff Engine](#3-diff-engine)
  - [4. Snapshot System](#4-snapshot-system)
  - [5. Modlist Explosion](#5-modlist-explosion)
  - [6. Sheet Utilities and Custom Functions](#6-sheet-utilities-and-custom-functions)
  - [7. Orchestrator Functions](#7-orchestrator-functions)
- [Project Structure](#project-structure)
- [Setup & Installation](#setup--installation)
- [Configuration](#configuration)
- [Usage Guide](#usage-guide)
- [Testing Strategy](#testing-strategy)
- [Troubleshooting](#troubleshooting)
- [Limitations & Notes](#limitations--notes)

---

## Overview

BeamMP Servers on Google Sheets is a Google Apps Script project that:

- Fetches live server data from the BeamMP backend endpoint.
- Imports selected fields into a structured table (header row and data row offsets are configurable).
- Normalizes values (map paths, color codes in names/descriptions, and modlist cleanup).
- Diffs the latest import vs a previous snapshot and logs changes.
- Separates “live-only” changes (volatile fields) from “config” changes.
- Extracts all unique mods referenced across servers into a dedicated sheet.

Everything described below is based directly on the provided code in `BeamMP Servers.js`.

---

## Problem Statement

If you want to track BeamMP servers in a spreadsheet, you quickly hit these issues:

- The API is raw JSON, not spreadsheet-friendly.
- It’s hard to tell what changed between two polls (added/removed/changed).
- Some fields change frequently (mod totals/size), which can bury real configuration changes.
- Mod lists are packed into one semicolon-separated string per server.

This project solves that by turning the API into a stable table, creating a snapshot-based diff system, and producing clean secondary datasets like a unique mod list.

---

## Brief MNP Description

Right now, this app can:

- Import the current server list from the BeamMP backend into a sheet.
- Normalize fields using regex rules (including modlist cleanup).
- Maintain a previous snapshot (“BeamMP Servers (Prev)”) and diff it against the latest import.
- Log detailed change events (“BeamMP Changes”) and run-level stats (“BeamMP Import Log”).
- Explode all server modlists into a unique, sorted “BeamMP Mods” sheet.

---

# Architecture Overview

## Import → Diff → Snapshot Lifecycle

This is the end-to-end lifecycle implemented by the orchestration functions (`updateAndCheckServers()`, `updateAndExplode()`).

### High-level flow

```
┌───────────────────────────────┐
│ BeamMP Backend API             │
│ https://backend.beammp.com/... │
└───────────────┬───────────────┘
                │ UrlFetchApp.fetch()
                ▼
┌───────────────────────────────┐
│ beammpImportServers()          │
│ - parse JSON array             │
│ - normalizeValue() per column  │
│ - write header + rows          │
│ - ensure filter/frozen rows    │
└───────────────┬───────────────┘
                │
                ▼
┌───────────────────────────────┐
│ beammpDiffLatestVsPrev()       │
│ - first run: init snapshot     │
│ - index latest & prev          │
│ - compute ADDED/REMOVED/CHANGED│
│ - append to "BeamMP Changes"   │
│ - log run stats                │
└───────────────┬───────────────┘
                │ copyTableArea_()
                ▼
┌───────────────────────────────┐
│ "BeamMP Servers (Prev)"        │
│ - overwritten snapshot table   │
│ - hidden after update          │
└───────────────────────────────┘
                │
                ▼
┌───────────────────────────────┐
│ explodeModlistToSheet()        │
│ - read modlist col             │
│ - split by ';'                 │
│ - normalize + dedupe + sort    │
│ - write "BeamMP Mods"          │
└───────────────────────────────┘
```

### First run behavior (important)

On the very first run, `beammpDiffLatestVsPrev()` checks if the “Prev” sheet is empty:

- If empty: it **copies the current table area** from “BeamMP Servers” into “BeamMP Servers (Prev)” and returns without generating a diff log.

This matches the code path:
- `if (prev.getLastRow() === 0) { copyTableArea_(latest, prev, spec); return; }`

---

## Data Model: Composite Key + Signature

The diff engine uses two core concepts:

1. **Composite key** (stable identity)
2. **Signature** (stable-ish configuration snapshot string)

### Composite key

Each server row is uniquely identified by a composite string built from 3 fields:

- `ip`
- `port`
- `ident`

Constructed as:

```
server_key = `${ip}:${port}|${ident}`
```

This is used as the primary dictionary key in both the “latest” and “prev” indexes.

### Signature

A signature is a single string representing the concatenated values of *compare columns*:

- compareCols in code:
  - owner, map, maxplayers, modstotal, modstotalsize, sname, sdesc, tags, modlist

Built as:

```
sig = (values for compareCols).join("||")
```

That means the signature changes if any of those compare fields change.

### Volatile fields vs config fields

The code treats some compare fields as “volatile” (likely to change during normal server runtime) and uses them to classify changes:

- volatileFields in code:
  - modstotal
  - modstotalsize

Diff classification logic:

- If *only* volatile fields changed → `CHANGED_LIVE`
- If any non-volatile field changed → `CHANGED_CONFIG`

### Internal data model diagram

```
API server object (JSON)
        │
        ▼ normalizeValue(col)
Table row in "BeamMP Servers"
        │
        ├── key fields: ip, port, ident
        │       │
        │       ▼
        │   server_key = ip:port|ident
        │
        └── compare fields: owner, map, maxplayers, ...
                │
                ▼
            sig = owner||map||maxplayers||...||modlist
```

### “Changed fields” list

When signatures differ, the script builds a list of actual changed fields by comparing old vs new `valuesByField`:

```
changedFields = diffFields_(old.valuesByField, cur.valuesByField, compareCols)
```

That list becomes the `changed_fields` column stored in “BeamMP Changes”.

---

# Core Systems

## 1. Import Engine

### Key functions

- `beammpImportServers()`
- `normalizeValue(v, key)`
- `formatBytes_(bytes)`

### What it does

- Fetches from `CONFIG.url` using `UrlFetchApp.fetch()`
- Expects an array of objects (throws if not an array)
- Builds a header from `CONFIG.columns`
- Writes header at `spec.headRow` and data at `spec.dataRowStart`
- Clears old output block before writing new
- Creates a filter if one isn’t present
- Freezes rows (note: the code calls `sheet.setFrozenRows(headerRowNum + 1)`)

---

## 2. Normalization & Field Transforms

Normalization is primarily implemented in `normalizeValue()` and `FIELD_TRANSFORMS`.

### Notable transforms

- `map`:
  - strips `/levels/` prefix
  - strips `/info.json` suffix
- `sname` / `sdesc`:
  - removes BeamNG-style caret color codes (`^0`, `^a`, etc)
  - collapses whitespace
  - trims
- `owner`:
  - removes trailing `#0`
- `modlist`:
  - strips path slashes
  - removes `.zip`
  - strips `_modland`
  - strips hash suffix patterns like `-deadbeef`
  - strips copy markers

There are also helper custom functions that apply these transforms inside spreadsheet formulas.

---

## 3. Diff Engine

### Key functions

- `beammpDiffLatestVsPrev()`
- `sheetToIndexWithValuesBySpec_()`
- `diffFields_(oldValuesByField, newValuesByField, fields)`
- `ensureChangesHeader_(sheet, headerRow)`
- `logImportStats_(recordCount, stats)`

### Sheets used

- Latest: `BeamMP Servers`
- Snapshot: `BeamMP Servers (Prev)`
- Change log: `BeamMP Changes`
- Import stats log: `BeamMP Import Log`

### Change event row shape

The script appends rows to “BeamMP Changes” with:

- timestamp
- type (ADDED / REMOVED / CHANGED_CONFIG / CHANGED_LIVE)
- server_key
- ip / port / ident
- changed_fields (comma list)
- old_sig / new_sig

---

## 4. Snapshot System

The snapshot system overwrites the “Prev” sheet table area each run:

- `copyTableArea_(latest, prev, spec)`
  - clears prev contents
  - locates `startCol` by finding `spec.headColName` in the header row
  - copies the table block from `headerRowNum` down to last row/col

Then the script hides the snapshot sheet:

- `prev.hideSheet()`

---

## 5. Modlist Explosion

### Key function

- `explodeModlistToSheet()`

### What it does

- Finds the `modlist` column by header name on row 3
- Reads values starting at data row 5
- Splits each cell by `;`
- Normalizes each mod name (lowercase + transforms)
- Dedupes with a `Set`
- Sorts case-insensitively
- Writes result to “BeamMP Mods” with a single column: `mod_name`

---

## 6. Sheet Utilities and Custom Functions

### Checkbox helpers

There are several checkbox helper functions that manipulate checkboxes on the “Search BeamMP Servers” sheet.

### Custom formula helpers

- `CONTAINS_ANY_WILDCCARD(hayRange, needleRange)`
  - builds one regex union of needles and tests each hay value (case-insensitive)
- `CONTAINS_ANY_WHOLE_FIELD(hayRange, needleRange)`
  - like above but anchors the entire field (`^(a|b|c)$`)
- `NORMALIZE_MODLIST(range)`
  - applies modlist transforms across a range
- `CLEAN_FILENAME(range)`
  - applies `FIELD_TRANSFORMS.modlist` rules to cells

---

## 7. Orchestrator Functions

Two convenience entry points exist:

- `updateAndCheckServers()`
  - `beammpImportServers()`
  - `beammpDiffLatestVsPrev()`
  - `explodeModlistToSheet()`
- `updateAndExplode()`
  - `beammpImportServers()`
  - `explodeModlistToSheet()`

---

# Project Structure

Based on the uploaded code:

```
.
└── BeamMP Servers.js
```

---

# Setup & Installation

1. Open your Google Sheet.
2. Go to **Extensions → Apps Script**.
3. Create a script file named `BeamMP Servers.js`.
4. Paste the code.
5. Save.

## Authorize permissions

The script fetches from a URL and creates/updates sheets, so the first run will prompt for authorization.

A helper exists:

- `testDriveAuth()`
  - calls `SpreadsheetApp` APIs and logs, forcing scopes to be requested/approved.

---

# Configuration

Configuration is centralized in:

- `const CONFIG = { ... }`

Key settings:

- `url`: BeamMP endpoint (default `https://backend.beammp.com/servers-info/`)
- `columns`: top-level JSON keys to write into the sheet
- `stringifyComplexValues`: if true, arrays/objects are JSON-stringified
- `serverSheetSpecs`:
  - `sheetName`: “BeamMP Servers”
  - `headRow`: 3 (header row)
  - `headColName`: “owner” (start column anchor)
  - `dataRowStart`: 5 (data starts here)

No environment variables or external config files are used.

---

# Usage Guide

## One-click update (import + diff + mods)

Run:

```js
updateAndCheckServers()
```

Expected:

- “BeamMP Servers” updated with latest API data
- “BeamMP Changes” appended with any detected changes (except first run)
- “BeamMP Servers (Prev)” overwritten with the latest snapshot and hidden
- “BeamMP Mods” regenerated as unique sorted mod names
- “BeamMP Import Log” appended with summary stats

## Import only

```js
beammpImportServers()
```

## Diff only

```js
beammpDiffLatestVsPrev()
```

## Regenerate mods list only

```js
explodeModlistToSheet()
```

---

# Testing Strategy

There is no automated test suite in the provided code, so testing is manual.

Recommended checks:

1. Run `beammpImportServers()` and confirm:
   - header row is written at row 3
   - data begins at row 5
   - filter exists
2. Run `beammpDiffLatestVsPrev()` twice:
   - first run should create “Prev” snapshot without diff output
   - second run should create diff output if anything changed
3. Verify change classification:
   - if only `modstotal` / `modstotalsize` changes, events should be `CHANGED_LIVE`
4. Run `explodeModlistToSheet()` and confirm:
   - output is deduped
   - normalized mod names look correct

---

# Troubleshooting

## Fetch failed: HTTP <code>

- The script throws if the response code is not 200.
- Check if the endpoint is reachable from Apps Script.
- Check Apps Script URLFetch quotas if you are polling frequently.

## Header “owner” not found

`beammpImportServers()` locates its table start column by searching for `headColName` in the configured header row.

Fix:
- Ensure row 3 contains the `owner` header (or change `serverSheetSpecs.headColName`).

## Diff produces no changes

Possible causes:
- First run (snapshot initialization)
- No server changes occurred
- Key columns are missing/blank in rows (the index skips rows missing ip/port/ident)

## Modlist explosion empty

- Ensure `modlist` exists in `CONFIG.columns` and is written into the server sheet.
- Ensure data exists starting at row 5.

---

# Limitations & Notes

- The diff system depends on the stability of the composite key (`ip`, `port`, `ident`). If any of these change, the server is treated as removed/added.
- “Signature” is a simple join string; if you change `compareCols`, you change what “changed” means.
- Regex-based transforms may need updates if API formats change (especially server name formatting and modlist conventions).
- The code includes duplicated function names in the checkbox section (`selectAllCols` / `getCheckCol` appear more than once). In Apps Script, later definitions override earlier ones, so keep an eye on which version you intend to use.
- All logic is intended for Google Apps Script only.

---

# End of Documentation
