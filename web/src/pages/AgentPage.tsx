import { ChatArea } from '@/features/chat/components/ChatArea';
import { ContextPanel } from '@/features/context/components/ContextPanel';
import { Sidebar } from '@/features/sidebar/components/Sidebar';
import { useAgentApp } from '@/hooks/useAgentApp';

const AgentPage = () => {
  const {
    sessionId,
    sessions,
    messages,
    uploadedFiles,
    toolActivities,
    workflow,
    selectedPreview,
    runtimeState,
    inputValue,
    isStreaming,
    availableModels,
    config,
    setInputValue,
    setConfig,
    handleSubmit,
    handleUpload,
    handlePreviewFile,
    handlePauseResume,
    handleNewSession,
    handleDeleteSession,
    loadSession,
    toggleThought,
    closePreview,
  } = useAgentApp();

  return (
    <div className="min-h-screen bg-background text-foreground font-sans">
      <div className="w-full h-screen flex overflow-hidden relative">
        <Sidebar
          sessions={sessions}
          activeSessionId={sessionId}
          onNewSession={handleNewSession}
          onSelectSession={loadSession}
          onDeleteSession={handleDeleteSession}
        />
        <ChatArea
          messages={messages}
          inputValue={inputValue}
          isStreaming={isStreaming}
          isPaused={runtimeState.isPaused}
          availableModels={availableModels}
          config={config}
          onInputChange={setInputValue}
          onSubmit={handleSubmit}
          onPause={handlePauseResume}
          onUpload={handleUpload}
          onToggleThought={toggleThought}
          onConfigChange={setConfig}
        />
        <ContextPanel
          sessionId={sessionId}
          files={uploadedFiles}
          workflow={workflow}
          selectedPreview={selectedPreview}
          onPreviewFile={handlePreviewFile}
          onClosePreview={closePreview}
        />
      </div>
    </div>
  );
};

export default AgentPage;
