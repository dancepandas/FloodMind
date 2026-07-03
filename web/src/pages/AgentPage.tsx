import { useState, useMemo } from 'react';
import { ChatArea } from '@/features/chat/components/ChatArea';
import { FilePreviewPanel } from '@/features/chat/components/FilePreviewPanel';
import { Sidebar } from '@/features/sidebar/components/Sidebar';
import { ScheduledTasksPanel } from '@/features/scheduler/components/ScheduledTasksPanel';
import { RightPanel } from '@/features/context/components/RightPanel';
import { ChatInteractionProvider, type ChatInteractionValue } from '@/features/chat/ChatInteractionContext';
import { useAgentApp } from '@/hooks/useAgentApp';
import { useIsMobile } from '@/hooks/use-mobile';
import { Sheet, SheetContent } from '@/components/ui/sheet';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Menu, Clock } from 'lucide-react';

const AgentPage = () => {
  const app = useAgentApp();

  // 构建交互面（代替原先 20 个 props 透传）。
  // isPaused 派生自 runtimeState；onPause 映射自 handlePauseResume；onConfigChange 等同于 setConfig。
  const interactionValue = useMemo<ChatInteractionValue>(
    () => ({
      inputValue: app.inputValue,
      setInputValue: app.setInputValue,
      isStreaming: app.isStreaming,
      isReconnecting: app.isReconnecting,
      isPaused: app.runtimeState.isPaused,
      availableModels: app.availableModels,
      config: app.config,
      setConfig: app.setConfig,
      uploadedFiles: app.uploadedFiles,
      pendingFiles: app.pendingFiles,
      onRemovePendingFile: app.removePendingFile,
      workflow: app.workflow,
      onSubmit: app.handleSubmit,
      onQuickSubmit: app.handleQuickSubmit,
      onPause: app.handlePauseResume,
      onUpload: app.handleUpload,
      onPreviewFile: app.handlePreviewFile,
      pendingPermissionAsk: app.pendingPermissionAsk,
      onRespondPermissionAsk: app.handleRespondPermissionAsk,
    }),
    [
      app.inputValue, app.setInputValue, app.isStreaming, app.isReconnecting,
      app.runtimeState.isPaused, app.availableModels, app.config, app.setConfig,
      app.uploadedFiles, app.pendingFiles, app.removePendingFile, app.workflow, app.handleSubmit, app.handleQuickSubmit,
      app.handlePauseResume, app.handleUpload, app.handlePreviewFile,
      app.pendingPermissionAsk, app.handleRespondPermissionAsk,
    ],
  );

  const isMobile = useIsMobile();
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [showScheduledTasks, setShowScheduledTasks] = useState(false);

  if (isMobile) {
    return (
      <div className="min-h-screen flex flex-col" style={{ background: 'var(--bg)', color: 'var(--text-primary)' }}>
        <div
          className="flex items-center justify-between px-3 py-2 shrink-0 z-10"
          style={{ background: 'var(--surface)', borderBottom: '1px solid var(--border)' }}
        >
          <button
            onClick={() => setMobileSidebarOpen(true)}
            className="p-2 rounded-lg active:scale-90 transition-transform"
            style={{ color: 'var(--text-primary)' }}
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
            style={{ color: 'var(--sand)' }}
          >
            <Clock size={20} strokeWidth={1.8} />
          </button>
        </div>

        <div className="flex-1 flex flex-col min-h-0">
          <ChatInteractionProvider value={interactionValue}>
            <ChatArea messages={app.messages} onToggleThought={app.toggleThought} />
          </ChatInteractionProvider>
        </div>

        <Sheet open={mobileSidebarOpen} onOpenChange={setMobileSidebarOpen}>
          <SheetContent side="left" className="!w-[280px] !max-w-[80vw] !p-0">
            <Sidebar
              sessions={app.sessions}
              activeSessionId={app.sessionId}
              onNewSession={() => { app.handleNewSession(); setMobileSidebarOpen(false); }}
              onSelectSession={(id) => { app.loadSession(id); setMobileSidebarOpen(false); }}
              onDeleteSession={app.handleDeleteSession}
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
    <div className="min-h-screen" style={{ background: 'var(--bg)', color: 'var(--text-primary)' }}>
      <div className="w-full h-screen flex overflow-hidden relative">
        <Sidebar
          sessions={app.sessions}
          activeSessionId={app.sessionId}
          onNewSession={app.handleNewSession}
          onSelectSession={app.loadSession}
          onDeleteSession={app.handleDeleteSession}
          onShowScheduledTasks={() => setShowScheduledTasks(true)}
        />
        <ChatInteractionProvider value={interactionValue}>
          <ChatArea messages={app.messages} onToggleThought={app.toggleThought} />
        </ChatInteractionProvider>

        <RightPanel
          sessionId={app.sessionId}
          tokenUsage={app.sessionTokenUsage}
          tokenHistory={app.tokenHistory}
          workflow={app.workflow}
          artifacts={app.allArtifacts}
          isStreaming={app.isStreaming}
          isPaused={app.runtimeState.isPaused}
          isContextCompressing={app.isContextCompressing}
        />

        {app.selectedPreview && (
          <div
            className="shrink-0 animate-slide-in-right"
            style={{
              width: '45vw',
              minWidth: '480px',
              maxWidth: '60vw',
              height: '100%',
              borderLeft: '1px solid var(--border)',
            }}
          >
            <FilePreviewPanel preview={app.selectedPreview} onClose={app.closePreview} />
          </div>
        )}

        <Dialog open={showScheduledTasks} onOpenChange={setShowScheduledTasks}>
          <DialogContent className="sm:max-w-2xl max-h-[85vh] flex flex-col" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2" style={{ color: 'var(--text-primary)' }}>
                <Clock size={18} style={{ color: 'var(--sand)' }} strokeWidth={1.8} />
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
