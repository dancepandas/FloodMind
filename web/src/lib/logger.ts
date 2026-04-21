type LogLevel = "debug" | "info" | "warn" | "error";

const LEVEL_ORDER: Record<LogLevel, number> = {
  debug: 0,
  info: 1,
  warn: 2,
  error: 3,
};

const IS_DEV = import.meta.env.DEV;
const MIN_LEVEL: LogLevel = IS_DEV ? "debug" : "info";

function shouldLog(level: LogLevel): boolean {
  return LEVEL_ORDER[level] >= LEVEL_ORDER[MIN_LEVEL];
}

function formatMsg(level: LogLevel, tag: string, args: unknown[]): unknown[] {
  const ts = new Date().toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    fractionalSecondDigits: 3,
  });
  return [`[${ts}] [${level.toUpperCase()}] [${tag}]`, ...args];
}

export function createLogger(tag: string) {
  return {
    debug: (...args: unknown[]) => {
      if (shouldLog("debug")) console.debug(...formatMsg("debug", tag, args));
    },
    info: (...args: unknown[]) => {
      if (shouldLog("info")) console.info(...formatMsg("info", tag, args));
    },
    warn: (...args: unknown[]) => {
      if (shouldLog("warn")) console.warn(...formatMsg("warn", tag, args));
    },
    error: (...args: unknown[]) => {
      if (shouldLog("error")) console.error(...formatMsg("error", tag, args));
    },
  };
}
