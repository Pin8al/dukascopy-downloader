//+------------------------------------------------------------------+
//| M30CacheWarmer.mq5 — export M30 bars to Common Files disk cache   |
//| Live chart: build cache. Tester: import cache (not used by tool). |
//+------------------------------------------------------------------+
#property copyright "Dukascopy Downloader"
#property version   "1.00"
#property strict
#property description "M30 disk cache: export on live chart, import in Strategy Tester"

input group "M30 cache";
input string InpCacheSubfolder = "M30Cache"; // Under Terminal\\Common\\Files
input bool   InpForceRebuild     = false;    // Live only: overwrite existing cache
input string InpJobId              = "";       // Optional: write dukascopy job progress

#define JOB_ROOT "dukascopy_jobs\\"

static const int CACHE_FORMAT_VERSION = 1;

//+------------------------------------------------------------------+
string JobProgressPath()
  {
   return JOB_ROOT + InpJobId + "\\progress.txt";
  }

//+------------------------------------------------------------------+
void WriteJobProgress(const string state, const string phase, const int percent,
                      const string message, const int errorCode = 0)
  {
   if(StringLen(InpJobId) < 8)
      return;

   int h = FileOpen(JobProgressPath(), FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
      return;

   FileWriteString(h,
                   "state=" + state + "\n"
                   + "phase=" + phase + "\n"
                   + "ticks_imported=0\n"
                   + "ticks_total=0\n"
                   + "percent=" + IntegerToString(percent) + "\n"
                   + "custom_symbol=" + _Symbol + "\n"
                   + "message=" + message + "\n"
                   + "error_code=" + IntegerToString(errorCode) + "\n"
                   + "script_version=M30CacheWarmer-1.00\n");
   FileClose(h);
  }

//+------------------------------------------------------------------+
string SanitizeSymbolForFileName(const string symbol)
  {
   string safe = symbol;
   StringReplace(safe, ".", "_");
   StringReplace(safe, ":", "_");
   StringReplace(safe, "\\", "_");
   StringReplace(safe, "/", "_");
   return safe;
  }

//+------------------------------------------------------------------+
string BuildCacheFileName()
  {
   return InpCacheSubfolder + "\\M30_" + SanitizeSymbolForFileName(_Symbol) + ".bin";
  }

//+------------------------------------------------------------------+
string BuildCacheFullPathHint()
  {
   return "Terminal\\Common\\Files\\" + BuildCacheFileName();
  }

//+------------------------------------------------------------------+
bool CacheFileExists()
  {
   return FileIsExist(BuildCacheFileName(), FILE_COMMON);
  }

//+------------------------------------------------------------------+
bool WriteLengthPrefixedString(const int handle, const string text)
  {
   uchar bytes[];
   const int len = StringToCharArray(text, bytes, 0, WHOLE_ARRAY, CP_UTF8) - 1;
   if(len < 0)
     {
      FileWriteInteger(handle, 0);
      return true;
     }

   FileWriteInteger(handle, len);
   if(len == 0)
      return true;

   return (FileWriteArray(handle, bytes, 0, len) == (uint)len);
  }

//+------------------------------------------------------------------+
bool ReadLengthPrefixedString(const int handle, string &text)
  {
   text = "";
   const int len = FileReadInteger(handle);
   if(len < 0)
      return false;
   if(len == 0)
      return true;

   uchar bytes[];
   ArrayResize(bytes, len);
   if(FileReadArray(handle, bytes, 0, len) != (uint)len)
      return false;

   text = CharArrayToString(bytes, 0, len, CP_UTF8);
   return true;
  }

//+------------------------------------------------------------------+
bool WriteM30Cache(const MqlRates &rates[], const int count)
  {
   const string fileName = BuildCacheFileName();
   const int handle = FileOpen(fileName, FILE_WRITE | FILE_BIN | FILE_COMMON);
   if(handle == INVALID_HANDLE)
     {
      Print("M30 cache export failed | FileOpen err=", GetLastError(), " | path=", BuildCacheFullPathHint());
      return false;
     }

   FileWriteInteger(handle, CACHE_FORMAT_VERSION);
   if(!WriteLengthPrefixedString(handle, _Symbol))
     {
      FileClose(handle);
      return false;
     }
   FileWriteInteger(handle, count);
   if(FileWriteArray(handle, rates, 0, count) != (uint)count)
     {
      FileClose(handle);
      Print("M30 cache export failed | FileWriteArray err=", GetLastError());
      return false;
     }
   FileClose(handle);

   Print("M30 cache exported | bars=", count,
         " | symbol=", _Symbol,
         " | path=", BuildCacheFullPathHint());
   return true;
  }

//+------------------------------------------------------------------+
bool ReadM30Cache(MqlRates &rates[], int &count, string &symbol)
  {
   count = 0;
   symbol = "";
   ArrayResize(rates, 0);

   const string fileName = BuildCacheFileName();
   if(!FileIsExist(fileName, FILE_COMMON))
      return false;

   const int handle = FileOpen(fileName, FILE_READ | FILE_BIN | FILE_COMMON);
   if(handle == INVALID_HANDLE)
     {
      Print("M30 cache import failed | FileOpen err=", GetLastError(), " | path=", BuildCacheFullPathHint());
      return false;
     }

   const int version = (int)FileReadInteger(handle);
   if(version != CACHE_FORMAT_VERSION)
     {
      FileClose(handle);
      Print("M30 cache import failed | unsupported version=", version);
      return false;
     }

   if(!ReadLengthPrefixedString(handle, symbol))
     {
      FileClose(handle);
      Print("M30 cache import failed | could not read symbol");
      return false;
     }

   count = (int)FileReadInteger(handle);
   if(count <= 0)
     {
      FileClose(handle);
      Print("M30 cache import failed | bar count is zero");
      return false;
     }

   ArrayResize(rates, count);
   const uint read = FileReadArray(handle, rates, 0, count);
   FileClose(handle);

   if((int)read != count)
     {
      Print("M30 cache import failed | expected ", count, " bars, read ", read);
      ArrayResize(rates, 0);
      count = 0;
      return false;
     }

   return true;
  }

//+------------------------------------------------------------------+
int CopyAllLiveM30(MqlRates &rates[])
  {
   ArrayResize(rates, 0);
   ArraySetAsSeries(rates, false);

   const int total = Bars(_Symbol, PERIOD_M30);
   if(total <= 0)
     {
      Print("M30 cache export failed | no M30 bars on chart for ", _Symbol);
      return 0;
     }

   ResetLastError();
   const int copied = CopyRates(_Symbol, PERIOD_M30, 0, total, rates);
   if(copied <= 0)
     {
      Print("M30 cache export failed | CopyRates err=", GetLastError(), " | iBars=", total);
      return 0;
     }

   return copied;
  }

//+------------------------------------------------------------------+
void LogImportedCacheSummary(const MqlRates &rates[], const int count, const string symbol)
  {
   Print("M30 cache imported | fileSymbol=", symbol,
         " | chartSymbol=", _Symbol,
         " | bars=", count);

   if(symbol != _Symbol)
      Print("M30 cache warning | file symbol does not match chart symbol");

   if(count > 0)
     {
      Print("M30 cache span | oldest=", TimeToString(rates[0].time, TIME_DATE | TIME_SECONDS),
            " | newest=", TimeToString(rates[count - 1].time, TIME_DATE | TIME_SECONDS));
     }
  }

//+------------------------------------------------------------------+
int RunLiveExport()
  {
   Print("M30 cache | live chart detected — export mode");

   if(CacheFileExists() && !InpForceRebuild)
     {
      Print("M30 cache already exists | path=", BuildCacheFullPathHint(),
            " | set InpForceRebuild=true to overwrite");
      WriteJobProgress("done", "warm_m30", 100, "M30 cache already exists");
      return INIT_SUCCEEDED;
     }

   MqlRates rates[];
   const int copied = CopyAllLiveM30(rates);
   if(copied <= 0)
     {
      WriteJobProgress("error", "warm_m30", 0, "M30 cache export failed — no M30 bars", GetLastError());
      return INIT_FAILED;
     }

   if(!WriteM30Cache(rates, copied))
     {
      WriteJobProgress("error", "warm_m30", 0, "M30 cache write failed", GetLastError());
      return INIT_FAILED;
     }

   WriteJobProgress("done", "warm_m30", 100,
                    "M30 cache ready — " + IntegerToString(copied) + " bars");
   Print("M30 cache | export complete — ", copied, " M30 bars");
   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
int RunTesterImport()
  {
   Print("M30 cache | Strategy Tester detected — import mode");

   if(!CacheFileExists())
     {
      Print("ERROR | M30 cache file not found: ", BuildCacheFullPathHint());
      Print("ERROR | Run this EA on a LIVE chart first to build the M30 cache, then re-run in the tester.");
      return INIT_FAILED;
     }

   MqlRates rates[];
   int count = 0;
   string symbol = "";
   if(!ReadM30Cache(rates, count, symbol))
      return INIT_FAILED;

   LogImportedCacheSummary(rates, count, symbol);
   Print("M30 cache | import complete — ", count, " M30 candles loaded from disk");
   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   if(!SymbolSelect(_Symbol, true))
     {
      Print("SymbolSelect failed for ", _Symbol, " err=", GetLastError());
      WriteJobProgress("error", "warm_m30", 0, "SymbolSelect failed", GetLastError());
      return INIT_FAILED;
     }

   int result;
   if(MQLInfoInteger(MQL_TESTER))
      result = RunTesterImport();
   else
      result = RunLiveExport();

   if(result == INIT_SUCCEEDED)
      SymbolSelect(_Symbol, false);

   return result;
  }

void OnTick()
  {
  }

void OnDeinit(const int reason)
  {
  }
