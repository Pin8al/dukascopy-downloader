//+------------------------------------------------------------------+
//| DukascopyTickImport.mq5 — import ticks & manage custom symbols    |
//| SCRIPT ONLY — must live under MQL5/Scripts/, not Experts/.       |
//| Launched via [StartUp] Script= in import.ini (live terminal).     |
//+------------------------------------------------------------------+
#property copyright "Dukascopy Downloader"
#property version   "1.19"
#property strict
#property script_show_inputs

input string JobId = "";

#define JOB_ROOT "dukascopy_jobs\\"
#define TICK_FLAGS 6
#define CHUNK_MAX 750000
#define PROGRESS_MIN_MS 2000
#define DAY_MS 86400000L

ulong g_lastProgressMs = 0;

//+------------------------------------------------------------------+
string JobDir()
  {
   return JOB_ROOT + JobId + "\\";
  }

//+------------------------------------------------------------------+
void JobLog(const string line)
  {
   string path = JobDir() + "job.log";
   int h = FileOpen(path, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
     {
      h = FileOpen(path, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
      if(h == INVALID_HANDLE)
        {
         Print("[dukascopy] JobLog open failed ", GetLastError(), ": ", line);
         return;
        }
     }
   else
      FileSeek(h, 0, SEEK_END);

   string stamp = TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS);
   FileWriteString(h, stamp + " " + line + "\n");
   FileClose(h);
   Print("[dukascopy] ", line);
  }

//+------------------------------------------------------------------+
void LogSymbolState(const string symbol, const string label)
  {
   bool custom = false;
   bool exists = SymbolExist(symbol, custom);
   long selected = exists ? SymbolInfoInteger(symbol, SYMBOL_SELECT) : -1;
   long visible = exists ? SymbolInfoInteger(symbol, SYMBOL_VISIBLE) : -1;
   string symPath = exists ? SymbolInfoString(symbol, SYMBOL_PATH) : "";

   MqlTick ticks[];
   int tickCopy = exists ? CopyTicks(symbol, ticks, COPY_TICKS_ALL, 1, 1) : -1;
   MqlRates rates[];
   int rateCopy = exists ? CopyRates(symbol, PERIOD_M1, 0, 1, rates) : -1;

   JobLog(label + " symbol=" + symbol
          + " exist=" + (exists ? "1" : "0")
          + " custom=" + (custom ? "1" : "0")
          + " select=" + IntegerToString(selected)
          + " visible=" + IntegerToString(visible)
          + " path=" + symPath
          + " copy_ticks=" + IntegerToString(tickCopy)
          + " copy_rates_m1=" + IntegerToString(rateCopy));
  }

//+------------------------------------------------------------------+
bool WriteProgress(const string state, const string phase, const long ticksImported,
                   const long ticksTotal, const int percent, const string customSymbol,
                   const string message, const int errorCode = 0,
                   const int filesDone = -1, const int filesTotal = -1)
  {
   return WriteProgressThrottled(state, phase, ticksImported, ticksTotal, percent,
                                 customSymbol, message, errorCode, false,
                                 filesDone, filesTotal);
  }

//+------------------------------------------------------------------+
bool WriteProgressThrottled(const string state, const string phase, const long ticksImported,
                            const long ticksTotal, const int percent, const string customSymbol,
                            const string message, const int errorCode, const bool force,
                            const int filesDone = -1, const int filesTotal = -1)
  {
   if(!force && phase == "import_ticks")
     {
      ulong now = GetTickCount64();
      if(now - g_lastProgressMs < (ulong)PROGRESS_MIN_MS)
         return true;
      g_lastProgressMs = now;
     }
   string path = JobDir() + "progress.txt";
   int h = FileOpen(path, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
     {
      Print("WriteProgress FileOpen failed: ", GetLastError());
      return false;
     }
   string filesLine = "";
   if(filesDone >= 0)
      filesLine += "files_done=" + IntegerToString(filesDone) + "\n";
   if(filesTotal >= 0)
      filesLine += "files_total=" + IntegerToString(filesTotal) + "\n";
   FileWriteString(h,
                   "state=" + state + "\n"
                   + "phase=" + phase + "\n"
                   + "ticks_imported=" + IntegerToString(ticksImported) + "\n"
                   + "ticks_total=" + IntegerToString(ticksTotal) + "\n"
                   + "percent=" + IntegerToString(percent) + "\n"
                   + "custom_symbol=" + customSymbol + "\n"
                   + "message=" + message + "\n"
                   + "error_code=" + IntegerToString(errorCode) + "\n"
                   + filesLine
                   + "script_version=1.19\n");
   FileClose(h);
   return true;
  }

//+------------------------------------------------------------------+
string ManifestValue(const string key)
  {
   string path = JobDir() + "manifest.txt";
   int h = FileOpen(path, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
      return "";
   string prefix = key + "=";
   while(!FileIsEnding(h))
     {
      string line = FileReadString(h);
      if(StringFind(line, prefix) == 0)
        {
         FileClose(h);
         return StringSubstr(line, StringLen(prefix));
        }
     }
   FileClose(h);
   return "";
  }

//+------------------------------------------------------------------+
long DayStartMsc(const long timeMsc)
  {
   return (timeMsc / DAY_MS) * DAY_MS;
  }

//+------------------------------------------------------------------+
void FillTickSlot(MqlTick &ticks[], const int index, const long time_msc,
                  const double bid, const double ask)
  {
   ticks[index].time = (datetime)(time_msc / 1000);
   ticks[index].time_msc = time_msc;
   ticks[index].bid = bid;
   ticks[index].ask = ask;
   ticks[index].last = 0;
   ticks[index].volume = 0;
   ticks[index].volume_real = 0.0;
   ticks[index].flags = TICK_FLAGS;
  }

//+------------------------------------------------------------------+
void FillTickRange(MqlTick &ticks[], const int offset,
                   const long &ts[], const double &bids[], const double &asks[],
                   const int from, const int to)
  {
   for(int i = from; i < to; i++)
      FillTickSlot(ticks, offset + (i - from), ts[i], bids[i], asks[i]);
  }

//+------------------------------------------------------------------+
bool AppendTickRange(const string customSymbol, MqlTick &dayTicks[], int &dayCount,
                     const int chunkMax, long &currentDay,
                     const long &ts[], const double &bids[], const double &asks[],
                     const int from, const int to,
                     long &ticksImported, const long ticksTotal)
  {
   int pos = from;
   while(pos < to)
     {
      long dayKey = DayStartMsc(ts[pos]);
      if(currentDay >= 0 && dayKey != currentDay)
        {
         if(!FlushDayTicks(customSymbol, dayTicks, dayCount, ticksImported, ticksTotal))
            return false;
         dayCount = 0;
        }
      currentDay = dayKey;

      int space = chunkMax - dayCount;
      if(space <= 0)
        {
         if(!FlushDayTicks(customSymbol, dayTicks, dayCount, ticksImported, ticksTotal))
            return false;
         dayCount = 0;
         space = chunkMax;
        }

      int end = pos + 1;
      while(end < to && DayStartMsc(ts[end]) == dayKey && (end - pos) < space)
         end++;

      FillTickRange(dayTicks, dayCount, ts, bids, asks, pos, end);
      dayCount += (end - pos);
      pos = end;

      if(dayCount >= chunkMax)
        {
         if(!FlushDayTicks(customSymbol, dayTicks, dayCount, ticksImported, ticksTotal))
            return false;
         dayCount = 0;
        }
     }
   return true;
  }

//+------------------------------------------------------------------+
bool QueueTick(const string customSymbol, const MqlTick &tick,
               MqlTick &dayTicks[], int &dayCount, const int chunkMax,
               long &currentDay, long &ticksImported, const long ticksTotal)
  {
   long dayKey = DayStartMsc(tick.time_msc);
   if(currentDay >= 0 && dayKey != currentDay)
     {
      if(!FlushDayTicks(customSymbol, dayTicks, dayCount, ticksImported, ticksTotal))
         return false;
      dayCount = 0;
     }
   currentDay = dayKey;

   if(dayCount >= chunkMax)
     {
      if(!FlushDayTicks(customSymbol, dayTicks, dayCount, ticksImported, ticksTotal))
         return false;
      dayCount = 0;
     }

   dayTicks[dayCount++] = tick;
   return true;
  }

//+------------------------------------------------------------------+
bool ParseCsvTick(const string line, MqlTick &tick)
  {
   if(StringLen(line) < 10)
      return false;
   if(StringGetCharacter(line, 0) == '<')
      return false;

   string parts[];
   int n = StringSplit(line, '\t', parts);
   if(n < 4)
      return false;

   string timePart = parts[1];
   int dot = StringFind(timePart, ".");
   int ms = 0;
   string timeBase = timePart;
   if(dot >= 0)
     {
      ms = (int)StringToInteger(StringSubstr(timePart, dot + 1));
      timeBase = StringSubstr(timePart, 0, dot);
     }

   datetime t = StringToTime(parts[0] + " " + timeBase);
   if(t <= 0)
      return false;

   tick.time = t;
   tick.time_msc = (long)t * 1000 + ms;
   tick.bid = StringToDouble(parts[2]);
   tick.ask = StringToDouble(parts[3]);
   tick.last = 0;
   tick.volume = 0;
   tick.volume_real = 0.0;
   tick.flags = TICK_FLAGS;
   return true;
  }

//+------------------------------------------------------------------+
bool FlushDayTicks(const string customSymbol, MqlTick &ticks[], const int count,
                   long &ticksImported, const long ticksTotal)
  {
   if(count <= 0)
      return true;

   long fromMsc = ticks[0].time_msc;
   long toMsc = ticks[count - 1].time_msc;
   ResetLastError();
   int replaced = CustomTicksReplace(customSymbol, fromMsc, toMsc, ticks, (uint)count);
   if(replaced < 0 || _LastError != 0)
     {
      Print("CustomTicksReplace failed: ", _LastError, " at ", TimeToString((datetime)(fromMsc / 1000)));
      return false;
     }

   ticksImported += count;
   return true;
  }

//+------------------------------------------------------------------+
bool ImportTicksFromCsv(const string customSymbol, const string tickPath,
                        const long ticksTotal, long &ticksImported)
  {
   int h = FileOpen(tickPath, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
     {
      Print("tick CSV open failed: ", GetLastError());
      return false;
     }

   MqlTick dayTicks[];
   int dayCount = 0;
   long currentDay = -1;
   const int chunkMax = CHUNK_MAX;
   ArrayResize(dayTicks, chunkMax);

   while(!FileIsEnding(h))
     {
      string line = FileReadString(h);
      if(StringLen(line) < 5)
         continue;

      MqlTick tick;
      if(!ParseCsvTick(line, tick))
         continue;

      if(!QueueTick(customSymbol, tick, dayTicks, dayCount, chunkMax,
                    currentDay, ticksImported, ticksTotal))
        {
         FileClose(h);
         return false;
        }
     }

   FileClose(h);

   if(dayCount > 0)
     {
      if(!FlushDayTicks(customSymbol, dayTicks, dayCount, ticksImported, ticksTotal))
         return false;
     }

   return true;
  }

//+------------------------------------------------------------------+
bool ImportBinaryStream(const int h, const string customSymbol,
                        MqlTick &dayTicks[], int &dayCount, long &currentDay,
                        const int chunkMax, long &ticksImported, const long ticksTotal)
  {
   while(!FileIsEnding(h))
     {
      int count = FileReadInteger(h, INT_VALUE);
      if(count <= 0)
         break;

      long ts[];
      double bids[];
      double asks[];
      ArrayResize(ts, count);
      ArrayResize(bids, count);
      ArrayResize(asks, count);

      if(FileReadArray(h, ts, 0, count) != (uint)count)
         return false;
      if(FileReadArray(h, bids, 0, count) != (uint)count)
         return false;
      if(FileReadArray(h, asks, 0, count) != (uint)count)
         return false;

      if(!AppendTickRange(customSymbol, dayTicks, dayCount, chunkMax, currentDay,
                          ts, bids, asks, 0, count,
                          ticksImported, ticksTotal))
         return false;
     }
   return true;
  }

//+------------------------------------------------------------------+
bool ImportTicksFromBinary(const string customSymbol, const string tickPath,
                           const long ticksTotal, long &ticksImported)
  {
   int h = FileOpen(tickPath, FILE_READ | FILE_BIN | FILE_COMMON);
   if(h == INVALID_HANDLE)
     {
      Print("tick binary open failed: ", GetLastError());
      return false;
     }

   MqlTick dayTicks[];
   int dayCount = 0;
   long currentDay = -1;
   const int chunkMax = CHUNK_MAX;
   ArrayResize(dayTicks, chunkMax);

   bool ok = ImportBinaryStream(h, customSymbol, dayTicks, dayCount, currentDay,
                                chunkMax, ticksImported, ticksTotal);
   FileClose(h);

   if(!ok)
      return false;

   if(dayCount > 0)
     {
      if(!FlushDayTicks(customSymbol, dayTicks, dayCount, ticksImported, ticksTotal))
         return false;
     }

   return true;
  }

//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
int CountHourFiles(const string hoursPath)
  {
   int total = 0;
   int h = FileOpen(hoursPath, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
      return 0;
   while(!FileIsEnding(h))
     {
      string line = FileReadString(h);
      StringTrimLeft(line);
      StringTrimRight(line);
      if(StringLen(line) >= 4)
         total++;
     }
   FileClose(h);
   return total;
  }

//+------------------------------------------------------------------+
bool ImportTicksFromHourFiles(const string customSymbol, const string hoursPath,
                              const long ticksTotal, long &ticksImported)
  {
   int list = FileOpen(hoursPath, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(list == INVALID_HANDLE)
     {
      Print("hours manifest open failed: ", GetLastError());
      return false;
     }

   string hoursTotalStr = ManifestValue("hours_total");
   int fileTotal = (hoursTotalStr != "") ? (int)StringToInteger(hoursTotalStr) : 0;
   if(fileTotal <= 0)
      fileTotal = CountHourFiles(hoursPath);

   MqlTick dayTicks[];
   int dayCount = 0;
   long currentDay = -1;
   const int chunkMax = CHUNK_MAX;
   ArrayResize(dayTicks, chunkMax);

   string jobDir = JobDir();
   int fileDone = 0;
   FileSeek(list, 0, SEEK_SET);
   while(!FileIsEnding(list))
     {
      string line = FileReadString(list);
      StringTrimLeft(line);
      StringTrimRight(line);
      if(StringLen(line) < 4)
         continue;

      string hourPath = jobDir + line;
      int h = FileOpen(hourPath, FILE_READ | FILE_BIN | FILE_COMMON);
      if(h == INVALID_HANDLE)
        {
         Print("hour file open failed: ", hourPath, " err=", GetLastError());
         FileClose(list);
         return false;
        }

      if(!ImportBinaryStream(h, customSymbol, dayTicks, dayCount, currentDay,
                             chunkMax, ticksImported, ticksTotal))
        {
         FileClose(h);
         FileClose(list);
         return false;
        }
      FileClose(h);

      fileDone++;
      int pct = fileTotal > 0 ? 10 + (int)((fileDone * 80L) / fileTotal) : 50;
      WriteProgress("running", "import_ticks", ticksImported, ticksTotal, pct, customSymbol,
                    "Hour file " + IntegerToString(fileDone) + " / " + IntegerToString(fileTotal),
                    0, fileDone, fileTotal);
     }

   FileClose(list);

   if(dayCount > 0)
     {
      if(!FlushDayTicks(customSymbol, dayTicks, dayCount, ticksImported, ticksTotal))
         return false;
     }

   if(fileTotal > 0)
      WriteProgress("running", "import_ticks", ticksImported, ticksTotal, 89, customSymbol,
                    "Finalizing import…", 0, fileTotal, fileTotal);

   return true;
  }

//+------------------------------------------------------------------+
long CountSymbolTicks(const string symbol)
  {
   MqlTick ticks[];
   long total = 0;
   ulong fromMsc = 1;
   const uint batch = 1000000;
   while(true)
     {
      int n = CopyTicks(symbol, ticks, COPY_TICKS_ALL, fromMsc, batch);
      if(n <= 0)
         break;
      total += n;
      fromMsc = (ulong)ticks[n - 1].time_msc + 1;
      if((uint)n < batch)
         break;
     }
   return total;
  }

//+------------------------------------------------------------------+
bool ApplySymbolProperties(const string customSymbol, const int digits)
  {
   if(digits > 0)
     {
      ResetLastError();
      if(!CustomSymbolSetInteger(customSymbol, SYMBOL_DIGITS, digits))
         Print("SYMBOL_DIGITS failed for ", customSymbol, " err=", GetLastError());
     }
   CustomSymbolSetInteger(customSymbol, SYMBOL_TRADE_MODE, SYMBOL_TRADE_MODE_DISABLED);
   return true;
  }

//+------------------------------------------------------------------+
bool EndsWith(const string text, const string suffix)
  {
   int textLen = StringLen(text);
   int suffixLen = StringLen(suffix);
   if(suffixLen <= 0 || textLen < suffixLen)
      return false;
   return StringSubstr(text, textLen - suffixLen) == suffix;
  }

//+------------------------------------------------------------------+
bool GetSymbolTickSpan(const string symbol, long &firstMsc, long &lastMsc)
  {
   MqlTick firstTick[];
   int nFirst = CopyTicks(symbol, firstTick, COPY_TICKS_ALL, 1, 1);
   if(nFirst <= 0)
      return false;
   firstMsc = firstTick[0].time_msc;

   MqlTick probe[];
   ulong fromMsc = (ulong)firstMsc;
   long last = firstMsc;
   const uint batch = 1000000;
   while(true)
     {
      int n = CopyTicks(symbol, probe, COPY_TICKS_ALL, fromMsc, batch);
      if(n <= 0)
         break;
      last = probe[n - 1].time_msc;
      fromMsc = (ulong)last + 1;
      if((uint)n < batch)
         break;
     }
   lastMsc = last;
   return true;
  }

//+------------------------------------------------------------------+
bool MatchesFilter(const string symbol, const string customPath, const string suffix)
  {
   bool isCustom = false;
   if(!SymbolExist(symbol, isCustom) || !isCustom)
      return false;

   if(suffix != "" && !EndsWith(symbol, suffix))
      return false;

   if(customPath == "")
      return true;

   string path = SymbolInfoString(symbol, SYMBOL_PATH);
   if(path == customPath)
      return true;
   if(StringFind(path, customPath) == 0)
      return true;
   return false;
  }

//+------------------------------------------------------------------+
bool ListCustomSymbols(const string customPath, const string suffix)
  {
   string outPath = JobDir() + "symbols.txt";
   int out = FileOpen(outPath, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(out == INVALID_HANDLE)
     {
      WriteProgress("error", "list", 0, 0, 0, "", "Failed to open symbols.txt", GetLastError());
      return false;
     }

   int total = SymbolsTotal(false);
   int listed = 0;
   for(int i = 0; i < total; i++)
     {
      string sym = SymbolName(i, false);
      if(!MatchesFilter(sym, customPath, suffix))
         continue;

      long ticks = CountSymbolTicks(sym);
      long firstMsc = 0;
      long lastMsc = 0;
      if(ticks > 0)
         GetSymbolTickSpan(sym, firstMsc, lastMsc);

      FileWriteString(out, sym + "|" + IntegerToString(ticks)
                      + "|" + IntegerToString(firstMsc)
                      + "|" + IntegerToString(lastMsc) + "\n");
      listed++;
     }

   FileClose(out);
   WriteProgress("done", "list", 0, 0, 100, "",
                "Listed " + IntegerToString(listed) + " custom symbol(s)");
   return true;
  }

//+------------------------------------------------------------------+
int CloseChartsForSymbol(const string symbol)
  {
   int closed = 0;
   const int max_passes = 8;
   for(int pass = 0; pass < max_passes; pass++)
     {
      bool found = false;
      long chart_id = ChartFirst();
      while(chart_id >= 0)
        {
         long next = ChartNext(chart_id);
         if(ChartSymbol(chart_id) == symbol)
           {
            found = true;
            if(ChartClose(chart_id))
               closed++;
           }
         chart_id = next;
        }
      if(!found)
         break;
      Sleep(250);
     }
   return closed;
  }

//+------------------------------------------------------------------+
bool CustomSymbolStillExists(const string symbol)
  {
   bool custom = false;
   return SymbolExist(symbol, custom) && custom;
  }

//+------------------------------------------------------------------+
bool SymbolHistoryEmpty(const string symbol)
  {
   MqlTick ticks[];
   if(CopyTicks(symbol, ticks, COPY_TICKS_ALL, 1, 1) > 0)
      return false;

   MqlRates rates[];
   if(CopyRates(symbol, PERIOD_M1, 0, 1, rates) > 0)
      return false;

   return true;
  }

//+------------------------------------------------------------------+
void PurgeSymbolHistory(const string symbol)
  {
   ResetLastError();
   int rates = CustomRatesDelete(symbol, 0, LONG_MAX);
   int ratesErr = GetLastError();
   ResetLastError();
   int ticks = CustomTicksDelete(symbol, 0, LONG_MAX);
   int ticksErr = GetLastError();
   JobLog("Purge " + symbol + ": rates_deleted=" + IntegerToString(rates)
          + " rates_err=" + IntegerToString(ratesErr)
          + " ticks_deleted=" + IntegerToString(ticks)
          + " ticks_err=" + IntegerToString(ticksErr));
  }

//+------------------------------------------------------------------+
bool TryCustomSymbolDelete(const string symbol, int &lastErr)
  {
   ResetLastError();
   bool ok = CustomSymbolDelete(symbol);
   lastErr = GetLastError();
   JobLog("CustomSymbolDelete(" + symbol + ") => " + (ok ? "true" : "false")
          + " err=" + IntegerToString(lastErr));
   return ok;
  }

//+------------------------------------------------------------------+
bool ForceDeselectSymbol(const string symbol)
  {
   for(int round = 0; round < 30; round++)
     {
      CloseChartsForSymbol(symbol);

      int inMw = SymbolsTotal(true);
      for(int i = inMw - 1; i >= 0; i--)
        {
         string sym = SymbolName(i, true);
         if(sym == symbol)
           {
            ResetLastError();
            if(!SymbolSelect(sym, false))
               JobLog("MW SymbolSelect(false) failed sym=" + sym
                      + " err=" + IntegerToString(GetLastError()));
           }
        }

      ResetLastError();
      bool deselected = SymbolSelect(symbol, false);
      int deselectErr = GetLastError();
      long stillSelected = SymbolInfoInteger(symbol, SYMBOL_SELECT);
      long stillVisible = SymbolInfoInteger(symbol, SYMBOL_VISIBLE);
      JobLog("Deselect round " + IntegerToString(round)
             + " call=" + (deselected ? "ok" : "fail")
             + " err=" + IntegerToString(deselectErr)
             + " select=" + IntegerToString(stillSelected)
             + " visible=" + IntegerToString(stillVisible));

      if(!stillSelected)
         return true;

      SymbolSelect(symbol, true);
      Sleep(100);
      SymbolSelect(symbol, false);
      Sleep(250);
      if(!SymbolInfoInteger(symbol, SYMBOL_SELECT))
         return true;
     }

   JobLog("ForceDeselectSymbol failed — still selected");
   return !SymbolInfoInteger(symbol, SYMBOL_SELECT);
  }

//+------------------------------------------------------------------+
bool DeleteCustomSymbol(const string symbol)
  {
   JobLog("DeleteCustomSymbol start v1.18 symbol=" + symbol);

   if(!CustomSymbolStillExists(symbol))
     {
      JobLog("Symbol already absent: " + symbol);
      WriteProgress("done", "delete", 0, 0, 100, symbol,
                    "Symbol already removed: " + symbol);
      return true;
     }

   LogSymbolState(symbol, "before_delete");

   if(!ForceDeselectSymbol(symbol))
     {
      JobLog("WARN: symbol still reports selected after ForceDeselectSymbol");
     }

   int err = 0;
   if(TryCustomSymbolDelete(symbol, err))
     {
      WriteProgress("done", "delete", 0, 0, 100, symbol, "Deleted " + symbol);
      return true;
     }

   const int max_attempts = 40;
   for(int attempt = 0; attempt < max_attempts; attempt++)
     {
      ForceDeselectSymbol(symbol);
      PurgeSymbolHistory(symbol);
      LogSymbolState(symbol, "retry_" + IntegerToString(attempt));

      if(TryCustomSymbolDelete(symbol, err))
        {
         WriteProgress("done", "delete", 0, 0, 100, symbol, "Deleted " + symbol);
         return true;
        }

      if(!CustomSymbolStillExists(symbol))
        {
         JobLog("Symbol gone after attempt " + IntegerToString(attempt));
         WriteProgress("done", "delete", 0, 0, 100, symbol, "Deleted " + symbol);
         return true;
        }

      if(SymbolHistoryEmpty(symbol))
        {
         JobLog("History empty after attempt " + IntegerToString(attempt)
                + " but CustomSymbolDelete still failed err=" + IntegerToString(err));
         break;
        }

      Sleep(500);
     }

   LogSymbolState(symbol, "final_failure");

   if(!CustomSymbolStillExists(symbol) || SymbolHistoryEmpty(symbol))
     {
      JobLog("Treating as success: symbol gone or history empty");
      WriteProgress("done", "delete", 0, 0, 100, symbol,
                    "Removed all history for " + symbol);
      return true;
     }

   string detail = "Could not delete " + symbol;
   if(err == 5306)
      detail += " — symbol still selected or chart open (MT5 error 5306)";
   else if(err != 0)
      detail += " (MT5 error " + IntegerToString(err) + ")";
   detail += " — see job.log in dukascopy_jobs folder";
   JobLog("FAILED: " + detail);
   WriteProgress("error", "delete", 0, 0, 0, symbol, detail, err);
   return false;
  }

//+------------------------------------------------------------------+
long CountCsvTicks(const string tickPath)
  {
   int h = FileOpen(tickPath, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
      return 0;

   long count = 0;
   while(!FileIsEnding(h))
     {
      string line = FileReadString(h);
      if(StringLen(line) >= 10 && StringGetCharacter(line, 0) != '<')
         count++;
     }
   FileClose(h);
   return count;
  }

//+------------------------------------------------------------------+
void OnStart()
  {
   if(StringLen(JobId) < 8)
     {
      Print("JobId input is required");
      WriteProgress("error", "config", 0, 0, 0, "", "JobId input is required", 1);
      return;
     }

   if(MQLInfoInteger(MQL_TESTER))
     {
      WriteProgress("error", "create_symbol", 0, 0, 0, "",
                    "Custom symbol import must run in live terminal, not Strategy Tester", 4014);
      return;
     }

   JobLog("OnStart v1.18 JobId=" + JobId + " chart=" + _Symbol);

   string action = ManifestValue("action");
   if(action == "list")
     {
      string listPath = ManifestValue("custom_path");
      if(listPath == "")
         listPath = "dukascopy";
      string suffix = ManifestValue("suffix");
      WriteProgress("running", "list", 0, 0, 0, "", "Listing custom symbols...");
      ListCustomSymbols(listPath, suffix);
      return;
     }

   if(action == "delete")
     {
      string deleteSymbol = ManifestValue("symbol");
      if(deleteSymbol == "")
        {
         JobLog("delete aborted: symbol missing from manifest");
         WriteProgress("error", "delete", 0, 0, 0, "", "symbol missing from manifest", 0);
         return;
        }
      JobLog("delete action for " + deleteSymbol);
      WriteProgress("running", "delete", 0, 0, 0, deleteSymbol, "Deleting custom symbol...");
      DeleteCustomSymbol(deleteSymbol);
      return;
     }

   string customSymbol = ManifestValue("custom_symbol");
   string customPath = ManifestValue("custom_path");
   string originSymbol = ManifestValue("origin_symbol");
   string replaceExisting = ManifestValue("replace_existing");

   if(customSymbol == "" || originSymbol == "")
     {
      WriteProgress("error", "read_manifest", 0, 0, 0, "", "manifest.txt missing or invalid", GetLastError());
      return;
     }

   if(customPath == "")
      customPath = "dukascopy";

   WriteProgress("running", "prepare", 0, 0, 0, customSymbol, "Preparing import...");

   bool newSymbol = false;
   bool custom = false;
   if(SymbolExist(customSymbol, custom) && custom)
     {
      if(replaceExisting == "1")
        {
         CustomTicksDelete(customSymbol, 0, LONG_MAX);
         CustomRatesDelete(customSymbol, 0, LONG_MAX);
        }
     }
   else
     {
      if(!CustomSymbolCreate(customSymbol, customPath, originSymbol))
        {
         WriteProgress("error", "create_symbol", 0, 0, 0, customSymbol,
                       "CustomSymbolCreate failed", GetLastError());
         return;
        }
      newSymbol = true;
     }

   SymbolSelect(customSymbol, true);

   string digitsStr = ManifestValue("digits");
   int digits = (digitsStr != "") ? (int)StringToInteger(digitsStr) : 0;
   if(newSymbol && digits > 0)
      ApplySymbolProperties(customSymbol, digits);

   string tickFormat = ManifestValue("tick_format");
   string tickMode = ManifestValue("tick_mode");
   string hoursFile = ManifestValue("hours_file");
   if(hoursFile == "")
      hoursFile = "hours.txt";
   string tickFile = ManifestValue("tick_file");
   if(tickFile == "")
      tickFile = (tickFormat == "bin_v1") ? "ticks.bin" : "ticks.csv";
   string tickPath = JobDir() + tickFile;
   string hoursPath = JobDir() + hoursFile;

   string ticksTotalStr = ManifestValue("ticks_total");
   long ticksTotal = (ticksTotalStr != "") ? (long)StringToInteger(ticksTotalStr) : 0;

   // Hours mode: do not scan/count files — just pump. ticks_total is optional UI hint.
   if(ticksTotal <= 0 && tickMode != "hours")
     {
      if(tickFormat == "bin_v1")
        {
         WriteProgress("error", "import", 0, 0, 0, customSymbol,
                       "ticks_total missing from manifest", 0);
         return;
        }
      ticksTotal = CountCsvTicks(tickPath);
      if(ticksTotal <= 0)
        {
         WriteProgress("error", "import", 0, 0, 0, customSymbol, "No ticks in staging file", 0);
         return;
        }
     }

   WriteProgress("running", "import", 0, ticksTotal, 5, customSymbol, "Importing ticks...");

   long ticksImported = 0;
   bool ok = false;
   if(tickMode == "hours" && tickFormat == "bin_v1")
      ok = ImportTicksFromHourFiles(customSymbol, hoursPath, ticksTotal, ticksImported);
   else if(tickFormat == "bin_v1")
      ok = ImportTicksFromBinary(customSymbol, tickPath, ticksTotal, ticksImported);
   else
      ok = ImportTicksFromCsv(customSymbol, tickPath, ticksTotal, ticksImported);

   if(!ok)
     {
      WriteProgress("error", "import_ticks", ticksImported, ticksTotal, 0, customSymbol,
                    "Tick import failed", GetLastError());
      return;
     }

   string doneMsg = "Import complete - " + IntegerToString(ticksImported) + " ticks";
   if(ticksTotal > 0 && ticksImported != ticksTotal)
      doneMsg = "Import complete - " + IntegerToString(ticksImported)
                + " source ticks (" + IntegerToString(ticksTotal) + " in files)";

   WriteProgress("ticks_done", "import_ticks", ticksImported, ticksTotal, 90, customSymbol, doneMsg);
   Print("DukascopyTickImport ticks done: ", customSymbol, " ticks=", ticksImported, " file_total=", ticksTotal);
  }
