import { useState } from 'react';
import { ChatArea } from '@/features/chat/components/ChatArea';
import { ContextPanel } from '@/features/context/components/ContextPanel';
import { Sidebar } from '@/features/sidebar/components/Sidebar';
import { useAgentApp } from '@/hooks/useAgentApp';
import { useIsMobile } from '@/hooks/use-mobile';
import { Sheet, SheetContent } from '@/components/ui/sheet';
import { Drawer, DrawerContent, DrawerHeader, DrawerTitle } from '@/components/ui/drawer';
import { Menu, PanelRightClose } from 'lucide-react';

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
    isReconnecting,
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
    handleQuickSubmit,
    loadSession,
    toggleThought,
    updateAction,
    pendingPermissionAsk,
    handleRespondPermissionAsk,
    closePreview,
  } = useAgentApp();

  const isMobile = useIsMobile();
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [mobileContextOpen, setMobileContextOpen] = useState(false);

  if (isMobile) {
    return (
      <div className="min-h-screen flex flex-col" style={{ background: 'hsl(var(--background))', color: 'hsl(var(--foreground))' }}>
        <div
          className="flex items-center justify-between px-3 py-2 shrink-0 z-10"
          style={{ background: 'var(--sidebar-bg)', borderBottom: '1px solid var(--sidebar-border)' }}
        >
          <button
            onClick={() => setMobileSidebarOpen(true)}
            className="p-2 rounded-lg active:scale-90 transition-transform"
            style={{ color: 'hsl(var(--foreground))' }}
          >
            <Menu size={20} strokeWidth={1.8} />
          </button>
          <div className="flex items-center gap-2">
            <span className="font-semibold text-[14px] tracking-tight" style={{ fontFamily: 'var(--font-display)' }}>
              FloodMind
            </span>
          </div>
          <button
            onClick={() => setMobileContextOpen(true)}
            className="p-2 rounded-lg active:scale-90 transition-transform"
            style={{ color: 'hsl(var(--foreground))' }}
          >
            <PanelRightClose size={20} strokeWidth={1.8} />
          </button>
        </div>

        <div className="flex-1 flex flex-col min-h-0">
          <ChatArea
            messages={messages}
            inputValue={inputValue}
            isStreaming={isStreaming}
            isReconnecting={isReconnecting}
            isPaused={runtimeState.isPaused}
            availableModels={availableModels}
            config={config}
            onInputChange={setInputValue}
            onSubmit={handleSubmit}
            onPause={handlePauseResume}
            onUpload={handleUpload}
            onToggleThought={toggleThought}
            onUpdateAction={updateAction}
            onQuickSubmit={handleQuickSubmit}
            onConfigChange={setConfig}
            pendingPermissionAsk={pendingPermissionAsk}
            onRespondPermissionAsk={handleRespondPermissionAsk}
          />
        </div>

        <Sheet open={mobileSidebarOpen} onOpenChange={setMobileSidebarOpen}>
          <SheetContent side="left" className="!w-[280px] !max-w-[80vw] !p-0">
            <Sidebar
              sessions={sessions}
              activeSessionId={sessionId}
              onNewSession={() => { handleNewSession(); setMobileSidebarOpen(false); }}
              onSelectSession={(id) => { loadSession(id); setMobileSidebarOpen(false); }}
              onDeleteSession={handleDeleteSession}
            />
          </SheetContent>
        </Sheet>

        <Drawer open={mobileContextOpen} onOpenChange={setMobileContextOpen}>
          <DrawerContent className="!max-h-[70vh]">
            <DrawerHeader className="px-0 pt-2 pb-0">
              <DrawerTitle className="sr-only">上下文面板</DrawerTitle>
            </DrawerHeader>
            <div className="px-0 overflow-y-auto flex-1 min-h-0">
              <ContextPanel
                sessionId={sessionId}
                files={uploadedFiles}
                workflow={workflow}
                selectedPreview={selectedPreview}
                onPreviewFile={handlePreviewFile}
                onClosePreview={closePreview}
              />
            </div>
          </DrawerContent>
        </Drawer>
      </div>
    );
  }

  return (
    <div className="min-h-screen" style={{ background: 'hsl(var(--background))', color: 'hsl(var(--foreground))' }}>
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
          isReconnecting={isReconnecting}
          isPaused={runtimeState.isPaused}
          availableModels={availableModels}
          config={config}
          onInputChange={setInputValue}
          onSubmit={handleSubmit}
          onPause={handlePauseResume}
          onUpload={handleUpload}
          onToggleThought={toggleThought}
          onUpdateAction={updateAction}
          onQuickSubmit={handleQuickSubmit}
          onConfigChange={setConfig}
          pendingPermissionAsk={pendingPermissionAsk}
          onRespondPermissionAsk={handleRespondPermissionAsk}
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
