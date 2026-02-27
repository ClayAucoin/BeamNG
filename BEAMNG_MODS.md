# BeamNG Mods on Google Sheets

> This app is for **BeamNG mod collectors who want to manage, combine, and analyze large mod inventories inside Google Sheets with structured data and rich message viewing**.

---

## Table of Contents

- [Overview](#overview)
- [Problem Statement](#problem-statement)
- [Brief MNP Description](#brief-mnp-description)
- [Architecture Overview](#architecture-overview)
- [Core Systems](#core-systems)
  - [1. CSV Import Engine](#1-csv-import-engine)
  - [2. Resumable Combine Engine](#2-resumable-combine-engine)
  - [3. Sidebar Message Viewer](#3-sidebar-message-viewer)
  - [4. Field Transform System](#4-field-transform-system)
  - [5. Utility & Helper Systems](#5-utility--helper-systems)
- [Project Structure](#project-structure)
- [Setup & Installation](#setup--installation)
- [Configuration](#configuration)
- [Usage Guide](#usage-guide)
- [Testing Strategy](#testing-strategy)
- [Troubleshooting](#troubleshooting)
- [Limitations & Notes](#limitations--notes)

---

## Overview

BeamNG Mods on Google Sheets is a Google Apps Script project attached to a spreadsheet that:

- Imports mod index CSV files from Google Drive.
- Combines multiple drive-based sheets into a unified dataset.
- Safely resumes long combine operations across executions.
- Displays long forum-style mod messages in a formatted sidebar.
- Cleans and normalizes filename fields.
- Provides filtering, checkbox, and navigation utilities.

The system is designed specifically for large mod libraries that exceed what simple formulas can handle efficiently.

---

## Problem Statement

Managing thousands of BeamNG mods introduces several real-world friction points:

- CSV exports are large and spread across multiple drives.
- Apps Script execution time limits interrupt large operations.
- Long forum “message” text cannot be comfortably viewed in-cell.
- Filename inconsistencies break filtering and search logic.
- Repeated filtering and checkbox cleanup is tedious.

This project solves those by introducing:

- Chunked combine processing with resumable state.
- Sidebar-based message rendering.
- Deterministic header-based column detection.
- Regex-based normalization rules.
- Structured logging and progress tracking.

---

## Brief MNP Description

Right now, the system can:

- Import `mods_index_on_<Drive>.csv` files into Sheets.
- Track CSV row counts in a `Stats` sheet.
- Combine multiple sheets into `Combined Directories` safely.
- Resume combine progress without reprocessing prior rows.
- Render BBCode message files from Google Drive into HTML.
- Normalize filenames and mod identifiers using transform rules.

---

# Architecture Overview

## High-Level Flow

```
Google Drive CSVs
        │
        ▼
importCsvIfUpdated_
        │
        ▼
mods_index_on_* sheets
        │
        ▼
combineStart_ → combineContinue_
        │
        ▼
Combined Directories
        │
        ├── Stats logging
        └── Sidebar message loading
```

---

## Combine Engine Architecture

```
User clicks "Start Combine"
        │
        ▼
combineStart_()
  - Validates headers
  - Computes total rows
  - Stores state in PropertiesService
        │
        ▼
User clicks "Continue"
        │
        ▼
combineContinue_()
  - Reads chunk from source tab
  - Writes chunk to destination
  - Persists pointers
  - Stops before execution limit
        │
        ▼
If unfinished → user clicks Continue again
If finished → clears pointers & logs completion
```

State persistence keys:

- COMBINE_srcIndex
- COMBINE_srcRow
- COMBINE_written
- COMBINE_total
- COMBINE_lastExit

This ensures idempotent progress across executions.

---

## Sidebar Flow Architecture

```
User selects row in Combined Directories
        │
        ▼
onSelectionChange()
        │
        ▼
LAST_ROW_ID stored in PropertiesService
        │
        ▼
Sidebar polling (every 800ms)
        │
        ▼
getSelectedRowInfo()
        │
        ▼
getMessageRendered(rowId)
        │
        ▼
bbcodeToSafeHtml_()
        │
        ▼
Rendered HTML in Sidebar
```

Message files are loaded from a configured Drive folder using `row_id.txt` naming.

---

# Core Systems

## 1. CSV Import Engine

Primary function: `importCsvIfUpdated_()`

Responsibilities:

- Locate CSV in Drive.
- Detect modification timestamp.
- Parse CSV using `Utilities.parseCsv()`.
- Locate `row_id` column dynamically.
- Clear existing data block.
- Write new data starting from configured position.
- Persist last-modified timestamp.

Supporting functions:

- `startImports()`
- `updateRowCounts()`
- `writeCsvRowCountToCell_()`
- `markImportDone_()`

---

## 2. Resumable Combine Engine

Core functions:

- `combineStart_()`
- `combineContinue_()`
- `combineStatus_()`

Design principles:

- Header-based width detection.
- Strict row chunking (`WRITE_CHUNK_ROWS`).
- Max batch guard (`MAX_WRITE_BATCHES_PER_RUN`).
- Persistent state in `PropertiesService`.
- Logging via `safeLog_()`.
- UI-safe behavior when triggered.

This system prevents reprocessing and protects against timeouts.

---

## 3. Sidebar Message Viewer

Files:

```
├── BeamNG Mods.js
├── Sidebar.html
└── SidebarService.js
```

Key components:

- `onSelectionChange(e)`
- `getSelectedRowInfo()`
- `getMessageRendered()`
- `bbcodeToSafeHtml_()`

Supported BBCode:

- [B]
- [CENTER]
- [URL=...]
- [ATTACH]
- Unknown tags stripped safely

The sidebar polls selection changes and renders formatted content without blocking the main sheet.

---

## 4. Field Transform System

Transformation rules are defined in:

```
const FIELD_TRANSFORMS
```

Applied via:

- `applyFieldTransforms_()`
- `CLEAN_FILENAME()`

Typical cleanup:

- Remove `.zip`
- Remove `_modland`
- Strip hash suffixes
- Remove copy indicators
- Normalize whitespace

Transforms are deterministic and composable.

---

## 5. Utility & Helper Systems

Includes:

- Checkbox clearing utilities
- Filter clearing utilities
- Navigation helper (`goToSelectedResult()`)
- Combine log sheet generation
- Status tracking in `Stats`
- Byte formatting (`formatBytes()`)

---

# Project Structure

```
.
├── BeamNG Mods.js
├── Sidebar.html
└── SidebarService.js
```

All files run within Google Apps Script bound to a spreadsheet.

---

# Setup & Installation

1. Open your Google Sheet.
2. Go to Extensions → Apps Script.
3. Create three files:
   - BeamNG Mods.js
   - SidebarService.js
   - Sidebar.html
4. Paste corresponding code.
5. Save and authorize.

Required sheets:

- mods_index_on_C
- mods_index_on_M
- mods_index_on_D
- Combined Directories
- Stats

Header `row_id` must exist in each source sheet.

---

# Configuration

Configuration is centralized in:

```
const CONFIG_
```

Key settings:

- SOURCE_TABS
- DEST_TAB
- DEST_DATA_ROW
- READ_CHUNK_ROWS
- WRITE_CHUNK_ROWS
- MAX_RUNTIME_MS
- Stats cell references

There are no environment variables or external config files.

---

# Usage Guide

## Import All CSVs

```
startImports()
```

Expected:

- Row counts updated
- Drive CSVs imported
- Stats timestamps updated

---

## Run Combine

```
newCombineStart()
newCombineContinue()
```

Click Continue until complete.

Expected:

- Rows progressively copied
- Status updated in Stats
- Completion alert

---

## Open Sidebar

Menu → Messages → Open Message Sidebar

Select a row in Combined Directories to view its message.

---

# Testing Strategy

Since there is no automated test framework:

1. Test on small CSVs.
2. Validate combine writes exact row counts.
3. Interrupt combine mid-way and confirm resume works.
4. Verify sidebar loads correct message file.
5. Confirm filename cleaning rules behave as expected.

---

# Troubleshooting

Combine Not Advancing:
- Ensure header row_id exists.
- Check COMBINE_running_flag in Stats.

Sidebar Not Rendering:
- Verify MESSAGES_FOLDER_ID.
- Ensure row_id.txt exists in Drive.

Import Not Updating:
- Confirm CSV file name matches expected.
- Confirm Drive permissions granted.

---

# Limitations & Notes

- Bound by Apps Script execution limits.
- Combine requires consistent headers across source tabs.
- Sidebar polling runs every 800ms.
- Relies on external Drive message files.
- Logging failures never break combine execution.
- Designed specifically for structured mod index exports.

---

# End of Documentation
