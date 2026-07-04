import { useCallback, useEffect, useState } from "react";
import { fetchModels, updateSessionConfig } from "@/api/agent";
import { createLogger } from "@/lib/logger";
import type { ModelOption, SessionConfig } from "@/types/app";

const log = createLogger("Models");

const DEFAULT_CONFIG: SessionConfig = {
  model_key: "deepseek_v4_flash",
  enable_search: true,
  enable_reasoning: true,
};

/**
 * 可用模型列表 + 当前会话配置。
 * config 由 useChatStream 读取（initAgent 需要），setConfig 持久化到后端。
 */
export function useModels(sessionId: string) {
  const [availableModels, setAvailableModels] = useState<ModelOption[]>([]);
  const [config, setConfigState] = useState<SessionConfig>(DEFAULT_CONFIG);

  useEffect(() => {
    let active = true;

    const loadModels = async () => {
      try {
        const modelsRes = await fetchModels();
        if (!active) return;
        const models = modelsRes.models || [];
        setAvailableModels(models);
        const defaultModel = models.find((m) => m.is_default) || models[0];
        if (defaultModel) {
          setConfigState((prev) =>
            prev.model_key === defaultModel.key ? prev : { ...prev, model_key: defaultModel.key }
          );
        }
      } catch (err) {
        log.warn("fetchModels failed, using defaults", err);
      }
    };

    loadModels();

    // 用户切回浏览器标签页时自动刷新（修改 settings.json 后无需手动刷新页面）
    const onVisible = () => {
      if (document.visibilityState === "visible") {
        loadModels();
      }
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      active = false;
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  const setConfig = useCallback(
    async (nextConfig: SessionConfig) => {
      setConfigState(nextConfig);
      await updateSessionConfig(sessionId, nextConfig);
    },
    [sessionId]
  );

  return { availableModels, config, setConfig };
}
