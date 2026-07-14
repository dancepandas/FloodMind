import { applyStreamEvent, type StreamHandlers } from "@/features/chat/lib/stream-events";
import { createLogger } from "@/lib/logger";
import { resumeStreamRequest } from "@/api/agent";

const log = createLogger("SSE");

export const MAX_SSE_RETRIES = 10;

/**
 * SSE 事件处理句柄。与 StreamHandlers 形状完全对齐，唯一区别是
 * setIsContextCompressing 和 setTokenUsage 在此处为 required（SSE 读循环总是注入这两个 handler）。
 *
 * 继承自 StreamHandlers 以保证未来新增 handler 字段时编译期即发现缺口，
 * 避免两套独立类型在维护中漂移。
 */
export interface SseStreamHandlers extends StreamHandlers {
  setIsContextCompressing: NonNullable<StreamHandlers['setIsContextCompressing']>;
  setTokenUsage: NonNullable<StreamHandlers['setTokenUsage']>;
}

export interface ConsumeSseOptions {
  /** 每解析出一条事件回调（applyStreamEvent 派发前）。用于 stream_end / artifact 等专项日志。 */
  onEvent?: (data: Record<string, unknown>) => void;
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * 消费一条 SSE 流：逐 chunk 读取、按行切分、JSON 解析后派发给 applyStreamEvent。
 * 返回已处理的事件数（用于断点续传的 after_index）。
 *
 * 替换了原先散落在 handleSubmit / handleQuickSubmit / 重连 / session-init 中 6 处几乎相同的读循环。
 *
 * **注意**：网络/读取异常时会抛出 Error，其上附 .eventCount 字段——
 * 调用方应从 err.eventCount 取已处理事件数作为 after_index，避免回归为 0 导致全量重发。
 */
export async function consumeSseStream(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  handlers: SseStreamHandlers,
  options: ConsumeSseOptions = {},
): Promise<number> {
  const { onEvent } = options;
  const decoder = new TextDecoder();
  let buffer = "";
  let eventCount = 0;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try {
          const data = JSON.parse(trimmed) as Record<string, unknown>;
          eventCount++;
          onEvent?.(data);
          applyStreamEvent(data, handlers);
        } catch (parseErr) {
          log.warn("SSE JSON parse error, line=", trimmed.slice(0, 200), parseErr);
        }
      }
    }
    return eventCount;
  } catch (err) {
    // 异常前 eventCount 已持有正确的最后索引；附加到 Error 上供调用方恢复。
    const wrapped = err instanceof Error ? err : new Error(String(err));
    (wrapped as Error & { eventCount: number }).eventCount = eventCount;
    throw wrapped;
  }
}

export interface ResumeOptions {
  maxRetries?: number;
  /** 拿到 reader 时回调（用于把 reader 存到 ref 以便取消/暂停）。 */
  onReader?: (reader: ReadableStreamDefaultReader<Uint8Array>) => void;
  onEvent?: (data: Record<string, unknown>) => void;
}

/**
 * 断线重连：指数退避重试 resumeStreamRequest，成功后消费返回的 SSE 流。
 * 返回是否成功恢复（true 表示已读到一条流并正常结束）。
 *
 * 行为等价于原 handleSubmit/handleQuickSubmit 末尾的重试块（每次先 sleep 再尝试）。
 */
export async function resumeStreamWithBackoff(
  sessionId: string,
  lastEventIndex: number,
  handlers: SseStreamHandlers,
  options: ResumeOptions = {},
): Promise<boolean> {
  const { maxRetries = MAX_SSE_RETRIES, onReader, onEvent } = options;
  let retries = 0;
  while (retries < maxRetries) {
    try {
      await sleep(Math.min(1000 * Math.pow(2, retries), 30000));
      const response = await resumeStreamRequest(sessionId, lastEventIndex);
      if (response.ok && response.body) {
        const reader = response.body.getReader();
        onReader?.(reader);
        await consumeSseStream(reader, handlers, { onEvent });
        log.info(`resume succeeded after ${retries} attempt(s)`);
        return true;
      }
      retries++;
    } catch (retryErr) {
      retries++;
      log.warn(`resume attempt ${retries}/${maxRetries} failed`, retryErr);
    }
  }
  return false;
}
