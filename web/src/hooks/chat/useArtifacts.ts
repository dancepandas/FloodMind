import { useCallback, useMemo, useState } from "react";
import { fetchFilePreview } from "@/api/agent";
import type { ChatMessage, FilePreview, GeneratedArtifact } from "@/types/app";

/**
 * 产物与文件预览：从消息流中派生去重的 artifact 列表，管理当前选中预览。
 * 会话切换时由聚合器（useAgentApp）调用 closePreview 清空，避免 effect 内同步 setState。
 */
export function useArtifacts(messages: ChatMessage[], sessionId: string) {
  const [selectedPreview, setSelectedPreview] = useState<FilePreview | null>(null);

  const allArtifacts = useMemo<GeneratedArtifact[]>(() => {
    const list: GeneratedArtifact[] = [];
    for (const msg of messages) {
      if (!msg.artifacts) continue;
      for (const artifact of msg.artifacts) {
        if (list.some((a) => a.filename === artifact.filename && a.download_url === artifact.download_url)) continue;
        list.push(artifact);
      }
    }
    return list;
  }, [messages]);

  const handlePreviewFile = useCallback(
    async (fileId: string) => {
      const preview = await fetchFilePreview(sessionId, fileId);
      setSelectedPreview(preview);
    },
    [sessionId]
  );

  const closePreview = useCallback(() => setSelectedPreview(null), []);

  return { allArtifacts, selectedPreview, handlePreviewFile, closePreview };
}
