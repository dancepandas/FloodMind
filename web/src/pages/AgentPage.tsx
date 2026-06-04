import { useState } from 'react';
import { ChatArea } from '@/features/chat/components/ChatArea';
import { FilePreviewPanel } from '@/features/chat/components/FilePreviewPanel';
import { Sidebar } from '@/features/sidebar/components/Sidebar';
import { ScheduledTasksPanel } from '@/features/scheduler/components/ScheduledTasksPanel';
import { useAgentApp } from '@/hooks/useAgentApp';
import { useIsMobile } from '@/hooks/use-mobile';
import { Sheet, SheetContent } from '@/components/ui/sheet';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Menu, Clock } from 'lucide-react';

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
  const [showScheduledTasks, setShowScheduledTasks] = useState(false);

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
            onClick={() => setShowScheduledTasks(true)}
            className="p-2 rounded-lg active:scale-90 transition-transform"
            style={{ color: 'var(--amber-500)' }}
          >
            <Clock size={20} strokeWidth={1.8} />
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
            files={uploadedFiles}
            workflow={workflow}
            onInputChange={setInputValue}
            onSubmit={handleSubmit}
            onPause={handlePauseResume}
            onUpload={handleUpload}
            onToggleThought={toggleThought}
            onUpdateAction={updateAction}
            onQuickSubmit={handleQuickSubmit}
            onConfigChange={setConfig}
            onPreviewFile={handlePreviewFile}
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
              onShowScheduledTasks={() => { setShowScheduledTasks(true); setMobileSidebarOpen(false); }}
            />
          </SheetContent>
        </Sheet>

        <Dialog open={showScheduledTasks} onOpenChange={setShowScheduledTasks}>
          <DialogContent className="sm:max-w-2xl max-h-[85vh] flex flex-col">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <Clock size={18} style={{ color: 'var(--amber-500)' }} strokeWidth={1.8} />
                <span>定时任务</span>
              </DialogTitle>
            </DialogHeader>
            <div className="flex-1 min-h-0 overflow-y-auto">
              <ScheduledTasksPanel />
            </div>
          </DialogContent>
        </Dialog>
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
          onShowScheduledTasks={() => setShowScheduledTasks(true)}
        />
        <ChatArea
          messages={messages}
          inputValue={inputValue}
          isStreaming={isStreaming}
          isReconnecting={isReconnecting}
          isPaused={runtimeState.isPaused}
          availableModels={availableModels}
          config={config}
          files={uploadedFiles}
          workflow={workflow}
          onInputChange={setInputValue}
          onSubmit={handleSubmit}
          onPause={handlePauseResume}
          onUpload={handleUpload}
          onToggleThought={toggleThought}
          onUpdateAction={updateAction}
          onQuickSubmit={handleQuickSubmit}
          onConfigChange={setConfig}
          onPreviewFile={handlePreviewFile}
          pendingPermissionAsk={pendingPermissionAsk}
          onRespondPermissionAsk={handleRespondPermissionAsk}
        />

        {selectedPreview && (
          <div
            className="shrink-0 animate-slide-in-right"
            style={{
              width: '45vw',
              minWidth: '480px',
              maxWidth: '60vw',
              height: '100%',
              borderLeft: '1px solid hsl(var(--border))',
            }}
          >
            <FilePreviewPanel preview={selectedPreview} onClose={closePreview} />
          </div>
        )}

        <Dialog open={showScheduledTasks} onOpenChange={setShowScheduledTasks}>
          <DialogContent className="sm:max-w-2xl max-h-[85vh] flex flex-col">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <Clock size={18} style={{ color: 'var(--amber-500)' }} strokeWidth={1.8} />
                <span>定时任务</span>
              </DialogTitle>
            </DialogHeader>
            <div className="flex-1 min-h-0 overflow-y-auto">
              <ScheduledTasksPanel />
            </div>
          </DialogContent>
        </Dialog>
      </div>
    </div>
  );
};

export default AgentPage;
