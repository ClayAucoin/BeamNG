' parallel_extract_then_combine.vbs
Option Explicit

Dim sh, fso
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' ---- CONFIG ----
Dim PYTHONW, EXTRACT_PY, COMBINE_PY
PYTHONW   = "pythonw.exe"
EXTRACT_PY = "C:\Users\Administrator\projects\BeamNG\beamng\extract\beamng_zip_extract_v4.5.py"
' COMBINE_PY = "C:\Users\Administrator\projects\BeamNG\beamng\combine\combine-csvs.py"

Dim OUT_DIR
OUT_DIR = "G:\My Drive\__BeamNG__\____directory-extract____\output"

' Dim COMBINED_OUT
' COMBINED_OUT = "G:\My Drive\__BeamNG__\____directory-extract____\combined.csv"

' Drives / roots you want to run in parallel
Dim roots(3)
roots(0) = "D:\__BeamNG__\___mods___"
roots(1) = "M:\__BeamNG__\___mods___"
roots(2) = "C:\__BeamNG__\___mods___"
roots(3) = "R:\__BeamNG__\___mods___"

' Expected output CSVs (must match your extractor naming)
Dim expected(3)
expected(0) = OUT_DIR & "\mods_index_on_D.csv"
expected(1) = OUT_DIR & "\mods_index_on_M.csv"
expected(2) = OUT_DIR & "\mods_index_on_C.csv"
expected(3) = OUT_DIR & "\mods_index_on_R.csv"

' How we decide a file is “done”
Const POLL_MS = 5000          ' check every 5 seconds
Const STABLE_ROUNDS = 3       ' must be unchanged for 3 consecutive checks
Const MAX_WAIT_SEC = 86400 ' 24 hours safety timeout

' ---- LAUNCH EXTRACTS IN PARALLEL ----
Dim i, cmd
For i = 0 To UBound(roots)
  cmd = """" & PYTHONW & """ """ & EXTRACT_PY & """ -r """ & roots(i) & """ --out-base-dir """ & OUT_DIR & """ --popup"
  ' cmd = """" & PYTHONW & """ """ & EXTRACT_PY & """ -r """ & roots(i) & """ --out-base-dir """ & OUT_DIR & """ --no-popup"
  sh.Run cmd, 0, False
Next

' ---- WAIT FOR ALL EXPECTED OUTPUTS TO BE STABLE ----
Dim startTime
startTime = Timer

For i = 0 To UBound(expected)
  WaitForStableFile expected(i), POLL_MS, STABLE_ROUNDS, MAX_WAIT_SEC
Next

' ---- RUN COMBINE (AFTER ALL FINISHED) ----
' cmd = """" & PYTHONW & """ """ & COMBINE_PY & """ -i """ & OUT_DIR & """ -o """ & COMBINED_OUT & """"
' sh.Run cmd, 0, True

Set sh = Nothing
Set fso = Nothing


' =========================
' Helpers
' =========================

Sub WaitForStableFile(path, pollMs, stableRoundsNeeded, maxWaitSec)
  Dim stableCount, lastSize, lastMTime, curSize, curMTime
  Dim waitedSec
  stableCount = 0
  lastSize = -1
  lastMTime = ""

  waitedSec = 0

  Do
    If fso.FileExists(path) Then
      curSize = fso.GetFile(path).Size
      curMTime = CStr(fso.GetFile(path).DateLastModified)

      If (curSize = lastSize) And (curMTime = lastMTime) Then
        stableCount = stableCount + 1
      Else
        stableCount = 0
        lastSize = curSize
        lastMTime = curMTime
      End If

      If stableCount >= stableRoundsNeeded Then Exit Do
    Else
      stableCount = 0
    End If

    WScript.Sleep pollMs
    waitedSec = waitedSec + (pollMs \ 1000)

    If waitedSec >= maxWaitSec Then
      ' Timeout safeguard: stop waiting, but you might want to quit instead
      Exit Do
    End If
  Loop
End Sub
