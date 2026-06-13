#!/usr/bin/env node
/**
 * Download historical ticks from Dukascopy's JETTA API.
 * Logic extracted from chunk-5LDCJFJT.js (Historical Data Feed widget).
 *
 * Usage:
 *   node download-ticks.js --instrument EUR-USD --from 2024-06-12 --to 2024-06-12
 *   node download-ticks.js -i eurusd -f 2024-06-12T10:00:00Z -t 2024-06-12T11:00:00Z
 *
 * Environment:
 *   JETTA_SERVER_URL  Base API URL (default: https://jetta.dukascopy.com)
 */

const PERIOD = {
  TICK: '1T',
  MINUTE: '1',
  HOUR: '1H',
  DAY: '1D',
};

const DEFAULT_BASE_URL = process.env.JETTA_SERVER_URL || 'https://jetta.dukascopy.com';

function usage() {
  console.error(`Usage: node download-ticks.js [options]

Options:
  -i, --instrument <code>   Instrument code (e.g. EUR-USD, eurusd, BTC-USD)
  -f, --from <datetime>     Start (ISO date or datetime, UTC if no offset)
  -t, --to <datetime>       End (inclusive-ish; parsed as UTC ms)
  -s, --side <bid|ask>      Price side (default: bid)
  -o, --output <file>       Output file (- for stdout; default: EURUSD_YYYY-MM-DD_YYYY-MM-DD.csv)
      --format <csv|json>   Output format: csv = MT5 tick CSV (default), json = raw ticks
      --timezone <tz>       Timezone for MT5 date/time columns (default: UTC)
      --base-url <url>      JETTA API base URL
      --parallel <n>        Concurrent hour requests (default: 4)
  -h, --help                Show this help

Examples:
  node download-ticks.js -i EUR-USD -f 2024-06-12 -t 2024-06-12
  node download-ticks.js -i XAU-USD -f "2024-06-12T08:00:00Z" -t "2024-06-12T09:00:00Z" --format json -o -
`);
}

function parseArgs(argv) {
  const args = {
    instrument: null,
    from: null,
    to: null,
    side: 'bid',
    output: null,
    format: 'csv',
    timezone: null,
    baseUrl: DEFAULT_BASE_URL,
    parallel: 4,
  };

  for (let i = 2; i < argv.length; i++) {
    const arg = argv[i];
    const next = argv[i + 1];
    switch (arg) {
      case '-h':
      case '--help':
        usage();
        process.exit(0);
      case '-i':
      case '--instrument':
        args.instrument = next;
        i++;
        break;
      case '-f':
      case '--from':
        args.from = next;
        i++;
        break;
      case '-t':
      case '--to':
        args.to = next;
        i++;
        break;
      case '-s':
      case '--side':
        args.side = next;
        i++;
        break;
      case '-o':
      case '--output':
        args.output = next;
        i++;
        break;
      case '--format':
        args.format = next;
        i++;
        break;
      case '--timezone':
        args.timezone = next;
        i++;
        break;
      case '--base-url':
        args.baseUrl = next;
        i++;
        break;
      case '--parallel':
        args.parallel = Number(next);
        i++;
        break;
      default:
        throw new Error(`Unknown argument: ${arg}`);
    }
  }

  if (!args.instrument || !args.from || !args.to) {
    usage();
    process.exit(1);
  }

  args.side = args.side.toUpperCase();
  if (args.side !== 'BID' && args.side !== 'ASK') {
    throw new Error('--side must be bid or ask');
  }
  if (args.format !== 'csv' && args.format !== 'json') {
    throw new Error('--format must be csv or json');
  }
  if (!Number.isFinite(args.parallel) || args.parallel < 1) {
    throw new Error('--parallel must be a positive number');
  }

  return args;
}

function normalizeInstrumentCode(code) {
  const trimmed = code.trim();
  if (trimmed.includes('-') || trimmed.includes('/')) {
    return trimmed.replace(/\//g, '-').toUpperCase();
  }
  const upper = trimmed.toUpperCase();
  if (upper.length === 6) {
    return `${upper.slice(0, 3)}-${upper.slice(3)}`;
  }
  return upper;
}

function parseDateInput(value, endOfDay = false) {
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const [y, m, d] = value.split('-').map(Number);
    if (endOfDay) {
      return Date.UTC(y, m - 1, d, 23, 59, 59, 999);
    }
    return Date.UTC(y, m - 1, d, 0, 0, 0, 0);
  }
  const ms = Date.parse(value);
  if (Number.isNaN(ms)) {
    throw new Error(`Invalid date: ${value}`);
  }
  return ms;
}

function pricePrecision(multiplier) {
  if (!multiplier) return 1;
  const exp = Math.floor(Math.log10(multiplier));
  return exp > 0 ? multiplier : 10 ** Math.abs(exp);
}

function applyDelta(base, delta, multiplier, precision) {
  return Math.round((base + delta * multiplier) * precision) / precision;
}

function parseTickChunk(json, chunk, side) {
  const times = json.times || [];
  const bids = json.bids || [];
  const asks = json.asks || [];
  const bidVolumes = json.bidVolumes || [];
  const askVolumes = json.askVolumes || [];

  if (!times.length) return [];
  const len = times.length;
  if (bids.length !== len || asks.length !== len || bidVolumes.length !== len || askVolumes.length !== len) {
    throw new Error('TICKS history is not consistent');
  }

  const multiplier = json.multiplier || 1;
  const precision = pricePrecision(multiplier);
  let time = json.timestamp || 0;
  let bid = json.bid || 0;
  let ask = json.ask || 0;
  const ticks = [];

  for (let i = 0; i < len; i++) {
    time += times[i];
    bid = applyDelta(bid, bids[i], multiplier, precision);
    ask = applyDelta(ask, asks[i], multiplier, precision);

    if (time >= chunk.from && time < chunk.till) {
      const price = side === 'BID' ? bid : ask;
      const volume = side === 'BID' ? bidVolumes[i] : askVolumes[i];
      ticks.push({
        time,
        bid,
        ask,
        bidVolume: bidVolumes[i],
        askVolume: askVolumes[i],
        price,
        volume,
      });
    }
  }

  return ticks;
}

function alignChunkStart(date, interval) {
  switch (interval) {
    case '1T':
      date.setUTCMinutes(0, 0, 0);
      break;
    case '1':
      date.setUTCHours(0, 0, 0, 0);
      break;
    case '1H':
      date.setUTCHours(0, 0, 0, 0);
      date.setUTCDate(1);
      break;
    case '1D':
      date.setUTCHours(0, 0, 0, 0);
      date.setUTCMonth(0, 1);
      break;
    default:
      throw new Error(`Unsupported interval: ${interval}`);
  }
}

function advanceChunk(date, interval) {
  switch (interval) {
    case '1T':
      date.setUTCHours(date.getUTCHours() + 1);
      break;
    case '1':
      date.setUTCDate(date.getUTCDate() + 1);
      break;
    case '1H':
      date.setUTCMonth(date.getUTCMonth() + 1);
      break;
    case '1D':
      date.setUTCFullYear(date.getUTCFullYear() + 1);
      break;
    default:
      throw new Error(`Unsupported interval: ${interval}`);
  }
}

function clampChunkRange(start, end, range) {
  if (start.getTime() <= range.from) start.setTime(range.from);
  if (end.getTime() >= range.till) end.setTime(range.till);
}

function buildTickPath(instrumentCode, chunkStart, nowMs) {
  const year = chunkStart.getUTCFullYear();
  const month = chunkStart.getUTCMonth() + 1;
  const day = chunkStart.getUTCDate();
  const hour = chunkStart.getUTCHours();

  if (nowMs <= chunkStart.getTime()) {
    return `/ticks/${instrumentCode}?from=${chunkStart.getTime()}`;
  }
  return `/ticks/${instrumentCode}/${year}/${month}/${day}/${hour}`;
}

function buildHourlyChunks(instrumentCode, range, nowMs, interval = '1T') {
  const chunks = [];
  const cursor = new Date(range.from);
  const rangeEnd = new Date(range.till);

  alignChunkStart(cursor, interval);
  alignChunkStart(rangeEnd, interval);
  advanceChunk(rangeEnd, interval);

  while (cursor < rangeEnd) {
    const chunkStart = new Date(cursor);
    const chunkEnd = new Date(cursor);
    advanceChunk(cursor, interval);
    advanceChunk(chunkEnd, interval);
    clampChunkRange(chunkStart, chunkEnd, range);

    if (chunkStart.getTime() === chunkEnd.getTime()) {
      continue;
    }

    chunks.push({
      path: buildTickPath(instrumentCode, chunkStart, nowMs),
      from: chunkStart.getTime(),
      till: chunkEnd.getTime(),
    });
  }

  return chunks;
}

function parseInstrument(raw) {
  const histories = {};
  for (const entry of raw.histories || []) {
    const code = PERIOD[entry.period];
    if (code != null && entry.from != null && !Number.isNaN(Number(entry.from))) {
      histories[code] = { from: Number(entry.from) };
    }
  }

  if (!histories['1T']) {
    throw new Error(`Instrument ${raw.code} has no tick history`);
  }

  return {
    code: raw.code,
    name: raw.name,
    histories,
    timeZone: raw.defaultTimezone || 'UTC',
    pipValue: raw.pipValue || 0.0001,
  };
}

class JettaClient {
  constructor(baseUrl) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.baseUrl = /\/v1$/.test(this.baseUrl) ? this.baseUrl : `${this.baseUrl}/v1`;
    this.cache = new Map();
  }

  async fetchJson(path, useCache = true) {
    const url = `${this.baseUrl}${path}`;
    if (useCache && !url.includes('?')) {
      const cached = this.cache.get(url);
      if (cached && Date.now() - cached.time < 10 * 60 * 1000) {
        return cached.json;
      }
    }

    const response = await fetch(url);
    let body;
    try {
      body = await response.json();
    } catch (error) {
      throw new Error(`Failed to parse JSON from ${url}: ${error.message}`);
    }

    if (!response.ok) {
      throw new Error(body.error || `${response.status} ${response.statusText} (${url})`);
    }

    if (useCache && !url.includes('?')) {
      this.cache.set(url, { time: Date.now(), json: body });
    }
    return body;
  }

  async instrument(code) {
    const normalized = normalizeInstrumentCode(code).replace(/\//g, '-');
    const raw = await this.fetchJson(`/instruments/${normalized}`);
    return parseInstrument(raw);
  }

  async downloadTicks(instrument, range, side, { parallel = 4, onProgress } = {}) {
    const now = Date.now();
    let from = range.from;
    let till = range.till;
    const tickHistory = instrument.histories['1T'];

    if (from > now) {
      throw new Error('Start date is in the future');
    }
    if (tickHistory.from > from) {
      from = tickHistory.from;
    }
    if (till > now) {
      till = now;
    }
    if (from >= till) {
      return [];
    }

    const chunks = buildHourlyChunks(instrument.code, { from, till }, now, '1T');
    const ticks = [];
    let completed = 0;

    for (let i = 0; i < chunks.length; i += parallel) {
      const batch = chunks.slice(i, i + parallel);
      const batchResults = await Promise.all(
        batch.map(async (chunk) => {
          const json = await this.fetchJson(chunk.path);
          return parseTickChunk(json, chunk, side);
        }),
      );

      for (const part of batchResults) {
        if (part.length) ticks.push(...part);
      }

      completed += batch.length;
      if (onProgress) {
        onProgress(completed, chunks.length);
      }
    }

    ticks.sort((a, b) => a.time - b.time);
    return ticks;
  }
}

function formatIso(ms) {
  return new Date(ms).toISOString();
}

const MT5_TAB = '\t';
const MT5_HEADER = ['<DATE>', '<TIME>', '<BID>', '<ASK>', '<LAST>', '<VOLUME>', '<FLAGS>'].join(MT5_TAB);

function mt5Symbol(code) {
  return code.replace(/-/g, '').toUpperCase();
}

function datePart(value) {
  const match = value.match(/^(\d{4}-\d{2}-\d{2})/);
  return match ? match[1] : value.slice(0, 10);
}

function defaultOutputPath(instrumentCode, fromValue, toValue) {
  return `${mt5Symbol(instrumentCode)}_${datePart(fromValue)}_${datePart(toValue)}.csv`;
}

function priceDigits(pipValue) {
  if (!pipValue || pipValue <= 0) return 5;
  return Math.max(2, Math.round(-Math.log10(pipValue)) + 1);
}

function formatMt5DateTime(ms, timeZone) {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).formatToParts(new Date(ms));

  const part = (type) => parts.find((entry) => entry.type === type)?.value ?? '00';
  const date = `${part('year')}.${part('month')}.${part('day')}`;
  const millis = String(ms % 1000).padStart(3, '0');
  const time = `${part('hour')}:${part('minute')}:${part('second')}.${millis}`;
  return { date, time };
}

function ticksToMt5Csv(ticks, { timeZone, digits }) {
  const lines = ticks.map((tick) => {
    const { date, time } = formatMt5DateTime(tick.time, timeZone);
    return [
      date,
      time,
      tick.bid.toFixed(digits),
      tick.ask.toFixed(digits),
      '0',
      '0',
      '6',
    ].join(MT5_TAB);
  });
  return [MT5_HEADER, ...lines].join('\n');
}

async function main() {
  const args = parseArgs(process.argv);
  const fromMs = parseDateInput(args.from, false);
  const toMs = parseDateInput(args.to, true);

  if (fromMs >= toMs) {
    throw new Error('--from must be before --to');
  }

  const client = new JettaClient(args.baseUrl);
  process.stderr.write(`Fetching instrument ${args.instrument}...\n`);
  const instrument = await client.instrument(args.instrument);

  process.stderr.write(
    `Downloading ticks for ${instrument.code} (${instrument.name}) ` +
      `${formatIso(fromMs)} -> ${formatIso(toMs)} [${args.side}]...\n`,
  );

  const ticks = await client.downloadTicks(
    instrument,
    { from: fromMs, till: toMs },
    args.side,
    {
      parallel: args.parallel,
      onProgress(done, total) {
        process.stderr.write(`\rProgress: ${done}/${total} hour chunks`);
      },
    },
  );
  process.stderr.write(`\nDownloaded ${ticks.length} ticks\n`);

  const outputPath =
    args.output ?? defaultOutputPath(instrument.code, args.from, args.to);
  const timeZone = args.timezone || 'UTC';

  let output;
  if (args.format === 'json') {
    output = JSON.stringify(ticks, null, 2);
  } else {
    output = ticksToMt5Csv(ticks, {
      timeZone,
      digits: priceDigits(instrument.pipValue),
    });
  }

  if (outputPath === '-') {
    process.stdout.write(output);
    if (!output.endsWith('\n')) process.stdout.write('\n');
  } else {
    const fs = await import('node:fs/promises');
    await fs.writeFile(outputPath, output.endsWith('\n') ? output : `${output}\n`, 'utf8');
    process.stderr.write(`Wrote ${outputPath}\n`);
  }
}

main().catch((error) => {
  console.error(`Error: ${error.message}`);
  process.exit(1);
});
